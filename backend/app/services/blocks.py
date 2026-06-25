"""
Block detection (A1, ADR 0011): deterministic time-gap grouping of a user's
temporally-contiguous activities into one Block, the event unit the coach
speaks about.

The rule is pure and auditable: an activity joins an existing block when the
time gap between them is under the threshold (BLOCK_GAP_SECONDS), else it
starts a new block-of-one. The block's primary activity — what the coach
report is keyed on — is the run, else the longest member. A block whose
exchange is CLOSED (fuller sent) never absorbs a late arrival; the late
activity starts a new block (ADR 0011: open-absorbs, closed-starts-new).

Split/merge are the runner's corrections, exposed as pure functions over a
member list here and wired to API endpoints separately; corrections set
`user_corrected` and never re-fire a notification.
"""

import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Activity, Block, CoachReport, Exchange

logger = logging.getLogger(__name__)


def activity_end(activity: Activity) -> datetime:
    """When the activity finished: start plus elapsed wall-clock time."""
    return activity.start_date + timedelta(seconds=activity.elapsed_time_s or 0)


def pick_primary(members: Sequence[Activity]) -> Activity:
    """The block's primary activity: the run, else the longest member.

    Ties (several runs, or no run) resolve to the longest by elapsed time,
    then earliest start for determinism.
    """
    runs = [a for a in members if (a.type or "").lower() == "run"]
    pool = runs or list(members)
    return sorted(pool, key=lambda a: (-(a.elapsed_time_s or 0), a.start_date))[0]


def assign_activity_to_block(
    db: Session,
    activity: Activity,
    *,
    gap_seconds: Optional[int] = None,
) -> Block:
    """Assign an ingested activity to its Block, creating one if none joins.

    Runs at ingestion for every new activity. Joins the user's existing block
    when the gap between block and activity is under `gap_seconds` and the
    block's exchange is still open; otherwise starts a new block-of-one (with
    its open Exchange row — the one-exchange-per-block invariant is kept
    atomically at block creation).
    """
    gap = settings.BLOCK_GAP_SECONDS if gap_seconds is None else gap_seconds

    block = _find_joinable_block(db, activity, gap)
    if block is None:
        block = Block(
            user_id=activity.user_id,
            start_date=activity.start_date,
            end_date=activity_end(activity),
            primary_activity_id=activity.id,
        )
        db.add(block)
        db.flush()
        db.add(Exchange(user_id=activity.user_id, block_id=block.id))
        activity.block_id = block.id
        db.commit()
        return block

    activity.block_id = block.id
    db.flush()  # sessions run with autoflush off; members query must see the join
    _recompute_block(db, block)
    db.commit()
    return block


def _find_joinable_block(db: Session, activity: Activity, gap: int) -> Optional[Block]:
    """The block this activity is temporally contiguous with, if it may absorb.

    Contiguity is symmetric so out-of-order sync converges to the same
    grouping: the activity may extend a block's end (starts within `gap` of
    the block's end), precede its start (ends within `gap` of the block's
    start), or overlap it. A block whose exchange is closed never absorbs.
    """
    window_start = activity.start_date - timedelta(seconds=gap)
    end = activity_end(activity)

    candidates = (
        db.query(Block)
        .filter(
            Block.user_id == activity.user_id,
            Block.start_date < end + timedelta(seconds=gap),
            Block.end_date > window_start,
        )
        .order_by(Block.end_date.desc())
        .all()
    )
    for block in candidates:
        exchange = db.query(Exchange).filter(Exchange.block_id == block.id).first()
        if exchange is not None and exchange.fuller_sent_at is not None:
            continue  # closed-starts-new (ADR 0011)
        return block
    return None


def split_block(db: Session, block: Block, *, at_activity: Activity) -> tuple[Block, Block]:
    """Split a block at an activity: it and every later member move to a new block.

    A runner correction (ADR 0011): both halves are marked `user_corrected`,
    bounds and primaries are recomputed, and nothing re-fires — the new block's
    exchange INHERITS the original's stage sentinels, so a correction can never
    re-open delivery.
    """
    members = sorted(
        db.query(Activity).filter(Activity.block_id == block.id).all(),
        key=lambda a: a.start_date,
    )
    if at_activity.block_id != block.id:
        raise ValueError("activity is not a member of this block")
    right_members = [a for a in members if a.start_date >= at_activity.start_date]
    left_members = [a for a in members if a.start_date < at_activity.start_date]
    if not left_members or not right_members:
        raise ValueError("split would leave an empty block")

    original_exchange = db.query(Exchange).filter(Exchange.block_id == block.id).one()

    new_block = Block(
        user_id=block.user_id,
        start_date=min(a.start_date for a in right_members),
        end_date=max(activity_end(a) for a in right_members),
        primary_activity_id=pick_primary(right_members).id,
        user_corrected=True,
    )
    db.add(new_block)
    db.flush()
    db.add(
        Exchange(
            user_id=block.user_id,
            block_id=new_block.id,
            opened_at=original_exchange.opened_at,
            opener_sent_at=original_exchange.opener_sent_at,
            fuller_sent_at=original_exchange.fuller_sent_at,
        )
    )
    for member in right_members:
        member.block_id = new_block.id
    db.flush()

    block.user_corrected = True
    _recompute_block(db, block, force_primary=True)
    db.commit()
    return block, new_block


def merge_blocks(db: Session, first: Block, second: Block) -> Block:
    """Merge two of a user's blocks into the first.

    A runner correction (ADR 0011): the second block's members move into the
    first, bounds and primary are recomputed, `user_corrected` is set, and the
    surviving exchange carries the most-advanced stage state from either side
    so nothing can re-fire. The absorbed block and its exchange are deleted.
    """
    if first.user_id != second.user_id:
        raise ValueError("blocks belong to different users")
    if first.id == second.id:
        raise ValueError("cannot merge a block with itself")

    surviving = db.query(Exchange).filter(Exchange.block_id == first.id).one()
    absorbed = db.query(Exchange).filter(Exchange.block_id == second.id).one()
    surviving.opened_at = _latest(surviving.opened_at, absorbed.opened_at)
    surviving.opener_sent_at = _latest(surviving.opener_sent_at, absorbed.opener_sent_at)
    surviving.fuller_sent_at = _latest(surviving.fuller_sent_at, absorbed.fuller_sent_at)

    for member in db.query(Activity).filter(Activity.block_id == second.id).all():
        member.block_id = first.id
    db.delete(absorbed)
    db.flush()
    db.delete(second)

    first.user_corrected = True
    db.flush()
    _recompute_block(db, first, force_primary=True)
    db.commit()
    return first


def remove_activity_from_block(db: Session, activity: Activity) -> None:
    """Remove a soft-deleted activity from its Block and recompute the survivor.

    Called on the delete webhook path (ADR 0011, #238): after an activity is
    soft-deleted (`is_deleted=True`) it must also leave its block so it no
    longer shapes bounds, holds the primary anchor, counts as the last member
    in the opener debounce, or attracts late arrivals.

    Two outcomes:
    - Survivors remain: detach the activity, recompute bounds and primary on
      the surviving members. `force_primary=True` so the deletion of a primary
      member is handled correctly even when the exchange is already open (the
      opened exchange was anchored on the now-deleted primary; we re-anchor on
      the best survivor so future reads are not keyed on a deleted row).
    - Block is now empty: detach the activity and delete the block together
      with its Exchange. The Exchange is removed rather than reset so the
      at-most-once delivery guarantee is preserved by absence, not by a
      sentinel value. No notification can re-fire against a row that no longer
      exists.

    Exchange sentinels are NEVER touched when survivors remain. The split/merge
    corrections rule (corrections inherit sentinels, never re-fire) is the
    model: removal inherits that posture.
    """
    if activity.block_id is None:
        return  # already unassigned, nothing to do

    block_id = activity.block_id
    block = db.query(Block).filter(Block.id == block_id).first()
    if block is None:
        activity.block_id = None
        db.add(activity)
        db.commit()
        return

    # Detach the activity first so the member query below sees it as gone.
    activity.block_id = None
    db.add(activity)
    db.flush()

    survivors = db.query(Activity).filter(Activity.block_id == block_id).all()
    if not survivors:
        # Block is empty: clean it up so it cannot attract late arrivals or
        # trigger an opener. Delete Exchange first (FK constraint order).
        exchange = db.query(Exchange).filter(Exchange.block_id == block_id).first()
        if exchange is not None:
            db.delete(exchange)
            db.flush()
        db.delete(block)
    else:
        # Recompute bounds and primary from surviving members.
        # force_primary=True because we may have removed the primary activity;
        # a correction-level recomputation is the right posture here.
        _recompute_block(db, block, force_primary=True)

    db.commit()


def _latest(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    """The most-advanced sentinel: non-null wins; both set, the later."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


def _primary_has_report(db: Session, block: Block) -> bool:
    """True once a CoachReport has been written for the block's current primary.

    This is the real invariant behind the freeze: the report row is keyed on the
    primary, so once it exists a late arrival must not move the anchor out from
    under it. Under the two-stage LLM exchange the opener writes that row (and
    sets `opened_at` at the same time, which is why the old `opened_at` proxy
    held). Under the receipt cadence the first activity's deterministic receipt
    sets `opened_at` but writes NO report, so the proxy froze the primary to a
    warmup walk before the run could win it (#487). Gating on the report itself
    is cadence-agnostic: the primary stays recomputable until a report is keyed
    to it, so a joining run still wins the anchor under either cadence.
    """
    if block.primary_activity_id is None:
        return False
    return (
        db.query(CoachReport.id)
        .filter(CoachReport.activity_id == block.primary_activity_id)
        .first()
        is not None
    )


def _recompute_block(db: Session, block: Block, *, force_primary: bool = False) -> None:
    """Recompute start/end/primary from the block's current members.

    The primary is FROZEN once a coach report has been keyed to it (see
    `_primary_has_report`): the report speaks about that activity, so a late
    arrival may grow the block's bounds but never moves the anchor out from
    under an existing report. A runner split/merge correction (`force_primary`)
    recomputes it regardless — an explicit correction outranks the frozen anchor.
    """
    members = db.query(Activity).filter(Activity.block_id == block.id).all()
    block.start_date = min(a.start_date for a in members)
    block.end_date = max(activity_end(a) for a in members)
    if force_primary or not _primary_has_report(db, block):
        block.primary_activity_id = pick_primary(members).id
    db.add(block)
