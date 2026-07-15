"""ADR 0026 Slice 3 (#673): the pure `build_intensity_read` / `build_intensity_mix`
reshape. These unit tests pin the merge logic over constructed inputs — the DB wiring +
byte-stability live in test_intensity_read_in_pack.py.
"""

from app.schemas.coach_context import (
    IntensityBandShare,
    IntensityContext,
    IntensitySession,
    PerceivedEffortContext,
)
from app.services.coach.intensity_read import build_intensity_read, build_intensity_mix


def _pe(**kw):
    base = dict(
        effort_axis="tempo",
        divergence=2,
        divergence_direction="felt_harder",
        hr_confounded=False,
        recommended_weighting="balanced",
        pain_trend=None,
    )
    base.update(kw)
    return PerceivedEffortContext(**base)


def _calibrated_drift(observed=8.2, expected=5.1, comparison="above"):
    return {
        "calibrated": True,
        "expected_drift_pct": expected,
        "observed_drift_pct": observed,
        "delta_pct": round(observed - expected, 1),
        "comparison": comparison,
        "sample_count": 6,
        "personal_norm_elevated": False,
        "basis": "your typical drift for these conditions is about 5.1% across 6 comparable runs",
    }


def _session(band="hard", hr_confounded=False, within=IntensityBandShare(easy_pct=18, moderate_pct=22, hard_pct=60)):
    return IntensitySession(band=band, within_run=within, hr_confounded=hr_confounded)


# --------------------------------------------------------------------------- #
# band / within-run passthrough                                                #
# --------------------------------------------------------------------------- #
def test_band_and_within_run_come_from_this_session():
    read = build_intensity_read(
        this_session=_session(band="moderate"),
        distribution_adjusted=None,
        perceived_effort=_pe(),
        calibration_hr_drift=_calibrated_drift(),
        confounders=[],
    )
    assert read.band == "moderate"
    assert read.within_run.hard_pct == 60


# --------------------------------------------------------------------------- #
# felt-vs-measured                                                             #
# --------------------------------------------------------------------------- #
def test_felt_vs_measured_present_with_a_check_in():
    read = build_intensity_read(
        this_session=_session(),
        distribution_adjusted=None,
        perceived_effort=_pe(divergence_direction="felt_harder", recommended_weighting="lead_with_felt"),
        calibration_hr_drift=_calibrated_drift(),
        confounders=["heat"],
    )
    assert read.felt_vs_measured.read == "felt_harder"
    assert read.felt_vs_measured.trust == "lead_with_felt"


def test_felt_vs_measured_absent_without_rpe():
    read = build_intensity_read(
        this_session=_session(),
        distribution_adjusted=None,
        perceived_effort=_pe(divergence_direction=None, recommended_weighting="hr_only"),
        calibration_hr_drift=_calibrated_drift(),
        confounders=[],
    )
    assert read.felt_vs_measured is None


def test_felt_vs_measured_absent_without_perceived_effort():
    read = build_intensity_read(
        this_session=_session(),
        distribution_adjusted=None,
        perceived_effort=None,
        calibration_hr_drift=_calibrated_drift(),
        confounders=[],
    )
    assert read.felt_vs_measured is None


# --------------------------------------------------------------------------- #
# drift-vs-typical                                                             #
# --------------------------------------------------------------------------- #
def test_drift_calibrated_reads_the_personal_norm():
    read = build_intensity_read(
        this_session=_session(),
        distribution_adjusted=None,
        perceived_effort=_pe(),
        calibration_hr_drift=_calibrated_drift(observed=8.2, expected=5.1, comparison="above"),
        confounders=[],
    )
    d = read.drift_vs_typical
    assert d.observed_pct == 8.2
    assert d.typical_pct == 5.1
    assert d.read == "above"
    assert d.personal_norm is True
    assert d.confounded is None  # no confounder fired


def test_drift_heuristic_fallback_has_no_personal_norm():
    hr_drift = {
        "calibrated": False,
        "expected_drift_pct": None,
        "observed_drift_pct": 8.0,
        "heuristic_threshold_pct": 5.0,
        "basis": "not enough comparable runs yet (1); using the general ~5.0% guideline",
    }
    read = build_intensity_read(
        this_session=_session(),
        distribution_adjusted=None,
        perceived_effort=_pe(),
        calibration_hr_drift=hr_drift,
        confounders=[],
    )
    d = read.drift_vs_typical
    assert d.personal_norm is False
    assert d.typical_pct is None
    assert d.read == "above"  # observed 8.0 > heuristic 5.0


def test_drift_absent_when_no_drift_measured():
    hr_drift = {"calibrated": False, "expected_drift_pct": None, "observed_drift_pct": None, "basis": "no HR drift"}
    read = build_intensity_read(
        this_session=_session(),
        distribution_adjusted=None,
        perceived_effort=_pe(),
        calibration_hr_drift=hr_drift,
        confounders=[],
    )
    assert read.drift_vs_typical is None


# --------------------------------------------------------------------------- #
# confounder linkage                                                           #
# --------------------------------------------------------------------------- #
def test_confounder_leads_the_block_and_links_to_the_drift():
    read = build_intensity_read(
        this_session=_session(hr_confounded=True),
        distribution_adjusted=None,
        perceived_effort=_pe(),
        calibration_hr_drift=_calibrated_drift(comparison="above"),
        confounders=["heat"],
    )
    assert read.confounders == ["heat"]
    # The link: an elevated drift on a confounded run is flagged as likely-inflated, not
    # left as a bare "above" for the coach to misread as fatigue.
    assert read.drift_vs_typical.confounded is True


# --------------------------------------------------------------------------- #
# vs-recent, confounder-symmetric (the #673 correctness fix)                   #
# --------------------------------------------------------------------------- #
def test_vs_recent_reads_harder_on_a_clean_hard_run_over_an_easy_norm():
    easy_norm = IntensityBandShare(easy_pct=100.0, moderate_pct=0.0, hard_pct=0.0)
    read = build_intensity_read(
        this_session=_session(band="hard", hr_confounded=False),
        distribution_adjusted=easy_norm,
        perceived_effort=_pe(),
        calibration_hr_drift=_calibrated_drift(),
        confounders=[],
    )
    assert read.vs_recent == "harder"


def test_vs_recent_exculpates_this_runs_own_band_when_confounded():
    # Same easy-dominant recent norm, same hard band — but a confounder fired on THIS run,
    # so its band is exculpated (like the recent baseline already is) and it no longer reads
    # "harder than recent" merely because heat inflated today's band.
    easy_norm = IntensityBandShare(easy_pct=100.0, moderate_pct=0.0, hard_pct=0.0)
    read = build_intensity_read(
        this_session=_session(band="hard", hr_confounded=True),
        distribution_adjusted=easy_norm,
        perceived_effort=_pe(),
        calibration_hr_drift=_calibrated_drift(),
        confounders=["heat"],
    )
    assert read.vs_recent == "in_line"


def test_vs_recent_no_norm_without_a_recent_distribution():
    read = build_intensity_read(
        this_session=_session(band="hard"),
        distribution_adjusted=None,
        perceived_effort=_pe(),
        calibration_hr_drift=_calibrated_drift(),
        confounders=[],
    )
    assert read.vs_recent == "no_norm"


# --------------------------------------------------------------------------- #
# build_intensity_mix                                                          #
# --------------------------------------------------------------------------- #
def _intensity_ctx(has_distribution=True, adjusted=IntensityBandShare(easy_pct=55, moderate_pct=20, hard_pct=25)):
    return IntensityContext(
        this_session=_session(),
        window_days=28,
        session_count=9,
        distribution=IntensityBandShare(easy_pct=45, moderate_pct=20, hard_pct=35),
        distribution_adjusted=adjusted if has_distribution else None,
        confounded_session_count=1,
        this_run_vs_recent="harder",
        trend_direction="harder",
        has_distribution=has_distribution,
    )


def test_intensity_mix_uses_the_adjusted_distribution():
    mix = build_intensity_mix(_intensity_ctx())
    assert mix is not None
    assert mix.window_days == 28
    assert mix.sessions == 9
    assert mix.distribution.hard_pct == 25  # the confounder-exculpated share
    assert mix.trend == "harder"


def test_intensity_mix_none_when_history_is_thin():
    assert build_intensity_mix(_intensity_ctx(has_distribution=False)) is None
    assert build_intensity_mix(None) is None
