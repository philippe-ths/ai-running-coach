import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload, undefer

from app.models import Activity


def get_activities(
    db: Session,
    skip: int = 0,
    limit: int = 20,
    *,
    user_id: uuid.UUID,
    types: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[Activity]:
    """List activities newest-first, paginated, with optional filters (#404).

    ``types`` narrows to the given activity types (OR within the list). ``start_date``
    and ``end_date`` bound the range on the activity's LOCAL calendar day and are both
    inclusive of the whole day. Filters compose; pagination is applied after filtering so
    it walks the filtered history rather than the full one.

    The bound is taken against ``coalesce(start_date_local, start_date)`` (#411): the
    list renders each row by its own local wall-clock date (``start_date_local``, falling
    back to UTC ``start_date`` for pre-#399 rows, mirroring ``Activity.local_start`` and
    the frontend's ``activityStartDate``), so filtering on the UTC instant could
    include/exclude a near-midnight row in disagreement with the date the runner sees.
    Bounds are naive datetimes so they compare against the naive local wall-clock column.
    ``start_date_local`` is not indexed (single-user, single-timezone today); the cost is
    a sequential scan over the runner's history, acceptable at this scale.
    """
    # The displayed/filtered day: the local wall-clock start, falling back to the UTC
    # instant when no local time was stored (the same precedence local_start uses).
    local_start = func.coalesce(Activity.start_date_local, Activity.start_date)
    query = (
        db.query(Activity)
        # undefer raw_summary (#359): the list composes a classification headline
        # per item (compose_headline -> sport_type/trainer reads raw_summary), so
        # without this the deferred column would lazy-load once per row -> N+1.
        .options(joinedload(Activity.metrics), undefer(Activity.raw_summary))
        # Exclude soft-deleted activities (#410), consistent with every other
        # read path (trends, readiness, training load, coach context/retrieval).
        .filter(Activity.is_deleted == False)  # noqa: E712
        # P2.1: scope to the authenticated user. A missing user_id filter here is
        # a cross-tenant list leak, so user_id is a required argument.
        .filter(Activity.user_id == user_id)
    )
    if types:
        query = query.filter(Activity.type.in_(types))
    if start_date is not None:
        # Bound on the local wall-clock day. Naive lower bound: midnight of the chosen
        # local day, compared against the naive local-start column.
        lower = datetime.combine(start_date, time.min)
        query = query.filter(local_start >= lower)
    if end_date is not None:
        # End inclusive: everything strictly before the start of the following local day.
        upper = datetime.combine(end_date + timedelta(days=1), time.min)
        query = query.filter(local_start < upper)
    return (
        query
        .order_by(Activity.start_date.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_activity(
    db: Session,
    activity_id: str | uuid.UUID,
    *,
    user_id: uuid.UUID,
) -> Activity | None:
    # Bind a real UUID: Postgres adapts a string, but SQLite (tests) does not.
    if isinstance(activity_id, str):
        activity_id = uuid.UUID(activity_id)
    query = (
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
        # A soft-deleted activity reads as not-present (#410): the detail view
        # 404s on it, consistent with the list and every other read path.
        .filter(Activity.is_deleted == False)  # noqa: E712
        # P2.1: scope to the authenticated user. user_id is REQUIRED (#706): an
        # unscoped read is a cross-tenant leak, so another user's activity reads
        # as absent (the endpoint then 404s), matching the sibling query helpers.
        .filter(Activity.user_id == user_id)
    )
    return query.first()


def get_owned_activity(
    db: Session, activity_id: str | uuid.UUID, user_id: uuid.UUID
) -> Activity | None:
    """A lean ownership check: the activity by id IF it belongs to ``user_id``.

    Returns None when the activity does not exist OR belongs to another user, so
    the calling endpoint raises one 404 for both and leaks nothing about other
    tenants' activities. No eager loads — callers that need the full graph use
    get_activity. Soft-deleted state is intentionally not filtered here: this is
    an ownership gate for the coach/checkin/intent write paths, which must reach
    the same set of activities they did pre-P2.1 for the owning user.
    """
    if isinstance(activity_id, str):
        activity_id = uuid.UUID(activity_id)
    return (
        db.query(Activity)
        .filter(Activity.id == activity_id, Activity.user_id == user_id)
        .first()
    )
