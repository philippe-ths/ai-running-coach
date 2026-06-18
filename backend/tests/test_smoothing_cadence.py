"""Regression tests for cadence smoothing dropout handling (#325).

A raw cadence sample of 0 is a DROPOUT (Strava reports 0 before the first
detected step and while not running / paused), not a measurement. The smoother
must not average dropout zeros together with real cadence, because a window
straddling the dropout/real boundary produces a physiologically meaningless low
ramp value (the observed 76 spm in the issue).

The reported case ([0,0,0,0,0,152,152,...] -> 76 ramp) is sourced from a real
prod API capture quoted in issue #325 (ground-truth trust level 4: a
hand-constructed minimal example confirmed by the reporter from real data). The
mid-run-dropout and all-nonzero cases are synthetic shapes exercising the
function's branches (test setup, built inline).
"""

from app.services.analysis.smoothing import smooth_cadence


def test_leading_dropout_does_not_emit_low_ramp():
    """The reported case: leading zero dropout must not bleed a ~76 ramp.

    Raw [0,0,0,0,0,152,152,...] previously smoothed to [0,0,76,152,...]. The
    leading dropout samples must be missing (None), never a low non-zero ramp.
    """
    n = 12
    cadence = [0, 0, 0, 0, 0] + [152] * (n - 5)
    velocity = [0.0] * 5 + [3.0] * (n - 5)
    moving = [False] * 5 + [True] * (n - 5)
    time = list(range(n))

    result = smooth_cadence(cadence, velocity, moving, time)

    # No sample may be a physiologically-meaningless low ramp value: every value
    # the smoother emits is either a real cadence reading or missing, never a
    # blend of dropout zeros and real cadence (the old [..,76,..] / [..,0,..]).
    real_values = [v for v in result if v is not None]
    assert all(
        v >= 100 for v in real_values
    ), f"smoothed cadence leaked a sub-physiological ramp value: {result}"

    # The start of the dropout region must be represented as missing, never 0.
    assert result[0] is None, f"leading dropout must be missing, got {result[0]}"
    assert 0.0 not in result, f"a dropout sample leaked as a literal 0, got {result}"

    # The real cadence must survive unchanged.
    assert result[-1] == 152.0


def test_midrun_stop_dropout_is_missing_not_zero():
    """A mid-run stop (cadence 0 while not moving) must read as missing, not 0.

    This is the broader bug class behind #325: the old guard only NaN'd zeros
    while moving, so a mid-run STOP (moving False, velocity 0) leaked a block of
    literal 0.0 values right next to real cadence. The dropout must be missing.
    """
    # 8 real, 24 dropout (a stop: not moving, zero velocity), 8 real. Long
    # enough that, after the rolling median spreads real cadence a few samples
    # into each edge, the central gap still exceeds the 10s interpolation
    # ceiling, so the core of the dropout must stay missing rather than bridged.
    cadence = [170] * 8 + [0] * 24 + [170] * 8
    n = len(cadence)
    velocity = [3.0] * 8 + [0.0] * 24 + [3.0] * 8  # stopped through the dropout
    moving = [True] * 8 + [False] * 24 + [True] * 8
    time = list(range(n))

    result = smooth_cadence(cadence, velocity, moving, time)

    real_values = [v for v in result if v is not None]
    assert all(
        v >= 100 for v in real_values
    ), f"mid-run dropout leaked a sub-physiological value: {result}"
    assert 0.0 not in result, f"a dropout sample leaked as a literal 0, got {result}"

    # The core of the dropout must be missing, not a low blend of zeros and the
    # surrounding real cadence. Index 20 sits in the centre of the dropout.
    assert result[20] is None, f"expected mid-dropout sample to be None, got {result[20]}"
    # Real cadence on both sides survives.
    assert result[0] == 170.0
    assert result[-1] == 170.0


def test_normal_cadence_is_unchanged():
    """A normal all-nonzero cadence series must smooth exactly as before.

    This is the only intended behaviour change boundary: legitimate cadence
    smoothing must be untouched.
    """
    cadence = [168, 170, 169, 171, 172, 170, 169, 171, 170]
    n = len(cadence)
    velocity = [3.2] * n
    moving = [True] * n
    time = list(range(n))

    result = smooth_cadence(cadence, velocity, moving, time)

    # No nulls: every sample is a real measurement.
    assert all(v is not None for v in result), f"normal series produced gaps: {result}"
    # Median-filtered values stay in the real cadence band.
    assert all(168 <= v <= 172 for v in result), f"normal series shifted out of band: {result}"
