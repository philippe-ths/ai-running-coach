"""Full account deletion with FK-ordered cascade (P2.3, #121, ADR 0022).

A user deletes their own account and ALL derived data, leaving no orphaned row.
There is no DB-level ON DELETE cascade on the foreign keys, so the cascade is
application-level and MUST run children-before-parents to satisfy the
constraints. The one wrinkle is the activities<->blocks cycle
(``activities.block_id`` <-> ``blocks.primary_activity_id``): we null
``activities.block_id`` before deleting blocks so neither side blocks the other.

The Clerk user is deleted separately by the endpoint (ADR 0022: a deleted
account must not be able to sign back in into an empty shell).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Activity,
    ActivityStream,
    Block,
    CheckIn,
    CoachChatMessage,
    CoachReport,
    CoachingRelationship,
    DerivedMetric,
    Exchange,
    RunnerBaseline,
    RunnerMemory,
    StravaAccount,
    StravaImport,
    User,
    UserMaterial,
    UserProfile,
)

logger = logging.getLogger(__name__)

# Models hung off an Activity (FK -> activities.id). Deleted by the user's
# activity id set before the activities themselves.
_ACTIVITY_CHILDREN = (ActivityStream, CheckIn, CoachChatMessage, CoachReport, DerivedMetric)

# Singletons / collections hung directly off the user (FK -> users.id), with no
# rows referencing THEM. Deleted after activities/blocks, before the user row.
_USER_OWNED = (
    CoachingRelationship,
    RunnerBaseline,
    RunnerMemory,
    StravaAccount,
    StravaImport,
    UserMaterial,
    UserProfile,
)


def delete_user_account(db: Session, user_id) -> dict:
    """Delete every row owned by ``user_id`` in FK-safe order, then the user.

    Returns a per-table count of deleted rows (audit/verification). Commits once
    at the end so the whole cascade is atomic — a failure leaves the account
    intact rather than half-deleted.
    """
    counts: dict[str, int] = {}
    activity_ids = select(Activity.id).where(Activity.user_id == user_id)

    # 1. Exchanges (FK -> blocks, users) — nothing references them.
    counts["exchanges"] = (
        db.query(Exchange).filter(Exchange.user_id == user_id)
        .delete(synchronize_session=False)
    )

    # 2. Activity-children (FK -> activities) for this user's activities.
    for model in _ACTIVITY_CHILDREN:
        counts[model.__tablename__] = (
            db.query(model).filter(model.activity_id.in_(activity_ids))
            .delete(synchronize_session=False)
        )

    # 3. Break the activities<->blocks cycle, then delete blocks, then activities.
    db.query(Activity).filter(Activity.user_id == user_id).update(
        {Activity.block_id: None}, synchronize_session=False
    )
    counts["blocks"] = (
        db.query(Block).filter(Block.user_id == user_id)
        .delete(synchronize_session=False)
    )
    counts["activities"] = (
        db.query(Activity).filter(Activity.user_id == user_id)
        .delete(synchronize_session=False)
    )

    # 4. Direct user-owned rows (FK -> users).
    for model in _USER_OWNED:
        counts[model.__tablename__] = (
            db.query(model).filter(model.user_id == user_id)
            .delete(synchronize_session=False)
        )

    # 5. The user row itself.
    counts["users"] = (
        db.query(User).filter(User.id == user_id).delete(synchronize_session=False)
    )

    db.commit()
    logger.info("account_deleted", extra={"user_id": str(user_id), "counts": counts})
    return counts
