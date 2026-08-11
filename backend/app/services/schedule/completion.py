"""Marking a planned session done — three ways in, one write (#830).

A session can be finished by being auto-matched to a synced activity, by a tap on
the card, or by telling the coach in conversation. All three converge here, on the
same columns, the way the in-app check-in form and the Telegram tap both go
through `write_checkin` rather than each owning a copy of what checking in means.

The matcher is deliberately conservative
----------------------------------------
Strava never sees the gym, and a 2 km jog is not the 18 km long run it happened to
fall inside the window of. So auto-matching only fires when it is CONFIDENT — the
right discipline, inside the window, and a substantial fraction of what was asked
for — and otherwise leaves the session open for the runner to tick or mention. A
missed auto-match costs one tap; a wrong one tells the runner they did a session
they did not do, and quietly feeds that into the plan-versus-actual read.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any, List, Optional

from sqlalchemy.orm import Session

from app.models.planned_session import PlannedSession
from app.services.activity_facts import local_day
from app.services.schedule import store
from app.services.schedule.disciplines import discipline_for_activity_type

logger = logging.getLogger(__name__)

# How much of the prescribed session has to be there before it auto-ticks. Well
# below the target, because a coach's "8 km easy" is satisfied by 7 km and a
# runner should not have to tap for that — but far enough above nothing that a
# warm-up walk cannot claim the long run.
MIN_COMPLETION_FRACTION = 0.5

AUTO = "auto"
MANUAL = "manual"
CONVERSATION = "conversation"


def _activity_local_day(activity: Any) -> date:
    """Routed through `activity_facts.local_day`, the one UTC-to-runner-local
    derivation. It was written three times before that module collected it; this
    would have been a fourth, and it happens to agree today, which is exactly how
    the previous three drifted."""
    return local_day(
        getattr(activity, "start_date", None),
        getattr(activity, "start_date_local", None),
    )


def _closeness(session: PlannedSession, activity: Any) -> float:
    """How well this activity fills this session, as a ratio of what was asked.

    1.0 is exactly the prescription. A session with no target at all sits at 1.0
    too: there is nothing to be far from.
    """
    if session.target_distance_m:
        actual = getattr(activity, "distance_m", 0) or 0
        return actual / session.target_distance_m
    if session.target_duration_s:
        actual = getattr(activity, "moving_time_s", 0) or 0
        return actual / session.target_duration_s
    return 1.0


def open_sessions_for(db: Session, activity: Any) -> List[PlannedSession]:
    """Every unfinished planned session this activity could plausibly be.

    Scoped to the ACTIVE plan and to the activity's own owner. A superseded
    plan's sessions are history and must not absorb today's run.
    """
    plan = store.get_active_plan(db, activity.user_id)
    if plan is None:
        return []
    day = _activity_local_day(activity)
    discipline = discipline_for_activity_type(getattr(activity, "type", None))
    return [
        session
        for session in db.query(PlannedSession)
        .filter(
            PlannedSession.user_id == activity.user_id,
            PlannedSession.plan_id == plan.id,
            PlannedSession.completed_at.is_(None),
            PlannedSession.dismissed_at.is_(None),
            PlannedSession.window_start <= day,
            PlannedSession.window_end >= day,
        )
        .all()
        if session.discipline == discipline and session.intent != "rest"
    ]


def credited_session(db: Session, activity: Any) -> Optional[PlannedSession]:
    """The session this activity has ALREADY been credited to, if any."""
    return (
        db.query(PlannedSession)
        .filter(
            PlannedSession.user_id == activity.user_id,
            PlannedSession.completed_activity_id == activity.id,
        )
        .first()
    )


def find_matching_session(db: Session, activity: Any) -> Optional[PlannedSession]:
    """The planned session this activity most plausibly IS, or None.

    Read-only. `analyze` calls this to hand the interval matcher a plan, and the
    auto-completion path calls it to decide what to tick; both need the same
    answer, so neither gets its own idea of what "this run was for" means.

    An ALREADY-CREDITED session wins outright, and that is not a nicety. The
    open-session search excludes anything completed, so on the second analysis of
    an activity it would find nothing and the run would stop being judged against
    the plan it was judged against the first time — `match_score` back to 1.0 and
    the `interval_structure_mismatch` reason replaced by `no_planned_workout`.
    Re-analysis is routine (a check-in re-analyses, and so does the Telegram RPE
    tap, the intent write, the stream backfill and the bulk re-analysis), so
    without this the runner tapping RPE on their notification would quietly erase
    the plan-versus-execution disagreement.
    """
    credited = credited_session(db, activity)
    if credited is not None:
        return credited

    candidates = [
        session
        for session in open_sessions_for(db, activity)
        if _closeness(session, activity) >= MIN_COMPLETION_FRACTION
    ]
    if not candidates:
        return None

    def _rank(session: PlannedSession):
        # A commitment outranks a suggestion; then whichever the activity fills
        # most exactly; then the tightest window, since a session the coach
        # placed narrowly is the more specific claim on this day.
        return (
            0 if session.commitment == "committed" else 1,
            abs(_closeness(session, activity) - 1.0),
            (session.window_end - session.window_start).days,
        )

    return sorted(candidates, key=_rank)[0]


def complete_planned_session(
    db: Session,
    session: PlannedSession,
    *,
    source: str,
    activity: Any = None,
    when: Optional[datetime] = None,
) -> PlannedSession:
    """The ONE write. Every route to "done" ends here."""
    session.completed_at = when or datetime.now(timezone.utc)
    session.completion_source = source
    session.completed_activity_id = getattr(activity, "id", None) if activity else None
    # Finishing something settles it; a suggestion the runner acted on is no
    # longer a suggestion they might dismiss.
    session.dismissed_at = None
    db.commit()
    db.refresh(session)
    return session


def clear_completion(db: Session, session: PlannedSession) -> PlannedSession:
    """Untick. The runner is allowed to be wrong about their own week."""
    session.completed_at = None
    session.completion_source = None
    session.completed_activity_id = None
    db.commit()
    db.refresh(session)
    return session


def dismiss_planned_session(db: Session, session: PlannedSession) -> PlannedSession:
    """Decline a SUGGESTION. Never a committed session.

    Declining something you agreed to is a plan change, and plan changes go
    through the coach rather than a button — otherwise the schedule quietly
    becomes a to-do list the runner edits, which is the thing it is not.
    """
    if session.commitment != "suggested":
        raise ValueError("only a suggestion can be dismissed")
    if session.completed_at is not None:
        # `complete_planned_session` clears `dismissed_at` on the way in, so the
        # two writers have to agree the pair is exclusive or a session ends up
        # carrying both stamps.
        raise ValueError("a completed session cannot be dismissed")
    session.dismissed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    return session


def complete_from_activity(db: Session, activity: Any) -> Optional[PlannedSession]:
    """Auto-tick whatever this synced activity satisfies, if anything.

    Idempotent: an activity already credited to a session cannot tick a second
    one, so a re-analysis or a replayed webhook changes nothing.
    """
    already = credited_session(db, activity)
    if already is not None:
        return already

    session = find_matching_session(db, activity)
    if session is None:
        return None
    logger.info(
        "schedule: activity %s auto-completes planned session %s",
        activity.id,
        session.id,
    )
    return complete_planned_session(db, session, source=AUTO, activity=activity)


def clear_completions_for_activity(db: Session, activity: Any) -> int:
    """Undo any auto-tick this activity earned, for when it is deleted.

    A session left ticked by an activity that no longer exists is a lie the
    plan-versus-actual read would carry forward. A MANUAL or conversational
    completion is left alone: the runner said they did it, and deleting a Strava
    upload does not unsay that.
    """
    sessions = (
        db.query(PlannedSession)
        .filter(
            PlannedSession.user_id == activity.user_id,
            PlannedSession.completed_activity_id == activity.id,
            PlannedSession.completion_source == AUTO,
        )
        .all()
    )
    for session in sessions:
        clear_completion(db, session)
    return len(sessions)
