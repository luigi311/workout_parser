from __future__ import annotations
import base64
from math import floor
from datetime import date
from workout_parser.models import (
    PointTarget,
    RampTarget,
    RangeTarget,
    Workout,
    WorkoutStep,
)

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

        ramp = node.get("ramp") is True

        # -------- Pace parsing --------
        p_abs_meta = node.get("_pace")
        speed_target = _target_from_icu(p_abs_meta, ramp=ramp)

        p_per_meta = node.get("pace")
        percent_speed_target = None
        if isinstance(p_per_meta, dict):
            units = (p_per_meta.get("units") or "").casefold()
            if "%pace" in units:
                percent_speed_target = _target_from_icu(p_per_meta, ramp=ramp)

        # -------- Power parsing --------
        pw_abs_meta = node.get("_power")
        power_target = _target_from_icu(pw_abs_meta, ramp=ramp, integer=True)

        pw_per_meta = node.get("power")
        percent_power_target = None
        if power_target is None and isinstance(pw_per_meta, dict):
            units = (pw_per_meta.get("units") or "").casefold()
            if "%power" in units or "ftp" in units:
                percent_power_target = _target_from_icu(pw_per_meta, ramp=ramp)

        step = WorkoutStep(
            text=text,
            duration_s=dur,
            power_watts=power_target,
            power_percent_ftp=percent_power_target,
            speed_mps=speed_target,
            speed_percent_threshold=percent_speed_target,
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
