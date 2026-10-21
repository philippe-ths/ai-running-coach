"""Account deletion (P2.3, #121, ADR 0022).

The authenticated user deletes their own account and all derived data. The Clerk
user is removed FIRST so a deleted account cannot sign back in into an empty
shell; only if that succeeds does the local FK-ordered cascade run. A Clerk
failure aborts before any local deletion, so the operation is all-or-nothing and
retryable.

Two guards stand in front of it (#969), because the profile page now reaches it
(#967) and the coaching history it destroys exists nowhere else:

- The request must carry the account's own email, typed by the runner. The
  failure it catches is the wrong account: the owner routinely holds test
  accounts that look identical on screen.
- The deployment owner's account is never deleted here. Removing it takes a
  deliberate act outside the app: changing `OWNER_EMAIL`, or the database.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.core.clerk_auth import delete_clerk_user_by_email, require_current_user
from app.db.session import get_db
from app.models import User
from app.services.account_deletion import delete_user_account
from app.services.notifications import resolve_owner_user

logger = logging.getLogger(__name__)

router = APIRouter()


class AccountRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str
    # False for the deployment owner, so the page explains rather than offers.
    deletable: bool
    # Why not, when not: the owner, or (#1125) a deployment whose owner cannot be
    # identified, where no account is deletable and none is told it is the owner.
    is_owner: bool


class AccountDeleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_email: str


def _may_be_deleted(db: Session, user: User) -> bool:
    """Whether this account is not the deployment owner's, with the owner as the
    rest of the app knows them (`resolve_owner_user`), so there is one answer to
    "who owns this deployment".

    Fails closed (#1125): when the owner cannot be identified (`OWNER_EMAIL`
    unset on a multi-user deployment, mistyped, or naming nobody), no account
    can be told apart from the owner's, so none is deletable.
    """
    owner = resolve_owner_user(db)
    return owner is not None and owner.id != user.id


@router.get("/account", response_model=AccountRead)
def read_account(
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
) -> AccountRead:
    """The signed-in account, as the profile page's Account section shows it."""
    owner = resolve_owner_user(db)
    return AccountRead(
        email=user.email,
        deletable=_may_be_deleted(db, user),
        is_owner=owner is not None and owner.id == user.id,
    )


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    body: AccountDeleteRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Delete the authenticated user's account and every row owned by it.

    Removes the Clerk user before committing anything local (so re-sign-in cannot
    create an empty shell); if Clerk removal fails, nothing local is committed
    and the caller can retry.
    """
    if resolve_owner_user(db) is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Deleting an account is not available right now, so nothing was deleted.",
        )
    if not _may_be_deleted(db, user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This is the account that owns this app, so it cannot be deleted here.",
        )
    if body.confirm_email.strip().lower() != (user.email or "").strip().lower():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That email does not match this account, so nothing was deleted.",
        )
    # The local cascade runs first, uncommitted (#1125): a row it cannot delete
    # fails here, before the sign-in is gone, rather than after it, which left
    # the sign-in deleted and every row kept. Clerk is still removed before
    # anything local is committed, so a deleted account cannot sign back in.
    email, user_id = user.email, user.id
    cascade = db.begin_nested()
    try:
        delete_user_account(db, user_id, commit=False)
    except Exception:
        cascade.rollback()
        logger.exception("account deletion: local cascade failed for %s", user_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Your account could not be deleted. Nothing has changed.",
        )
    if not delete_clerk_user_by_email(email):
        cascade.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not remove the authentication account; please retry.",
        )
    cascade.commit()
    db.commit()
