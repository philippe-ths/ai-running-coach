"""Block correction endpoints (A1, ADR 0011): split and merge, API-level only.

The runner's corrections to a wrong automatic grouping. Corrections set
`user_corrected`, reassign members and recompute bounds/primary, and NEVER
send anything — there is no notifier in this surface, and the corrected
exchanges inherit their stage sentinels so delivery can never re-fire (AC4).
The UI surface is I3 territory.

Both routes take TWO owned resources: one from the path and one named in the
request body. Both resolve through the shared dependencies in `deps.py` (#802),
so the body-carried id is gated by exactly the same rule as the path-carried
one — the handler never sees an unowned row.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import (
    CurrentUser,
    DbSession,
    OwnedBlock,
    require_owned_activity,
    require_owned_block,
)
from app.models import Activity, Block
from app.schemas.block import BlockMergeRequest, BlockRead, BlockSplitRequest
from app.services.blocks import blocks_are_adjacent, merge_blocks, split_block

router = APIRouter()


def get_split_target_activity(
    request: BlockSplitRequest, db: DbSession, user: CurrentUser
) -> Activity:
    """The activity named in a split request, gated on ownership."""
    return require_owned_activity(db, request.activity_id, user)


SplitTargetActivity = Annotated[Activity, Depends(get_split_target_activity)]


def get_merge_other_block(
    request: BlockMergeRequest, db: DbSession, user: CurrentUser
) -> Block:
    """The other block named in a merge request, gated on ownership."""
    return require_owned_block(db, request.other_block_id, user)


MergeOtherBlock = Annotated[Block, Depends(get_merge_other_block)]


@router.post("/blocks/{block_id}/split", response_model=list[BlockRead])
def split(
    block: OwnedBlock,
    activity: SplitTargetActivity,
    db: DbSession,
):
    """Split the block at the named member: it and every later member move to a
    new block. Returns both halves."""
    try:
        left, right = split_block(db, block, at_activity=activity)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return [left, right]


@router.post("/blocks/{block_id}/merge", response_model=BlockRead)
def merge(
    block: OwnedBlock,
    other: MergeOtherBlock,
    db: DbSession,
):
    """Merge the named ADJACENT block into this one. Adjacency: no other block
    of the user lies between the two in time."""
    if not blocks_are_adjacent(db, block, other):
        raise HTTPException(status_code=422, detail="Blocks are not adjacent")

    try:
        merged = merge_blocks(db, block, other)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return merged
