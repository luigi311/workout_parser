from __future__ import annotations
import base64
from math import floor
from datetime import date
from workout_parser.models import WorkoutStep, Workout

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


def _flatten_icu_steps(steps: list[dict]) -> list[WorkoutStep]:
    """
    Convert Intervals.icu 'steps' (which may include nested sets with 'reps')
    into a flat list of WorkoutStep, capturing explicit bands when present.
    """
    flat: list[WorkoutStep] = []

    def handle_step(node: dict, text: str | None = None):
        # If it's a repeated block with 'reps'
        if "reps" in node and isinstance(node.get("steps"), list):
            reps = int(node.get("reps", 1) or 1)
            for _ in range(max(1, reps)):
                for sub in node["steps"]:
                    sub_text = sub.get("text") or text
                    handle_step(sub, text=sub_text)
            return

        dur = _coerce_float(node.get("duration"), 0.0) or 0.0
        if dur <= 0:
            return

        # Targets we might parse
        speed_mid = None
        speed_lo = speed_hi = None

        watts_mid = None
        watts_lo = watts_hi = None

        percent_watts_mid = None
        percent_watts_lo = percent_watts_hi = None

        percent_speed_mid = None
        percent_speed_lo = percent_speed_hi = None

        # -------- Pace parsing --------
        p_abs_meta = node.get("_pace")
        if isinstance(p_abs_meta, dict):
            if p_abs_meta.get("value") is not None:
                speed_mid = _coerce_float(p_abs_meta.get("value"))

            s0 = _coerce_float(p_abs_meta.get("start"))
            s1 = _coerce_float(p_abs_meta.get("end"))

            if s0 is not None and s1 is not None:
                speed_lo, speed_hi = s0, s1

        p_per_meta = node.get("pace")
        if isinstance(p_per_meta, dict):
            units = (p_per_meta.get("units") or "").casefold()
            if "%pace" in units:
                if p_per_meta.get("value") is not None:
                    percent_speed_mid = _coerce_float(p_per_meta.get("value"))

                s0 = _coerce_float(p_per_meta.get("start"))
                s1 = _coerce_float(p_per_meta.get("end"))

                if s0 is not None and s1 is not None:
                    percent_speed_lo, percent_speed_hi = s0, s1

        # -------- Power parsing --------
        pw_abs_meta = node.get("_power")
        if isinstance(pw_abs_meta, dict):
            if pw_abs_meta.get("value") is not None:
                watts_mid = _coerce_float(pw_abs_meta.get("value"))

            w0 = _coerce_float(pw_abs_meta.get("start"))
            w1 = _coerce_float(pw_abs_meta.get("end"))

            if w0 is not None and w1 is not None:
                watts_lo, watts_hi = w0, w1

        pw_per_meta = node.get("power")
        if watts_mid is None and isinstance(pw_per_meta, dict):
            units = (pw_per_meta.get("units") or "").casefold()
            if "%power" in units or "ftp" in units:
                if pw_per_meta.get("value") is not None:
                    percent_watts_mid = _coerce_float(pw_per_meta.get("value"))

                w0 = _coerce_float(pw_per_meta.get("start"))
                w1 = _coerce_float(pw_per_meta.get("end"))

                if w0 is not None and w1 is not None:
                    percent_watts_lo, percent_watts_hi = w0, w1

        step = WorkoutStep(
            text=text,
            duration_s=dur,
            watts_mid=floor(watts_mid) if watts_mid is not None else None,
            watts_lo=floor(watts_lo) if watts_lo is not None else None,
            watts_hi=floor(watts_hi) if watts_hi is not None else None,
            speed_mps_mid=speed_mid,
            speed_mps_lo=speed_lo,
            speed_mps_hi=speed_hi,
            percent_watts_mid=percent_watts_mid,
            percent_watts_lo=percent_watts_lo,
            percent_watts_hi=percent_watts_hi,
            percent_speed_mid=percent_speed_mid,
            percent_speed_lo=percent_speed_lo,
            percent_speed_hi=percent_speed_hi,
        )
        flat.append(step)

    for s in steps:
        text = s.get("text")
        handle_step(s, text=text)
    return flat


def parse_intervals_icu_json(data: dict, path: Path) -> Workout:
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
                workout = parse_intervals_icu_json(decoded_data, path)
            except Exception as e:
                raise ValueError(
                    f"Failed to parse decoded Intervals.icu workout JSON: {e}"
                )

        elif filename.endswith(".fit"):
            # If its a .fit file then call the fit parser on the decoded bytes
            from workout_parser.fit import parse_fit_from_bytes

            workout = parse_fit_from_bytes(decoded_bytes, name=name)
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
    steps = _flatten_icu_steps(steps_in)

    return Workout(name=name, steps=steps)


def parse_intervals_icu_json_file(path: Path) -> Workout:
    """Parse Intervals.icu exported workout JSON (running/cycling)."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return parse_intervals_icu_json(data, path)
