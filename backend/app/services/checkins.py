"""Shared CheckIn write path.

Both entry points that record a post-run check-in — the in-app POST
`/activities/{id}/checkin` and the Telegram inbound callback (I1b, #220) — go
through `write_checkin`, so the two cannot drift: an in-app RPE tap and a
Telegram RPE tap produce the same CheckIn, the same re-analysis, and the same
A4 fuller-turn trigger.
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.models import CheckIn
from app.schemas import CheckInCreate
from app.services import analysis


def write_checkin(db: Session, activity_id: UUID, checkin_data: CheckInCreate) -> CheckIn:
    """Upsert the activity's CheckIn, re-analyze to fold in the feedback, and
    fire the A4 fuller turn early when the two-stage exchange is still open.

    A check-in is one CheckIn per activity, so a second write updates the first
    (only the fields the caller set, leaving the rest intact). Returns the row."""
    existing = db.query(CheckIn).filter(CheckIn.activity_id == activity_id).first()
    if existing:
        for k, v in checkin_data.model_dump(exclude_unset=True).items():
            setattr(existing, k, v)
        db_obj = existing
    else:
        db_obj = CheckIn(activity_id=activity_id, **checkin_data.model_dump())
        db.add(db_obj)

    db.commit()
    db.refresh(db_obj)

    # Re-process to incorporate the subjective feedback (perceived-effort, pain).
    analysis.analyze(db, str(activity_id))

    # A4: a check-in is a reply — if the two-stage exchange is still open, fire
    # the fuller turn early (best-effort; never breaks the check-in write).
    from app.jobs.process_new_activity import maybe_enqueue_fuller_turn

    maybe_enqueue_fuller_turn(db, activity_id)

    return db_obj
