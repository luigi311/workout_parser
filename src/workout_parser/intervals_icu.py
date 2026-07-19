from __future__ import annotations
import base64
import binascii
from datetime import date
from math import floor
from pathlib import Path
from workout_parser.models import (
    DistanceDuration,
    OpenDuration,
    ParseDiagnostic,
    PointTarget,
    RampTarget,
    RangeTarget,
    RepeatBlock,
    TimeDuration,
    Workout,
    WorkoutStep,
)
from workout_parser.errors import (
    InvalidWorkoutError,
    UnsupportedFormatError,
    UnsupportedWorkoutFeatureError,
)
from workout_parser.metadata import normalize_sport

import json


# -----------------------
# Intervals.icu JSON parser
# -----------------------


def _coerce_float(v, default: float | None = None) -> float | None:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _target_from_icu(
    metadata: object,
    *,
    ramp: bool,
    integer: bool = False,
) -> PointTarget | RangeTarget | RampTarget | None:
    if not isinstance(metadata, dict):
        return None

    value = _coerce_float(metadata.get("value"))
    start = _coerce_float(metadata.get("start"))
    end = _coerce_float(metadata.get("end"))

    if integer:
        value = floor(value) if value is not None else None
        start = floor(start) if start is not None else None
        end = floor(end) if end is not None else None

    if ramp and start is not None and end is not None:
        return RampTarget(start=start, end=end)
    if start is not None and end is not None:
        return RangeTarget(low=start, high=end)
    if value is not None:
        return PointTarget(value=value)
    return None


def _power_comparison_pairs(
    relative: PointTarget | RangeTarget | RampTarget,
    absolute: PointTarget | RangeTarget | RampTarget,
) -> list[tuple[int | float, int | float]] | None:
    if isinstance(relative, PointTarget):
        if isinstance(absolute, PointTarget):
            return [(relative.value, absolute.value)]
        if isinstance(absolute, RangeTarget):
            return [(relative.value, absolute.mid)]
        return None
    if isinstance(relative, RangeTarget) and isinstance(absolute, RangeTarget):
        return [(relative.low, absolute.low), (relative.high, absolute.high)]
    if isinstance(relative, RampTarget) and isinstance(absolute, RampTarget):
        return [(relative.start, absolute.start), (relative.end, absolute.end)]
    return None


def _validate_resolved_power_targets(
    instructions: list[WorkoutStep | RepeatBlock],
    source_ftp_watts: int | float | None,
    *,
    strict: bool,
    diagnostics: list[ParseDiagnostic],
) -> None:
    if source_ftp_watts is None:
        return

    def walk(
        nested: list[WorkoutStep | RepeatBlock],
    ):
        for instruction in nested:
            if isinstance(instruction, WorkoutStep):
                yield instruction
            else:
                yield from walk(instruction.instructions)

    for step_index, step in enumerate(walk(instructions)):
        relative = step.power_percent_ftp
        absolute = step.power_watts
        if relative is None or absolute is None:
            continue

        pairs = _power_comparison_pairs(relative, absolute)
        inconsistent = pairs is None or any(
            abs(float(percent) * float(source_ftp_watts) / 100.0 - float(watts))
            > 1.0
            for percent, watts in (pairs or [])
        )
        if not inconsistent:
            continue

        message = (
            f"Step {step_index} resolved watts conflict with its %FTP target "
            f"at source FTP {source_ftp_watts} W"
        )
        if strict:
            raise InvalidWorkoutError(message)
        diagnostics.append(
            ParseDiagnostic(
                code="conflicting_resolved_power",
                message=message,
                step_index=step_index,
            )
        )


def _parse_workout_date(
    value: object,
    *,
    strict: bool,
    diagnostics: list[ParseDiagnostic],
) -> date | None:
    if value is None:
        return None
    try:
        if not isinstance(value, str):
            raise ValueError("date must be a string")
        return date.fromisoformat(value.split("T", 1)[0])
    except ValueError as error:
        message = f"Invalid workout date: {value!r}"
        if strict:
            raise InvalidWorkoutError(message) from error
        diagnostics.append(ParseDiagnostic(code="invalid_date", message=message))
        return None


def _parse_icu_instructions(
    steps: list[dict], *, strict: bool
) -> tuple[list[WorkoutStep | RepeatBlock], list[ParseDiagnostic]]:
    """
    Convert Intervals.icu 'steps' (which may include nested sets with 'reps')
    into a flat list of WorkoutStep, capturing explicit bands when present.
    """
    instructions: list[WorkoutStep | RepeatBlock] = []
    diagnostics: list[ParseDiagnostic] = []
    source_index = 0

    def invalid_repeat(message: str, step_index: int) -> None:
        diagnostic = ParseDiagnostic(
            code="invalid_repeat",
            message=message,
            step_index=step_index,
        )
        if strict:
            raise InvalidWorkoutError(message)
        diagnostics.append(diagnostic)

    def unsupported(message: str, step_index: int) -> None:
        diagnostic = ParseDiagnostic(
            code="unsupported_feature", message=message, step_index=step_index
        )
        if strict:
            raise UnsupportedWorkoutFeatureError(message)
        diagnostics.append(diagnostic)

    def handle_step(node: dict) -> WorkoutStep | RepeatBlock | None:
        nonlocal source_index
        step_index = source_index
        source_index += 1

        # If it declares repeat structure, validate the whole block.
        if "reps" in node or "steps" in node:
            if not isinstance(node.get("steps"), list):
                invalid_repeat("Repeat block must contain a steps list", step_index)
                return None
            repetitions = node.get("reps")
            if (
                not isinstance(repetitions, int)
                or isinstance(repetitions, bool)
                or repetitions <= 0
            ):
                invalid_repeat(
                    f"Repeat count must be a positive integer, got {repetitions!r}",
                    step_index,
                )
                return None

            nested: list[WorkoutStep | RepeatBlock] = []
            for sub in node["steps"]:
                instruction = handle_step(sub)
                if instruction is not None:
                    nested.append(instruction)
            if not nested:
                invalid_repeat("Repeat block cannot be empty", step_index)
                return None
            return RepeatBlock(
                name=node.get("text"),
                repetitions=repetitions,
                instructions=nested,
            )

        calories = _coerce_float(node.get("calories"))
        distance = _coerce_float(node.get("distance"))
        seconds = _coerce_float(node.get("duration"))
        if calories is not None and calories > 0:
            unsupported("Calorie-based durations are not supported", step_index)
            return None
        if node.get("until_lap_press") is True:
            duration = OpenDuration()
        elif distance is not None and distance > 0:
            duration = DistanceDuration(meters=distance)
        elif seconds is not None and seconds > 0:
            duration = TimeDuration(seconds=seconds)
        else:
            unsupported(
                "Step has no supported time, distance, or open duration", step_index
            )
            return None

        ramp = node.get("ramp") is True

        # -------- Pace parsing --------
        p_abs_meta = node.get("_pace")
        speed_target = _target_from_icu(p_abs_meta, ramp=ramp)

        p_per_meta = node.get("pace")
        percent_speed_target = None
        speed_zone = None
        if isinstance(p_per_meta, dict):
            units = (p_per_meta.get("units") or "").casefold()
            if "%pace" in units:
                percent_speed_target = _target_from_icu(p_per_meta, ramp=ramp)
            elif "zone" in units:
                speed_zone = _target_from_icu(p_per_meta, ramp=ramp)
            elif speed_target is None:
                unsupported(
                    f"Unsupported pace units: {units or 'missing'}", step_index
                )

        # -------- Power parsing --------
        pw_abs_meta = node.get("_power")
        power_target = _target_from_icu(pw_abs_meta, ramp=ramp, integer=True)

        pw_per_meta = node.get("power")
        percent_power_target = None
        power_zone = None
        if isinstance(pw_per_meta, dict):
            units = (pw_per_meta.get("units") or "").casefold()
            if "%power" in units or "ftp" in units:
                percent_power_target = _target_from_icu(pw_per_meta, ramp=ramp)
            elif "zone" in units:
                power_zone = _target_from_icu(pw_per_meta, ramp=ramp)
            elif power_target is None:
                unsupported(
                    f"Unsupported power units: {units or 'missing'}", step_index
                )

        # -------- Heart-rate parsing --------
        heart_rate_target = _target_from_icu(node.get("_hr"), ramp=ramp)
        heart_rate_percent_max = None
        heart_rate_percent_lthr = None
        heart_rate_zone = None
        hr_meta = node.get("hr")
        if isinstance(hr_meta, dict):
            units = str(hr_meta.get("units") or "").casefold()
            if "zone" in units:
                heart_rate_zone = _target_from_icu(hr_meta, ramp=ramp)
            elif units in {"bpm", "beats/min", "beats_per_minute"}:
                if heart_rate_target is None:
                    heart_rate_target = _target_from_icu(hr_meta, ramp=ramp)
            elif "lthr" in units or "threshold" in units:
                heart_rate_percent_lthr = _target_from_icu(hr_meta, ramp=ramp)
            elif "max" in units and "%" in units:
                heart_rate_percent_max = _target_from_icu(hr_meta, ramp=ramp)
            elif heart_rate_target is None:
                unsupported(
                    f"Unsupported heart-rate units: {units or 'missing'}",
                    step_index,
                )

        # -------- Cadence parsing --------
        cadence_target = _target_from_icu(node.get("_cadence"), ramp=ramp)
        cadence_zone = None
        cadence_meta = node.get("cadence")
        if isinstance(cadence_meta, dict):
            units = str(cadence_meta.get("units") or "").casefold()
            if "zone" in units:
                cadence_zone = _target_from_icu(cadence_meta, ramp=ramp)
            elif "rpm" in units or units in {"cadence", ""}:
                if cadence_target is None:
                    cadence_target = _target_from_icu(cadence_meta, ramp=ramp)
            else:
                unsupported(f"Unsupported cadence units: {units}", step_index)

        step = WorkoutStep(
            instruction=node.get("text"),
            duration=duration,
            power_watts=power_target,
            power_percent_ftp=percent_power_target,
            power_zone=power_zone,
            speed_mps=speed_target,
            speed_percent_threshold=percent_speed_target,
            speed_zone=speed_zone,
            heart_rate_bpm=heart_rate_target,
            heart_rate_percent_max=heart_rate_percent_max,
            heart_rate_percent_lthr=heart_rate_percent_lthr,
            heart_rate_zone=heart_rate_zone,
            cadence_rpm=cadence_target,
            cadence_zone=cadence_zone,
        )
        return step

    for s in steps:
        instruction = handle_step(s)
        if instruction is not None:
            instructions.append(instruction)
    return instructions, diagnostics


def parse_intervals_icu_json(
    data: dict, path: Path, *, strict: bool = True
) -> Workout:
    """Parse Intervals.icu exported workout JSON (running/cycling)."""
    if not isinstance(data, dict):
        raise InvalidWorkoutError("Intervals.icu JSON root must be an object")

    name = data.get("name") or path.stem

    # Check if the json is in the Intervals.icu API format with a base64-encoded workout file; if so, decode and parse that instead of the JSON steps
    if "workout_filename" in data and "workout_file_base64" in data:
        filename = data["workout_filename"]
        if not isinstance(filename, str) or not filename:
            raise InvalidWorkoutError(
                "Intervals.icu API wrapper has an invalid workout filename"
            )
        try:
            decoded_bytes = base64.b64decode(data["workout_file_base64"])
        except (TypeError, ValueError, binascii.Error) as error:
            raise InvalidWorkoutError(
                "Failed to decode Intervals.icu API workout payload"
            ) from error

        embedded_suffix = Path(filename).suffix.lower()
        if embedded_suffix == ".json":
            try:
                decoded_data = json.loads(decoded_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise InvalidWorkoutError(
                    "Failed to parse embedded Intervals.icu workout JSON"
                ) from error
            workout = parse_intervals_icu_json(
                decoded_data, Path(filename), strict=strict
            )

        elif embedded_suffix == ".fit":
            # If its a .fit file then call the fit parser on the decoded bytes
            from workout_parser.fit import parse_fit_from_bytes

            workout = parse_fit_from_bytes(
                decoded_bytes,
                name=data.get("name") or None,
                fallback_name=Path(filename).stem,
                strict=strict,
            )
        else:
            raise UnsupportedFormatError(
                f"Unsupported embedded workout format: {filename}"
            )

        updates = {}
        if data.get("name"):
            updates["name"] = data["name"]
        if data.get("description") is not None:
            updates["description"] = data["description"]
        wrapper_sport = normalize_sport(data.get("type") or data.get("sport"))
        if wrapper_sport is not None:
            updates["sport"] = wrapper_sport
        diagnostics = list(workout.diagnostics)
        if data.get("start_date_local") is not None:
            wrapper_date = _parse_workout_date(
                data["start_date_local"],
                strict=strict,
                diagnostics=diagnostics,
            )
            if wrapper_date is not None:
                updates["workout_date"] = wrapper_date
        updates["diagnostics"] = tuple(diagnostics)
        return workout.model_copy(update=updates, deep=True)

    steps_in = data.get("steps") or []
    if not isinstance(steps_in, list):
        raise InvalidWorkoutError("Intervals.icu steps must be a list")
    instructions, diagnostics = _parse_icu_instructions(steps_in, strict=strict)
    source_ftp_watts = _coerce_float(data.get("ftp"))
    _validate_resolved_power_targets(
        instructions,
        source_ftp_watts,
        strict=strict,
        diagnostics=diagnostics,
    )

    workout = Workout(
        name=name,
        description=data.get("description"),
        workout_date=_parse_workout_date(
            data.get("start_date_local"),
            strict=strict,
            diagnostics=diagnostics,
        ),
        sport=normalize_sport(data.get("type") or data.get("sport")),
        source_ftp_watts=source_ftp_watts,
        instructions=instructions,
        diagnostics=diagnostics,
    )
    if not workout.instructions and not (not strict and workout.diagnostics):
        raise InvalidWorkoutError("Workout contains no usable instructions")
    return workout


def parse_intervals_icu_json_file(path: Path, *, strict: bool = True) -> Workout:
    """Parse Intervals.icu exported workout JSON (running/cycling)."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return parse_intervals_icu_json(data, path, strict=strict)
