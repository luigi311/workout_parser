"""workout-parser CLI – parse a workout file and dump results."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from workout_parser import PointTarget, RampTarget, RangeTarget, Workout, load_workout


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
    lines.append(f"Total:       {_format_duration(workout.total_seconds)}")
    lines.append(f"Steps:       {len(workout.steps)}")
    lines.append("─" * 60)

    for i, step in enumerate(workout.steps):
        lines.append(f"\n  Step {i + 1}:")
        if step.text:
            lines.append(f"    Text:     {step.text}")
        lines.append(f"    Duration: {_format_duration(step.duration_s)}")

        # Power targets
        if step.power_watts is not None:
            lines.append(
                f"    Watts:    {_format_target(step.power_watts, decimals=0, suffix=' W')}"
            )
        elif step.power_percent_ftp is not None:
            lines.append(
                f"    %FTP:     {_format_target(step.power_percent_ftp, decimals=0, suffix='%')}"
            )

        # Pace targets
        if step.speed_mps is not None:
            lines.append(
                f"    Speed:    {_format_target(step.speed_mps, decimals=2, suffix=' m/s')}"
            )
        elif step.speed_percent_threshold is not None:
            lines.append(
                "    %Pace:    "
                + _format_target(
                    step.speed_percent_threshold, decimals=0, suffix="%"
                )
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

    path: Path = args.file
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    workout = load_workout(path)
    print(_dump_workout(workout, json_out=args.json_out))
