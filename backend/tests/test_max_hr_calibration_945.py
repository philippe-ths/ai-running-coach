"""#945: durable profile fact revision (max HR) -- pure-logic tests.

find_max_hr_revision is deterministic and abstains by default: no stated max,
too little history, a lone exceedance, a sub-threshold exceedance, several
rows from the SAME training block, and a fact already surfaced within the
cooldown all abstain. It only raises when the runner's own recent activities
give real, repeated, INDEPENDENT, meaningful evidence.

`observed` is a list of `(block_id, max_hr)` pairs -- one per RUN activity,
already filtered to running activities by the caller (gather_max_hr_revision
in the real module; see test_max_hr_revision_adapter_945.py for that half).
`_obs` below builds that list with a distinct synthetic block id per entry by
default, so most tests read as a flat list of values exactly as before; the
block-independence tests pass explicit shared ids.
"""

from datetime import datetime, timedelta, timezone
from itertools import count

from app.services.coach.max_hr_calibration import (
    EXCEEDANCE_MARGIN_BPM,
    MIN_HISTORY_ACTIVITIES,
    MIN_QUALIFYING_BLOCKS,
    RESURFACE_COOLDOWN_DAYS,
    find_max_hr_revision,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)

_next_block = count()


def _obs(*maxes):
    """One independent block per value -- the common case in these tests."""
    return [(f"block-{next(_next_block)}", m) for m in maxes]


def _same_block(block_id, *maxes):
    """Several rows sharing ONE block id -- one training event, many rows."""
    return [(block_id, m) for m in maxes]


# --------------------------------------------------------------------------- #
# Abstain conditions
# --------------------------------------------------------------------------- #


def test_no_stated_max_abstains():
    assert find_max_hr_revision(None, _obs(190, 191, 192)) is None
    assert find_max_hr_revision(0, _obs(190, 191, 192)) is None


def test_thin_history_abstains_even_with_qualifying_exceedances():
    # Only 2 samples total, both would qualify -- but MIN_HISTORY_ACTIVITIES is 3:
    # two data points is the runner's whole thin record, not "recent training".
    assert 2 < MIN_HISTORY_ACTIVITIES
    assert find_max_hr_revision(180, _obs(190, 191)) is None


def test_single_exceedance_abstains():
    # One spike among a real sample is a sensor artefact, not evidence.
    result = find_max_hr_revision(180, _obs(170, 175, 190))
    assert result is None


def test_subthreshold_exceedance_abstains():
    # Two activities barely over the stated max -- within noise, not a real signal.
    margin_under = EXCEEDANCE_MARGIN_BPM - 1
    bumped = 180 + margin_under
    assert find_max_hr_revision(180, _obs(bumped, bumped, 175)) is None


def test_missing_samples_are_ignored_not_counted_as_history():
    # None entries (activities with no recorded HR) do not count toward the
    # history floor or the exceedance count.
    result = find_max_hr_revision(180, _obs(None, None, None, 190, 191))
    assert result is None  # only 2 real samples total, below MIN_HISTORY_ACTIVITIES


def test_implausible_sensor_readings_are_dropped_not_treated_as_evidence():
    # A couple of glitched GPS-watch readings must not be enough to offer a
    # nonsensical max HR -- they are dropped before anything else runs, not
    # merely excluded from "exceeding".
    result = find_max_hr_revision(180, _obs(999, 998, 175, 178))
    assert result is None


def test_implausibly_low_readings_are_also_dropped():
    result = find_max_hr_revision(180, _obs(5, 3, 190, 193, 178))
    # Only 3 plausible samples remain (190, 193, 178); still qualifies on its
    # own plausible evidence, but the garbage low readings play no part in it.
    assert result is not None
    assert result.sample_count == 3


# --------------------------------------------------------------------------- #
# Independence: distinct BLOCKS, not distinct activity rows (#945 review fix 2)
# --------------------------------------------------------------------------- #


def test_two_exceeding_rows_from_the_same_block_do_not_qualify():
    """A single training event Strava (or the app's own clustering) splits
    into several activity rows -- a gym block, an interval run logged as
    laps-as-activities -- is ONE physiological event. Two exceeding rows from
    the SAME block must count as ONE vote, not two, exactly like the real
    seeded shapes the review cited (e.g. a `Run, Walk, Run, Walk` block)."""
    observed = _same_block("block-x", 193, 194) + _obs(175)
    result = find_max_hr_revision(180, observed)
    assert result is None


def test_two_exceeding_rows_from_different_blocks_do_qualify():
    assert MIN_QUALIFYING_BLOCKS == 2
    observed = _same_block("block-a", 193) + _same_block("block-b", 194) + _obs(175)
    result = find_max_hr_revision(180, observed)
    assert result is not None
    assert result.exceeding_block_count == 2
    assert result.suggested_max == 194


def test_unassigned_none_block_activities_are_never_pooled_together():
    """Two activities with no block assignment must not be silently treated
    as the same training event -- each None gets its own identity."""
    observed = [(None, 193), (None, 194), (None, 175)]
    result = find_max_hr_revision(180, observed)
    assert result is not None
    assert result.exceeding_block_count == 2


def test_a_block_with_one_exceeding_row_still_contributes_at_most_one_vote():
    """Three rows in the same block, only one clearing the bar, plus a
    second independent block -- the same-block extra rows must not inflate
    the count past what one real training event can contribute."""
    observed = (
        _same_block("block-x", 193, 170, 171)
        + _same_block("block-y", 194)
        + _obs(172)
    )
    result = find_max_hr_revision(180, observed)
    assert result is not None
    assert result.exceeding_block_count == 2


# --------------------------------------------------------------------------- #
# Positive case
# --------------------------------------------------------------------------- #


def test_qualifying_exceedance_raises_with_the_highest_recorded_peak():
    result = find_max_hr_revision(180, _obs(175, 190, 193, 178))
    assert result is not None
    assert result.stated_max == 180
    # The highest of the qualifying peaks, not an invented number.
    assert result.suggested_max == 193
    assert result.margin_bpm == 13
    assert result.exceeding_block_count == 2
    assert result.sample_count == 4
    assert "180" in result.basis and "193" in result.basis


def test_exceedance_margin_boundary_is_inclusive():
    stated = 180
    exact = stated + EXCEEDANCE_MARGIN_BPM
    result = find_max_hr_revision(stated, _obs(exact, exact, 170))
    assert result is not None
    assert result.suggested_max == exact


# --------------------------------------------------------------------------- #
# Anti-nag (#945 AC5): never repeatedly re-raise the same evidence
# --------------------------------------------------------------------------- #


def test_recently_surfaced_same_evidence_is_suppressed():
    result = find_max_hr_revision(
        180,
        _obs(190, 193, 178),
        last_surfaced_value=193,
        last_surfaced_at=NOW - timedelta(days=1),
        as_of=NOW,
    )
    assert result is None


def test_suppression_lifts_once_cooldown_elapses():
    assert RESURFACE_COOLDOWN_DAYS > 0
    result = find_max_hr_revision(
        180,
        _obs(190, 193, 178),
        last_surfaced_value=193,
        last_surfaced_at=NOW - timedelta(days=RESURFACE_COOLDOWN_DAYS + 1),
        as_of=NOW,
    )
    assert result is not None
    assert result.suggested_max == 193


def test_materially_higher_new_evidence_reraises_within_cooldown():
    # The runner declined (or never confirmed) a 193 offer yesterday; a fresh,
    # genuinely higher peak (well past EXCEEDANCE_MARGIN_BPM over the old
    # offer) is new evidence and is not suppressed by the old cooldown --
    # re-deriving from evidence that has materially changed is one of the
    # sanctioned anti-nag mechanisms.
    result = find_max_hr_revision(
        180,
        _obs(193, 199, 178),
        last_surfaced_value=193,
        last_surfaced_at=NOW - timedelta(days=1),
        as_of=NOW,
    )
    assert result is not None
    assert result.suggested_max == 199


def test_a_trivially_higher_suggestion_does_not_bypass_the_cooldown():
    """#945 review fix 5, demonstrated: declined at 193 yesterday, a fresh
    194 bpm reading today must NOT re-raise -- 1 bpm is noise on a 3-digit
    heart rate, not a second independent signal, and bypassing the cooldown
    on noise is exactly the nagging AC5 forbids."""
    result = find_max_hr_revision(
        180,
        _obs(193, 194, 178),
        last_surfaced_value=193,
        last_surfaced_at=NOW - timedelta(days=1),
        as_of=NOW,
    )
    assert result is None


def test_the_bypass_boundary_is_exactly_the_exceedance_margin():
    # last_surfaced_value + EXCEEDANCE_MARGIN_BPM is the smallest suggestion
    # that counts as material and is allowed to bypass the cooldown.
    boundary = 193 + EXCEEDANCE_MARGIN_BPM
    just_under = boundary - 1

    suppressed = find_max_hr_revision(
        180,
        _obs(193, just_under, 178),
        last_surfaced_value=193,
        last_surfaced_at=NOW - timedelta(days=1),
        as_of=NOW,
    )
    assert suppressed is None

    bypasses = find_max_hr_revision(
        180,
        _obs(193, boundary, 178),
        last_surfaced_value=193,
        last_surfaced_at=NOW - timedelta(days=1),
        as_of=NOW,
    )
    assert bypasses is not None
    assert bypasses.suggested_max == boundary


def test_lower_or_equal_repeat_evidence_without_a_stamp_still_raises():
    # No prior surfacing recorded -- nothing to suppress against.
    result = find_max_hr_revision(180, _obs(190, 193, 178))
    assert result is not None
