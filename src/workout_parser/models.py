from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from math import floor
from pydantic import BaseModel, Field


class PointTarget(BaseModel):
    value: int | float


class RangeTarget(BaseModel):
    low: int | float
    high: int | float

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


class RampTarget(BaseModel):
    start: int | float
    end: int | float


class TimeDuration(BaseModel):
    seconds: float


class DistanceDuration(BaseModel):
    meters: float


class OpenDuration(BaseModel):
    event: str = "lap_button"


class ParseDiagnostic(BaseModel):
    code: str
    message: str
    step_index: int | None = None


def _scale_target(
    target: PointTarget | RangeTarget | RampTarget,
    factor: float,
    *,
    integer: bool,
) -> PointTarget | RangeTarget | RampTarget:
    def scale(value: int | float) -> int | float:
        scaled = value * factor
        return floor(scaled) if integer else scaled

    if isinstance(target, PointTarget):
        return PointTarget(value=scale(target.value))
    if isinstance(target, RangeTarget):
        return RangeTarget(
            low=scale(target.low),
            high=scale(target.high),
        )
    return RampTarget(start=scale(target.start), end=scale(target.end))


class WorkoutStep(BaseModel):
    text: str | None = None
    duration: TimeDuration | DistanceDuration | OpenDuration

    power_watts: PointTarget | RangeTarget | RampTarget | None = None
    power_percent_ftp: PointTarget | RangeTarget | RampTarget | None = None
    power_zone: PointTarget | RangeTarget | RampTarget | None = None
    speed_mps: PointTarget | RangeTarget | RampTarget | None = None
    speed_percent_threshold: PointTarget | RangeTarget | RampTarget | None = None
    speed_zone: PointTarget | RangeTarget | RampTarget | None = None
    heart_rate_bpm: PointTarget | RangeTarget | RampTarget | None = None
    heart_rate_percent_max: PointTarget | RangeTarget | RampTarget | None = None
    heart_rate_percent_lthr: PointTarget | RangeTarget | RampTarget | None = None
    heart_rate_zone: PointTarget | RangeTarget | RampTarget | None = None
    cadence_rpm: PointTarget | RangeTarget | RampTarget | None = None
    cadence_zone: PointTarget | RangeTarget | RampTarget | None = None

    model_config = {"frozen": False}

    @property
    def duration_s(self) -> float | None:
        if isinstance(self.duration, TimeDuration):
            return self.duration.seconds
        return None

    def generate_absolute_power_targets_from_percent(self, ftp_watts: int) -> None:
        """Resolve the percent-FTP target while preserving its target shape."""
        if self.power_percent_ftp is not None:
            self.power_watts = _scale_target(
                self.power_percent_ftp,
                float(ftp_watts) / 100.0,
                integer=True,
            )

    def generate_pace_targets_from_percent(self, threshold_speed_mps: float) -> None:
        """Resolve the percent-threshold target while preserving its target shape."""
        if self.speed_percent_threshold is not None:
            self.speed_mps = _scale_target(
                self.speed_percent_threshold,
                float(threshold_speed_mps) / 100.0,
                integer=False,
            )


class RepeatBlock(BaseModel):
    repetitions: int = Field(gt=0, strict=True)
    instructions: list[WorkoutStep | RepeatBlock] = Field(default_factory=list)


class Workout(BaseModel):
    name: str
    description: str | None = None
    workout_date: date | None = None
    source_ftp_watts: int | float | None = None
    diagnostics: list[ParseDiagnostic] = Field(default_factory=list)

    instructions: list[WorkoutStep | RepeatBlock] = Field(default_factory=list)

    def _iter_steps(self) -> Iterator[WorkoutStep]:
        def walk(
            instructions: list[WorkoutStep | RepeatBlock],
        ) -> Iterator[WorkoutStep]:
            for instruction in instructions:
                if isinstance(instruction, WorkoutStep):
                    yield instruction
                else:
                    for _ in range(instruction.repetitions):
                        yield from walk(instruction.instructions)

        yield from walk(self.instructions)

    def expanded_steps(self) -> list[WorkoutStep]:
        """Materialize the instruction tree as independent executable steps."""
        return [step.model_copy(deep=True) for step in self._iter_steps()]

    @property
    def total_seconds(self) -> float | None:
        total = 0.0
        for step in self._iter_steps():
            if step.duration_s is None:
                return None
            total += step.duration_s
        return total

    def get_step_at(self, t_s: float) -> tuple[int | None, WorkoutStep | None]:
        """Returns the WorkoutStep active at time t_s into the workout."""
        elapsed = 0.0
        for idx, step in enumerate(self._iter_steps()):
            duration_s = step.duration_s
            if duration_s is None:
                return (None, None)
            if elapsed <= t_s < elapsed + duration_s:
                return (idx, step)
            elapsed += duration_s
        return (None, None)
