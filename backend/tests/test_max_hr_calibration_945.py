"""#945: durable profile fact revision (max HR) -- pure-logic tests.

find_max_hr_revision is deterministic and abstains by default: no stated max,
too little history, a lone exceedance, a sub-threshold exceedance, and a fact
already surfaced within the cooldown all abstain. It only raises when the
runner's own recent activities give real, repeated, meaningful evidence.
"""

from datetime import datetime, timedelta, timezone

from app.services.coach.max_hr_calibration import (
    EXCEEDANCE_MARGIN_BPM,
    MIN_HISTORY_ACTIVITIES,
    MIN_QUALIFYING_ACTIVITIES,
    RESURFACE_COOLDOWN_DAYS,
    find_max_hr_revision,
)

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Abstain conditions
# --------------------------------------------------------------------------- #


def test_no_stated_max_abstains():
    assert find_max_hr_revision(None, [190, 191, 192]) is None
    assert find_max_hr_revision(0, [190, 191, 192]) is None


def test_thin_history_abstains_even_with_qualifying_exceedances():
    # Only 2 samples total, both would qualify -- but MIN_HISTORY_ACTIVITIES is 3:
    # two data points is the runner's whole thin record, not "recent training".
    assert len([190, 191]) < MIN_HISTORY_ACTIVITIES
    assert find_max_hr_revision(180, [190, 191]) is None


def test_single_exceedance_abstains():
    # One spike among a real sample is a sensor artefact, not evidence.
    result = find_max_hr_revision(180, [170, 175, 190])
    assert result is None


def test_subthreshold_exceedance_abstains():
    # Two activities barely over the stated max -- within noise, not a real signal.
    margin_under = EXCEEDANCE_MARGIN_BPM - 1
    bumped = 180 + margin_under
    assert find_max_hr_revision(180, [bumped, bumped, 175]) is None


def test_missing_samples_are_ignored_not_counted_as_history():
    # None entries (activities with no recorded HR) do not count toward the
    # history floor or the exceedance count.
    result = find_max_hr_revision(180, [None, None, None, 190, 191])
    assert result is None  # only 2 real samples total, below MIN_HISTORY_ACTIVITIES


def test_implausible_sensor_readings_are_dropped_not_treated_as_evidence():
    # A couple of glitched GPS-watch readings must not be enough to offer a
    # nonsensical max HR -- they are dropped before anything else runs, not
    # merely excluded from "exceeding".
    result = find_max_hr_revision(180, [999, 998, 175, 178])
    assert result is None


def test_implausibly_low_readings_are_also_dropped():
    result = find_max_hr_revision(180, [5, 3, 190, 193, 178])
    # Only 3 plausible samples remain (190, 193, 178); still qualifies on its
    # own plausible evidence, but the garbage low readings play no part in it.
    assert result is not None
    assert result.sample_count == 3


# --------------------------------------------------------------------------- #
# Positive case
# --------------------------------------------------------------------------- #


def test_qualifying_exceedance_raises_with_the_highest_recorded_peak():
    assert MIN_QUALIFYING_ACTIVITIES == 2
    result = find_max_hr_revision(180, [175, 190, 193, 178])
    assert result is not None
    assert result.stated_max == 180
    # The highest of the qualifying peaks, not an invented number.
    assert result.suggested_max == 193
    assert result.margin_bpm == 13
    assert result.exceeding_count == 2
    assert result.sample_count == 4
    assert "180" in result.basis and "193" in result.basis


def test_exceedance_margin_boundary_is_inclusive():
    stated = 180
    exact = stated + EXCEEDANCE_MARGIN_BPM
    result = find_max_hr_revision(stated, [exact, exact, 170])
    assert result is not None
    assert result.suggested_max == exact


# --------------------------------------------------------------------------- #
# Anti-nag (#945 AC5): never repeatedly re-raise the same evidence
# --------------------------------------------------------------------------- #


def test_recently_surfaced_same_evidence_is_suppressed():
    result = find_max_hr_revision(
        180,
        [190, 193, 178],
        last_surfaced_value=193,
        last_surfaced_at=NOW - timedelta(days=1),
        as_of=NOW,
    )
    assert result is None


def test_suppression_lifts_once_cooldown_elapses():
    assert RESURFACE_COOLDOWN_DAYS > 0
    result = find_max_hr_revision(
        180,
        [190, 193, 178],
        last_surfaced_value=193,
        last_surfaced_at=NOW - timedelta(days=RESURFACE_COOLDOWN_DAYS + 1),
        as_of=NOW,
    )
    assert result is not None
    assert result.suggested_max == 193


def test_materially_higher_new_evidence_reraises_within_cooldown():
    # The runner declined (or never confirmed) a 193 offer yesterday; a fresh,
    # genuinely higher peak is new evidence and is not suppressed by the old
    # cooldown -- re-deriving from evidence that has changed is one of the
    # sanctioned anti-nag mechanisms.
    result = find_max_hr_revision(
        180,
        [193, 199, 178],
        last_surfaced_value=193,
        last_surfaced_at=NOW - timedelta(days=1),
        as_of=NOW,
    )
    assert result is not None
    assert result.suggested_max == 199


def test_lower_or_equal_repeat_evidence_without_a_stamp_still_raises():
    # No prior surfacing recorded -- nothing to suppress against.
    result = find_max_hr_revision(180, [190, 193, 178])
    assert result is not None
