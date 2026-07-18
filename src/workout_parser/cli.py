"""workout-parser CLI – parse a workout file and dump results."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from workout_parser import load_workout, Workout


def _format_duration(seconds: float) -> str:
    """Format seconds as MM:SS."""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


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
        if step.watts_mid is not None:
            lo = step.watts_lo
            hi = step.watts_hi
            if lo is not None and hi is not None:
                lines.append(f"    Watts:    {lo} – {step.watts_mid} – {hi}")
            else:
                lines.append(f"    Watts:    {step.watts_mid}")
        elif step.percent_watts_mid is not None:
            lo = step.percent_watts_lo
            hi = step.percent_watts_hi
            if lo is not None and hi is not None:
                lines.append(f"    %FTP:     {lo:.0f}% – {step.percent_watts_mid:.0f}% – {hi:.0f}%")
            else:
                lines.append(f"    %FTP:     {step.percent_watts_mid:.0f}%")

        # Pace targets
        if step.speed_mps_mid is not None:
            lo = step.speed_mps_lo
            hi = step.speed_mps_hi
            if lo is not None and hi is not None:
                lines.append(f"    Speed:    {lo:.2f} – {step.speed_mps_mid:.2f} – {hi:.2f} m/s")
            else:
                lines.append(f"    Speed:    {step.speed_mps_mid:.2f} m/s")
        elif step.percent_speed_mid is not None:
            lo = step.percent_speed_lo
            hi = step.percent_speed_hi
            if lo is not None and hi is not None:
                lines.append(f"    %Pace:    {lo:.0f}% – {step.percent_speed_mid:.0f}% – {hi:.0f}%")
            else:
                lines.append(f"    %Pace:    {step.percent_speed_mid:.0f}%")

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
