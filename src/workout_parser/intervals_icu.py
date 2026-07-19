from __future__ import annotations
import base64
from math import floor
from datetime import date
from workout_parser.models import (
    DistanceDuration,
    OpenDuration,
    ParseDiagnostic,
    PointTarget,
    RampTarget,
    RangeTarget,
    TimeDuration,
    Workout,
    WorkoutStep,
)
from workout_parser.errors import InvalidWorkoutError, UnsupportedWorkoutFeatureError

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


# -----------------------
# Intervals.icu JSON parser
# -----------------------


def _coerce_float(v, default: float | None = None) -> float | None:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
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
    steps: list[WorkoutStep],
    source_ftp_watts: int | float | None,
    *,
    strict: bool,
    diagnostics: list[ParseDiagnostic],
) -> None:
    if source_ftp_watts is None:
        return

    for step_index, step in enumerate(steps):
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


def _flatten_icu_steps(
    steps: list[dict], *, strict: bool
) -> tuple[list[WorkoutStep], list[ParseDiagnostic]]:
    """
    Convert Intervals.icu 'steps' (which may include nested sets with 'reps')
    into a flat list of WorkoutStep, capturing explicit bands when present.
    """
    flat: list[WorkoutStep] = []
    diagnostics: list[ParseDiagnostic] = []

    def unsupported(message: str) -> None:
        diagnostic = ParseDiagnostic(
            code="unsupported_feature",
            message=message,
            step_index=len(flat),
        )
        if strict:
            raise UnsupportedWorkoutFeatureError(message)
        diagnostics.append(diagnostic)

    def handle_step(node: dict, text: str | None = None):
        # If it's a repeated block with 'reps'
        if "reps" in node and isinstance(node.get("steps"), list):
            reps = int(node.get("reps", 1) or 1)
            for _ in range(max(1, reps)):
                for sub in node["steps"]:
                    sub_text = sub.get("text") or text
                    handle_step(sub, text=sub_text)
            return

        calories = _coerce_float(node.get("calories"))
        distance = _coerce_float(node.get("distance"))
        seconds = _coerce_float(node.get("duration"))
        if calories is not None and calories > 0:
            unsupported("Calorie-based durations are not supported")
            return
        if node.get("until_lap_press") is True:
            duration = OpenDuration()
        elif distance is not None and distance > 0:
            duration = DistanceDuration(meters=distance)
        elif seconds is not None and seconds > 0:
            duration = TimeDuration(seconds=seconds)
        else:
            unsupported("Step has no supported time, distance, or open duration")
            return

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
                unsupported(f"Unsupported pace units: {units or 'missing'}")

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
                unsupported(f"Unsupported power units: {units or 'missing'}")

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
                unsupported(f"Unsupported heart-rate units: {units or 'missing'}")

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
                unsupported(f"Unsupported cadence units: {units}")

        step = WorkoutStep(
            text=text,
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
        flat.append(step)

    for s in steps:
        text = s.get("text")
        handle_step(s, text=text)
    return flat, diagnostics


def parse_intervals_icu_json(
    data: dict, path: Path, *, strict: bool = True
) -> Workout:
    """Parse Intervals.icu exported workout JSON (running/cycling)."""

    name = data.get("name") or path.stem

    # Check if the json is in the Intervals.icu API format with a base64-encoded workout file; if so, decode and parse that instead of the JSON steps
    if "workout_filename" in data and "workout_file_base64" in data:
        filename = data["workout_filename"]
        try:
            decoded_bytes = base64.b64decode(data["workout_file_base64"])
        except Exception as e:
            raise ValueError(f"Failed to decode Intervals.icu API workout JSON: {e}")

        if filename.endswith(".json"):
            try:
                decoded_data = json.loads(decoded_bytes)
                workout = parse_intervals_icu_json(decoded_data, path, strict=strict)
            except (InvalidWorkoutError, UnsupportedWorkoutFeatureError):
                raise
            except Exception as e:
                raise ValueError(
                    f"Failed to parse decoded Intervals.icu workout JSON: {e}"
                )

        elif filename.endswith(".fit"):
            # If its a .fit file then call the fit parser on the decoded bytes
            from workout_parser.fit import parse_fit_from_bytes

            workout = parse_fit_from_bytes(decoded_bytes, name=name, strict=strict)
        else:
            raise ValueError(
                f"Unsupported workout file type in Intervals.icu API JSON: {filename}"
            )

        # Parse out the name from the original JSON if available, otherwise use the filename stem
        workout.name = data.get("name") or Path(filename).stem
        # Parse out the description from the original JSON if available
        workout.description = data.get("description")
        # Parse out the workout date from the original JSON if available
        workout_date_str = data.get("start_date_local")
        if workout_date_str:
            try:
                # Parse out the date from 2026-04-07T08:00:00
                workout.workout_date = date.fromisoformat(
                    workout_date_str.split("T")[0]
                )
            except Exception:
                pass  # Ignore date parsing errors and leave workout_date as None
        return workout

    steps_in = data.get("steps") or []
    steps, diagnostics = _flatten_icu_steps(steps_in, strict=strict)
    source_ftp_watts = _coerce_float(data.get("ftp"))
    _validate_resolved_power_targets(
        steps,
        source_ftp_watts,
        strict=strict,
        diagnostics=diagnostics,
    )

    return Workout(
        name=name,
        source_ftp_watts=source_ftp_watts,
        steps=steps,
        diagnostics=diagnostics,
    )


def parse_intervals_icu_json_file(path: Path, *, strict: bool = True) -> Workout:
    """Parse Intervals.icu exported workout JSON (running/cycling)."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return parse_intervals_icu_json(data, path, strict=strict)
