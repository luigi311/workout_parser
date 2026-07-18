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
    duration_s: float

    power_watts: PointTarget | RangeTarget | RampTarget | None = None
    power_percent_ftp: PointTarget | RangeTarget | RampTarget | None = None
    speed_mps: PointTarget | RangeTarget | RampTarget | None = None
    speed_percent_threshold: PointTarget | RangeTarget | RampTarget | None = None

    model_config = {"frozen": False}

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


class Workout(BaseModel):
    name: str
    description: str | None = None
    workout_date: date | None = None

    steps: list[WorkoutStep] = Field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(s.duration_s for s in self.steps)

    def get_step_at(self, t_s: float) -> tuple[int | None, WorkoutStep | None]:
        """Returns the WorkoutStep active at time t_s into the workout."""
        elapsed = 0.0
        for idx, step in enumerate(self.steps):
            if elapsed <= t_s < elapsed + step.duration_s:
                return (idx, step)
            elapsed += step.duration_s
        return (None, None)
