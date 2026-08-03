"""A1 block detection: pure time-gap grouping + split/merge (ADR 0011).

Timing fixtures are synthetic test setup chosen to exercise the gap boundary,
not ground truth about real training data; the backfill over real data is
verified separately on the seeded Postgres snapshot.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Activity, Block, CoachReport, Exchange, User
from app.services.coach import exchange_lifecycle as lifecycle
from app.services.blocks import activity_end as activity_end_of
from app.services.blocks import (
    assign_activity_to_block,
    merge_blocks,
    split_block,
)

GAP = 1800

T0 = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; normalise for comparison against UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _make_user(db) -> User:
    user = User(email=f"blocks-svc-{uuid.uuid4().hex[:8]}@example.com")
    db.add(user)
    db.commit()
    return user


def _make_activity(db, user, *, start, elapsed=1500, type="Run", **overrides) -> Activity:
    fields = dict(
        user_id=user.id,
        strava_activity_id=int(uuid.uuid4().int % 10**12),
        start_date=start,
        type=type,
        name=f"{type} at {start:%H:%M}",
        distance_m=5000,
        moving_time_s=elapsed,
        elapsed_time_s=elapsed,
        elev_gain_m=10.0,
        raw_summary={},
    )
    fields.update(overrides)
    activity = Activity(**fields)
    db.add(activity)
    db.commit()
    return activity


def test_first_activity_starts_a_block_of_one_with_open_exchange(db):
    user = _make_user(db)
    activity = _make_activity(db, user, start=T0)

    block = assign_activity_to_block(db, activity, gap_seconds=GAP)

    assert activity.block_id == block.id
    assert block.primary_activity_id == activity.id
    assert _aware(block.start_date) == T0
    assert _aware(block.end_date) == T0 + timedelta(seconds=1500)

    exchange = db.query(Exchange).filter(Exchange.block_id == block.id).one()
    assert exchange.opener_sent_at is None
    assert exchange.fuller_sent_at is None


def test_activity_within_gap_joins_and_run_becomes_primary(db):
    user = _make_user(db)
    walk = _make_activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)

    run_start = T0 + timedelta(seconds=1200 + 600)  # 10 min after the walk ends
    run = _make_activity(db, user, start=run_start, elapsed=2400, type="Run")
    joined = assign_activity_to_block(db, run, gap_seconds=GAP)

    assert joined.id == block.id
    assert {a.id for a in (walk, run)} == {a.id for a in joined.activities}
    assert joined.primary_activity_id == run.id  # the run wins over the walk
    assert _aware(joined.start_date) == T0
    assert _aware(joined.end_date) == run_start + timedelta(seconds=2400)
    assert db.query(Exchange).filter(Exchange.block_id == block.id).count() == 1


def test_virtual_run_becomes_primary_over_a_longer_non_run(db):
    """A connected-platform run (Zwift/Peloton) surfaces as `type == "VirtualRun"`;
    it is still a run, so it wins the block primary over a longer walk (#644),
    keeping "block contains a run" == "primary is a run"."""
    user = _make_user(db)
    walk = _make_activity(db, user, start=T0, elapsed=3600, type="Walk")  # longer
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)

    vrun_start = T0 + timedelta(seconds=3600 + 600)  # 10 min after the walk ends
    vrun = _make_activity(db, user, start=vrun_start, elapsed=1200, type="VirtualRun")
    joined = assign_activity_to_block(db, vrun, gap_seconds=GAP)

    assert joined.id == block.id
    assert joined.primary_activity_id == vrun.id  # the virtual run wins over the longer walk


def test_activity_past_gap_starts_a_new_block(db):
    user = _make_user(db)
    first = _make_activity(db, user, start=T0, elapsed=1500)
    block1 = assign_activity_to_block(db, first, gap_seconds=GAP)

    late_start = activity_end_of(first) + timedelta(seconds=GAP + 1)
    second = _make_activity(db, user, start=late_start, elapsed=1500)
    block2 = assign_activity_to_block(db, second, gap_seconds=GAP)

    assert block2.id != block1.id
    assert second.block_id == block2.id


def test_boundary_gap_exactly_at_threshold_starts_a_new_block(db):
    user = _make_user(db)
    first = _make_activity(db, user, start=T0, elapsed=1500)
    block1 = assign_activity_to_block(db, first, gap_seconds=GAP)

    at_threshold = activity_end_of(first) + timedelta(seconds=GAP)
    second = _make_activity(db, user, start=at_threshold, elapsed=1500)
    block2 = assign_activity_to_block(db, second, gap_seconds=GAP)

    assert block2.id != block1.id


def test_closed_exchange_never_absorbs_a_late_arrival(db):
    user = _make_user(db)
    first = _make_activity(db, user, start=T0, elapsed=1500)
    block1 = assign_activity_to_block(db, first, gap_seconds=GAP)

    exchange = db.query(Exchange).filter(Exchange.block_id == block1.id).one()
    exchange.fuller_sent_at = datetime.now(timezone.utc)
    db.commit()

    within_gap = activity_end_of(first) + timedelta(seconds=300)
    late = _make_activity(db, user, start=within_gap, elapsed=1500)
    block2 = assign_activity_to_block(db, late, gap_seconds=GAP)

    assert block2.id != block1.id
    assert block1.primary_activity_id == first.id  # the closed block is untouched


def test_out_of_order_sync_converges_to_one_block(db):
    user = _make_user(db)
    later = _make_activity(db, user, start=T0 + timedelta(seconds=2100), elapsed=1800)
    block = assign_activity_to_block(db, later, gap_seconds=GAP)

    # The earlier activity arrives second (polling out of order); it ends 600s
    # before the existing block starts, so it belongs to the same event.
    earlier = _make_activity(db, user, start=T0, elapsed=1500)
    joined = assign_activity_to_block(db, earlier, gap_seconds=GAP)

    assert joined.id == block.id
    assert _aware(joined.start_date) == T0
    assert joined.primary_activity_id == later.id  # still the longest run


def _add_report(db, activity) -> None:
    """A coach report keyed to an activity — the anchor commitment that freezes
    the block's primary (the LLM opener writes this row; the receipt does not)."""
    db.add(CoachReport(activity_id=activity.id, report={}, meta={}, context_pack={}))
    db.commit()


def test_primary_is_frozen_once_a_report_is_keyed_to_it(db):
    # A report speaks about the primary activity and is keyed on it; a late
    # arrival into the open exchange must not move that anchor, however long it
    # is. (Under the LLM exchange the opener writes this report and opens the
    # exchange together.)
    user = _make_user(db)
    run = _make_activity(db, user, start=T0, elapsed=1500, type="Run")
    block = assign_activity_to_block(db, run, gap_seconds=GAP)
    exchange = db.query(Exchange).filter(Exchange.block_id == block.id).one()
    exchange.opened_at = datetime.now(timezone.utc)
    db.commit()
    _add_report(db, run)  # the opener's report row, keyed to the primary

    longer = _make_activity(
        db, user, start=T0 + timedelta(seconds=1800), elapsed=7200, type="Run"
    )
    joined = assign_activity_to_block(db, longer, gap_seconds=GAP)

    assert joined.id == block.id
    assert joined.primary_activity_id == run.id  # frozen, not the longer run
    assert _aware(joined.end_date) == _aware(
        longer.start_date + timedelta(seconds=7200)
    )  # bounds still grow


def test_receipt_opened_exchange_does_not_freeze_primary_before_a_report(db):
    # Under the receipt cadence the first activity's deterministic receipt opens
    # the exchange (sets opened_at) but writes NO coach report. A run joining the
    # block afterwards must still win the primary, so the single session report
    # is about the run and not the warmup walk (#487).
    user = _make_user(db)
    walk = _make_activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)
    exchange = db.query(Exchange).filter(Exchange.block_id == block.id).one()
    exchange.opened_at = datetime.now(timezone.utc)  # the receipt opened it
    db.commit()
    assert block.primary_activity_id == walk.id  # nothing else yet

    run_start = T0 + timedelta(seconds=1800)
    run = _make_activity(db, user, start=run_start, elapsed=2400, type="Run")
    joined = assign_activity_to_block(db, run, gap_seconds=GAP)

    assert joined.id == block.id
    assert joined.primary_activity_id == run.id  # not frozen to the walk


def _grouped_pair(db, user):
    """A walk then a run grouped into one block (run primary)."""
    walk = _make_activity(db, user, start=T0, elapsed=1200, type="Walk")
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)
    run = _make_activity(
        db, user, start=T0 + timedelta(seconds=1800), elapsed=2400, type="Run"
    )
    assign_activity_to_block(db, run, gap_seconds=GAP)
    db.refresh(block)
    return block, walk, run


def test_split_block_at_activity_corrects_grouping_without_refiring(db):
    user = _make_user(db)
    block, walk, run = _grouped_pair(db, user)
    exchange = db.query(Exchange).filter(Exchange.block_id == block.id).one()
    exchange.opener_sent_at = datetime.now(timezone.utc)
    exchange.fuller_sent_at = datetime.now(timezone.utc)
    db.commit()

    left, right = split_block(db, block, at_activity=run)

    assert left.id == block.id
    assert walk.block_id == left.id and run.block_id == right.id
    assert left.user_corrected is True and right.user_corrected is True
    assert left.primary_activity_id == walk.id
    assert right.primary_activity_id == run.id
    assert _aware(left.end_date) == activity_end_of(walk).replace(tzinfo=timezone.utc)

    # The new block's exchange inherits the closed state: a correction never
    # re-opens delivery, so nothing can re-fire (AC4).
    new_exchange = db.query(Exchange).filter(Exchange.block_id == right.id).one()
    assert new_exchange.opener_sent_at is not None
    assert new_exchange.fuller_sent_at is not None


def test_split_block_carries_the_done_tap_to_both_halves(db):
    # #750: the defect window is tapped-done-but-not-yet-closed (the full report is
    # scheduled, `fuller_sent_at` still null). Before the fix the new half started with
    # `done_at` null, so a second tap read as the first and scheduled a SECOND
    # generation. Both halves must now refuse that tap.
    user = _make_user(db)
    block, walk, run = _grouped_pair(db, user)
    exchange = db.query(Exchange).filter(Exchange.block_id == block.id).one()
    exchange.opened_at = datetime.now(timezone.utc)
    exchange.done_at = datetime.now(timezone.utc)
    db.commit()

    left, right = split_block(db, block, at_activity=run)

    for half in (left, right):
        ex = db.query(Exchange).filter(Exchange.block_id == half.id).one()
        assert ex.done_at is not None, f"block {half.id} lost the done tap"
        assert lifecycle.mark_done(db, ex) is False, "a second tap would re-schedule"


def test_split_block_leaves_an_untapped_exchange_tappable(db):
    # The complement: a correction on a session the runner never marked done must not
    # invent a tap, or the new half could never trigger its own full report early.
    user = _make_user(db)
    block, walk, run = _grouped_pair(db, user)
    exchange = db.query(Exchange).filter(Exchange.block_id == block.id).one()
    exchange.opened_at = datetime.now(timezone.utc)
    db.commit()

    left, right = split_block(db, block, at_activity=run)

    for half in (left, right):
        ex = db.query(Exchange).filter(Exchange.block_id == half.id).one()
        assert ex.done_at is None
        assert lifecycle.mark_done(db, ex) is True


def test_merge_blocks_carries_the_done_tap_from_the_absorbed_side(db):
    # #750: the tap lived on the block being absorbed, whose row is deleted by the
    # merge. The survivor must take it, or the tap is lost with the row.
    user = _make_user(db)
    first = _make_activity(db, user, start=T0, elapsed=1500, type="Walk")
    block1 = assign_activity_to_block(db, first, gap_seconds=GAP)
    second_start = activity_end_of(first) + timedelta(seconds=GAP * 2)
    second = _make_activity(db, user, start=second_start, elapsed=2400, type="Run")
    block2 = assign_activity_to_block(db, second, gap_seconds=GAP)
    ex2 = db.query(Exchange).filter(Exchange.block_id == block2.id).one()
    ex2.opened_at = datetime.now(timezone.utc)
    ex2.done_at = datetime.now(timezone.utc)
    db.commit()

    merged = merge_blocks(db, block1, block2)

    survivor = db.query(Exchange).filter(Exchange.block_id == merged.id).one()
    assert survivor.done_at is not None
    assert lifecycle.mark_done(db, survivor) is False


def test_merge_blocks_combines_members_without_refiring(db):
    user = _make_user(db)
    first = _make_activity(db, user, start=T0, elapsed=1500, type="Walk")
    block1 = assign_activity_to_block(db, first, gap_seconds=GAP)
    second_start = activity_end_of(first) + timedelta(seconds=GAP * 2)
    second = _make_activity(db, user, start=second_start, elapsed=2400, type="Run")
    block2 = assign_activity_to_block(db, second, gap_seconds=GAP)
    ex1 = db.query(Exchange).filter(Exchange.block_id == block1.id).one()
    ex1.opener_sent_at = datetime.now(timezone.utc)
    ex1.fuller_sent_at = datetime.now(timezone.utc)
    db.commit()

    merged = merge_blocks(db, block1, block2)

    assert merged.id == block1.id
    assert first.block_id == merged.id and second.block_id == merged.id
    assert merged.user_corrected is True
    assert merged.primary_activity_id == second.id  # the run
    assert _aware(merged.end_date) == _aware(second_start + timedelta(seconds=2400))

    # One exchange survives, carrying the most-advanced (non-refiring) state.
    assert db.query(Block).filter(Block.id == block2.id).count() == 0
    survivor = db.query(Exchange).filter(Exchange.block_id == merged.id).one()
    assert survivor.opener_sent_at is not None
    assert survivor.fuller_sent_at is not None
    assert db.query(Exchange).count() == 1
