"""Account deletion (P2.3, #121, ADR 0022).

The authenticated user deletes their own account and all derived data. The Clerk
user is removed FIRST so a deleted account cannot sign back in into an empty
shell; only if that succeeds does the local FK-ordered cascade run. A Clerk
failure aborts before any local deletion, so the operation is all-or-nothing and
retryable.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.clerk_auth import delete_clerk_user_by_email, require_current_user
from app.db.session import get_db
from app.models import User
from app.services.account_deletion import delete_user_account

router = APIRouter()


@router.delete("/account", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Delete the authenticated user's account and every row owned by it.

    Removes the Clerk user first (so re-sign-in cannot create an empty shell); if
    Clerk removal fails, nothing local is touched and the caller can retry.
    """
    if not delete_clerk_user_by_email(user.email):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not remove the authentication account; please retry.",
        )
    delete_user_account(db, user.id)
