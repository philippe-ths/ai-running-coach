"""Block correction endpoints (A1, ADR 0011): split and merge, API-level only.

The runner's corrections to a wrong automatic grouping. Corrections set
`user_corrected`, reassign members and recompute bounds/primary, and NEVER
send anything — there is no notifier in this surface, and the corrected
exchanges inherit their stage sentinels so delivery can never re-fire (AC4).
The UI surface is I3 territory.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.clerk_auth import require_current_user
from app.db.session import get_db
from app.models import Activity, Block, User
from app.schemas.block import BlockMergeRequest, BlockRead, BlockSplitRequest
from app.services.blocks import blocks_are_adjacent, merge_blocks, split_block

router = APIRouter()


def _get_block(db: Session, block_id: uuid.UUID, user: User) -> Block:
    # P2.1: a block of another user reads as not-found, so a correction can
    # never reach across tenants.
    block = (
        db.query(Block)
        .filter(Block.id == block_id, Block.user_id == user.id)
        .first()
    )
    if block is None:
        raise HTTPException(status_code=404, detail="Block not found")
    return block


@router.post("/blocks/{block_id}/split", response_model=list[BlockRead])
def split(
    block_id: uuid.UUID,
    request: BlockSplitRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Split the block at the named member: it and every later member move to a
    new block. Returns both halves."""
    block = _get_block(db, block_id, user)
    activity = db.query(Activity).filter(
        Activity.id == request.activity_id, Activity.user_id == user.id
    ).first()
    if activity is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    try:
        left, right = split_block(db, block, at_activity=activity)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [left, right]


@router.post("/blocks/{block_id}/merge", response_model=BlockRead)
def merge(
    block_id: uuid.UUID,
    request: BlockMergeRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_current_user),
):
    """Merge the named ADJACENT block into this one. Adjacency: no other block
    of the user lies between the two in time."""
    block = _get_block(db, block_id, user)
    other = _get_block(db, request.other_block_id, user)

    if not blocks_are_adjacent(db, block, other):
        raise HTTPException(status_code=422, detail="Blocks are not adjacent")

    try:
        merged = merge_blocks(db, block, other)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return merged
