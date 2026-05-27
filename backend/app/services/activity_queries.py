from sqlalchemy.orm import Session, joinedload

from app.models import Activity


def get_activities(db: Session, skip: int = 0, limit: int = 20) -> list[Activity]:
    return (
        db.query(Activity)
        .order_by(Activity.start_date.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )


def get_activity(db: Session, activity_id: str) -> Activity | None:
    return (
        db.query(Activity)
        .options(
            joinedload(Activity.metrics),
            joinedload(Activity.check_in),
            joinedload(Activity.streams),
        )
        .filter(Activity.id == activity_id)
        .first()
    )
