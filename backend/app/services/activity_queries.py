import uuid
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy.orm import Session, joinedload, undefer

from app.models import Activity


def get_activities(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    *,
    types: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Activity]:
    """List activities newest-first, paginated, with optional filters (#404).

    ``types`` narrows to the given activity types (OR within the list). ``start_date``
    and ``end_date`` bound the range on ``start_date`` (UTC) and are both inclusive of
    the whole day. Filters compose; pagination is applied after filtering so it walks
    the filtered history rather than the full one.
    """
    query = (
        db.query(Activity)
        # undefer raw_summary (#359): the list composes a classification headline
        # per item (compose_headline -> sport_type/trainer reads raw_summary), so
        # without this the deferred column would lazy-load once per row -> N+1.
        .options(joinedload(Activity.metrics), undefer(Activity.raw_summary))
    )
    if types:
        query = query.filter(Activity.type.in_(types))
    if start_date is not None:
        # Bound on start_date (UTC, the indexed ordering column). The chosen date is
        # treated as a UTC day, consistent with how the list orders/windows.
        lower = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
        query = query.filter(Activity.start_date >= lower)
    if end_date is not None:
        # End inclusive: everything strictly before the start of the following day.
        upper = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=timezone.utc)
        query = query.filter(Activity.start_date < upper)
    return (
        query
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
