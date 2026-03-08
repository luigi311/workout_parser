from math import floor
import enum
from datetime import date

from pydantic import BaseModel, Field, model_validator


class WorkoutStep(BaseModel):
    text: str | None = None

    duration_s: float

    # Absolute targets

    # Power targets
    watts_mid: int | None = None
    watts_lo: int | None = None
    watts_hi: int | None = None

    # Pace targets
    speed_mps_mid: float | None = None
    speed_mps_lo: float | None = None
    speed_mps_hi: float | None = None

    # Percent targets

    # Power targets as %FTP
    percent_watts_mid: float | None = None
    percent_watts_lo: float | None = None
    percent_watts_hi: float | None = None

    percent_speed_mid: float | None = None
    percent_speed_lo: float | None = None
    percent_speed_hi: float | None = None

    model_config = {"frozen": False}

    # --- Non-canonical pace targets for UI purposes ---
    @property
    def speed_kph_mid(self) -> float | None:
        return self.speed_mps_mid * 3.6 if self.speed_mps_mid is not None else None

    @property
    def speed_kph_lo(self) -> float | None:
        return self.speed_mps_lo * 3.6 if self.speed_mps_lo is not None else None

    @property
    def speed_kph_hi(self) -> float | None:
        return self.speed_mps_hi * 3.6 if self.speed_mps_hi is not None else None

    @property
    def speed_mph_mid(self) -> float | None:
        return self.speed_mps_mid * 2.23694 if self.speed_mps_mid is not None else None

    @property
    def speed_mph_lo(self) -> float | None:
        return self.speed_mps_lo * 2.23694 if self.speed_mps_lo is not None else None

    @property
    def speed_mph_hi(self) -> float | None:
        return self.speed_mps_hi * 2.23694 if self.speed_mps_hi is not None else None

    # --- Compute bands w/ fallbacks for gauge UI ---
    def _generate_bands(self) -> "WorkoutStep":
        # For watts use floor to round down to nearest integer (since watts are typically integers and this avoids weird fractional watt targets)
        if self.watts_mid is not None and (self.watts_lo is None or self.watts_hi is None):
            mid_val = self.watts_mid
            self.watts_lo = floor(mid_val * 0.95) if self.watts_lo is None else self.watts_lo
            self.watts_hi = floor(mid_val * 1.05) if self.watts_hi is None else self.watts_hi
        elif self.watts_lo is not None and self.watts_hi is not None and self.watts_mid is None:
            self.watts_mid = floor(0.5 * (self.watts_lo + self.watts_hi))

        # For pace targets use regular float math for the bands
        for attr in ["percent_speed", "speed_mps", "percent_watts"]:
            mid_val = getattr(self, f"{attr}_mid")
            lo_val = getattr(self, f"{attr}_lo")
            hi_val = getattr(self, f"{attr}_hi")

            if mid_val is not None and (lo_val is None or hi_val is None):
                setattr(self, f"{attr}_lo", mid_val * 0.95)
                setattr(self, f"{attr}_hi", mid_val * 1.05)
            elif lo_val is not None and hi_val is not None and mid_val is None:
                setattr(self, f"{attr}_mid", 0.5 * (lo_val + hi_val))
            
        return self

    @model_validator(mode="after")
    def _on_init(self) -> "WorkoutStep":
        self._generate_bands()
        return self

    def generate_absolute_power_targets_from_percent(self, ftp_watts: int) -> None:
        """Generate absolute power targets from %FTP."""
        if self.percent_watts_mid is not None:
            self.watts_mid = floor(float(ftp_watts) * float(self.percent_watts_mid) / 100.0)
        if self.percent_watts_lo is not None:
            self.watts_lo = floor(float(ftp_watts) * float(self.percent_watts_lo) / 100.0)
        if self.percent_watts_hi is not None:
            self.watts_hi = floor(float(ftp_watts) * float(self.percent_watts_hi) / 100.0)

        self._generate_bands()

    def generate_pace_targets_from_percent(self, threshold_speed_mps: float) -> None:
        """Generate absolute pace targets from threshold meters per second pace."""
        if self.percent_speed_mid is not None:
            self.speed_mps_mid = (
                float(threshold_speed_mps) * float(self.percent_speed_mid) / 100.0
            )
        if self.percent_speed_lo is not None:
            self.speed_mps_lo = (
                float(threshold_speed_mps) * float(self.percent_speed_lo) / 100.0
            )
        if self.percent_speed_hi is not None:
            self.speed_mps_hi = (
                float(threshold_speed_mps) * float(self.percent_speed_hi) / 100.0
            )

        self._generate_bands()


class Workout(BaseModel):
    name: str
    workout_date: date | None = None

    steps: list[WorkoutStep] = Field(default_factory=list)

    @property
    def total_seconds(self) -> float:
        return sum(s.duration_s for s in self.steps)

    def get_step_at(self, t_s: float) -> tuple[int | None, WorkoutStep | None]:
        """Returns the WorkoutStep active at time t_s into the workout."""
        elapsed = 0.0
        for idx,step in enumerate(self.steps):
            if elapsed <= t_s < elapsed + step.duration_s:
                return (idx, step)
            elapsed += step.duration_s
        return (None, None)
