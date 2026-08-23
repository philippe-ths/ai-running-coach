"""#945: gather_max_hr_revision -- the DB-reading half, integration-level.

Covers what the pure-logic tests cannot: real Activity rows, real Block
grouping via services.blocks.assign_activity_to_block, and the run-only
filter. Fixtures are built from the REAL shapes a review found in production
data: a multi-activity gym block (Walk, WeightTraining, Rowing, Ride inside
84 minutes; Run, Walk, Run, Walk inside 43 minutes) and WeightTraining rows
carrying a recorded max_hr (wrist-optical motion-artefact risk).
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.models import Activity, User, UserProfile
from app.services.blocks import assign_activity_to_block
from app.services.coach.max_hr_calibration import gather_max_hr_revision

T0 = datetime(2026, 8, 1, 7, 0, tzinfo=timezone.utc)
GAP = 1800  # BLOCK_GAP_SECONDS default


def _user(db, *, max_hr=180) -> User:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    db.add(
        UserProfile(
            user_id=user.id,
            goal_type="general",
            experience_level="intermediate",
            weekly_days_available=4,
            max_hr=max_hr,
        )
    )
    db.commit()
    return user


def _activity(db, user, *, start, elapsed, type, max_hr=None, distance_m=5000):
    activity = Activity(
        user_id=user.id,
        strava_activity_id=abs(hash(str(uuid4()))) % 10**9,
        start_date=start,
        type=type,
        name=type,
        distance_m=distance_m,
        moving_time_s=elapsed,
        elapsed_time_s=elapsed,
        elev_gain_m=0.0,
        max_hr=max_hr,
        raw_summary={},
    )
    db.add(activity)
    db.commit()
    db.refresh(activity)
    return activity


def test_split_gym_block_contributes_no_evidence_at_all(db):
    """The real shape the review cited: `Walk, WeightTraining, Rowing, Ride`
    inside 84 minutes. None of these are runs, so after the run-only filter
    this block contributes nothing regardless of how it is grouped."""
    user = _user(db, max_hr=180)
    base = T0
    walk = _activity(db, user, start=base, elapsed=600, type="Walk", max_hr=140)
    block = assign_activity_to_block(db, walk, gap_seconds=GAP)
    weights = _activity(
        db, user, start=base + timedelta(seconds=700), elapsed=1800, type="WeightTraining", max_hr=195,
    )
    assign_activity_to_block(db, weights, gap_seconds=GAP)
    row = _activity(
        db, user, start=base + timedelta(seconds=2600), elapsed=1800, type="Rowing", max_hr=196,
    )
    assign_activity_to_block(db, row, gap_seconds=GAP)
    ride = _activity(
        db, user, start=base + timedelta(seconds=4500), elapsed=1800, type="Ride", max_hr=197,
    )
    assign_activity_to_block(db, ride, gap_seconds=GAP)
    # A separate genuine run, well outside this block, with no exceedance --
    # just so the "too little history" floor isn't itself what abstains here.
    run = _activity(
        db, user, start=base + timedelta(days=1), elapsed=1800, type="Run", max_hr=170,
    )
    assign_activity_to_block(db, run, gap_seconds=GAP)

    finding = gather_max_hr_revision(db, user.id)
    assert finding is None


def test_run_walk_run_walk_block_counts_as_one_training_event(db):
    """The real shape the review cited: two RUN legs inside one 43-minute
    block. Both clear the exceedance bar on their own, but as one block they
    must not satisfy MIN_QUALIFYING_BLOCKS by themselves."""
    user = _user(db, max_hr=180)
    base = T0
    run1 = _activity(db, user, start=base, elapsed=600, type="Run", max_hr=193)
    assign_activity_to_block(db, run1, gap_seconds=GAP)
    walk = _activity(db, user, start=base + timedelta(seconds=700), elapsed=300, type="Walk", max_hr=150)
    assign_activity_to_block(db, walk, gap_seconds=GAP)
    run2 = _activity(db, user, start=base + timedelta(seconds=1100), elapsed=600, type="Run", max_hr=194)
    assign_activity_to_block(db, run2, gap_seconds=GAP)
    walk2 = _activity(db, user, start=base + timedelta(seconds=1800), elapsed=300, type="Walk", max_hr=148)
    assign_activity_to_block(db, walk2, gap_seconds=GAP)
    # A plain, unrelated, non-exceeding run so history floor is met from real
    # data rather than accidentally by the block above alone.
    filler = _activity(db, user, start=base + timedelta(days=2), elapsed=1800, type="Run", max_hr=172)
    assign_activity_to_block(db, filler, gap_seconds=GAP)

    finding = gather_max_hr_revision(db, user.id)
    assert finding is None


def test_weight_training_spikes_are_never_evidence_even_across_separate_blocks(db):
    """WeightTraining is the type most prone to wrist-optical motion
    artefacts (arm movement read as cardiac). Two WeightTraining spikes in
    TWO separate blocks -- genuinely "independent" by the block rule -- must
    still never revise a RUNNING max HR ceiling."""
    user = _user(db, max_hr=180)
    base = T0
    w1 = _activity(db, user, start=base, elapsed=1800, type="WeightTraining", max_hr=210)
    assign_activity_to_block(db, w1, gap_seconds=GAP)
    w2 = _activity(db, user, start=base + timedelta(days=1), elapsed=1800, type="WeightTraining", max_hr=212)
    assign_activity_to_block(db, w2, gap_seconds=GAP)
    r1 = _activity(db, user, start=base + timedelta(days=2), elapsed=1800, type="Run", max_hr=172)
    assign_activity_to_block(db, r1, gap_seconds=GAP)

    finding = gather_max_hr_revision(db, user.id)
    assert finding is None


def test_two_genuinely_separate_run_blocks_do_qualify(db):
    """The positive control: two DIFFERENT run blocks, each on its own day
    (well past BLOCK_GAP_SECONDS apart), each clearing the exceedance bar --
    real independent evidence, and the WeightTraining/Walk noise around them
    plays no part."""
    user = _user(db, max_hr=180)
    base = T0
    run1 = _activity(db, user, start=base, elapsed=1800, type="Run", max_hr=193)
    assign_activity_to_block(db, run1, gap_seconds=GAP)
    weights = _activity(db, user, start=base + timedelta(days=1), elapsed=1800, type="WeightTraining", max_hr=220)
    assign_activity_to_block(db, weights, gap_seconds=GAP)
    run2 = _activity(db, user, start=base + timedelta(days=2), elapsed=1800, type="Run", max_hr=194)
    assign_activity_to_block(db, run2, gap_seconds=GAP)
    run3 = _activity(db, user, start=base + timedelta(days=3), elapsed=1800, type="Run", max_hr=175)
    assign_activity_to_block(db, run3, gap_seconds=GAP)

    finding = gather_max_hr_revision(db, user.id)
    assert finding is not None
    assert finding.stated_max == 180
    assert finding.suggested_max == 194
    assert finding.exceeding_block_count == 2
    # The word "runs" in the basis text is true here BY CONSTRUCTION: the
    # WeightTraining row that also cleared the bar (220 bpm) never reached
    # the pure detector at all, filtered out before find_max_hr_revision
    # ever ran.
    assert "runs" in finding.basis
