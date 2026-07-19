# Migrating from workout-parser 0.1.x to 0.2.0

Version `0.2.0` intentionally makes one coordinated breaking migration. The
canonical model now represents source semantics directly instead of flattening
repeats, synthesizing target bands, and mutating derived fields.

## Migration checklist

1. Replace `workout.steps` with `workout.instructions` or
   `workout.expanded_steps()`.
2. Construct and inspect the `duration` union instead of assigning
   `duration_s`.
3. Replace `*_mid`, `*_lo`, and `*_hi` fields with typed target objects.
4. Replace mutating `generate_*` calls with returned copies from `resolve_*`.
5. Replace `step.text` with `name`, `instruction`, or `notes` as appropriate.
6. Stop mutating parsed models; create copies or new models instead.
7. Catch `WorkoutParserError` (or a specific subtype) around `load_workout()`.
8. Update consumers of serialized JSON for nested instructions and target
   objects.

## Flat steps became an instruction tree

In `0.1.x`, repeats were expanded into `Workout.steps`. In `0.2.0`,
`Workout.instructions` contains `WorkoutStep` and `RepeatBlock` values and is the
canonical representation.

Before:

```python
for step in workout.steps:
    execute(step)
```

After, when repeat structure matters:

```python
from workout_parser import RepeatBlock, WorkoutStep

for instruction in workout.instructions:
    if isinstance(instruction, WorkoutStep):
        execute(instruction)
    elif isinstance(instruction, RepeatBlock):
        schedule_repeat(instruction.repetitions, instruction.instructions)
```

After, for a consumer that still needs a flat sequence:

```python
for step in workout.expanded_steps():
    execute(step)
```

`expanded_steps()` is explicit because expansion has a cost. Each occurrence is
an independent immutable deep copy. Expansion is limited to 10,000 steps.

## Durations are typed

`WorkoutStep.duration_s` is no longer a constructor field. Use one of the three
duration models directly on `WorkoutStep.duration`.

Before:

```python
step = WorkoutStep(duration_s=60)
```

After:

```python
from workout_parser import (
    DistanceDuration,
    OpenDuration,
    TimeDuration,
    WorkoutStep,
)

timed = WorkoutStep(duration=TimeDuration(seconds=60))
distance = WorkoutStep(duration=DistanceDuration(meters=1000))
open_ended = WorkoutStep(duration=OpenDuration())
```

`step.duration_s` remains a read-only convenience property. It returns `None`
for distance and open durations. Consequently, `workout.total_seconds` can also
be `None`.

## Target triplets became target models

The old scalar triplets have been replaced with models whose Python type carries
the target shape:

| 0.1.x fields | 0.2.0 field |
|---|---|
| `watts_mid/lo/hi` | `power_watts` |
| `percent_watts_mid/lo/hi` | `power_percent_ftp` |
| `speed_mps_mid/lo/hi` | `speed_mps` |
| `percent_speed_mid/lo/hi` | `speed_percent_threshold` |

`0.2.0` also adds explicit power, speed, heart-rate, and cadence zone fields and
absolute/relative heart-rate and cadence targets.

Before:

```python
step = WorkoutStep(
    duration_s=60,
    watts_mid=250,
    watts_lo=240,
    watts_hi=260,
)
```

After:

```python
from workout_parser import RangeTarget, TimeDuration, WorkoutStep

step = WorkoutStep(
    duration=TimeDuration(seconds=60),
    power_watts=RangeTarget(low=240, high=260),
)
print(step.power_watts.mid)  # 250
```

Use `isinstance()` when the source may contain different shapes:

```python
from workout_parser import PointTarget, RampTarget, RangeTarget

target = step.power_watts
if isinstance(target, PointTarget):
    display_exact(target.value)
elif isinstance(target, RangeTarget):
    display_range(target.low, target.mid, target.high)
elif isinstance(target, RampTarget):
    display_ramp(target.start, target.end)
```

There is no `kind` field and no `WorkoutTarget` alias. Infer shape from the
concrete class. A point no longer receives an automatic ±5% band, and a ramp's
`start`/`end` direction is never reordered. If a UI needs a display tolerance,
derive it outside the canonical model.

The old `speed_kph_*` and `speed_mph_*` convenience properties were removed.
Convert stored metres per second in the presentation layer (`m/s * 3.6` for
km/h or `m/s * 2.23694` for mph).

## Target resolution returns a new step

The old conversion methods mutated a `WorkoutStep`. Models are now immutable,
and resolution methods return a new value.

Before:

```python
step.generate_absolute_power_targets_from_percent(ftp_watts=250)
step.generate_pace_targets_from_percent(threshold_speed_mps=3.5)
```

After:

```python
resolved_power = step.resolve_power_targets(ftp_watts=250)
resolved_pace = step.resolve_pace_targets(threshold_speed_mps=3.5)
```

Relative targets remain on the returned step. Any existing absolute target is
replaced only when the corresponding relative target can be resolved. Power
values are rounded down to integral watts, matching the previous conversion
policy.

`Workout.source_ftp_watts` records the FTP used to produce absolute values in an
export. It is provenance only. Supplying an external current FTP remains the
correct way to reuse an old workout file.

## Text and metadata fields are explicit

`WorkoutStep.text` was ambiguous and has been removed. Use:

- `name` for a source label or FIT `wkt_step_name`;
- `instruction` for athlete-facing Intervals.icu leaf text;
- `notes` for additional FIT/source notes.

Repeat text maps to `RepeatBlock.name`. Workouts also expose normalized `sport`,
`description`, `workout_date`, and `source_ftp_watts`. Metadata precedence is
wrapper, embedded source, FIT header, then filename fallback.

## Models are immutable and validation is stricter

Assignment such as `step.power_watts = ...` now fails. Construct a new model or
validate changed model data explicitly:

```python
from workout_parser import WorkoutStep

data = step.model_dump()
data["power_watts"] = {"value": 275}
updated = WorkoutStep.model_validate(data)
```

Avoid using `model_copy(update=...)` with untrusted values because Pydantic does
not validate its update mapping.

Durations/reference values must be finite and positive, targets must be finite
and non-negative, and ranges cannot be reversed. `get_step_at()` now raises
`ValueError` for negative or non-finite timestamps; the exact workout end has no
active step.

## Loading now fails explicitly

`load_workout()` no longer returns an empty workout for unsupported or corrupt
input. Catch the public hierarchy:

```python
from workout_parser import WorkoutParserError, load_workout

try:
    workout = load_workout(path)
except WorkoutParserError as error:
    report_error(error)
```

Specific subtypes are `WorkoutFileError`, `UnsupportedFormatError`,
`InvalidWorkoutError`, `UnsupportedWorkoutFeatureError`, and
`WorkoutLimitError`.

Strict mode is the default. `load_workout(path, strict=False)` retains supported
content where possible and adds `ParseDiagnostic` entries. Safety-budget and
structural failures are not converted into diagnostics.

## Serialized JSON changed

`model_dump()` and `model_dump_json()` now serialize the recursive canonical
model. Important changes include:

- `steps` became nested `instructions`;
- `duration_s` became a `duration` object such as `{"seconds": 60}`;
- target triplets became objects such as `{"value": 250}` or
  `{"low": 240, "high": 260}`;
- ramps serialize as `{"start": 180, "end": 260}`;
- diagnostics and new metadata fields are stored in the workout;
- computed values such as `total_seconds`, `duration_s`, and range `mid` are not
  serialized.

The target objects intentionally have no discriminator. Consumers should infer
shape from their keys, just as Python consumers infer it from the concrete
class. Treat the `0.2.0` JSON structure as a breaking replacement for `0.1.x`
rather than attempting to deserialize it with the old schema.

## Resource budgets

Files and embedded decoded payloads are limited to 2 MiB. Repeat depth is
limited to 8, repetitions per block to 100, expanded steps to 10,000, and timed
duration to 7 days. Wrapper base64 must be strictly encoded. Inputs exceeding a
budget raise `WorkoutLimitError`, including when models are constructed directly.

## CLI behavior

The CLI understands the new duration and target shapes, expands repeats for
human display, and preserves the canonical tree in `--json` output. Public parse
errors print without a traceback and exit with status 1. Argument parsing errors
continue to use status 2.
