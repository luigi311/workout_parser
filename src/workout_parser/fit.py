from math import floor
from workout_parser.errors import UnsupportedWorkoutFeatureError
from workout_parser.models import (
    DistanceDuration,
    OpenDuration,
    ParseDiagnostic,
    PointTarget,
    RangeTarget,
    TimeDuration,
    Workout,
    WorkoutStep,
)
from pathlib import Path
from fitparse import FitFile


def _coerce_float(v, default: float | None = None) -> float | None:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _first_non_none(d: dict, *keys):
    for k in keys:
        if k is None:
            continue
        if k in d and d[k] is not None:
            return d[k]
    return None


def parse_fit_from_bytes(
    data: bytes, name: str = "Unnamed Workout", *, strict: bool = True
) -> Workout:
    """
    Parse Intervals.icu-style FIT workouts from raw bytes, including pace/power and repeat blocks.
    """
    from io import BytesIO

    ff = FitFile(BytesIO(data))

    return parse_fit(ff, name=name, strict=strict)


def parse_fit_from_file(path: Path, *, strict: bool = True) -> Workout:
    """
    Parse Intervals.icu-style FIT workouts including pace/power and repeat blocks.
    """
    ff = FitFile(str(path))
    return parse_fit(ff, name=path.stem, strict=strict)


def parse_fit(
    ff: FitFile, name: str = "Unnamed Workout", *, strict: bool = True
) -> Workout:
    """Parse Intervals.icu-style FIT workouts including pace/power and repeat blocks."""
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

    for msg in ff.get_messages("workout_step"):
        fields = {f.name: f.value for f in msg}
        msg_idx = int(fields.get("message_index") or len(entries))

        dur_type = str(fields.get("duration_type") or "").lower()

        # Repeat marker?
        if "repeat_until_steps_cmplt" in dur_type:
            try:
                duration_step = fields.get("duration_step")
                repeat_steps = fields.get("repeat_steps")

                if duration_step is None or repeat_steps is None:
                    continue

                start_index = int(duration_step)
                reps = int(repeat_steps)
            except Exception:
                continue

            entries.append(
                {
                    "type": "repeat",
                    "message_index": msg_idx,
                    "start_index": start_index,
                    "reps": reps,
                },
            )
            continue

        # Decode duration according to its declared FIT type.
        duration = None
        if dur_type == "time":
            seconds = _coerce_float(fields.get("duration_time"))
            if seconds is not None and seconds > 0:
                duration = TimeDuration(seconds=seconds)
        elif "distance" in dur_type:
            meters = _coerce_float(fields.get("duration_distance"))
            if meters is not None and meters > 0:
                duration = DistanceDuration(meters=meters)
        elif dur_type in {"open", "lap_button", "until_manual_lap"}:
            duration = OpenDuration()
        elif "calorie" in dur_type:
            unsupported("Calorie-based FIT durations are not supported", msg_idx)
            continue
        else:
            unsupported(
                f"Unsupported FIT duration type: {dur_type or 'missing'}", msg_idx
            )
            continue

        if duration is None:
            unsupported(f"Invalid FIT {dur_type} duration", msg_idx)
            continue

        tgt_type = str(fields.get("target_type") or "").lower()

        # ---------- targets ----------
        speed_lo = speed_hi = None

        watts_lo = watts_hi = None

        percent_watts_lo = percent_watts_hi = None

        heart_rate_bpm = None
        heart_rate_percent_max = None
        cadence_rpm = None
        power_zone = None
        speed_zone = None
        heart_rate_zone = None
        cadence_zone = None

        def fit_zone(field_name: str):
            zone = _coerce_float(fields.get(field_name))
            return PointTarget(value=zone) if zone is not None and zone > 0 else None

        # PACE / SPEED
        if ("pace" in tgt_type) or ("speed" in tgt_type):
            speed_zone = fit_zone("target_speed_zone")
            lo_raw = _first_non_none(
                fields,
                "custom_target_speed_low",
                "target_speed_low",
                # some files abuse generic value fields; allow if labeled as pace/speed
                "custom_target_value_low"
                if ("pace" in tgt_type or "speed" in tgt_type)
                else None,
            )
            hi_raw = _first_non_none(
                fields,
                "custom_target_speed_high",
                "target_speed_high",
                "custom_target_value_high"
                if ("pace" in tgt_type or "speed" in tgt_type)
                else None,
            )
            speed_lo = _coerce_float(lo_raw)
            speed_hi = _coerce_float(hi_raw)

        # POWER
        elif "power" in tgt_type:
            power_zone = fit_zone("target_power_zone")
            lo_raw = _first_non_none(
                fields,
                "custom_target_power_low",
                "target_power_low",
                # allow generic value fields if type is power (but not percent)
                "custom_target_value_low"
                if ("percent" not in tgt_type and "ftp" not in tgt_type)
                else None,
            )
            hi_raw = _first_non_none(
                fields,
                "custom_target_power_high",
                "target_power_high",
                "custom_target_value_high"
                if ("percent" not in tgt_type and "ftp" not in tgt_type)
                else None,
            )
            lo_f = _coerce_float(lo_raw)
            hi_f = _coerce_float(hi_raw)

            # Based on the fit spec
            # Values < 1000 are percentage of ftp based
            # Values > 1000 are absolute watts shifted by 1000
            if lo_f and hi_f:
                if lo_f > 1000:
                    watts_lo = lo_f - 1000
                else:
                    percent_watts_lo = lo_f

                if hi_f > 1000:
                    watts_hi = hi_f - 1000
                else:
                    percent_watts_hi = hi_f

        # HEART RATE
        elif "heart_rate" in tgt_type or tgt_type == "hr":
            heart_rate_zone = fit_zone("target_hr_zone")
            lo_f = _coerce_float(
                _first_non_none(
                    fields,
                    "custom_target_heart_rate_low",
                    "target_heart_rate_low",
                    "custom_target_value_low",
                )
            )
            hi_f = _coerce_float(
                _first_non_none(
                    fields,
                    "custom_target_heart_rate_high",
                    "target_heart_rate_high",
                    "custom_target_value_high",
                )
            )
            if (
                lo_f is not None
                and hi_f is not None
                and (lo_f != 0 or hi_f != 0)
            ):
                if lo_f > 100 and hi_f > 100:
                    heart_rate_bpm = RangeTarget(low=lo_f - 100, high=hi_f - 100)
                elif lo_f <= 100 and hi_f <= 100:
                    heart_rate_percent_max = RangeTarget(low=lo_f, high=hi_f)
                else:
                    unsupported("Mixed FIT heart-rate target encodings", msg_idx)

        # CADENCE
        elif "cadence" in tgt_type:
            cadence_zone = fit_zone("target_cadence_zone")
            lo_f = _coerce_float(
                _first_non_none(
                    fields,
                    "custom_target_cadence_low",
                    "target_cadence_low",
                    "custom_target_value_low",
                )
            )
            hi_f = _coerce_float(
                _first_non_none(
                    fields,
                    "custom_target_cadence_high",
                    "target_cadence_high",
                    "custom_target_value_high",
                )
            )
            if (
                lo_f is not None
                and hi_f is not None
                and (lo_f != 0 or hi_f != 0)
            ):
                cadence_rpm = RangeTarget(low=lo_f, high=hi_f)
        elif tgt_type not in {"", "open", "no_target"}:
            unsupported(f"Unsupported FIT target type: {tgt_type}", msg_idx)

        # ---------- build step (prefer power, then pace; else duration-only) ----------
        power_watts = None
        if watts_lo is not None and watts_hi is not None:
            power_watts = RangeTarget(
                low=floor(watts_lo), high=floor(watts_hi)
            )

        power_percent_ftp = None
        if percent_watts_lo is not None and percent_watts_hi is not None:
            power_percent_ftp = RangeTarget(
                low=percent_watts_lo, high=percent_watts_hi
            )

        speed_mps = None
        if (
            speed_lo is not None
            and speed_hi is not None
            and (speed_lo != 0 or speed_hi != 0)
        ):
            speed_mps = RangeTarget(low=speed_lo, high=speed_hi)

        step = WorkoutStep(
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

    # ---------- second pass: expand repeats ----------
    entries.sort(key=lambda e: e["message_index"])
    final_steps: list[WorkoutStep] = []

    for e in entries:
        if e["type"] == "step":
            final_steps.append(e["step"])
        else:
            start = int(e["start_index"])
            end = int(e["message_index"])
            reps = int(e["reps"])
            # Collect the block of steps between start..end (message_index)
            block: list[WorkoutStep] = []
            for e2 in entries:
                if e2["type"] != "step":
                    continue
                mi = int(e2["message_index"])
                if start <= mi < end:
                    block.append(e2["step"])
            if not block or reps <= 1:
                continue
            # Append (reps-1) more copies
            for _ in range(reps - 1):
                for s in block:
                    final_steps.append(WorkoutStep(**s.__dict__))

    return Workout(name=name, steps=final_steps, diagnostics=diagnostics)
