from datetime import date, datetime, timedelta
from typing import Optional, List, Dict, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from app.services.personal_bests import DISTANCES, STATED_TIME_BOUNDS
from app.services.schedule.runner_rules import ABSURD_SESSIONS_PER_DAY
from app.services.weeks import MONDAY, SUNDAY


class StatedPB(BaseModel):
    """One PB the runner tells us (#1068). `distance` uses Strava's best-effort
    labels so a stated and a derived PB share one key."""

    distance: str
    time_s: int
    on: Optional[date] = None

    @field_validator("distance")
    @classmethod
    def _distance_is_standard(cls, v: str) -> str:
        if v not in DISTANCES:
            raise ValueError(f"distance must be one of {', '.join(DISTANCES)}")
        return v

    @field_validator("on")
    @classmethod
    def _not_in_the_future(cls, v: Optional[date]) -> Optional[date]:
        # A day of slack: the server's today can trail a runner east of it.
        if v is not None and v > date.today() + timedelta(days=1):
            raise ValueError("a PB date cannot be in the future")
        return v

    @field_serializer("on")
    def _on_as_iso(self, v: Optional[date]) -> Optional[str]:
        # Stored in a JSON column, which cannot hold a date object.
        return v.isoformat() if v else None


class UserProfileBase(BaseModel):
    goal_type: str
    target_date: Optional[date] = None
    experience_level: str
    weekly_days_available: int
    current_weekly_km: Optional[int] = None
    max_hr: Optional[int] = None
    max_hr_source: Optional[str] = None  # "user_entered", "race_estimate", "lab_test"
    resting_hr: Optional[int] = None  # manual resting HR (bpm), interim source for #555
    # The runner's build (#742). Null = not stated, which the coach pack drops
    # rather than filling in with a typical runner.
    weight_kg: Optional[float] = None
    height_cm: Optional[float] = None
    upcoming_races: List[Dict[str, Any]] = []
    injury_notes: Optional[str] = None
    stimulant_use: Optional[bool] = None
    # Week start: Monday (0) or Sunday (6); null resolves to Monday (#676).
    week_starts_on: Optional[int] = None
    # Most activities in a day, walks included; null = no limit (#1080).
    max_activities_per_day: Optional[int] = None

    @field_validator("week_starts_on")
    @classmethod
    def _week_start_is_monday_or_sunday(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v not in (MONDAY, SUNDAY):
            raise ValueError("week_starts_on must be 0 (Monday) or 6 (Sunday)")
        return v

    @field_validator("max_activities_per_day")
    @classmethod
    def _daily_limit_in_range(cls, v: Optional[int]) -> Optional[int]:
        # Capped at the plan checker's own absurdity ceiling: a setting above it
        # would promise days the checker rejects anyway.
        if v is not None and not 1 <= v <= ABSURD_SESSIONS_PER_DAY:
            raise ValueError(
                f"max_activities_per_day must be between 1 and {ABSURD_SESSIONS_PER_DAY}"
            )
        return v

    @field_validator("weight_kg")
    @classmethod
    def _weight_is_physiologically_possible(cls, v: Optional[float]) -> Optional[float]:
        # A generous envelope, not a judgement about what a runner should weigh.
        # It exists to catch a unit slip (pounds typed into a kg field reads as
        # ~240) and a stray keystroke, both of which would otherwise reach the
        # coach as a fact and skew its read of the runner's build.
        if v is not None and not (20 <= v <= 300):
            raise ValueError("weight_kg must be between 20 and 300 (kilograms)")
        return v

    @field_validator("height_cm")
    @classmethod
    def _height_is_physiologically_possible(cls, v: Optional[float]) -> Optional[float]:
        # Same intent: catches metres (1.87) and inches (74) typed into a cm field.
        if v is not None and not (100 <= v <= 250):
            raise ValueError("height_cm must be between 100 and 250 (centimetres)")
        return v


class UserProfileCreate(UserProfileBase):
    # PBs the runner has told us (#1068). Null = none stated. Validated on write
    # only, so a stored row a later envelope would refuse never breaks a read.
    stated_pbs: Optional[List[StatedPB]] = None

    @field_validator("stated_pbs")
    @classmethod
    def _stated_pbs_are_plausible(cls, v: Optional[List[StatedPB]]) -> Optional[List[StatedPB]]:
        # Same intent as the weight envelope: a time outside world-record-to-walking
        # is a unit slip (minutes typed as seconds), not a fact to hand the coach.
        if v is None:
            return v
        seen = set()
        for pb in v:
            if pb.distance in seen:
                raise ValueError(f"only one stated PB per distance ({pb.distance})")
            seen.add(pb.distance)
            low, high = STATED_TIME_BOUNDS[pb.distance]
            if not (low <= pb.time_s <= high):
                raise ValueError(
                    f"{pb.distance} time must be between {low} and {high} seconds"
                )
        return v or None


class UserProfileRead(UserProfileBase):
    stated_pbs: Optional[List[Dict[str, Any]]] = None
    user_id: UUID
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)
