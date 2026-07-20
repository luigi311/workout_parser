# workout-parser

`workout-parser` parses Intervals.icu JSON exports and Garmin/ANT+ FIT workout
files into one validated Python model. It preserves repeat structure, target
shape and direction, source-relative targets, and workout metadata.

Version `0.2.0` contains breaking model and error-handling changes. Applications
upgrading from `0.1.x` should read [MIGRATION.md](MIGRATION.md).

## Installation

Python 3.11 or newer is required.

```bash
uv sync
```

## Quick start

```python
from pathlib import Path

from workout_parser import load_workout

workout = load_workout(Path("my_workout.json"))  # or .fit

print(workout.name)
print(workout.sport)
print(workout.total_seconds)  # None when a duration is distance/open-ended

# Repeats remain structured in workout.instructions. Expand explicitly when a
# consumer needs a flat execution sequence.
for step in workout.expanded_steps():
    print(step.duration, step.power_watts, step.speed_mps)
```

`load_workout(path, strict=True)` validates that the path is a regular `.json`
or `.fit` file and dispatches to the correct parser. Strict parsing is the
default.

## Supported workout features

| Feature | Intervals.icu JSON | FIT |
|---|---|---|
| Time duration | Supported | Supported |
| Distance duration | Supported | Supported |
| Open/manual-lap duration | Supported | Supported |
| Calorie duration | Rejected/diagnostic | Rejected/diagnostic |
| Nested repeat blocks | Supported | Supported |
| Power target or zone | Supported | Supported |
| Speed/pace target or zone | Supported | Supported |
| Heart-rate target or zone | Supported | Supported |
| Cadence target or zone | Supported | Supported |
| No target | Supported | Supported |

Unknown duration and target types raise `UnsupportedWorkoutFeatureError` in
strict mode. With `strict=False`, supported content is retained and issues are
recorded as immutable `ParseDiagnostic` entries in `workout.diagnostics`.
Instructions whose duration cannot be represented are omitted; an unsupported
target is omitted from an otherwise valid step. Unrecoverable root/schema
failures and resource-limit violations remain errors.

## Canonical model

All canonical models are immutable Pydantic models. Numeric durations and
reference values must be finite and positive. Targets must be finite and
non-negative. A `RangeTarget` requires `low <= high`; a `RampTarget` may move in
either direction.

### Workouts and repeats

`Workout.instructions` is the source-preserving execution tree:

```python
from workout_parser import RepeatBlock, WorkoutStep

for instruction in workout.instructions:
    if isinstance(instruction, WorkoutStep):
        print("step", instruction)
    elif isinstance(instruction, RepeatBlock):
        print("repeat", instruction.repetitions, instruction.instructions)
```

| `Workout` field/property | Type | Meaning |
|---|---|---|
| `name` | `str` | Workout name |
| `description` | `str \| None` | Workout description |
| `workout_date` | `date \| None` | Optional scheduled date |
| `sport` | `str \| None` | Normalized sport such as `running` or `cycling` |
| `source_ftp_watts` | `int \| float \| None` | Historical FTP used by source-resolved watts |
| `diagnostics` | `tuple[ParseDiagnostic, ...]` | Permissive-mode parse findings |
| `instructions` | `tuple[WorkoutStep \| RepeatBlock, ...]` | Canonical repeat tree |
| `expanded_steps()` | `list[WorkoutStep]` | Independent deep-copied executable steps |
| `total_seconds` | `float \| None` | Expanded time total, or `None` for variable duration |

`get_step_at(seconds)` uses the expanded timeline without changing the
canonical tree. It raises `ValueError` for negative or non-finite input, returns
no step at the exact workout end, and cannot resolve a timeline beyond an
open/distance step.

### Durations

Each `WorkoutStep.duration` is exactly one of:

```python
TimeDuration(seconds=60)
DistanceDuration(meters=1000)
OpenDuration(event="lap_button")
```

The convenience property `step.duration_s` returns seconds only for a
`TimeDuration`; otherwise it returns `None`.

### Targets

Target kind is inferred from the model type and fields; there is no discriminator
or wrapper target type.

```python
from workout_parser import PointTarget, RampTarget, RangeTarget

PointTarget(value=250)
RangeTarget(low=240, high=260)  # .mid is computed as 250
RampTarget(start=180, end=260)  # direction is preserved
```

A point remains a point: the parser does not invent a ±5% display band. The
fields on `WorkoutStep` are:

| Field | Units/meaning |
|---|---|
| `power_watts` | Absolute watts |
| `power_percent_ftp` | Percent of FTP |
| `power_zone` | Source power zone |
| `speed_mps` | Metres per second |
| `speed_percent_threshold` | Percent of threshold speed |
| `speed_zone` | Source pace/speed zone |
| `heart_rate_bpm` | Beats per minute |
| `heart_rate_percent_max` | Percent of maximum HR |
| `heart_rate_percent_lthr` | Percent of lactate-threshold HR |
| `heart_rate_zone` | Source heart-rate zone |
| `cadence_rpm` | Revolutions per minute |
| `cadence_zone` | Source cadence zone |

Power and pace resolution return new steps and preserve relative targets:

```python
resolved_power = step.resolve_power_targets(ftp_watts=250)
resolved_pace = step.resolve_pace_targets(threshold_speed_mps=3.5)

assert resolved_power is not step
assert resolved_power.power_percent_ftp == step.power_percent_ftp
```

When an Intervals.icu export contains both `%FTP` and resolved watts, both are
retained. `source_ftp_watts` is provenance, not the athlete's current FTP. Pass
the current value to `resolve_power_targets()` when reusing an older file.

### Text and metadata

Metadata precedence is wrapper metadata, embedded workout metadata, FIT header,
then filename fallback. Sports are normalized across names such as
`Run`/`running` and `Ride`/`cycling`.

- Intervals.icu leaf `text` becomes `WorkoutStep.instruction`.
- Intervals.icu repeat `text` becomes `RepeatBlock.name`.
- FIT `wkt_step_name` becomes `name`.
- FIT `notes` remains `notes`.

## Errors and permissive parsing

All public loader failures derive from `WorkoutParserError`:

| Error | Meaning |
|---|---|
| `WorkoutFileError` | Missing, unreadable, or non-regular path |
| `UnsupportedFormatError` | File or embedded payload format is unsupported |
| `InvalidWorkoutError` | Source data is malformed or contradictory |
| `UnsupportedWorkoutFeatureError` | Strict mode encountered an unsupported construct |
| `WorkoutLimitError` | A safety budget was exceeded |

```python
from workout_parser import WorkoutParserError, load_workout

try:
    workout = load_workout(path)
except WorkoutParserError as error:
    print(f"Could not parse workout: {error}")

partial = load_workout(path, strict=False)
for diagnostic in partial.diagnostics:
    print(diagnostic.code, diagnostic.step_index, diagnostic.message)
```

## Serialization

`workout.model_dump()` and `workout.model_dump_json()` serialize stored canonical
fields. Target shapes are represented by their fields (`value`, `low`/`high`, or
`start`/`end`), and repeat blocks remain nested under `instructions`.

Computed properties are intentionally omitted, including `total_seconds`,
`duration_s`, and `RangeTarget.mid`. Add them in an application-specific output
adapter if a wire format requires them. The CLI `--json` output uses the same
Pydantic serialization.

## Resource limits

Parsing and model expansion enforce fixed workout-oriented safety budgets:

- Source files and decoded embedded payloads: 2 MiB each
- Nested repeat depth: 8 levels
- Repetitions per block: 100
- Expanded workout size: 10,000 steps
- Total timed duration: 7 days

Base64 wrapper payloads must use strict base64 encoding. Exceeding any budget
raises `WorkoutLimitError`; permissive parsing does not bypass safety limits.

## Command line

```bash
workout-parser path/to/workout.fit
workout-parser --json path/to/workout.json
```

Human output expands repeats for display. Parse failures print a concise error
to stderr and exit with status 1; command-line usage errors use status 2. JSON
output preserves the canonical nested model.

## Running tests

The suite uses an explicit manifest of tracked fixture pairs, semantic oracles,
secondary cross-format comparisons, and focused parser/model/API checks. Stray
local fixture files do not change test collection.

```bash
uv run pytest
```
