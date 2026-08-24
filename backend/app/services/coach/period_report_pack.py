"""The period-report pack (#946): what a coach needs to review a runner-chosen
STRETCH of training, over the disciplines the runner chose.

Deliberately not the per-activity context pack. `build_context_pack` threads a
single `Activity` non-optionally through `_assemble_pack` ->
`assemble_working_context` -> `build_b_baseline`/`build_focus_payload`; there is
no window in that chain to retrofit. This pack answers a different coaching
question — "how is this block going", never "how did that run go" — so it
carries no per-activity focus payload and no stream data at all, composed
instead from the pure, activity-agnostic reads that already exist:
`activity_facts.query_facts` for the fact stream, the shared `_totals`/`_by_type`
projections `query_tools.get_training_summary` already uses (not re-derived
here), and the thread turn's own relationship baseline
(`thread_turn._build_baseline_sections`/`_profile_dict`) for profile, memory,
current readiness, the runner's active plan and running norm — the same reads a
conversational turn assembles for "the runner and now" rather than for one run.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.models.user import User
from app.services.activity_facts import query_facts
from app.services.coach.coach_units import duration as _duration
from app.services.coach.coach_units import km as _km
from app.services.coach.coach_units import pace as _pace
from app.services.coach.context import zones_calibration
from app.services.coach.query_tools import _by_type, _totals
from app.services.coach.thread_turn import _build_baseline_sections, _profile_dict
from app.services.schedule import store as schedule_store

logger = logging.getLogger(__name__)

# Bounded like every other per-session enumeration this project puts in front of
# a model (`query_tools._MAX_LIST_ACTIVITIES`); a period can span a year, and the
# coach reads the computed totals for scale, individual sessions for texture.
MAX_PACK_SESSIONS = 60


class PeriodReportSession(BaseModel):
    """One session's coach-framed summary — the `list_activities_in_range`
    entry shape, minus the fields that tool has and a period review does not
    need (interval/long-run markers belong to depth on ONE run)."""

    model_config = ConfigDict(extra="forbid")

    activity_id: str
    date: str
    weekday: str
    type: str
    distance_km: Optional[float] = None
    duration: Optional[str] = None
    pace_per_km: Optional[str] = None
    effort: Optional[str] = None


class PeriodReportPack(BaseModel):
    """Strict pack, `extra="forbid"`: an off-shape field fails at construction
    rather than reaching the prompt. No stream data, no per-activity focus
    payload — see module docstring."""

    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date
    disciplines: List[str] = Field(default_factory=list)
    zones_calibrated: bool

    profile: Dict[str, Any] = Field(default_factory=dict)
    goal_race: Optional[Dict[str, Any]] = None
    memory: Optional[Dict[str, Any]] = None
    readiness: Optional[Dict[str, Any]] = None
    schedule: Optional[Dict[str, Any]] = None
    running_norm: Optional[Dict[str, Any]] = None

    totals: Dict[str, Any] = Field(default_factory=dict)
    by_type: List[Dict[str, Any]] = Field(default_factory=list)
    sessions: List[PeriodReportSession] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """True when nothing the runner logged falls in this period under these
        disciplines — the pack's own answer to "is there anything to review"."""
        return int(self.totals.get("sessions", 0)) == 0


def build_period_report_pack(
    db: Session,
    user: User,
    *,
    period_start: date,
    period_end: date,
    disciplines: List[str],
) -> PeriodReportPack:
    """Assemble the pack for one (user, period, disciplines) request.

    `period_end` is INCLUSIVE (the runner's own request), so the underlying
    `[start, end)` fact query is asked for `period_end + 1 day`.
    """
    profile = getattr(user, "profile", None)
    zones_calibrated, _basis = zones_calibration(profile)

    facts = query_facts(
        db,
        period_start,
        period_end + timedelta(days=1),
        types=disciplines or None,
        user_id=user.id,
        include_session_shape=True,
    )

    # The relationship baseline (#946 requirement 3): memory, current readiness,
    # the active plan and the running norm, read exactly the way a thread turn
    # reads them — this is a review of the runner and their training, not of one
    # activity, so it shares that read rather than the per-activity pack's.
    try:
        baseline = _build_baseline_sections(db, user)
    except Exception:  # noqa: BLE001 — a baseline hiccup must not block the pack
        logger.exception("period report: baseline sections failed for user %s", user.id)
        baseline = {}

    goal_race = None
    try:
        races = schedule_store.list_goal_races(db, user.id, on_or_after=period_end)
    except Exception:  # noqa: BLE001
        races = []
    if races:
        race = races[0]
        goal_race = {
            "name": race.name,
            "race_date": race.race_date.isoformat(),
            "distance_m": race.distance_m,
            "priority": race.priority,
        }

    ordered = sorted(facts, key=lambda f: f.local_date)
    sessions = [
        PeriodReportSession(
            activity_id=str(f.activity_id),
            date=f.local_date.isoformat(),
            weekday=f.local_date.strftime("%a"),
            type=f.activity_type,
            distance_km=_km(f.distance_m),
            duration=_duration(f.moving_time_s),
            pace_per_km=_pace(f.distance_m, f.moving_time_s),
            effort=f.effort,
        )
        for f in ordered[-MAX_PACK_SESSIONS:]
    ]

    return PeriodReportPack(
        period_start=period_start,
        period_end=period_end,
        disciplines=list(disciplines or []),
        zones_calibrated=zones_calibrated,
        profile=_profile_dict(profile),
        goal_race=goal_race,
        memory=baseline.get("memory"),
        readiness=baseline.get("readiness"),
        schedule=baseline.get("schedule"),
        running_norm=baseline.get("running_norm"),
        totals=_totals(facts),
        by_type=_by_type(facts),
        sessions=sessions,
    )
