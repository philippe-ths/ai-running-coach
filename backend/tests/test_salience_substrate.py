"""Unit tests for the A4 salience substrate: novelty + safety override.

Both are pure deterministic functions (no DB). Novelty is first-of-its-kind over
the runner's classification-axis history; the safety override reuses the M9 referral
red-flag predicate to force a fuller turn. The context-assembly side (gathering the
DB rows into these functions) is tested in the context tests.
"""

from app.services.analysis.novelty import (
    MIN_HISTORY_FOR_NOVELTY,
    AxisSnapshot,
    compute_novelty,
)
from app.services.coach.salience import compute_safety_override


def _easy() -> AxisSnapshot:
    return AxisSnapshot(structure="continuous", duration_class="standard",
                        is_race=False, is_hilly=False)


def _history(n: int) -> list[AxisSnapshot]:
    return [_easy() for _ in range(n)]


class TestNovelty:
    def test_abstains_under_thin_history(self):
        # A cold-start runner: fewer than MIN_HISTORY priors -> no novelty signal,
        # even though this interval run is technically "first of its kind".
        current = AxisSnapshot(structure="intervals", duration_class="standard",
                               is_race=False, is_hilly=False)
        result = compute_novelty(current, _history(MIN_HISTORY_FOR_NOVELTY - 1))
        assert result["has_history"] is False
        assert result["first_of_kind"] == []

    def test_first_interval_session_flagged(self):
        current = AxisSnapshot(structure="intervals", duration_class="standard",
                               is_race=False, is_hilly=False)
        result = compute_novelty(current, _history(5))
        assert result["has_history"] is True
        assert "first_interval_session" in result["first_of_kind"]

    def test_not_novel_when_prior_interval_exists(self):
        current = AxisSnapshot(structure="intervals", duration_class="standard",
                               is_race=False, is_hilly=False)
        history = _history(4) + [
            AxisSnapshot(structure="intervals", duration_class="standard",
                         is_race=False, is_hilly=False)
        ]
        result = compute_novelty(current, history)
        assert "first_interval_session" not in result["first_of_kind"]

    def test_first_long_run_flagged(self):
        current = AxisSnapshot(structure="continuous", duration_class="long",
                               is_race=False, is_hilly=False)
        result = compute_novelty(current, _history(5))
        assert "first_long_run" in result["first_of_kind"]

    def test_first_race_flagged(self):
        current = AxisSnapshot(structure="continuous", duration_class="standard",
                               is_race=True, is_hilly=False)
        result = compute_novelty(current, _history(5))
        assert "first_race" in result["first_of_kind"]

    def test_first_hilly_run_flagged(self):
        current = AxisSnapshot(structure="continuous", duration_class="standard",
                               is_race=False, is_hilly=True)
        result = compute_novelty(current, _history(5))
        assert "first_hilly_run" in result["first_of_kind"]

    def test_unremarkable_run_no_novelty(self):
        result = compute_novelty(_easy(), _history(10))
        assert result["has_history"] is True
        assert result["first_of_kind"] == []

    def test_multiple_first_of_kind_together(self):
        # First long AND first hilly run on the same session.
        current = AxisSnapshot(structure="continuous", duration_class="long",
                               is_race=False, is_hilly=True)
        result = compute_novelty(current, _history(5))
        assert set(result["first_of_kind"]) == {"first_long_run", "first_hilly_run"}

    def test_novelty_ignores_load_axes(self):
        # Salience is not intensity/load: an easy run with rich history is never
        # novel just because (e.g.) it was the first run of some effort level —
        # the predicate set is structure/duration/race/terrain only.
        result = compute_novelty(_easy(), _history(20))
        assert result["first_of_kind"] == []


class TestSafetyOverride:
    def test_abstains_when_no_red_flag(self):
        result = compute_safety_override(flags=["high_drift"], pain_scores=[2, 3])
        assert result["force_fuller"] is False
        assert result["reasons"] == []

    def test_illness_flag_forces_fuller(self):
        result = compute_safety_override(
            flags=["illness_or_extreme_fatigue"], pain_scores=None
        )
        assert result["force_fuller"] is True
        assert result["reasons"] == ["multi_signal_fatigue"]

    def test_sustained_pain_forces_fuller(self):
        # Three notable pain readings (>= 4) is the sustained-pain referral pattern.
        result = compute_safety_override(flags=[], pain_scores=[5, 6, 4])
        assert result["force_fuller"] is True
        assert result["reasons"] == ["persistent_pain"]

    def test_single_painful_run_does_not_force(self):
        result = compute_safety_override(flags=[], pain_scores=[7])
        assert result["force_fuller"] is False

    def test_none_inputs_abstain(self):
        result = compute_safety_override(flags=None, pain_scores=None)
        assert result["force_fuller"] is False
        assert result["reasons"] == []
