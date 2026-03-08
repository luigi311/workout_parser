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
    print(step.duration_s, step.watts_mid, step.speed_mps_mid)
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

Targets are stored as `mid / lo / hi` triplets. On construction, if only `mid` is provided the model synthesises a ±5% band; if only `lo`/`hi` are provided it computes `mid` automatically.

| Field | Description |
|---|---|
| `duration_s` | Step duration in seconds |
| `watts_mid/lo/hi` | Absolute power targets (watts) |
| `percent_watts_mid/lo/hi` | Power as % FTP |
| `speed_mps_mid/lo/hi` | Absolute pace (metres per second) |
| `percent_speed_mid/lo/hi` | Pace as % of threshold pace |
| `speed_kph_mid/lo/hi` | Absolute pace kilometers per hour derived from `speed_mps_*` (property) |
| `speed_mph_mid/lo/hi` | Absolute pace miles per hour derived from `speed_mps_*` (property) |

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
