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

A rep session is prescribed by its structure, so how long or far a run went
cannot show it was one (#971). It only auto-matches a run that analysis found
reps in: the interval gate's answer, stored on the run's `DerivedMetric`.
"""

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.derived_metric import DerivedMetric
from app.models.planned_session import PlannedSession
from app.models.user_profile import UserProfile
from app.models.week_recommendation import WeekRecommendation
from app.services.activity_facts import local_day
from app.services.schedule import store
from app.services.weeks import resolve_week_start, week_start
from app.services.schedule.disciplines import discipline_for_activity_type, same_sport

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


def shows_reps(db: Session, activity: Any) -> bool:
    """Whether analysis found reps in this run, as it stored them.

    The interval gate (`IntervalSession`) is the one owner of that answer and
    persists its structure as `interval_structure`. A run not yet analysed has
    shown nothing, so it cannot claim a rep session.
    """
    structure = (
        db.query(DerivedMetric.interval_structure)
        .filter(DerivedMetric.activity_id == activity.id)
        .scalar()
    )
    return structure is not None


def _is_rep_session(session: PlannedSession) -> bool:
    """A session prescribed as reps. Since #878 a structure can carry only a
    warm-up and cool-down (a tempo), and that is not one."""
    return bool((session.structure or {}).get("reps_planned"))


def _options(session: PlannedSession) -> List[dict]:
    """The ways this slot can be filled, as dicts: the session itself first, then
    its alternatives (#1082). The index of each is its `done_option`."""
    main = {
        "discipline": session.discipline,
        "activity_type": getattr(session, "activity_type", None),
        "target_distance_m": session.target_distance_m,
        "target_duration_s": session.target_duration_s,
        "reps": _is_rep_session(session),
    }
    alternatives = [
        alt for alt in (getattr(session, "alternatives", None) or []) if isinstance(alt, dict)
    ]
    return [main] + alternatives


def _sport_type(activity: Any) -> Optional[str]:
    """The activity's exact sport in Strava's `SportType` names, which is what a
    session names (#1129). `Activity.type` is Strava's legacy type and folds
    sports together: a tennis match is stored as `Workout`, a gravel ride as
    `Ride`. Read the way the classifier reads it."""
    raw = getattr(activity, "raw_summary", None) or {}
    return raw.get("sport_type") or getattr(activity, "type", None)


def _option_fits(option: dict, activity: Any) -> bool:
    """Whether the activity is this option's discipline and, when the option
    names one, its sport (#1090): a yoga class is `other` like a swim, but it
    is not the swim."""
    legacy = getattr(activity, "type", None)
    named = option.get("activity_type")
    # A session naming the exact sport is filled by it; one naming a parent
    # sport ("Ride", "Workout") is still filled by the sports Strava files under
    # it, as before #1129.
    return option.get("discipline") == discipline_for_activity_type(legacy) and (
        same_sport(named, _sport_type(activity)) or same_sport(named, legacy)
    )


def _option_closeness(option: dict, activity: Any) -> Optional[float]:
    """How well this activity fills one option, as a ratio of what was asked.

    1.0 is exactly the prescription. None when the option has no target: there
    is nothing to measure, which is not the same as a perfect fit (#971).
    """
    if option.get("target_distance_m"):
        actual = getattr(activity, "distance_m", 0) or 0
        return actual / option["target_distance_m"]
    if option.get("target_duration_s"):
        actual = getattr(activity, "moving_time_s", 0) or 0
        return actual / option["target_duration_s"]
    return None


def _miss(closeness: Optional[float]) -> tuple:
    """A sort key: measured fits by how far they miss, then unmeasured ones."""
    return (1, 0.0) if closeness is None else (0, abs(closeness - 1.0))


def _fit_rank(option: dict, activity: Any) -> tuple:
    """A sort key over options the activity qualifies for. Reps seen are the
    strongest evidence a run can carry, so a rep option it qualifies for comes
    first; then how exactly the run fills the rest."""
    return (0 if option.get("reps") else 1, *_miss(_option_closeness(option, activity)))


# Intents a runner states for a run that WAS the rep work (#1121). A tempo run
# as one continuous block does the work of its reps without the rests the
# interval gate looks for.
_REP_WORK_INTENTS = {"Tempo", "Intervals"}


def _stated_rep_work(activity: Any) -> bool:
    """Whether the runner said this RUN was the session's work. A swim's
    "Intervals" is not a run's, and a swim set is not what #1121 settled."""
    return (
        getattr(activity, "user_intent", None) in _REP_WORK_INTENTS
        and same_sport("Run", _sport_type(activity))
    )


def _option_qualifies(option: dict, activity: Any, reps_shown: bool) -> bool:
    """Whether this option could be what the activity was: its discipline, reps
    seen (or the work stated) when it asks for reps, and enough of it done when
    it can be measured."""
    if not _option_fits(option, activity):
        return False
    if option.get("reps") and not (reps_shown or _stated_rep_work(activity)):
        return False
    closeness = _option_closeness(option, activity)
    return closeness is None or closeness >= MIN_COMPLETION_FRACTION


def matching_option(
    session: PlannedSession, activity: Any, reps_shown: bool = False
) -> Optional[int]:
    """Which option of this slot the activity is: the one it qualifies for that
    it fills most exactly, or None when it qualifies for none. `reps_shown`
    defaults to the cautious answer: no reps seen."""
    fits = [
        (_fit_rank(option, activity), index)
        for index, option in enumerate(_options(session))
        if _option_qualifies(option, activity, reps_shown)
    ]
    return min(fits)[1] if fits else None


def _fit_for(session: PlannedSession, activity: Any, reps_shown: bool) -> tuple:
    """How well this activity fits the option of this slot it matches."""
    index = matching_option(session, activity, reps_shown)
    return _fit_rank(_options(session)[index], activity)


def open_sessions_for(db: Session, activity: Any) -> List[PlannedSession]:
    """Every unfinished planned session this activity could plausibly be.

    Scoped to the ACTIVE plan and to the activity's own owner. A superseded
    plan's sessions are history and must not absorb today's run.
    """
    plan = store.get_active_plan(db, activity.user_id)
    if plan is None:
        return []
    day = _activity_local_day(activity)
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
        if session.intent != "rest"
        and any(_option_fits(option, activity) for option in _options(session))
    ]


def _week_row(db: Session, activity: Any) -> Optional[WeekRecommendation]:
    day = _activity_local_day(activity)
    profile = db.query(UserProfile).filter(UserProfile.user_id == activity.user_id).first()
    start = week_start(day, resolve_week_start(profile))
    return (
        db.query(WeekRecommendation)
        .filter(
            WeekRecommendation.user_id == activity.user_id,
            WeekRecommendation.week_start == start,
        )
        .first()
    )


def _shown_days(db: Session, activity: Any, plan_id: Any) -> Dict[str, date]:
    """The day each session was last shown on, for this activity's week.

    Read straight from the stored recommendation rather than through
    `recommend_store.load`, which resets a row from another plan in place: a
    read here must not leave a write behind for the tick's commit to flush.
    """
    row = _week_row(db, activity)
    if row is None or row.plan_id != plan_id:
        return {}
    shown = {}
    for sid, value in (row.assignment or {}).items():
        try:
            shown[sid] = date.fromisoformat(value)
        except (TypeError, ValueError):
            continue
    return shown


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


def find_matching_session(
    db: Session, activity: Any, reps_shown: Optional[bool] = None
) -> Optional[PlannedSession]:
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

    An AUTO credit wins only while it still qualifies (#971): one the matcher
    should never have made is not this run's plan. A tap or a confirmed
    conversation always wins; the runner said they did it.

    `reps_shown` is the interval gate's answer. Analysis passes it live, since
    it runs before the answer is stored; every other caller reads it stored.
    """
    if reps_shown is None:
        reps_shown = shows_reps(db, activity)

    credited = credited_session(db, activity)
    if credited is not None and (
        credited.completion_source != AUTO
        or matching_option(credited, activity, reps_shown) is not None
    ):
        return credited

    candidates = [
        session
        for session in open_sessions_for(db, activity)
        if matching_option(session, activity, reps_shown) is not None
    ]
    if not candidates:
        return None
    # #1162: never an EASY session shown for a later day. Easy sessions are
    # interchangeable, so done ahead of its day an easy activity is something
    # the runner added, and the session stays where they were shown it. A
    # specific session (quality, long, strength) done early is that session.
    day = _activity_local_day(activity)
    shown_days = _shown_days(db, activity, candidates[0].plan_id)
    # With nothing stored the runner has not been shown the week, so every day
    # in a session's window is still its day.
    candidates = [
        s
        for s in candidates
        if (s.intent != "easy" or shown_days.get(str(s.id), day) <= day)
        # A suggestion is never in the recommendation; it is offered only on
        # the day it is pinned to, so that is the only day it can be done.
        and (s.commitment == "committed" or s.window_start == s.window_end == day)
    ]
    if not candidates:
        return None
    shown = {sid for sid, d in shown_days.items() if d == day}

    def _rank(session: PlannedSession):
        # A commitment outranks a suggestion; then a rep session the run showed
        # reps for; then whichever the activity fills most exactly, a session
        # with nothing to measure after any that can be; then the tightest
        # window, since a session the coach placed narrowly is the more specific
        # claim on this day. Last, between sessions that tie on all of that, the
        # one the runner was shown for this day (#1155): ticking an identical
        # walk recommended for Thursday left today's still on today, so the day
        # kept a second walk the runner had in fact already done.
        return (
            0 if session.commitment == "committed" else 1,
            _fit_for(session, activity, reps_shown),
            (session.window_end - session.window_start).days,
            0 if str(session.id) in shown else 1,
            str(session.id),
        )

    return sorted(candidates, key=_rank)[0]


def complete_planned_session(
    db: Session,
    session: PlannedSession,
    *,
    source: str,
    activity: Any = None,
    when: Optional[datetime] = None,
    option: Optional[int] = None,
) -> PlannedSession:
    """The ONE write. Every route to "done" ends here."""
    session.completed_at = when or datetime.now(timezone.utc)
    session.completion_source = source
    session.completed_activity_id = getattr(activity, "id", None) if activity else None
    # #1082: which option filled the slot. Given by the runner's tap, or read off
    # the activity; a tap that names none is the session itself.
    if option is None and activity is not None:
        option = matching_option(session, activity, shows_reps(db, activity))
    session.done_option = option if option is not None else 0
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
    session.done_option = None
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
    one, so a re-analysis or a replayed webhook changes nothing. The exception
    is an auto credit that no longer qualifies, which is withdrawn first.
    """
    already = credited_session(db, activity)
    if already is not None:
        if not _unfounded_auto_credit(db, activity, already):
            return already
        clear_completion(db, already)

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


def _unfounded_auto_credit(
    db: Session, activity: Any, credited: PlannedSession
) -> bool:
    """An auto credit the matcher would not make today. Never a tap or a
    conversation: those are the runner's word, not the matcher's guess."""
    return (
        credited.completion_source == AUTO
        and matching_option(credited, activity, shows_reps(db, activity)) is None
    )


def correct_auto_credit(db: Session, activity: Any) -> Optional[PlannedSession]:
    """Withdraw this activity's auto credit if it no longer qualifies, and give
    the run to the session it does match, if any (#971).

    For re-analysis, which corrects old ticks once the rules change. It acts
    only on a wrong auto credit, so it never ticks anything for a run that was
    not credited, and never touches a session the runner ticked or unticked.
    """
    credited = credited_session(db, activity)
    if credited is None or not _unfounded_auto_credit(db, activity, credited):
        return None
    logger.info(
        "schedule: activity %s no longer completes planned session %s",
        activity.id,
        credited.id,
    )
    return complete_from_activity(db, activity)
