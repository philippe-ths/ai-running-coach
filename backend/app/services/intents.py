"""Shared stated-intent write path.

The activity intent can be updated from the activity page form and from the
thread proposed-action confirm path. One function owns the write + re-analysis
so the two cannot drift.
"""

from app.models.activity import Activity
from sqlalchemy.orm import Session

from app.services import analysis


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
