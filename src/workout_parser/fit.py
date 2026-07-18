from math import floor
from workout_parser.models import PointTarget, RangeTarget, Workout, WorkoutStep
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


def parse_fit_from_bytes(data: bytes, name: str = "Unnamed Workout") -> Workout:
    """
    Parse Intervals.icu-style FIT workouts from raw bytes, including pace/power and repeat blocks.
    """
    from io import BytesIO

    ff = FitFile(BytesIO(data))

    return parse_fit(ff, name=name)


def parse_fit_from_file(path: Path) -> Workout:
    """
    Parse Intervals.icu-style FIT workouts including pace/power and repeat blocks.
    """
    ff = FitFile(str(path))
    return parse_fit(ff, name=path.stem)


def parse_fit(ff: FitFile, name: str = "Unnamed Workout") -> Workout:
    """Parse Intervals.icu-style FIT workouts including pace/power and repeat blocks."""
    # ---------- first pass: collect steps & repeat markers ----------
    entries: list[dict] = []
    for msg in ff.get_messages("workout_step"):
        fields = {f.name: f.value for f in msg}
        msg_idx = int(fields.get("message_index") or len(entries))

        # Duration
        duration_s = None
        dt_time = _coerce_float(fields.get("duration_time"))
        dt_val = _coerce_float(fields.get("duration_value"))
        if dt_time is not None and dt_time > 0:
            duration_s = dt_time
        elif dt_val is not None and dt_val > 0:
            duration_s = dt_val

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

        # Skip non-time steps
        if duration_s is None or duration_s <= 0:
            continue

        tgt_type = str(fields.get("target_type") or "").lower()

        # ---------- targets ----------
        speed_mid = None
        speed_lo = speed_hi = None

        watts_mid = None
        watts_lo = watts_hi = None

        percent_watts_mid = None
        percent_watts_lo = percent_watts_hi = None

        # PACE / SPEED
        if ("pace" in tgt_type) or ("speed" in tgt_type):
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
            mid_raw = fields.get("target_value")

            speed_lo = _coerce_float(lo_raw)
            speed_hi = _coerce_float(hi_raw)
            speed_mid = _coerce_float(mid_raw)

        # POWER
        elif "power" in tgt_type:
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
            mid_raw = fields.get("target_value")
            lo_f = _coerce_float(lo_raw)
            hi_f = _coerce_float(hi_raw)
            mid_f = _coerce_float(mid_raw)

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

            if mid_f:
                if mid_f > 1000:
                    watts_mid = mid_f - 1000
                else:
                    percent_watts_mid = mid_f

        # ---------- build step (prefer power, then pace; else duration-only) ----------
        power_watts = None
        if watts_lo is not None and watts_hi is not None:
            power_watts = RangeTarget(
                low=floor(watts_lo), high=floor(watts_hi)
            )
        elif watts_mid is not None:
            power_watts = PointTarget(value=floor(watts_mid))

        power_percent_ftp = None
        if percent_watts_lo is not None and percent_watts_hi is not None:
            power_percent_ftp = RangeTarget(
                low=percent_watts_lo, high=percent_watts_hi
            )
        elif percent_watts_mid is not None:
            power_percent_ftp = PointTarget(value=percent_watts_mid)

        speed_mps = None
        if speed_lo is not None and speed_hi is not None:
            speed_mps = RangeTarget(low=speed_lo, high=speed_hi)
        elif speed_mid is not None:
            speed_mps = PointTarget(value=speed_mid)

        step = WorkoutStep(
            duration_s=duration_s,
            power_watts=power_watts,
            power_percent_ftp=power_percent_ftp,
            speed_mps=speed_mps,
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

    return Workout(name=name, steps=final_steps)
