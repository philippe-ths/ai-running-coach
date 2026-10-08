"""Owner-scoped reads and writes for seasons (#1064).

The `store.py` idioms, in their own module because a season is a different row
with a different lifecycle (rewritten when the goals change, not when the weeks
do). Every function takes a REQUIRED `user_id`.

A `drafting` season older than `stale_after()` reads as failed. The bound is the
longest job that writes a season plus a margin: the season job, and the schedule
job that writes a season before the weeks. So a season is never declared
abandoned while its job could still be running, and a worker that died cannot
leave the runner looking at a spinner forever (the `store.draft_in_flight`
problem, with the threshold derived rather than guessed).
"""

import hashlib
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.season import Season
from app.schemas.season import SeasonPlan
from app.services.schedule import store

logger = logging.getLogger(__name__)

DRAFTING = "drafting"
ACTIVE = "active"
SUPERSEDED = "superseded"
FAILED = "failed"

FAILURE_MESSAGE = (
    "Your coach could not settle a season that held together. Nothing has changed: "
    "ask again, or talk it through in a conversation."
)
STALE_MESSAGE = (
    "Writing your season took too long and was stopped. Nothing has changed: ask "
    "again."
)
UNREACHABLE_MESSAGE = (
    "Your coach could not be reached, so no season was written. Nothing has "
    "changed: try again in a few minutes."
)
OVER_BUDGET_MESSAGE = (
    "You have used this period's coaching allowance, so no season was written. "
    "Nothing has changed: ask again once the allowance resets."
)
NO_GOALS_MESSAGE = "Add a goal first: the season is built around what you are aiming at."
GOALS_CHANGED_MESSAGE = (
    "Your goals have changed since your season was planned, so these weeks cannot "
    "be changed against it. Draft your plan again first, then change the weeks."
)


def stale_after() -> timedelta:
    """The longest a season's writer can legitimately run, plus a margin.

    A season is written by its own job (`RQ_JOB_TIMEOUT_SECONDS`) and also inside
    the schedule job, which plans the season and then the weeks
    (`SCHEDULE_JOB_TIMEOUT_SECONDS`), so the bound is the longer of the two.
    """
    longest = max(settings.RQ_JOB_TIMEOUT_SECONDS, settings.SCHEDULE_JOB_TIMEOUT_SECONDS)
    return timedelta(seconds=longest + 180)


def goals_fingerprint(goals: Sequence[Any]) -> str:
    """A stable hash of the goals a season is written against.

    Every field the coach is shown, in a fixed order, so editing a goal's date,
    notes or booked bit changes it and re-sorting or re-reading does not.
    """
    rows = sorted(
        (
            {
                "id": str(g.id),
                "name": g.name,
                "priority": g.priority,
                "race_date": g.race_date.isoformat() if g.race_date else None,
                "window_start": g.window_start.isoformat() if g.window_start else None,
                "window_end": g.window_end.isoformat() if g.window_end else None,
                "distance_m": g.distance_m,
                "target_time_s": g.target_time_s,
                "notes": g.notes or None,
                "booked": bool(g.booked),
            }
            for g in goals
        ),
        key=lambda row: row["id"],
    )
    blob = json.dumps(rows, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def stamp(goals: Sequence[Any], as_of: date) -> str:
    """The fingerprint a season stores: the day it was written plus the hash of
    the goals it was written against (those still ahead on that day).

    The day travels WITH the hash so the comparison can look at the same set of
    goals. Read over "goals still ahead today" instead, a dated goal that simply
    passed would drop out and mark the season stale; only an edit, an addition or
    a deletion should.
    """
    return f"{as_of.isoformat()}:{goals_fingerprint(goals)}"


def is_stale(db: Session, season: Season) -> bool:
    """Whether the runner's goals changed since this season was written.

    Compared over the goals still ahead on the day the season was written
    (`stamp`), so a goal whose date has since passed is not a change. A season
    with no readable stamp reads as stale: nothing says what it was written for.
    """
    day, _, _ = (season.goals_fingerprint or "").partition(":")
    try:
        written_on = date.fromisoformat(day)
    except ValueError:
        return True
    goals = store.list_goal_races(db, season.user_id, on_or_after=written_on)
    return season.goals_fingerprint != stamp(goals, written_on)


def season_plan(season: Season) -> Optional[SeasonPlan]:
    """The stored plan, strict-coerced, or None when absent or off-shape."""
    if not season.plan:
        return None
    try:
        return SeasonPlan.model_validate(season.plan)
    except Exception:  # noqa: BLE001 - one bad row must not take the screen down
        logger.exception("season %s: stored plan is off-contract", season.id)
        return None


def _aware(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _expire_if_stale(db: Session, season: Season) -> Season:
    """A drafting season past `stale_after` becomes failed, and is returned so."""
    created = _aware(season.created_at)
    if (
        season.status == DRAFTING
        and created is not None
        and created < datetime.now(timezone.utc) - stale_after()
    ):
        return fail_season(db, season, STALE_MESSAGE)
    return season


def _newest(db: Session, user_id: uuid.UUID, *statuses: str) -> Optional[Season]:
    return (
        db.query(Season)
        .filter(Season.user_id == user_id, Season.status.in_(statuses))
        .order_by(Season.created_at.desc(), Season.id.desc())
        .first()
    )


def active_season(db: Session, user_id: uuid.UUID) -> Optional[Season]:
    return _newest(db, user_id, ACTIVE)


def latest_season(db: Session, user_id: uuid.UUID) -> Optional[Season]:
    """The runner's newest season row of any status (stale drafting read as failed)."""
    season = (
        db.query(Season)
        .filter(Season.user_id == user_id)
        .order_by(Season.created_at.desc(), Season.id.desc())
        .first()
    )
    return _expire_if_stale(db, season) if season is not None else None


def drafting_in_flight(db: Session, user_id: uuid.UUID) -> Optional[Season]:
    """A season being written right now, or None. The idempotency guard for a
    billed operation, asked on its own rather than read off `latest_season`."""
    season = _newest(db, user_id, DRAFTING)
    if season is None:
        return None
    season = _expire_if_stale(db, season)
    return season if season.status == DRAFTING else None


def create_drafting_season(db: Session, user_id: uuid.UUID) -> Season:
    """An empty season in `drafting`, created before generation so the client has
    something to poll and a dead worker leaves a visible row."""
    season = Season(user_id=user_id, status=DRAFTING, goals_fingerprint="")
    db.add(season)
    db.commit()
    db.refresh(season)
    return season


def activate_season(db: Session, season: Season) -> Season:
    """Make this the runner's active season, superseding the previous one.

    One commit, so at no instant does the runner have two active seasons or none.
    """
    now = datetime.now(timezone.utc)
    db.query(Season).filter(
        Season.user_id == season.user_id,
        Season.status == ACTIVE,
        Season.id != season.id,
    ).update(
        {Season.status: SUPERSEDED, Season.superseded_at: now},
        synchronize_session=False,
    )
    season.status = ACTIVE
    season.superseded_at = None
    season.generated_at = now
    season.failure_message = None
    db.commit()
    db.refresh(season)
    return season


def supersede_season(db: Session, season: Season) -> Season:
    """Retire an active season without a successor: the runner has no goal left
    to plan it around."""
    season.status = SUPERSEDED
    season.superseded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(season)
    return season


def fail_season(db: Session, season: Season, message: str) -> Season:
    logger.warning("season %s failed: %s", season.id, message)
    season.status = FAILED
    season.failure_message = message
    db.commit()
    db.refresh(season)
    return season
