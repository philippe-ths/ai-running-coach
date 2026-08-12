"""Stated intent: the vocabulary and the shared write path.

The activity intent can be updated from the activity page form and from the
thread proposed-action confirm path. One function owns the write + re-analysis
so the two cannot drift.

This module also owns the VOCABULARY those writes speak (#779). It used to exist
twice — once in the frontend picker, once in the coach's proposed-action guard —
with nothing but care keeping the copies in step, and the intent API takes free
text so no contract enforced the agreement. A label added to one copy and not the
other failed quietly in both directions; during #768 the mismatch surfaced as the
coach telling the runner it could not set an intent for this activity type, which
was simply false. The list now lives here, the coach validates against it, and the
activity-detail read serves the runner's picker from it, so the frontend holds no
copy to drift.
"""

from app.models.activity import Activity
from sqlalchemy.orm import Session

from app.services import analysis

# The stated-intent labels the runner may choose per activity type. Keyed on the
# stored `Activity.type` (Strava's coarse type), with "Default" covering every
# type without its own list.
INTENT_OPTIONS_BY_TYPE: dict[str, tuple[str, ...]] = {
    "Run": (
        "Easy Run",
        "Recovery",
        "Long Run",
        "Tempo",
        "Intervals",
        "Hills",
        "Race",
        "Treadmill",
    ),
    "Walk": ("Leisure Walk", "Power Walk", "Hike", "Commute"),
    "Ride": ("Easy Ride", "Workout", "Long Ride", "Race", "Indoor Ride"),
    "Swim": ("Endurance", "Drills", "Intervals", "Race"),
    "Workout": ("Strength", "Cardio", "Yoga", "Mobility"),
    "Default": ("Easy", "Moderate", "Hard", "Workout"),
}


def intent_options_for(activity_type: str | None) -> tuple[str, ...]:
    """The canonical vocabulary for an activity type.

    This is what the coach may propose: the labels themselves, with no runner-
    specific additions, so a legacy value stored on one activity can never become
    something the coach offers on another.
    """
    return INTENT_OPTIONS_BY_TYPE.get(
        activity_type or "", INTENT_OPTIONS_BY_TYPE["Default"]
    )


def selectable_intent_options(
    activity_type: str | None, stored_intent: str | None
) -> list[str]:
    """The vocabulary as the runner's picker should render it for one activity.

    Activities carry free-text intents written before the picker existed, and an
    activity whose type has since been re-keyed can hold a label its current
    vocabulary does not list. A `<select>` given a value with no matching option
    silently displays the wrong thing and overwrites it on the next save, so the
    stored value is appended when it is not already there: what the runner said
    stays readable and round-trippable, without widening what anyone may choose
    next.
    """
    options = list(intent_options_for(activity_type))
    stored = (stored_intent or "").strip()
    if stored and stored not in options:
        options.append(stored)
    return options


def write_activity_intent(
    db: Session, activity: Activity, *, user_intent: str | None
) -> Activity:
    """Set or clear the runner's stated intent for an activity, then re-analyze.

    The measured classification remains the source of truth; the stated intent
    is the runner's override on top, so every write re-runs analysis to fold the
    new framing into the downstream projections.
    """
    activity.user_intent = user_intent
    db.add(activity)
    db.commit()
    db.refresh(activity)
    analysis.analyze(db, str(activity.id))
    return activity
