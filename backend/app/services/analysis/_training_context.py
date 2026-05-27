"""Recent-history intensity signals used by the analysis pipeline.

Splits the previous reach into `coach.context`. The coach pipeline reads the
persisted result from `DerivedMetric.training_context` rather than recomputing.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Activity

HARD_CLASSES = {"Intervals", "Tempo", "Race", "Hills"}
MODERATE_CLASSES = {"Long Run"}


def build_training_context(db: Session, activity: Activity) -> dict:
    """Compute intensity distribution and recency signals for the last 7 days."""
    activity_date = activity.start_date.date()
    start = activity_date - timedelta(days=7)

    recent = (
        db.execute(
            select(Activity)
            .where(
                Activity.user_id == activity.user_id,
                Activity.start_date >= start,
                Activity.start_date < activity.start_date,
                Activity.is_deleted == False,
            )
            .options(selectinload(Activity.metrics))
            .order_by(Activity.start_date.desc())
        )
        .scalars()
        .all()
    )

    easy = 0
    moderate = 0
    hard = 0
    days_since_last_hard = None

    for a in recent:
        ac = a.metrics.activity_class if a.metrics else "Easy Run"
        if ac in HARD_CLASSES:
            hard += 1
            if days_since_last_hard is None:
                days_since_last_hard = (activity_date - a.start_date.date()).days
        elif ac in MODERATE_CLASSES:
            moderate += 1
        else:
            easy += 1

    return {
        "intensity_distribution_7d": {
            "easy": easy,
            "moderate": moderate,
            "hard": hard,
        },
        "days_since_last_hard": days_since_last_hard,
        "hard_sessions_this_week": hard,
    }
