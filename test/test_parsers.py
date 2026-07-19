from __future__ import annotations

import math
from itertools import combinations
from pathlib import Path

import pytest
from workout_parser import (
    DistanceDuration,
    InvalidWorkoutError,
    OpenDuration,
    PointTarget,
    RampTarget,
    RangeTarget,
    TimeDuration,
    UnsupportedWorkoutFeatureError,
    load_workout,
)
from workout_parser.intervals_icu import parse_intervals_icu_json
from workout_parser.fit import parse_fit

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

    assert len(w_a.steps) > 0, f"{json_path.name} yielded no steps"
    assert len(w_a.steps) == len(w_b.steps), f"Step count mismatch: {len(w_a.steps)} vs {len(w_b.steps)}"
    assert _close(w_a.total_seconds, w_b.total_seconds, 1.0), f"Total duration mismatch"

    for i, (sa, sb) in enumerate(zip(w_a.steps, w_b.steps)):
        assert _close(sa.duration_s, sb.duration_s, 0.5), f"Step {i} duration: {sa.duration_s} vs {sb.duration_s}"
        assert _close(_target_mid(sa.power_watts), _target_mid(sb.power_watts), 1.0)
        assert _close(_target_mid(sa.speed_mps), _target_mid(sb.speed_mps), 0.01)


# Test 30_Minute_Threshold_Test_New_Build_Phase_fit.json and 30_Minute_Threshold_Test_New_Build_Phase_json.json to make sure they match
def test_intervals_json() -> None:
    json_path = DATA / "30_Minute_Threshold_Test_New_Build_Phase_json.json"
    fit_path = DATA / "30_Minute_Threshold_Test_New_Build_Phase_fit.json"

    w_a = load_workout(json_path)
    w_b = load_workout(fit_path)

    assert len(w_a.steps) > 0, f"{json_path.name} yielded no steps"
    assert len(w_b.steps) > 0, f"{fit_path.name} yielded no steps"
    assert len(w_a.steps) == len(w_b.steps), f"Step count mismatch: {len(w_a.steps)} vs {len(w_b.steps)}"
    assert _close(w_a.total_seconds, w_b.total_seconds, 1.0), f"Total duration mismatch: {w_a.total_seconds} vs {w_b.total_seconds}"

    # Should be 4 steps with a total duration of 75 minutes
    assert len(w_a.steps) == 4, f"Expected 4 steps, got {len(w_a.steps)}"
    assert _close(w_a.total_seconds, 75 * 60, 1.0), f"Expected total duration of 75 minutes, got {w_a.total_seconds / 60:.2f} minutes"

    for i, (sa, sb) in enumerate(zip(w_a.steps, w_b.steps)):
        assert _close(sa.duration_s, sb.duration_s, 0.5), f"Step {i} duration: {sa.duration_s} vs {sb.duration_s}"
        assert _close(_target_mid(sa.power_watts), _target_mid(sb.power_watts), 1.0)
        assert _close(_target_mid(sa.speed_mps), _target_mid(sb.speed_mps), 0.01)


def test_json_preserves_target_shapes() -> None:
    workout = load_workout(DATA / "30_Minute_Threshold_Test_New_Build_Phase_json.json")

    assert workout.steps[0].speed_percent_threshold == RampTarget(
        start=50, end=90
    )
    assert isinstance(workout.steps[1].speed_mps, RangeTarget)
    assert workout.steps[1].speed_percent_threshold == PointTarget(value=70)
    assert workout.steps[3].speed_percent_threshold == RampTarget(
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

    assert workout.steps[0].duration == TimeDuration(seconds=60)
    assert workout.steps[0].heart_rate_bpm == RangeTarget(low=146, high=153)
    assert workout.steps[0].heart_rate_zone == PointTarget(value=2)
    assert workout.steps[1].heart_rate_bpm == PointTarget(value=135)
    assert workout.steps[2].heart_rate_percent_lthr == RangeTarget(
        low=85, high=90
    )
    assert workout.steps[3].duration == DistanceDuration(meters=400)
    assert workout.steps[3].cadence_rpm == PointTarget(value=90)
    assert workout.steps[4].duration == OpenDuration()
    assert workout.total_seconds is None


def test_unsupported_duration_is_strict_or_diagnostic() -> None:
    data = {"steps": [{"calories": 100}]}

    with pytest.raises(UnsupportedWorkoutFeatureError):
        parse_intervals_icu_json(data, Path("calories.json"))

    workout = parse_intervals_icu_json(
        data, Path("calories.json"), strict=False
    )
    assert workout.steps == []
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

    assert workout.steps[0].duration == DistanceDuration(meters=400)
    assert workout.steps[0].heart_rate_zone == PointTarget(value=3)
    assert workout.steps[0].heart_rate_percent_max is None
    assert workout.steps[1].heart_rate_percent_max == RangeTarget(
        low=70, high=80
    )
    assert workout.steps[2].duration == OpenDuration()
    assert workout.steps[2].cadence_rpm == RangeTarget(low=85, high=95)


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
    step = workout.steps[0]

    assert workout.source_ftp_watts == 165
    assert step.power_percent_ftp == PointTarget(value=105)
    assert step.power_watts == RangeTarget(low=168, high=178)

    step.generate_absolute_power_targets_from_percent(ftp_watts=250)
    assert step.power_percent_ftp == PointTarget(value=105)
    assert step.power_watts == PointTarget(value=262)


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
    assert workout.steps[0].power_percent_ftp == PointTarget(value=105)
    assert workout.steps[0].power_watts == RangeTarget(low=215, high=225)
    assert workout.diagnostics[0].code == "conflicting_resolved_power"
