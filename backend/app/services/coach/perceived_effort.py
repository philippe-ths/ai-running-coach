"""M6 perceived-effort signal: the gap between what the runner felt (RPE) and
what the body showed (HR-derived intensity), plus a pain-score trend.

This app uniquely holds both sides of the perception-vs-physiology gap, and that
gap is coaching signal (ADR-0007's "the gap is the signal", extended from
intent-vs-execution to perception-vs-physiology). When an HR confounder fired
(N4 discount_signals), RPE is the more trustworthy intensity read because it
survives HR distortion, so the coach should weight it above HR.

Design note: divergence is computed against the HR-derived `effort` axis
(intensity), NOT `effort_score`. `effort_score` is a TRIMP-like cumulative load
(minutes x HR-ratio^3) that conflates duration with intensity, so it is the
wrong comparator for "did it feel as hard as the HR says"; it is still carried
raw for the LLM. Both RPE and the effort axis are mapped onto a coarse 1-5
ordinal band so the comparison is like-for-like without fabricating a precise
RPE<->HR numeric mapping that has no ground-truth source.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.schemas.coach_context import PerceivedEffortContext
from app.services.analysis.baseline import compute_trend

# Pain check-ins are sparse; abstain from a trend until there are enough
# like-for-like samples, mirroring the M2 baseline abstention discipline.
MIN_PAIN_SAMPLES = 4

# Borg CR10 RPE (1-10) bucketed to a 1-5 ordinal intensity band.
def _rpe_band(rpe: int) -> int:
    return min(5, max(1, (rpe + 1) // 2))


# The HR-derived effort axis mapped to the same 1-5 ordinal band.
_EFFORT_AXIS_BAND = {
    "recovery": 1,
    "easy": 2,
    "moderate": 3,
    "tempo": 4,
    "hard": 5,
}


def compute_divergence(rpe: Optional[int], effort_axis: Optional[str]) -> Optional[Dict[str, Any]]:
    """Signed perceived-vs-measured intensity gap, or None when either side is
    missing. Positive = the runner felt it harder than HR showed (the
    heat-suppressed case); negative = felt easier."""
    if rpe is None or effort_axis is None:
        return None
    measured_band = _EFFORT_AXIS_BAND.get(effort_axis)
    if measured_band is None:
        return None
    divergence = _rpe_band(rpe) - measured_band
    # A 1-band gap is within RPE self-report + bucketing noise (RPE 6 vs 7 straddle
    # a band edge), so only a >= 2-band gap counts as a real perception-physiology split.
    if divergence >= 2:
        direction = "felt_harder"
    elif divergence <= -2:
        direction = "felt_easier"
    else:
        direction = "aligned"
    return {"divergence": divergence, "direction": direction}


def recommend_weighting(rpe: Optional[int], hr_confounded: bool) -> str:
    """Which intensity signal the report should lead with.

    No RPE -> HR is all we have. A confounder fired and we have RPE -> weight RPE
    over HR (it survives HR distortion). Otherwise both agree enough to balance."""
    if rpe is None:
        return "hr_only"
    if hr_confounded:
        return "rpe_over_hr"
    return "balanced"


def compute_pain_trend(pain_scores: List[int]) -> Optional[Dict[str, Any]]:
    """Trend over chronological (oldest-first) pain scores for ONE pain location.
    None when there are no samples; an abstention marker below MIN_PAIN_SAMPLES;
    otherwise a trend with direction + slope (lower pain is better). Never a
    diagnosis, just the shape of the curve.

    Deliberately omits compute_trend's `magnitude_pct`: pain is an ordinal 1-10
    score that starts near 1, so a percent change (e.g. 1 -> 9 reads as +800%) is
    meaningless and alarming. Direction and slope are the honest signals."""
    scores = [s for s in pain_scores if s is not None]
    n = len(scores)
    if n == 0:
        return None
    if n < MIN_PAIN_SAMPLES:
        return {"abstained": True, "sample_count": n}
    trend = compute_trend(scores, higher_is_better=False) or {}
    return {
        "abstained": False,
        "sample_count": n,
        "direction": trend.get("direction"),
        "slope": trend.get("slope"),
    }


def build_perceived_effort(
    *,
    rpe: Optional[int],
    effort_axis: Optional[str],
    effort_score: Optional[float],
    discount_signals: Optional[Dict[str, Any]],
    pain_scores: List[int],
) -> PerceivedEffortContext:
    """Assemble the perceived-effort section. Pure; all inputs are already
    gathered. Degrades cleanly when the CheckIn (and so RPE/pain) is absent."""
    hr_confounded = bool(discount_signals and discount_signals.get("likely_inflated_by"))
    divergence = compute_divergence(rpe, effort_axis)
    return PerceivedEffortContext(
        rpe=rpe,
        effort_axis=effort_axis,
        effort_score=effort_score,
        divergence=divergence["divergence"] if divergence else None,
        divergence_direction=divergence["direction"] if divergence else None,
        hr_confounded=hr_confounded,
        recommended_weighting=recommend_weighting(rpe, hr_confounded),
        pain_trend=compute_pain_trend(pain_scores),
    )
