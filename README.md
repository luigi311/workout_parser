# workout_parser

A Python library for parsing structured workout files from [Intervals.icu](https://intervals.icu), supporting both `.json` exports and `.fit` files. Produces a unified `Workout` model with consistent pace and power targets across formats.

## Supported Formats

- **Intervals.icu JSON** — exported workout definitions including nested repeat blocks, absolute pace/power, and `%FTP` / `%pace` targets
- **FIT** — Garmin/ANT+ FIT workout files including pace, power, and repeat blocks

## Installation

Requires Python 3.11+. Dependencies are managed with [uv](https://github.com/astral-sh/uv).

```bash
uv sync
```

## Usage

```python
from pathlib import Path
from workout_parser import load_workout

workout = load_workout(Path("my_workout.json"))  # or .fit
print(workout.name)
print(workout.total_seconds)

for step in workout.steps:
    print(step.duration_s, step.power_watts, step.speed_mps)
```

`load_workout` dispatches to the correct parser based on file extension.

## Data Model

### `Workout`

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Workout name |
| `workout_date` | `date \| None` | Optional date |
| `steps` | `list[WorkoutStep]` | Flat list of steps (repeats are expanded) |
| `total_seconds` | `float` | Sum of all step durations (property) |

### `WorkoutStep`

Targets use distinct models inferred from their fields. A `PointTarget` has one
`value`, a `RangeTarget` stores `low` and `high` and provides their computed
`mid`, and a `RampTarget` has ordered `start` and `end` values. Ramp direction is
therefore preserved and point targets do not acquire a synthetic display band.

| Field | Description |
|---|---|
| `duration_s` | Step duration in seconds |
| `power_watts` | Absolute power target (watts) |
| `power_percent_ftp` | Power target as % FTP |
| `speed_mps` | Absolute pace target (metres per second) |
| `speed_percent_threshold` | Pace target as % of threshold pace |

To resolve percent targets into absolute values after construction:

```python
step.generate_absolute_power_targets_from_percent(ftp_watts=250)
step.generate_pace_targets_from_percent(threshold_speed_mps=3.5)
```

## Running Tests

Tests compare JSON and FIT parsers against each other for every matched file pair in `test/data/`.

```bash
uv run pytest
```
