"""Correcting ONE planned session (#881).

The coach could write a whole plan and tick a session off, and could not change
one. Asked to fix a single session's distance it said so, correctly, twice — and
its only suggestion was to redraft the entire seven-week block, which supersedes
the plan the runner is training to and which failed anyway. The cost was wildly
out of proportion to the correction, and it pushed the runner toward the one
schedule action that does not undo.

What may change, and what may not
---------------------------------
The PRESCRIPTION: how far or how long. Nothing else. Not the day, because
placement is what the spacing rules constrain and moving a session is a question
about the week rather than about the session; not the intent or the discipline,
because a session whose intent changed is a different session and belongs in a
draft. Keeping this to one axis is what makes it safe to reach from a
conversation: the widest thing it can do is restate a number the runner is
looking at.

The load is REPRICED, never accepted
------------------------------------
`target_effort_score` was priced from the original prescription by the runner's
own history, so a corrected distance with the old load attached would put a
number nothing produced into the week's mix. It is recomputed here through the
same `estimate_effort` the draft uses — the model never states load, and neither
does the runner (the North Star's hard case: a bare load figure reads as an
intensity verdict).
"""

import logging
from datetime import date, timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.services.activity_facts import query_facts
from app.services.schedule.effort import build_load_model, estimate_effort

logger = logging.getLogger(__name__)

# The same window the drafting context reads, so a session's load is priced from
# the same history whether it was written or corrected.
_HISTORY_DAYS = 200

# The bounds the drafted-session contract already holds a coach to. A correction
# is not a looser channel than the one that wrote the session.
MAX_DISTANCE_M = 200_000
MAX_DURATION_S = 86_400

# And a floor, which "greater than zero" alone does not give. A correction to
# half a metre is a deletion wearing a correction's clothes just as much as one
# to zero, and the card renders it as "0.00 km" — the very value this refuses.
# The realistic route here is a unit slip: "make it 4.5" meaning kilometres.
MIN_DISTANCE_M = 100
MIN_DURATION_S = 60


class AdjustRefused(ValueError):
    """The correction is not one this session can take."""


def _refuse_unless_correctable(
    session: Any, distance_m: Optional[float], duration_s: Optional[int], today: date
) -> None:
    """Every reason this correction cannot happen, asked once.

    Shared by the offer and the write so a card can never go up for a correction
    that will then be refused. A runner who taps and is told no has already
    agreed to something that was never going to happen, which is worse than
    never being offered it.
    """
    if (distance_m is None) == (duration_s is None):
        # Both would be two prescriptions for one session, and neither is not a
        # correction. The card has to name a specific change for the runner to
        # agree to, and "something about this session" is not one.
        raise AdjustRefused("a correction states a distance or a duration, not both")
    if distance_m is not None and not MIN_DISTANCE_M <= distance_m <= MAX_DISTANCE_M:
        raise AdjustRefused("that distance is not a distance")
    if duration_s is not None and not MIN_DURATION_S <= duration_s <= MAX_DURATION_S:
        raise AdjustRefused("that duration is not a duration")
    if session.intent == "rest":
        # A rest day with a target on it is the one thing the drafting gate
        # refuses outright, and a correction must not be the way around it.
        raise AdjustRefused("a rest day has nothing to correct")
    if duration_s is not None and (session.structure or {}).get("reps_planned"):
        # A rep session prescribed by time would carry a rep structure that says
        # one thing and a duration that says another, and every reader picks a
        # different one: the week's headline would go on counting the reps'
        # distance while the card said minutes. Changing what a session IS is a
        # draft, not a correction.
        raise AdjustRefused(
            "that session is built out of reps, so it cannot be given a duration"
        )
    if session.dismissed_at is not None:
        # A dismissed suggestion is dropped from the week read entirely, so
        # correcting it changes nothing the runner can see — the same reason a
        # superseded plan's session is refused below.
        raise AdjustRefused("that session was already declined")
    if session.completed_at is not None:
        # Correcting what a finished session ASKED FOR rewrites the runner's
        # record of what they agreed to do, after the fact.
        raise AdjustRefused("that session is already done")
    if session.window_end < today:
        # A past session is history. Nothing reads its planned distance any more,
        # which is the same reason the #876 backfill declines to touch one.
        raise AdjustRefused("that session has already passed")
    plan = getattr(session, "plan", None)
    if plan is not None and plan.status != "active":
        # A superseded plan's sessions are not the week the runner is training
        # to, so a correction there would change nothing they can see.
        raise AdjustRefused("that session is not on your current plan")


def _prescription(distance_m: Optional[float], duration_s: Optional[int]) -> str:
    if distance_m is not None:
        return f"{distance_m / 1000:.2f} km"
    return f"{int(duration_s // 60)} min"


def _current_prescription(session: Any) -> str:
    """What this session asks for now: EVERY axis it carries, not the first one.

    A session may carry a distance and a duration at once — "16 km, cap at 2h"
    is a real prescription the drafting contract permits — and a correction
    clears the axis it does not set. Naming only one would let the card announce
    a change to the distance while silently dropping the cap, which is the exact
    thing the card exists to make impossible.

    The distance comes through `planned_distance_m` so it is the number on the
    runner's own card, which for a rep session is its warm-up, work and cool-down
    rather than a total nobody wrote.
    """
    from app.services.schedule.planned_distance import planned_distance_m

    parts = []
    distance = planned_distance_m(session)
    if distance:
        parts.append(f"{distance / 1000:.2f} km")
    if session.target_duration_s:
        parts.append(f"{int(session.target_duration_s // 60)} min")
    return " and ".join(parts) if parts else "no stated distance"


def describe_adjustment(
    session: Any,
    *,
    distance_m: Optional[float] = None,
    duration_s: Optional[int] = None,
    today: Optional[date] = None,
) -> str:
    """What the card says, naming BOTH the old prescription and the new.

    The old one is not decoration. This action does not undo with a tap, so the
    card is where the previous prescription is written down — a runner who wants
    it back can read it off their own transcript and ask. Stating only the new
    value would make the change unrecoverable in exactly the way the card is
    supposed to prevent, so the old side has to be the WHOLE prescription.
    """
    _refuse_unless_correctable(session, distance_m, duration_s, today or date.today())

    return (
        f"Change “{session.title}” from {_current_prescription(session)} to "
        f"{_prescription(distance_m, duration_s)}"
    )


def adjust_planned_session(
    db: Session,
    session: Any,
    *,
    distance_m: Optional[float] = None,
    duration_s: Optional[int] = None,
    today: Optional[date] = None,
) -> Any:
    """Correct one session's prescription, or refuse and change nothing.

    Ownership is the caller's to have settled — this takes a session it has
    already resolved against the acting runner, the `complete_planned_session`
    shape.
    """
    today = today or date.today()
    # Re-asked at write time, not merely at offer time. The token lives half an
    # hour, and a session can be ticked done or fall into the past between the
    # card going up and the runner tapping it.
    _refuse_unless_correctable(session, distance_m, duration_s, today)

    if distance_m is not None:
        session.target_distance_m = float(distance_m)
        session.target_duration_s = None
    else:
        session.target_duration_s = int(duration_s)
        session.target_distance_m = None

    session.target_effort_score = _reprice(db, session, today)
    db.commit()
    db.refresh(session)
    logger.info(
        "schedule: corrected session %s to distance=%s duration=%s",
        session.id,
        session.target_distance_m,
        session.target_duration_s,
    )
    return session


def _reprice(db: Session, session: Any, today: date) -> Optional[float]:
    """What the corrected session costs THIS runner, from their own history.

    Abstains rather than guessing, exactly as the draft does: a discipline with
    too little history has no rate to price against, and a load number invented
    for one is worse than none.
    """
    facts = query_facts(
        db,
        today - timedelta(days=_HISTORY_DAYS),
        today + timedelta(days=1),
        user_id=session.user_id,
    )
    model = build_load_model(facts, today)
    return estimate_effort(
        model,
        session.discipline,
        duration_s=session.target_duration_s,
        distance_m=session.target_distance_m,
    )
