from __future__ import annotations

import json
import math
from itertools import combinations
from pathlib import Path

import pytest
from pydantic import ValidationError
from workout_parser import (
    DistanceDuration,
    InvalidWorkoutError,
    OpenDuration,
    PointTarget,
    RampTarget,
    RangeTarget,
    RepeatBlock,
    TimeDuration,
    UnsupportedFormatError,
    UnsupportedWorkoutFeatureError,
    WorkoutFileError,
    load_workout,
)
from workout_parser.intervals_icu import parse_intervals_icu_json
from workout_parser.fit import parse_fit
from workout_parser.cli import main as cli_main

HERE = Path(__file__).parent
DATA = HERE / "data"
SUPPORTED = {".json", ".fit"}
FTPS = [150, 200, 250]


def discover_pairs() -> list[tuple[Path, Path]]:
    by_stem: dict[str, list[Path]] = {}
    for p in DATA.glob("*"):
        if p.suffix.lower() in SUPPORTED and p.is_file():
            by_stem.setdefault(p.stem, []).append(p)

    pairs: list[tuple[Path, Path]] = []
    for files in by_stem.values():
        jsons = [p for p in files if p.suffix.lower() == ".json"]
        others = [p for p in files if p.suffix.lower() != ".json"]
        if jsons and others:
            pairs.extend((j, o) for j in jsons for o in others)
        elif len(files) >= 2:
            pairs.extend(combinations(files, 2))
    return pairs


PAIRS = discover_pairs()
if not PAIRS:
    raise SystemExit(f"No comparable file pairs found in {DATA}")


def _close(a: float | None, b: float | None, tol: float) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(a, b, rel_tol=0, abs_tol=tol)


def _target_mid(target) -> float | None:
    if isinstance(target, PointTarget):
        return target.value
    if isinstance(target, RangeTarget):
        return target.mid
    if isinstance(target, RampTarget):
        return (target.start + target.end) / 2
    return None


@pytest.mark.parametrize("ftp", FTPS)
@pytest.mark.parametrize("json_path,other_path", PAIRS, ids=lambda p: p.name)
def test_parsers_agree(json_path: Path, other_path: Path, ftp: int) -> None:
    w_a = load_workout(json_path)
    w_b = load_workout(other_path)
    steps_a = w_a.expanded_steps()
    steps_b = w_b.expanded_steps()

    assert len(steps_a) > 0, f"{json_path.name} yielded no steps"
    assert len(steps_a) == len(steps_b), f"Step count mismatch: {len(steps_a)} vs {len(steps_b)}"
    assert _close(w_a.total_seconds, w_b.total_seconds, 1.0), f"Total duration mismatch"

    for i, (sa, sb) in enumerate(zip(steps_a, steps_b)):
        assert _close(sa.duration_s, sb.duration_s, 0.5), f"Step {i} duration: {sa.duration_s} vs {sb.duration_s}"
        assert _close(_target_mid(sa.power_watts), _target_mid(sb.power_watts), 1.0)
        assert _close(_target_mid(sa.speed_mps), _target_mid(sb.speed_mps), 0.01)


# Test 30_Minute_Threshold_Test_New_Build_Phase_fit.json and 30_Minute_Threshold_Test_New_Build_Phase_json.json to make sure they match
def test_intervals_json() -> None:
    json_path = DATA / "30_Minute_Threshold_Test_New_Build_Phase_json.json"
    fit_path = DATA / "30_Minute_Threshold_Test_New_Build_Phase_fit.json"

    w_a = load_workout(json_path)
    w_b = load_workout(fit_path)
    steps_a = w_a.expanded_steps()
    steps_b = w_b.expanded_steps()

    assert len(steps_a) > 0, f"{json_path.name} yielded no steps"
    assert len(steps_b) > 0, f"{fit_path.name} yielded no steps"
    assert len(steps_a) == len(steps_b), f"Step count mismatch: {len(steps_a)} vs {len(steps_b)}"
    assert _close(w_a.total_seconds, w_b.total_seconds, 1.0), f"Total duration mismatch: {w_a.total_seconds} vs {w_b.total_seconds}"

    # Should be 4 steps with a total duration of 75 minutes
    assert len(steps_a) == 4, f"Expected 4 steps, got {len(steps_a)}"
    assert _close(w_a.total_seconds, 75 * 60, 1.0), f"Expected total duration of 75 minutes, got {w_a.total_seconds / 60:.2f} minutes"

    for i, (sa, sb) in enumerate(zip(steps_a, steps_b)):
        assert _close(sa.duration_s, sb.duration_s, 0.5), f"Step {i} duration: {sa.duration_s} vs {sb.duration_s}"
        assert _close(_target_mid(sa.power_watts), _target_mid(sb.power_watts), 1.0)
        assert _close(_target_mid(sa.speed_mps), _target_mid(sb.speed_mps), 0.01)


def test_json_preserves_target_shapes() -> None:
    workout = load_workout(DATA / "30_Minute_Threshold_Test_New_Build_Phase_json.json")
    steps = workout.expanded_steps()

    assert steps[0].speed_percent_threshold == RampTarget(
        start=50, end=90
    )
    assert isinstance(steps[1].speed_mps, RangeTarget)
    assert steps[1].speed_percent_threshold == PointTarget(value=70)
    assert steps[3].speed_percent_threshold == RampTarget(
        start=70, end=50
    )


def test_json_preserves_supported_targets_and_durations() -> None:
    workout = parse_intervals_icu_json(
        {
            "steps": [
                {
                    "duration": 60,
                    "hr": {"value": 2, "units": "hr_zone"},
                    "_hr": {"start": 146, "end": 153},
                },
                {
                    "duration": 30,
                    "hr": {"value": 135, "units": "bpm"},
                },
                {
                    "duration": 30,
                    "hr": {"start": 85, "end": 90, "units": "%lthr"},
                },
                {
                    "duration": 120,
                    "distance": 400,
                    "cadence": {"value": 90, "units": "rpm"},
                },
                {"duration": 30, "until_lap_press": True},
            ]
        },
        Path("supported.json"),
    )
    steps = workout.expanded_steps()

    assert steps[0].duration == TimeDuration(seconds=60)
    assert steps[0].heart_rate_bpm == RangeTarget(low=146, high=153)
    assert steps[0].heart_rate_zone == PointTarget(value=2)
    assert steps[1].heart_rate_bpm == PointTarget(value=135)
    assert steps[2].heart_rate_percent_lthr == RangeTarget(
        low=85, high=90
    )
    assert steps[3].duration == DistanceDuration(meters=400)
    assert steps[3].cadence_rpm == PointTarget(value=90)
    assert steps[4].duration == OpenDuration()
    assert workout.total_seconds is None


def test_unsupported_duration_is_strict_or_diagnostic() -> None:
    data = {"steps": [{"calories": 100}]}

    with pytest.raises(UnsupportedWorkoutFeatureError):
        parse_intervals_icu_json(data, Path("calories.json"))

    workout = parse_intervals_icu_json(
        data, Path("calories.json"), strict=False
    )
    assert workout.instructions == ()
    assert workout.diagnostics[0].code == "unsupported_feature"


class _FitField:
    def __init__(self, name: str, value) -> None:
        self.name = name
        self.value = value


class _FitMessage:
    def __init__(self, **fields) -> None:
        self.fields = [_FitField(name, value) for name, value in fields.items()]

    def __iter__(self):
        return iter(self.fields)


class _FitFile:
    def __init__(self, *messages: _FitMessage) -> None:
        self.messages = messages

    def get_messages(self, name: str):
        assert name == "workout_step"
        return self.messages


def test_fit_decodes_typed_duration_and_target_fields() -> None:
    workout = parse_fit(
        _FitFile(
            _FitMessage(
                message_index=0,
                duration_type="distance",
                duration_distance=400.0,
                target_type="heart_rate",
                target_hr_zone=3,
                custom_target_heart_rate_low=0,
                custom_target_heart_rate_high=0,
            ),
            _FitMessage(
                message_index=1,
                duration_type="time",
                duration_time=60.0,
                target_type="heart_rate",
                target_hr_zone=0,
                custom_target_heart_rate_low=70,
                custom_target_heart_rate_high=80,
            ),
            _FitMessage(
                message_index=2,
                duration_type="open",
                target_type="cadence",
                target_cadence_zone=0,
                custom_target_cadence_low=85,
                custom_target_cadence_high=95,
            ),
        )
    )
    steps = workout.expanded_steps()

    assert steps[0].duration == DistanceDuration(meters=400)
    assert steps[0].heart_rate_zone == PointTarget(value=3)
    assert steps[0].heart_rate_percent_max is None
    assert steps[1].heart_rate_percent_max == RangeTarget(
        low=70, high=80
    )
    assert steps[2].duration == OpenDuration()
    assert steps[2].cadence_rpm == RangeTarget(low=85, high=95)


def test_json_preserves_relative_and_resolved_power() -> None:
    workout = parse_intervals_icu_json(
        {
            "ftp": 165,
            "steps": [
                {
                    "duration": 60,
                    "power": {"value": 105, "units": "%ftp"},
                    "_power": {"value": 173, "start": 168, "end": 178},
                }
            ],
        },
        Path("power.json"),
    )
    step = workout.expanded_steps()[0]

    assert workout.source_ftp_watts == 165
    assert step.power_percent_ftp == PointTarget(value=105)
    assert step.power_watts == RangeTarget(low=168, high=178)

    resolved = step.resolve_power_targets(ftp_watts=250)
    assert step.power_percent_ftp == PointTarget(value=105)
    assert step.power_watts == RangeTarget(low=168, high=178)
    assert resolved.power_percent_ftp == PointTarget(value=105)
    assert resolved.power_watts == PointTarget(value=262)


def test_conflicting_resolved_power_is_strict_or_diagnostic() -> None:
    data = {
        "ftp": 165,
        "steps": [
            {
                "duration": 60,
                "power": {"value": 105, "units": "%ftp"},
                "_power": {"value": 220, "start": 215, "end": 225},
            }
        ],
    }

    with pytest.raises(InvalidWorkoutError):
        parse_intervals_icu_json(data, Path("conflict.json"))

    workout = parse_intervals_icu_json(
        data, Path("conflict.json"), strict=False
    )
    step = workout.expanded_steps()[0]
    assert step.power_percent_ftp == PointTarget(value=105)
    assert step.power_watts == RangeTarget(low=215, high=225)
    assert workout.diagnostics[0].code == "conflicting_resolved_power"


def test_json_preserves_nested_repeat_structure() -> None:
    workout = parse_intervals_icu_json(
        {
            "steps": [
                {
                    "reps": 2,
                    "steps": [
                        {"duration": 10, "text": "work"},
                        {
                            "reps": 3,
                            "steps": [{"duration": 5, "text": "surge"}],
                        },
                    ],
                }
            ]
        },
        Path("nested-repeat.json"),
    )

    outer = workout.instructions[0]
    assert isinstance(outer, RepeatBlock)
    assert outer.repetitions == 2
    assert isinstance(outer.instructions[1], RepeatBlock)
    assert outer.instructions[1].repetitions == 3
    assert workout.total_seconds == 50

    expanded = workout.expanded_steps()
    assert [step.text for step in expanded] == [
        "work",
        "surge",
        "surge",
        "surge",
        "work",
        "surge",
        "surge",
        "surge",
    ]
    assert expanded[0] is not expanded[4]


@pytest.mark.parametrize("repetitions", [0, -1, 1.5, None, True])
def test_json_rejects_invalid_repeat_counts(repetitions) -> None:
    data = {
        "steps": [
            {"reps": repetitions, "steps": [{"duration": 10}]}
        ]
    }

    with pytest.raises(InvalidWorkoutError):
        parse_intervals_icu_json(data, Path("invalid-repeat.json"))

    workout = parse_intervals_icu_json(
        data, Path("invalid-repeat.json"), strict=False
    )
    assert workout.instructions == ()
    assert workout.diagnostics[0].code == "invalid_repeat"


@pytest.mark.parametrize(
    "block",
    [
        {"steps": [{"duration": 10}]},
        {"reps": 2, "steps": []},
        {"reps": 2, "steps": "invalid"},
    ],
)
def test_json_rejects_malformed_repeat_blocks(block) -> None:
    with pytest.raises(InvalidWorkoutError):
        parse_intervals_icu_json(
            {"steps": [block]}, Path("malformed-repeat.json")
        )


def test_fit_preserves_nested_repeat_structure() -> None:
    workout = parse_fit(
        _FitFile(
            _FitMessage(
                message_index=0,
                duration_type="time",
                duration_time=10,
                target_type="open",
            ),
            _FitMessage(
                message_index=1,
                duration_type="time",
                duration_time=5,
                target_type="open",
            ),
            _FitMessage(
                message_index=2,
                duration_type="repeat_until_steps_cmplt",
                duration_step=1,
                repeat_steps=2,
            ),
            _FitMessage(
                message_index=3,
                duration_type="repeat_until_steps_cmplt",
                duration_step=0,
                repeat_steps=3,
            ),
        )
    )

    outer = workout.instructions[0]
    assert isinstance(outer, RepeatBlock)
    assert outer.repetitions == 3
    assert isinstance(outer.instructions[1], RepeatBlock)
    assert outer.instructions[1].repetitions == 2
    assert len(workout.expanded_steps()) == 9
    assert workout.total_seconds == 60


def test_fit_rejects_invalid_repeat_reference() -> None:
    fit_file = _FitFile(
        _FitMessage(
            message_index=0,
            duration_type="time",
            duration_time=10,
            target_type="open",
        ),
        _FitMessage(
            message_index=1,
            duration_type="repeat_until_steps_cmplt",
            duration_step=5,
            repeat_steps=2,
        ),
    )

    with pytest.raises(InvalidWorkoutError):
        parse_fit(fit_file)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TimeDuration(seconds=0),
        lambda: TimeDuration(seconds=-1),
        lambda: TimeDuration(seconds=math.nan),
        lambda: DistanceDuration(meters=math.inf),
        lambda: PointTarget(value=-1),
        lambda: PointTarget(value=math.nan),
        lambda: RangeTarget(low=100, high=50),
    ],
)
def test_models_reject_impossible_numeric_state(factory) -> None:
    with pytest.raises(ValidationError):
        factory()


def test_models_are_deeply_immutable() -> None:
    step = parse_intervals_icu_json(
        {"steps": [{"duration": 60}]}, Path("immutable.json")
    ).instructions[0]
    assert not isinstance(step, RepeatBlock)
    workout = parse_intervals_icu_json(
        {"steps": [{"duration": 60}]}, Path("immutable.json")
    )

    with pytest.raises(ValidationError):
        step.text = "changed"
    with pytest.raises(ValidationError):
        workout.instructions = ()


def test_resolution_returns_new_step_and_validates_reference() -> None:
    step = parse_intervals_icu_json(
        {
            "steps": [
                {
                    "duration": 60,
                    "power": {"value": 100, "units": "%ftp"},
                }
            ]
        },
        Path("resolve.json"),
    ).instructions[0]
    assert not isinstance(step, RepeatBlock)

    resolved = step.resolve_power_targets(250)
    assert step.power_watts is None
    assert resolved.power_watts == PointTarget(value=250)
    with pytest.raises(ValueError):
        step.resolve_power_targets(0)
    with pytest.raises(ValueError):
        step.resolve_pace_targets(math.nan)


def test_timeline_rejects_invalid_queries_and_excludes_exact_end() -> None:
    workout = parse_intervals_icu_json(
        {"steps": [{"duration": 60}]}, Path("timeline.json")
    )

    for timestamp in (-1, math.nan, math.inf):
        with pytest.raises(ValueError):
            workout.get_step_at(timestamp)
    assert workout.get_step_at(0)[0] == 0
    assert workout.get_step_at(60) == (None, None)


def test_loader_rejects_invalid_paths_formats_and_empty_files(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    directory = tmp_path / "directory.json"
    directory.mkdir()
    unsupported = tmp_path / "workout.txt"
    unsupported.write_text("workout")
    empty = tmp_path / "empty.json"
    empty.write_text("{}")

    with pytest.raises(WorkoutFileError):
        load_workout(missing)
    with pytest.raises(WorkoutFileError):
        load_workout(directory)
    with pytest.raises(UnsupportedFormatError):
        load_workout(unsupported)
    with pytest.raises(InvalidWorkoutError):
        load_workout(empty)


def test_loader_preserves_malformed_source_as_error_cause(tmp_path) -> None:
    malformed_json = tmp_path / "malformed.json"
    malformed_json.write_text("{")
    malformed_fit = tmp_path / "malformed.fit"
    malformed_fit.write_bytes(b"not a fit file")

    with pytest.raises(InvalidWorkoutError) as json_error:
        load_workout(malformed_json)
    assert isinstance(json_error.value.__cause__, json.JSONDecodeError)

    with pytest.raises(InvalidWorkoutError) as fit_error:
        load_workout(malformed_fit)
    assert fit_error.value.__cause__ is not None


def test_permissive_loader_allows_diagnostic_only_result(tmp_path) -> None:
    path = tmp_path / "unsupported-duration.json"
    path.write_text(json.dumps({"steps": [{"calories": 100}]}))

    workout = load_workout(path, strict=False)
    assert workout.instructions == ()
    assert workout.diagnostics[0].code == "unsupported_feature"


def test_cli_maps_public_errors_to_exit_one(tmp_path, capsys) -> None:
    missing = tmp_path / "missing.json"

    with pytest.raises(SystemExit) as exit_error:
        cli_main([str(missing)])

    assert exit_error.value.code == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("low", "high", "field", "expected"),
    [
        (0, 0, "power_percent_ftp", RangeTarget(low=0, high=0)),
        (90, 100, "power_percent_ftp", RangeTarget(low=90, high=100)),
        (1100, 1200, "power_watts", RangeTarget(low=100, high=200)),
    ],
)
def test_fit_decodes_power_bound_encodings(low, high, field, expected) -> None:
    workout = parse_fit(
        _FitFile(
            _FitMessage(
                message_index=0,
                duration_type="time",
                duration_time=60,
                target_type="power",
                target_power_zone=0,
                custom_target_power_low=low,
                custom_target_power_high=high,
            )
        )
    )

    assert getattr(workout.expanded_steps()[0], field) == expected


@pytest.mark.parametrize(
    "target_fields",
    [
        {
            "target_type": "power",
            "target_power_zone": 0,
            "custom_target_power_low": 1100,
        },
        {
            "target_type": "power",
            "target_power_zone": 0,
            "custom_target_power_low": 1100,
            "custom_target_power_high": 90,
        },
        {
            "target_type": "heart_rate",
            "target_hr_zone": 0,
            "custom_target_heart_rate_low": 180,
            "custom_target_heart_rate_high": 90,
        },
        {
            "target_type": "cadence",
            "target_cadence_zone": 2,
            "custom_target_cadence_low": 80,
            "custom_target_cadence_high": 90,
        },
    ],
)
def test_fit_rejects_incomplete_mixed_or_conflicting_targets(target_fields) -> None:
    message = _FitMessage(
        message_index=0,
        duration_type="time",
        duration_time=60,
        **target_fields,
    )

    with pytest.raises(InvalidWorkoutError):
        parse_fit(_FitFile(message))

    workout = parse_fit(_FitFile(message), strict=False)
    assert len(workout.expanded_steps()) == 1
    assert workout.diagnostics[0].code == "invalid_field"


def test_fit_zone_ignores_zero_custom_sentinels() -> None:
    workout = parse_fit(
        _FitFile(
            _FitMessage(
                message_index=0,
                duration_type="time",
                duration_time=60,
                target_type="power",
                target_power_zone=3,
                custom_target_power_low=0,
                custom_target_power_high=0,
            )
        )
    )
    step = workout.expanded_steps()[0]

    assert step.power_zone == PointTarget(value=3)
    assert step.power_watts is None
    assert step.power_percent_ftp is None


def test_fit_never_reinterprets_generic_duration_value_as_seconds() -> None:
    with pytest.raises(InvalidWorkoutError):
        parse_fit(
            _FitFile(
                _FitMessage(
                    message_index=0,
                    duration_type="distance",
                    duration_value=40000,
                    target_type="open",
                )
            )
        )
