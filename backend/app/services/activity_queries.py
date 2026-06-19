import uuid

from sqlalchemy.orm import Session, joinedload, undefer

from app.models import Activity


def get_activities(db: Session, skip: int = 0, limit: int = 20) -> list[Activity]:
    return (
        db.query(Activity)
        # undefer raw_summary (#359): the list composes a classification headline
        # per item (compose_headline -> sport_type/trainer reads raw_summary), so
        # without this the deferred column would lazy-load once per row -> N+1.
        .options(joinedload(Activity.metrics), undefer(Activity.raw_summary))
        .order_by(Activity.start_date.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_activity(db: Session, activity_id: str | uuid.UUID) -> Activity | None:
    # Bind a real UUID: Postgres adapts a string, but SQLite (tests) does not.
    if isinstance(activity_id, str):
        activity_id = uuid.UUID(activity_id)
    return (
        db.query(Activity)
        .options(
            joinedload(Activity.metrics),
            joinedload(Activity.check_in),
            joinedload(Activity.streams),
            # The detail view reads raw_summary (laps projection + headline);
            # undefer so it loads with the row, not as a follow-up query (#359).
            undefer(Activity.raw_summary),
        )
        .filter(Activity.id == activity_id)
        .first()
    )
