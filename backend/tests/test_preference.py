"""M10 per-runner preference model — pure-logic tests.

build_preference_profile derives, from the accumulated adherence beliefs (M8
adherence_pattern rows carrying acted/total per theme), which advice themes the
runner acts on / ignores / is mixed on. It is the deterministic, no-fine-tune
"reward signal" the coach uses to rerank framing. A theme needs enough
observations before a decisive tendency is claimed.
"""

from app.services.coach.preference import (
    MIN_TOTAL_FOR_TENDENCY,
    build_preference_profile,
)


def _belief(theme, acted, total):
    """Mimics an M8 adherence_pattern belief row's relevant shape."""
    return {"key": theme, "value": {"acted": acted, "total": total}}


def test_acts_on_when_high_ratio_and_enough_history():
    prof = build_preference_profile([_belief("easy_discipline", 5, 6)])
    assert len(prof.themes) == 1
    t = prof.themes[0]
    assert t.theme == "easy_discipline"
    assert t.tendency == "acts_on"
    assert t.acted == 5
    assert t.total == 6


def test_ignores_when_low_ratio():
    prof = build_preference_profile([_belief("add_quality", 0, 4)])
    assert prof.themes[0].tendency == "ignores"


def test_mixed_in_between():
    prof = build_preference_profile([_belief("add_long_run", 2, 4)])
    assert prof.themes[0].tendency == "mixed"


def test_thin_history_excluded():
    """A theme without enough observations carries no tendency at all."""
    prof = build_preference_profile([_belief("easy_discipline", 1, 2)])
    assert prof.themes == []


def test_min_total_constant():
    assert MIN_TOTAL_FOR_TENDENCY == 3


def test_multiple_themes_ranked_acts_on_first():
    prof = build_preference_profile([
        _belief("add_quality", 0, 4),       # ignores
        _belief("easy_discipline", 6, 6),   # acts_on
        _belief("add_long_run", 2, 4),      # mixed
    ])
    tendencies = [t.tendency for t in prof.themes]
    # acts_on ranked ahead of mixed ahead of ignores
    assert tendencies == ["acts_on", "mixed", "ignores"]


def test_empty_beliefs_empty_profile():
    assert build_preference_profile([]).themes == []


def test_tolerates_object_beliefs_and_missing_value():
    class _Row:
        def __init__(self, key, value):
            self.key = key
            self.value = value
    prof = build_preference_profile([
        _Row("easy_discipline", {"acted": 4, "total": 5}),
        _Row("add_quality", {}),  # malformed/missing counts -> skipped
    ])
    assert [t.theme for t in prof.themes] == ["easy_discipline"]


def test_boundary_ratios():
    # exactly 0.7 -> acts_on; exactly 0.3 -> ignores
    assert build_preference_profile([_belief("t", 7, 10)]).themes[0].tendency == "acts_on"
    assert build_preference_profile([_belief("t", 3, 10)]).themes[0].tendency == "ignores"
    assert build_preference_profile([_belief("t", 2, 3)]).themes[0].tendency == "mixed"


def test_non_dict_value_does_not_crash():
    for bad in ([1, 2], "oops", 5):
        assert build_preference_profile([{"key": "t", "value": bad}]).themes == []
