"""Tests for the deterministic risk score computation."""

from app.services.processing.risk import compute_risk_score


class TestRiskScoreBasics:
    def test_no_flags_no_checkin_returns_green(self):
        result = compute_risk_score([], None, None)
        assert result["risk_level"] == "green"
        assert result["risk_score"] == 0
        assert result["risk_reasons"] == []

    def test_empty_inputs_returns_green(self):
        result = compute_risk_score([], {}, {})
        assert result["risk_level"] == "green"
        assert result["risk_score"] == 0

    def test_unknown_flags_ignored(self):
        result = compute_risk_score(["unknown_flag", "another_unknown"], None, None)
        assert result["risk_level"] == "green"
        assert result["risk_score"] == 0


class TestFlagPoints:
    def test_fatigue_possible_scores_1(self):
        result = compute_risk_score(["fatigue_possible"], None, None)
        assert result["risk_score"] == 1
        assert result["risk_level"] == "green"
        assert "fatigue_possible (+1)" in result["risk_reasons"]

    def test_pain_reported_scores_2_amber(self):
        result = compute_risk_score(["pain_reported"], None, None)
        assert result["risk_score"] == 2
        assert result["risk_level"] == "amber"

    def test_load_spike_scores_3_amber(self):
        result = compute_risk_score(["load_spike"], None, None)
        assert result["risk_score"] == 3
        assert result["risk_level"] == "amber"

    def test_pain_severe_scores_4_red(self):
        result = compute_risk_score(["pain_severe"], None, None)
        assert result["risk_score"] == 4
        assert result["risk_level"] == "red"

    def test_illness_scores_4_red(self):
        result = compute_risk_score(["illness_or_extreme_fatigue"], None, None)
        assert result["risk_score"] == 4
        assert result["risk_level"] == "red"

    def test_multiple_flags_additive(self):
        result = compute_risk_score(["fatigue_possible", "pain_reported"], None, None)
        assert result["risk_score"] == 3
        assert result["risk_level"] == "amber"
        assert len(result["risk_reasons"]) == 2

    def test_load_spike_plus_pain_is_red(self):
        result = compute_risk_score(["load_spike", "pain_reported"], None, None)
        assert result["risk_score"] == 5
        assert result["risk_level"] == "red"


class TestCheckInCombo:
    def test_poor_sleep_high_rpe_adds_2(self):
        check_in = {"sleep_quality": 2, "rpe": 8}
        result = compute_risk_score([], check_in, None)
        assert result["risk_score"] == 2
        assert result["risk_level"] == "amber"
        assert "poor_sleep_high_rpe (+2)" in result["risk_reasons"]

    def test_good_sleep_high_rpe_no_extra(self):
        check_in = {"sleep_quality": 4, "rpe": 9}
        result = compute_risk_score([], check_in, None)
        assert result["risk_score"] == 0
        assert result["risk_level"] == "green"

    def test_poor_sleep_low_rpe_no_extra(self):
        check_in = {"sleep_quality": 1, "rpe": 5}
        result = compute_risk_score([], check_in, None)
        assert result["risk_score"] == 0

    def test_null_sleep_or_rpe_no_extra(self):
        result = compute_risk_score([], {"sleep_quality": None, "rpe": 9}, None)
        assert result["risk_score"] == 0

    def test_checkin_combo_stacks_with_flags(self):
        check_in = {"sleep_quality": 1, "rpe": 9}
        result = compute_risk_score(["pain_reported"], check_in, None)
        assert result["risk_score"] == 4
        assert result["risk_level"] == "red"


class TestTrainingContext:
    def test_consecutive_hard_sessions_adds_1(self):
        ctx = {"hard_sessions_this_week": 3, "days_since_last_hard": 1}
        result = compute_risk_score([], None, ctx)
        assert result["risk_score"] == 1
        assert result["risk_level"] == "green"
        assert "consecutive_hard_sessions (+1)" in result["risk_reasons"]

    def test_hard_sessions_but_rest_days_no_extra(self):
        ctx = {"hard_sessions_this_week": 3, "days_since_last_hard": 5}
        result = compute_risk_score([], None, ctx)
        assert result["risk_score"] == 0

    def test_one_hard_session_no_extra(self):
        ctx = {"hard_sessions_this_week": 1, "days_since_last_hard": 1}
        result = compute_risk_score([], None, ctx)
        assert result["risk_score"] == 0

    def test_no_days_since_last_hard_no_extra(self):
        ctx = {"hard_sessions_this_week": 3, "days_since_last_hard": None}
        result = compute_risk_score([], None, ctx)
        assert result["risk_score"] == 0


class TestRiskLevelBoundaries:
    def test_score_0_is_green(self):
        result = compute_risk_score([], None, None)
        assert result["risk_level"] == "green"

    def test_score_1_is_green(self):
        result = compute_risk_score(["fatigue_possible"], None, None)
        assert result["risk_level"] == "green"

    def test_score_2_is_amber(self):
        result = compute_risk_score(["pain_reported"], None, None)
        assert result["risk_level"] == "amber"

    def test_score_3_is_amber(self):
        result = compute_risk_score(["load_spike"], None, None)
        assert result["risk_level"] == "amber"

    def test_score_4_is_red(self):
        result = compute_risk_score(["pain_severe"], None, None)
        assert result["risk_level"] == "red"

    def test_combined_all_sources_red(self):
        """Flags + check-in + training context can all stack to red."""
        check_in = {"sleep_quality": 1, "rpe": 9}
        ctx = {"hard_sessions_this_week": 2, "days_since_last_hard": 1}
        result = compute_risk_score(["fatigue_possible"], check_in, ctx)
        # 1 (fatigue) + 2 (poor_sleep_high_rpe) + 1 (consecutive_hard) = 4
        assert result["risk_score"] == 4
        assert result["risk_level"] == "red"
        assert len(result["risk_reasons"]) == 3
