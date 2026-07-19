from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from math import floor, isfinite
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


FiniteNonNegative = Annotated[
    int | float, Field(ge=0, allow_inf_nan=False)
]
FinitePositive = Annotated[int | float, Field(gt=0, allow_inf_nan=False)]
NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class PointTarget(_ImmutableModel):
    value: FiniteNonNegative


class RangeTarget(_ImmutableModel):
    low: FiniteNonNegative
    high: FiniteNonNegative

    @model_validator(mode="after")
    def validate_order(self) -> "RangeTarget":
        if self.low > self.high:
            raise ValueError("range low must not exceed high")
        return self

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


class RampTarget(_ImmutableModel):
    start: FiniteNonNegative
    end: FiniteNonNegative


class TimeDuration(_ImmutableModel):
    seconds: FinitePositive


class DistanceDuration(_ImmutableModel):
    meters: FinitePositive


class OpenDuration(_ImmutableModel):
    event: NonEmptyString = "lap_button"


class ParseDiagnostic(_ImmutableModel):
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


class WorkoutStep(_ImmutableModel):
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

    @property
    def duration_s(self) -> float | None:
        if isinstance(self.duration, TimeDuration):
            return self.duration.seconds
        return None

    def resolve_power_targets(self, ftp_watts: int | float) -> "WorkoutStep":
        """Return a copy resolved against an externally supplied FTP."""
        if not isfinite(float(ftp_watts)) or ftp_watts <= 0:
            raise ValueError("ftp_watts must be finite and greater than zero")
        power_watts = self.power_watts
        if self.power_percent_ftp is not None:
            power_watts = _scale_target(
                self.power_percent_ftp,
                float(ftp_watts) / 100.0,
                integer=True,
            )
        return self.model_copy(update={"power_watts": power_watts}, deep=True)

    def resolve_pace_targets(
        self, threshold_speed_mps: int | float
    ) -> "WorkoutStep":
        """Return a copy resolved against an externally supplied threshold pace."""
        if not isfinite(float(threshold_speed_mps)) or threshold_speed_mps <= 0:
            raise ValueError(
                "threshold_speed_mps must be finite and greater than zero"
            )
        speed_mps = self.speed_mps
        if self.speed_percent_threshold is not None:
            speed_mps = _scale_target(
                self.speed_percent_threshold,
                float(threshold_speed_mps) / 100.0,
                integer=False,
            )
        return self.model_copy(update={"speed_mps": speed_mps}, deep=True)


class RepeatBlock(_ImmutableModel):
    repetitions: int = Field(gt=0, strict=True)
    instructions: tuple[WorkoutStep | RepeatBlock, ...] = ()


class Workout(_ImmutableModel):
    name: str
    description: str | None = None
    workout_date: date | None = None
    source_ftp_watts: FinitePositive | None = None
    diagnostics: tuple[ParseDiagnostic, ...] = ()

    instructions: tuple[WorkoutStep | RepeatBlock, ...] = ()

    def _iter_steps(self) -> Iterator[WorkoutStep]:
        def walk(
            instructions: tuple[WorkoutStep | RepeatBlock, ...],
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
        if not isfinite(float(t_s)) or t_s < 0:
            raise ValueError("t_s must be finite and non-negative")
        elapsed = 0.0
        for idx, step in enumerate(self._iter_steps()):
            duration_s = step.duration_s
            if duration_s is None:
                return (None, None)
            if elapsed <= t_s < elapsed + duration_s:
                return (idx, step)
            elapsed += duration_s
        return (None, None)
