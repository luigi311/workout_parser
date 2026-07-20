"""workout-parser CLI – parse a workout file and dump results."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from workout_parser import (
    DistanceDuration,
    OpenDuration,
    PointTarget,
    RampTarget,
    RangeTarget,
    TimeDuration,
    Workout,
    WorkoutParserError,
    load_workout,
)


def _format_duration(seconds: float) -> str:
    """Format seconds as MM:SS."""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def _format_target(target, *, decimals: int, suffix: str) -> str:
    def value(number: float) -> str:
        return f"{number:.{decimals}f}{suffix}"

    if isinstance(target, PointTarget):
        return value(target.value)
    if isinstance(target, RangeTarget):
        return f"{value(target.low)} – {value(target.mid)} – {value(target.high)}"
    if isinstance(target, RampTarget):
        return f"ramp {value(target.start)} → {value(target.end)}"
    raise TypeError(f"Unknown target type: {type(target).__name__}")


def _dump_workout(workout: Workout, json_out: bool = False) -> str:
    """Serialize a Workout to a human-readable string or JSON."""
    if json_out:
        return workout.model_dump_json(indent=2)

    lines: list[str] = []
    lines.append(f"Name:        {workout.name}")
    if workout.description:
        lines.append(f"Description: {workout.description}")
    if workout.workout_date:
        lines.append(f"Date:        {workout.workout_date}")
    total = (
        _format_duration(workout.total_seconds)
        if workout.total_seconds is not None
        else "variable"
    )
    lines.append(f"Total:       {total}")
    steps = workout.expanded_steps()
    lines.append(f"Steps:       {len(steps)}")
    lines.append("─" * 60)

    for i, step in enumerate(steps):
        lines.append(f"\n  Step {i + 1}:")
        if step.name:
            lines.append(f"    Name:     {step.name}")
        if step.instruction:
            lines.append(f"    Instruction: {step.instruction}")
        if step.notes:
            lines.append(f"    Notes:    {step.notes}")
        if isinstance(step.duration, TimeDuration):
            duration = _format_duration(step.duration.seconds)
        elif isinstance(step.duration, DistanceDuration):
            duration = f"{step.duration.meters:g} m"
        elif isinstance(step.duration, OpenDuration):
            duration = f"open ({step.duration.event})"
        lines.append(f"    Duration: {duration}")

        # Power targets
        if step.power_watts is not None:
            lines.append(
                f"    Watts:    {_format_target(step.power_watts, decimals=0, suffix=' W')}"
            )
        if step.power_percent_ftp is not None:
            lines.append(
                f"    %FTP:     {_format_target(step.power_percent_ftp, decimals=0, suffix='%')}"
            )
        if step.power_zone is not None:
            lines.append(
                "    Power zone: "
                + _format_target(step.power_zone, decimals=0, suffix="")
            )

        # Pace targets
        if step.speed_mps is not None:
            lines.append(
                f"    Speed:    {_format_target(step.speed_mps, decimals=2, suffix=' m/s')}"
            )
        if step.speed_percent_threshold is not None:
            lines.append(
                "    %Pace:    "
                + _format_target(
                    step.speed_percent_threshold, decimals=0, suffix="%"
                )
            )
        if step.speed_zone is not None:
            lines.append(
                "    Pace zone: "
                + _format_target(step.speed_zone, decimals=0, suffix="")
            )

        if step.heart_rate_bpm is not None:
            lines.append(
                "    HR:       "
                + _format_target(step.heart_rate_bpm, decimals=0, suffix=" bpm")
            )
        if step.heart_rate_percent_max is not None:
            lines.append(
                "    %Max HR:  "
                + _format_target(
                    step.heart_rate_percent_max, decimals=0, suffix="%"
                )
            )
        if step.heart_rate_percent_lthr is not None:
            lines.append(
                "    %LTHR:    "
                + _format_target(
                    step.heart_rate_percent_lthr, decimals=0, suffix="%"
                )
            )
        if step.heart_rate_zone is not None:
            lines.append(
                "    HR zone:  "
                + _format_target(step.heart_rate_zone, decimals=0, suffix="")
            )

        if step.cadence_rpm is not None:
            lines.append(
                "    Cadence:  "
                + _format_target(step.cadence_rpm, decimals=0, suffix=" rpm")
            )
        if step.cadence_zone is not None:
            lines.append(
                "    Cadence zone: "
                + _format_target(step.cadence_zone, decimals=0, suffix="")
            )

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="workout-parser",
        description="Parse a workout file (.fit or .json) and dump the result.",
    )
    parser.add_argument(
        "file",
        type=Path,
        help="Path to a .fit or .json workout file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_out",
        help="Output as JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    try:
        workout = load_workout(args.file)
    except WorkoutParserError as error:
        print(f"Error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    print(_dump_workout(workout, json_out=args.json_out))
