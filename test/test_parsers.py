from __future__ import annotations

import math
from itertools import combinations
from pathlib import Path

import pytest
from workout_parser import PointTarget, RampTarget, RangeTarget, load_workout

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
