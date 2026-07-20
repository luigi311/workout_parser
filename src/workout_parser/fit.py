from itertools import pairwise

from workout_parser.errors import (
    InvalidWorkoutError,
    UnsupportedWorkoutFeatureError,
    WorkoutLimitError,
)
from workout_parser.limits import (
    MAX_DECODED_BYTES,
    MAX_REPETITIONS,
    MAX_SOURCE_BYTES,
)
from workout_parser.models import (
    DistanceDuration,
    OpenDuration,
    ParseDiagnostic,
    PointTarget,
    RangeTarget,
    RepeatBlock,
    TimeDuration,
    Workout,
    WorkoutStep,
)
from workout_parser.metadata import normalize_sport
from pathlib import Path
from fitparse import FitFile, FitParseError
from pydantic import ValidationError


def _coerce_float(v, default: float | None = None) -> float | None:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _first_non_none(d: dict, *keys):
    for k in keys:
        if k is None:
            continue
        if k in d and d[k] is not None:
            return d[k]
    return None


def _fit_custom_bounds(
    fields: dict,
    low_keys: tuple[str, ...],
    high_keys: tuple[str, ...],
    label: str,
) -> tuple[float, float] | None:
    low_raw = _first_non_none(fields, *low_keys)
    high_raw = _first_non_none(fields, *high_keys)
    if low_raw is None and high_raw is None:
        return None
    if low_raw is None or high_raw is None:
        raise InvalidWorkoutError(f"FIT {label} target requires both bounds")
    low = _coerce_float(low_raw)
    high = _coerce_float(high_raw)
    if low is None or high is None:
        raise InvalidWorkoutError(f"FIT {label} target bounds must be numeric")
    if low > high:
        raise InvalidWorkoutError(f"FIT {label} target bounds are reversed")
    return (low, high)


def _fit_zone(fields: dict, field_name: str, label: str) -> PointTarget | None:
    raw_zone = fields.get(field_name)
    if raw_zone is None:
        return None
    zone = _coerce_float(raw_zone)
    if zone is None or zone < 0 or not zone.is_integer():
        raise InvalidWorkoutError(f"FIT {label} zone must be a non-negative integer")
    return PointTarget(value=int(zone)) if zone > 0 else None


def _fit_zone_and_bounds(
    fields: dict,
    *,
    zone_field: str,
    low_keys: tuple[str, ...],
    high_keys: tuple[str, ...],
    label: str,
) -> tuple[PointTarget | None, tuple[float, float] | None]:
    zone = _fit_zone(fields, zone_field, label)
    bounds = _fit_custom_bounds(fields, low_keys, high_keys, label)
    if zone is not None and bounds is not None:
        if bounds != (0.0, 0.0):
            raise InvalidWorkoutError(
                f"FIT {label} target cannot specify both a zone and custom bounds"
            )
        bounds = None
    return zone, bounds


def _decode_fit_duration(fields: dict) -> TimeDuration | DistanceDuration | OpenDuration:
    duration_type = str(fields.get("duration_type") or "").lower()
    if duration_type == "time":
        seconds = _coerce_float(fields.get("duration_time"))
        if seconds is None or seconds <= 0:
            raise InvalidWorkoutError("FIT time duration must be positive")
        return TimeDuration(seconds=seconds)
    if duration_type == "distance":
        meters = _coerce_float(fields.get("duration_distance"))
        if meters is None or meters <= 0:
            raise InvalidWorkoutError("FIT distance duration must be positive")
        return DistanceDuration(meters=meters)
    if duration_type in {"open", "lap_button", "until_manual_lap"}:
        return OpenDuration()
    if "calorie" in duration_type:
        raise UnsupportedWorkoutFeatureError(
            "Calorie-based FIT durations are not supported"
        )
    raise UnsupportedWorkoutFeatureError(
        f"Unsupported FIT duration type: {duration_type or 'missing'}"
    )


def parse_fit_from_bytes(
    data: bytes,
    name: str | None = None,
    fallback_name: str = "Unnamed Workout",
    *,
    strict: bool = True,
) -> Workout:
    """
    Parse Intervals.icu-style FIT workouts from raw bytes, including pace/power and repeat blocks.
    """
    from io import BytesIO

    if len(data) > MAX_DECODED_BYTES:
        raise WorkoutLimitError(
            f"Embedded workout exceeds {MAX_DECODED_BYTES} decoded bytes"
        )

    try:
        ff = FitFile(BytesIO(data))
        return parse_fit(
            ff, name=name, fallback_name=fallback_name, strict=strict
        )
    except (InvalidWorkoutError, UnsupportedWorkoutFeatureError):
        raise
    except (FitParseError, ValidationError, ValueError, TypeError) as error:
        raise InvalidWorkoutError("Invalid embedded FIT workout") from error


def parse_fit_from_file(path: Path, *, strict: bool = True) -> Workout:
    """
    Parse Intervals.icu-style FIT workouts including pace/power and repeat blocks.
    """
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise WorkoutLimitError(
            f"Workout source exceeds {MAX_SOURCE_BYTES} bytes: {path}"
        )
    ff = FitFile(str(path))
    return parse_fit(ff, fallback_name=path.stem, strict=strict)


def parse_fit(
    ff: FitFile,
    name: str | None = None,
    fallback_name: str = "Unnamed Workout",
    *,
    strict: bool = True,
) -> Workout:
    """Parse Intervals.icu-style FIT workouts including pace/power and repeat blocks."""
    header_fields: dict = {}
    for message in ff.get_messages("workout"):
        header_fields = {field.name: field.value for field in message}
        break
    header_name = header_fields.get("wkt_name")
    workout_name = name or header_name or fallback_name
    sport = normalize_sport(header_fields.get("sport"))

    # ---------- first pass: collect steps & repeat markers ----------
    entries: list[dict] = []
    diagnostics: list[ParseDiagnostic] = []

    def unsupported(message: str, step_index: int) -> None:
        if strict:
            raise UnsupportedWorkoutFeatureError(message)
        diagnostics.append(
            ParseDiagnostic(
                code="unsupported_feature",
                message=message,
                step_index=step_index,
            )
        )

    def invalid_source(message: str, step_index: int) -> None:
        if strict:
            raise InvalidWorkoutError(message)
        diagnostics.append(
            ParseDiagnostic(
                code="invalid_field",
                message=message,
                step_index=step_index,
            )
        )

    def invalid_repeat(message: str, step_index: int) -> None:
        if strict:
            raise InvalidWorkoutError(message)
        diagnostics.append(
            ParseDiagnostic(
                code="invalid_repeat",
                message=message,
                step_index=step_index,
            )
        )

    for msg in ff.get_messages("workout_step"):
        fields = {f.name: f.value for f in msg}
        raw_message_index = fields.get("message_index")
        msg_idx = (
            int(raw_message_index)
            if raw_message_index is not None
            else len(entries)
        )

        dur_type = str(fields.get("duration_type") or "").lower()

        # Repeat marker?
        if "repeat_until_steps_cmplt" in dur_type:
            try:
                duration_step = fields.get("duration_step")
                repeat_steps = fields.get("repeat_steps")

                if duration_step is None or repeat_steps is None:
                    invalid_repeat(
                        "FIT repeat marker is missing its start or count", msg_idx
                    )
                    continue

                start_index = int(duration_step)
                reps = int(repeat_steps)
            except (TypeError, ValueError):
                invalid_repeat("FIT repeat marker has invalid numeric fields", msg_idx)
                continue

            if reps <= 0:
                invalid_repeat(
                    f"FIT repeat count must be positive, got {reps}", msg_idx
                )
                continue
            if reps > MAX_REPETITIONS:
                raise WorkoutLimitError(
                    f"FIT repeat count exceeds {MAX_REPETITIONS} at step {msg_idx}"
                )

            entries.append(
                {
                    "type": "repeat",
                    "message_index": msg_idx,
                    "start_index": start_index,
                    "reps": reps,
                    "name": fields.get("wkt_step_name"),
                    "notes": fields.get("notes"),
                },
            )
            continue

        try:
            duration = _decode_fit_duration(fields)
        except UnsupportedWorkoutFeatureError as error:
            unsupported(str(error), msg_idx)
            continue
        except InvalidWorkoutError as error:
            invalid_source(str(error), msg_idx)
            continue

        tgt_type = str(fields.get("target_type") or "").lower()

        power_watts = power_percent_ftp = power_zone = None
        speed_mps = speed_zone = None
        heart_rate_bpm = heart_rate_percent_max = heart_rate_zone = None
        cadence_rpm = cadence_zone = None
        try:
            if "pace" in tgt_type or "speed" in tgt_type:
                speed_zone, bounds = _fit_zone_and_bounds(
                    fields,
                    zone_field="target_speed_zone",
                    low_keys=(
                        "custom_target_speed_low",
                        "target_speed_low",
                        "custom_target_value_low",
                    ),
                    high_keys=(
                        "custom_target_speed_high",
                        "target_speed_high",
                        "custom_target_value_high",
                    ),
                    label="speed",
                )
                if bounds is not None:
                    speed_mps = RangeTarget(low=bounds[0], high=bounds[1])
            elif "power" in tgt_type:
                power_zone, bounds = _fit_zone_and_bounds(
                    fields,
                    zone_field="target_power_zone",
                    low_keys=(
                        "custom_target_power_low",
                        "target_power_low",
                        "custom_target_value_low",
                    ),
                    high_keys=(
                        "custom_target_power_high",
                        "target_power_high",
                        "custom_target_value_high",
                    ),
                    label="power",
                )
                if bounds is not None:
                    low, high = bounds
                    if low <= 1000 and high <= 1000:
                        power_percent_ftp = RangeTarget(low=low, high=high)
                    elif low > 1000 and high > 1000:
                        power_watts = RangeTarget(low=low - 1000, high=high - 1000)
                    else:
                        raise InvalidWorkoutError(
                            "FIT power bounds mix relative and absolute encodings"
                        )
            elif "heart_rate" in tgt_type or tgt_type == "hr":
                heart_rate_zone, bounds = _fit_zone_and_bounds(
                    fields,
                    zone_field="target_hr_zone",
                    low_keys=(
                        "custom_target_heart_rate_low",
                        "target_heart_rate_low",
                        "custom_target_value_low",
                    ),
                    high_keys=(
                        "custom_target_heart_rate_high",
                        "target_heart_rate_high",
                        "custom_target_value_high",
                    ),
                    label="heart-rate",
                )
                if bounds is not None:
                    low, high = bounds
                    if low <= 100 and high <= 100:
                        heart_rate_percent_max = RangeTarget(low=low, high=high)
                    elif low > 100 and high > 100:
                        heart_rate_bpm = RangeTarget(low=low - 100, high=high - 100)
                    else:
                        raise InvalidWorkoutError(
                            "FIT heart-rate bounds mix relative and absolute encodings"
                        )
            elif "cadence" in tgt_type:
                cadence_zone, bounds = _fit_zone_and_bounds(
                    fields,
                    zone_field="target_cadence_zone",
                    low_keys=(
                        "custom_target_cadence_low",
                        "target_cadence_low",
                        "custom_target_value_low",
                    ),
                    high_keys=(
                        "custom_target_cadence_high",
                        "target_cadence_high",
                        "custom_target_value_high",
                    ),
                    label="cadence",
                )
                if bounds is not None:
                    cadence_rpm = RangeTarget(low=bounds[0], high=bounds[1])
            elif tgt_type not in {"", "open", "no_target"}:
                unsupported(f"Unsupported FIT target type: {tgt_type}", msg_idx)
        except InvalidWorkoutError as error:
            invalid_source(str(error), msg_idx)

        step = WorkoutStep(
            name=fields.get("wkt_step_name"),
            notes=fields.get("notes"),
            duration=duration,
            power_watts=power_watts,
            power_percent_ftp=power_percent_ftp,
            power_zone=power_zone,
            speed_mps=speed_mps,
            speed_zone=speed_zone,
            heart_rate_bpm=heart_rate_bpm,
            heart_rate_percent_max=heart_rate_percent_max,
            heart_rate_zone=heart_rate_zone,
            cadence_rpm=cadence_rpm,
            cadence_zone=cadence_zone,
        )

        entries.append({"type": "step", "message_index": msg_idx, "step": step})

    # ---------- second pass: normalize repeat references into a tree ----------
    entries.sort(key=lambda e: e["message_index"])
    unique_entries: list[dict] = []
    seen_indices: set[int] = set()
    for entry in entries:
        message_index = int(entry["message_index"])
        if message_index in seen_indices:
            invalid_repeat(
                f"Duplicate FIT workout-step index {message_index}", message_index
            )
            continue
        seen_indices.add(message_index)
        unique_entries.append(entry)

    records: list[dict] = []

    for e in unique_entries:
        if e["type"] == "step":
            message_index = int(e["message_index"])
            records.append(
                {
                    "start": message_index,
                    "end": message_index,
                    "instruction": e["step"],
                }
            )
            continue

        start = int(e["start_index"])
        marker_index = int(e["message_index"])
        repetitions = int(e["reps"])
        block_position = next(
            (
                index
                for index, record in enumerate(records)
                if int(record["start"]) == start
            ),
            None,
        )
        block_records = (
            records[block_position:] if block_position is not None else []
        )
        contiguous = bool(block_records) and int(block_records[-1]["end"]) == (
            marker_index - 1
        )
        contiguous = contiguous and all(
            int(right["start"]) == int(left["end"]) + 1
            for left, right in pairwise(block_records)
        )
        if not contiguous:
            invalid_repeat(
                f"FIT repeat at index {marker_index} does not reference a valid "
                f"contiguous block starting at {start}",
                marker_index,
            )
            continue

        instructions = [record["instruction"] for record in block_records]
        del records[block_position:]
        records.append(
            {
                "start": start,
                "end": marker_index,
                "instruction": RepeatBlock(
                    name=e.get("name"),
                    notes=e.get("notes"),
                    repetitions=repetitions,
                    instructions=instructions,
                ),
            }
        )

    workout = Workout(
        name=workout_name,
        sport=sport,
        instructions=[record["instruction"] for record in records],
        diagnostics=diagnostics,
    )
    if not workout.instructions and not (not strict and workout.diagnostics):
        raise InvalidWorkoutError("Workout contains no usable instructions")
    return workout
