"""ADR 0026 Slice 3 (#673): the merged this-run intensity read + the recent mix.

"How hard was this run, really?" was answered by four sibling pack sections, each in
its own units, inviting the model to narrate each in isolation (the #636 misread).
This module is the pure reshape that collapses the THIS-RUN lenses into one
`this_run.intensity_read` and splits the RECENT distribution/trend out to
`right_now.intensity_mix`:

  - `build_intensity_read` composes the already-built pieces — the HR intensity band +
    within-run split (from the #578 intensity computation's `this_session`), the RPE-vs-HR
    read (M6 `perceived_effort`), the drift-vs-your-typical comparison (M9
    `calibration.hr_drift`), and the confounders that fired (N4 `discount_signals`) — into
    one read. The confounder LEADS the read and links to the drift, so an elevated drift on
    a hot day is framed as likely-inflated rather than fatigue. `vs_recent` is recomputed
    here confounder-SYMMETRICALLY (this run's own band is exculpated the same way the recent
    baseline already is), fixing an asymmetry in the standalone `intensity` section without
    touching that section (so every prior prompt stays byte-stable).

  - `build_intensity_mix` reshapes the recent confounder-exculpated distribution + trend
    into the compact `right_now` read, or None when history is too thin for a distribution.

Pure functions over already-built section objects (no DB, no LLM); the wiring lives in
`context.py`.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.schemas.coach_context import (
    IntensityBandShare,
    IntensityContext,
    IntensityDriftRead,
    IntensityFeltVsMeasured,
    IntensityMix,
    IntensityRead,
    IntensitySession,
    PerceivedEffortContext,
)
from app.services.coach.intensity import _BAND_ORDINAL, _VS_RECENT_DEADBAND


def _felt_vs_measured(
    perceived_effort: Optional[PerceivedEffortContext],
) -> Optional[IntensityFeltVsMeasured]:
    """The RPE-vs-HR read, or None when there is no check-in to compare (no RPE, so the
    weighting is `hr_only` and the divergence is null)."""
    if perceived_effort is None:
        return None
    if (
        perceived_effort.recommended_weighting == "hr_only"
        or perceived_effort.divergence_direction is None
    ):
        return None
    return IntensityFeltVsMeasured(
        read=perceived_effort.divergence_direction,
        trust=perceived_effort.recommended_weighting,
    )


def _drift_vs_typical(
    hr_drift: Optional[Dict[str, Any]], confounders: List[str]
) -> Optional[IntensityDriftRead]:
    """This run's drift against the runner's own typical (M9), or None when no drift was
    measured. When the personal norm has too few samples, `typical_pct` is null and the
    read compares against the general ~5% guideline. `confounded` is set only when a
    confounder fired, so an elevated drift reads as likely-inflated, not fatigue."""
    if not hr_drift:
        return None
    observed = hr_drift.get("observed_drift_pct")
    if observed is None:
        return None
    calibrated = bool(hr_drift.get("calibrated"))
    if calibrated:
        typical = hr_drift.get("expected_drift_pct")
        read = hr_drift.get("comparison") or "in_line"
    else:
        typical = None
        threshold = hr_drift.get("heuristic_threshold_pct")
        read = "above" if (threshold is not None and observed > threshold) else "in_line"
    return IntensityDriftRead(
        observed_pct=observed,
        typical_pct=typical,
        read=read,
        personal_norm=calibrated,
        confounded=True if confounders else None,
        basis=hr_drift.get("basis", ""),
    )


def _vs_recent(
    band: Optional[str],
    hr_confounded: bool,
    distribution_adjusted: Optional[IntensityBandShare],
) -> str:
    """This run's intensity against the runner's recent 4-week norm, confounder-SYMMETRIC.

    The recent distribution (`distribution_adjusted`) already exculpates confounded
    sessions; this run's own band is exculpated the same way here, so a hot run is not
    read as "harder than recent" merely because heat inflated today's band while the
    comparison set was heat-corrected. Abstains (`no_norm`) when the run has no band or
    the recent distribution is too thin."""
    if band is None or distribution_adjusted is None:
        return "no_norm"
    effective = "easy" if (hr_confounded and band != "easy") else band
    mean_ordinal = (
        distribution_adjusted.easy_pct * _BAND_ORDINAL["easy"]
        + distribution_adjusted.moderate_pct * _BAND_ORDINAL["moderate"]
        + distribution_adjusted.hard_pct * _BAND_ORDINAL["hard"]
    ) / 100.0
    delta = _BAND_ORDINAL[effective] - mean_ordinal
    if delta > _VS_RECENT_DEADBAND:
        return "harder"
    if delta < -_VS_RECENT_DEADBAND:
        return "easier"
    return "in_line"


def build_intensity_read(
    *,
    this_session: IntensitySession,
    distribution_adjusted: Optional[IntensityBandShare],
    perceived_effort: Optional[PerceivedEffortContext],
    calibration_hr_drift: Optional[Dict[str, Any]],
    confounders: List[str],
) -> IntensityRead:
    """Assemble the single this-run intensity read from the already-built pieces. Pure;
    every input is already gathered. The sparse fields degrade to None/empty and are
    dropped from serialization, so a bare indoor run stays lean."""
    return IntensityRead(
        confounders=list(confounders),
        band=this_session.band,
        within_run=this_session.within_run,
        felt_vs_measured=_felt_vs_measured(perceived_effort),
        drift_vs_typical=_drift_vs_typical(calibration_hr_drift, confounders),
        vs_recent=_vs_recent(
            this_session.band, this_session.hr_confounded, distribution_adjusted
        ),
    )


def build_intensity_mix(intensity: Optional[IntensityContext]) -> Optional[IntensityMix]:
    """The recent intensity distribution + trend, or None when history is too thin for a
    distribution (nothing meaningful to say about the recent mix). Uses the
    confounder-exculpated distribution as the honest recent read."""
    if intensity is None or not intensity.has_distribution:
        return None
    if intensity.distribution_adjusted is None:
        return None
    return IntensityMix(
        window_days=intensity.window_days,
        sessions=intensity.session_count,
        distribution=intensity.distribution_adjusted,
        trend=intensity.trend_direction,
    )
