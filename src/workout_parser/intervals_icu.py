from __future__ import annotations
from os import name
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
            watts_mid=watts_mid,
            watts_lo=watts_lo,
            watts_hi=watts_hi,
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


def parse_intervals_icu_json(data: dict, name: str) -> Workout:
    """Parse Intervals.icu exported workout JSON (running/cycling)."""

    steps_in = data.get("steps") or []
    steps = _flatten_icu_steps(steps_in)

    return Workout(name=name, steps=steps)


def parse_intervals_icu_json_file(path: Path) -> Workout:
    """Parse Intervals.icu exported workout JSON (running/cycling)."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    return parse_intervals_icu_json(data, name=path.stem)
