"""Tests for the M6 perceived-effort signal: RPE-vs-HR divergence, confounder
weighting, and pain-score trend.

Ground truth = hand-constructed RPE/effort pairs with known gaps (the brief's
TDD oracle). The canonical case is a run the runner reported as hard (high RPE)
while HR put it in the easy band (heat-suppressed): divergence should read
"felt harder" and, with a confounder fired, weighting should favour RPE.
"""

from app.services.coach.perceived_effort import (
    MIN_PAIN_SAMPLES,
    build_perceived_effort,
    compute_divergence,
    compute_pain_trend,
    recommend_weighting,
)


class TestComputeDivergence:
    def test_felt_harder_than_hr(self):
        # RPE 8 (band 4) vs measured "easy" (band 2) -> +2, felt harder.
        d = compute_divergence(rpe=8, effort_axis="easy")
        assert d["divergence"] == 2
        assert d["direction"] == "felt_harder"

    def test_felt_easier_than_hr(self):
        d = compute_divergence(rpe=3, effort_axis="hard")
        assert d["divergence"] == -3
        assert d["direction"] == "felt_easier"

    def test_aligned(self):
        d = compute_divergence(rpe=5, effort_axis="moderate")
        assert d["divergence"] == 0
        assert d["direction"] == "aligned"

    def test_one_band_gap_is_aligned_noise_floor(self):
        # RPE 7 (band 4) vs "moderate" (band 3) -> +1, within bucketing noise.
        d = compute_divergence(rpe=7, effort_axis="moderate")
        assert d["divergence"] == 1
        assert d["direction"] == "aligned"

    def test_none_when_no_rpe(self):
        assert compute_divergence(rpe=None, effort_axis="easy") is None

    def test_none_when_no_effort_axis(self):
        assert compute_divergence(rpe=7, effort_axis=None) is None


class TestRecommendWeighting:
    def test_hr_only_when_no_rpe(self):
        assert recommend_weighting(rpe=None, divergence_direction=None) == "hr_only"

    def test_lead_with_felt_when_felt_harder(self):
        # The runner felt it much harder than HR showed: lead with the felt read.
        assert recommend_weighting(rpe=8, divergence_direction="felt_harder") == "lead_with_felt"

    def test_dont_dismiss_hr_when_felt_easier(self):
        # The mirror (#654): felt easy but real HR work — hold both, don't call it easy.
        assert recommend_weighting(rpe=3, divergence_direction="felt_easier") == "dont_dismiss_hr"

    def test_balanced_when_aligned(self):
        assert recommend_weighting(rpe=5, divergence_direction="aligned") == "balanced"

    def test_balanced_when_direction_unknown(self):
        # No effort axis to compare against -> no divergence direction -> balanced.
        assert recommend_weighting(rpe=5, divergence_direction=None) == "balanced"


class TestComputePainTrend:
    def test_none_when_no_samples(self):
        assert compute_pain_trend([]) is None

    def test_abstains_below_threshold(self):
        trend = compute_pain_trend([2, 3, 4])  # 3 < MIN_PAIN_SAMPLES
        assert trend["abstained"] is True
        assert trend["sample_count"] == 3
        assert "direction" not in trend

    def test_declining_when_pain_worsens(self):
        # rising pain over >= MIN_PAIN_SAMPLES sessions: lower-is-better -> declining.
        trend = compute_pain_trend([1, 2, 4, 6, 7])
        assert trend["abstained"] is False
        assert trend["sample_count"] == 5
        assert trend["direction"] == "declining"

    def test_improving_when_pain_eases(self):
        trend = compute_pain_trend([7, 5, 4, 2])
        assert trend["abstained"] is False
        assert trend["direction"] == "improving"

    def test_omits_percent_change_for_ordinal_pain(self):
        # magnitude_pct is meaningless for a 1-10 ordinal score (1->9 reads +800%).
        trend = compute_pain_trend([1, 2, 4, 9])
        assert "magnitude_pct" not in trend
        assert "slope" in trend and "direction" in trend


class TestBuildPerceivedEffort:
    def test_heat_suppressed_hard_run_leads_with_felt(self):
        # The runner felt it hard while HR read easy: lead with their felt read.
        ctx = build_perceived_effort(
            rpe=8,
            effort_axis="easy",
            effort_score=4.0,
            discount_signals={"likely_inflated_by": ["heat"], "confidence": "high"},
            pain_scores=[],
        )
        assert ctx.rpe == 8
        assert ctx.effort_axis == "easy"
        assert ctx.effort_score == 4.0
        assert ctx.divergence == 2
        assert ctx.divergence_direction == "felt_harder"
        assert ctx.hr_confounded is True
        assert ctx.recommended_weighting == "lead_with_felt"
        assert ctx.pain_trend is None

    def test_easy_rpe_with_real_hr_work_does_not_dismiss_hr(self):
        # #654: an easy RPE on a run with real Z3-Z4 HR time, even with an HR
        # confounder, must NOT resolve to 'trust RPE, genuinely easy'. It holds both.
        ctx = build_perceived_effort(
            rpe=3,
            effort_axis="tempo",  # HR-derived intensity band 4
            effort_score=45.0,
            discount_signals={"likely_inflated_by": ["stimulant_use"], "confidence": "high"},
            pain_scores=[],
        )
        assert ctx.rpe == 3
        assert ctx.divergence == -2  # RPE band 2 - HR band 4
        assert ctx.divergence_direction == "felt_easier"
        assert ctx.hr_confounded is True
        # The fix: not a wholesale substitution toward RPE, but hold both reads.
        assert ctx.recommended_weighting == "dont_dismiss_hr"

    def test_no_checkin_degrades_safely(self):
        ctx = build_perceived_effort(
            rpe=None, effort_axis="moderate", effort_score=10.0,
            discount_signals=None, pain_scores=[],
        )
        assert ctx.rpe is None
        assert ctx.divergence is None
        assert ctx.divergence_direction is None
        assert ctx.hr_confounded is False
        assert ctx.recommended_weighting == "hr_only"
        assert ctx.pain_trend is None

    def test_clean_run_balanced(self):
        ctx = build_perceived_effort(
            rpe=5, effort_axis="moderate", effort_score=8.0,
            discount_signals=None, pain_scores=[2, 2, 1, 1],
        )
        assert ctx.recommended_weighting == "balanced"
        assert ctx.divergence == 0
        assert ctx.pain_trend["abstained"] is False

    def test_empty_inflators_not_confounded(self):
        # discount_signals present but no concrete confound (temp-unknown case): the
        # confounder does not fire. Weighting is now direction-driven (#654), so this
        # felt-harder run leads with the felt read regardless of the (absent) confound.
        ctx = build_perceived_effort(
            rpe=8, effort_axis="easy", effort_score=4.0,
            discount_signals={"likely_inflated_by": [], "confidence": "low"},
            pain_scores=[],
        )
        assert ctx.hr_confounded is False
        assert ctx.divergence_direction == "felt_harder"
        assert ctx.recommended_weighting == "lead_with_felt"

    def test_min_pain_samples_is_four(self):
        assert MIN_PAIN_SAMPLES == 4
