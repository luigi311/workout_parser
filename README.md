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
Parsing is strict by default. Pass `strict=False` to retain supported steps and
receive structured entries in `workout.diagnostics` for unsupported constructs.

## Supported workout features

| Feature | Intervals.icu JSON | FIT |
|---|---|---|
| Time duration | Supported | Supported |
| Distance duration | Supported | Supported |
| Open/manual-lap duration | Supported | Supported |
| Calorie duration | Rejected/diagnostic | Rejected/diagnostic |
| Power target or zone | Supported | Supported |
| Speed/pace target or zone | Supported | Supported |
| Heart-rate target or zone | Supported | Supported |
| Cadence target or zone | Supported | Supported |
| No target | Supported | Supported |

Unknown duration and target types raise `UnsupportedWorkoutFeatureError` in
strict mode. In permissive mode, unsupported target instructions are omitted and
reported; steps with unsupported durations cannot be represented and are
therefore omitted with a diagnostic.

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
| `duration` | `TimeDuration`, `DistanceDuration`, or `OpenDuration` |
| `power_watts` | Absolute power target (watts) |
| `power_percent_ftp` | Power target as % FTP |
| `power_zone` | Source power-zone target |
| `speed_mps` | Absolute pace target (metres per second) |
| `speed_percent_threshold` | Pace target as % of threshold pace |
| `speed_zone` | Source pace-zone target |
| `heart_rate_bpm` | Absolute heart-rate target |
| `heart_rate_percent_max` | Heart rate as % of maximum |
| `heart_rate_percent_lthr` | Heart rate as % of lactate-threshold HR |
| `heart_rate_zone` | Source heart-rate zone target |
| `cadence_rpm` | Absolute cadence target |
| `cadence_zone` | Source cadence zone target |

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
