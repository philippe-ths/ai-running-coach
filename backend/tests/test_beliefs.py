"""M8 belief-state write-back — pure-logic unit tests.

The pure layer: deterministically EXTRACT candidate belief deltas from one
report's signals (HR confounds, adherence outcomes); APPLY a delta to the
existing belief (create or reinforce, with contradiction resolution baked into
the count recompute); and decide TTL/decay + retrievability. All `now` values
are passed in so the logic is pure and testable. The DB layer (test_beliefs_db)
is thin on top of this.
"""

from datetime import datetime, timedelta, timezone

from app.services.coach.beliefs import (
    MIN_OBSERVATIONS_FOR_RETRIEVAL,
    BeliefState,
    apply_delta,
    confidence_for,
    extract_belief_deltas,
    is_decayed,
    is_retrievable,
    recency_days,
    ttl_days,
)

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

_HEAT_DISCOUNT = {
    "hr_drift_pct": 9.0,
    "likely_inflated_by": ["heat"],
    "temperature_c": 29.0,
    "confidence": "high",
    "interpretation": "HR drift likely inflated by heat.",
}


def _outcome(theme, label, overridden=False):
    return {"theme": theme, "label": label, "overridden": overridden}


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #

def test_extract_heat_confound():
    deltas = extract_belief_deltas(discount_signals=_HEAT_DISCOUNT, adherence_outcomes=[])
    assert len(deltas) == 1
    d = deltas[0]
    assert d.kind == "hr_confound"
    assert d.key == "heat"
    assert "heat" in d.statement.lower()


def test_no_confound_when_low_confidence():
    """Temp unknown -> discount_signals confidence 'low' -> never a durable belief
    (do not fabricate a heat sensitivity from an unmeasured confound)."""
    ds = {**_HEAT_DISCOUNT, "confidence": "low", "temperature_c": None}
    deltas = extract_belief_deltas(discount_signals=ds, adherence_outcomes=[])
    assert deltas == []


def test_no_confound_when_heat_absent():
    ds = {**_HEAT_DISCOUNT, "likely_inflated_by": ["terrain"]}
    deltas = extract_belief_deltas(discount_signals=ds, adherence_outcomes=[])
    assert [d for d in deltas if d.kind == "hr_confound"] == []


def test_no_confound_when_no_discount_signals():
    assert extract_belief_deltas(discount_signals=None, adherence_outcomes=[]) == []


def test_extract_adherence_outcome():
    deltas = extract_belief_deltas(
        discount_signals=None,
        adherence_outcomes=[_outcome("easy_discipline", "acted_on")],
    )
    assert len(deltas) == 1
    d = deltas[0]
    assert d.kind == "adherence_pattern"
    assert d.key == "easy_discipline"
    assert d.value["outcome"] == "acted_on"


def test_disputed_outcome_is_not_extracted():
    """A disputed outcome is the runner rejecting prior advice, not an adherence
    data point -> no belief."""
    deltas = extract_belief_deltas(
        discount_signals=None,
        adherence_outcomes=[_outcome("easy_discipline", "disputed", overridden=True)],
    )
    assert deltas == []


# --------------------------------------------------------------------------- #
# confidence + TTL + decay
# --------------------------------------------------------------------------- #

def test_confidence_for_thresholds():
    assert confidence_for(1) == "low"
    assert confidence_for(2) == "medium"
    assert confidence_for(3) == "high"
    assert confidence_for(9) == "high"


def test_ttl_days_by_kind():
    assert ttl_days("hr_confound") == 120
    assert ttl_days("adherence_pattern") == 90
    assert ttl_days("transient") == 14
    assert ttl_days("unknown_kind") == 90  # default


def test_is_decayed():
    fresh = NOW - timedelta(days=30)
    stale = NOW - timedelta(days=200)
    assert is_decayed("hr_confound", fresh, NOW) is False
    assert is_decayed("hr_confound", stale, NOW) is True
    # transient decays fast
    assert is_decayed("transient", NOW - timedelta(days=20), NOW) is True


def test_recency_days():
    assert recency_days(NOW - timedelta(days=5), NOW) == 5


# --------------------------------------------------------------------------- #
# apply_delta: create + reinforce + contradiction resolution
# --------------------------------------------------------------------------- #

def _confound_delta():
    return extract_belief_deltas(discount_signals=_HEAT_DISCOUNT, adherence_outcomes=[])[0]


def test_apply_delta_creates_when_absent():
    state = apply_delta(None, _confound_delta(), NOW)
    assert state.kind == "hr_confound"
    assert state.key == "heat"
    assert state.observation_count == 1
    assert state.confidence == "low"  # a single observation is not yet a belief
    assert state.first_observed_at == NOW
    assert state.last_reinforced_at == NOW


def test_apply_delta_reinforces_confound():
    s1 = apply_delta(None, _confound_delta(), NOW)
    later = NOW + timedelta(days=10)
    s2 = apply_delta(s1, _confound_delta(), later)
    assert s2.observation_count == 2
    assert s2.confidence == "medium"
    assert s2.last_reinforced_at == later
    assert s2.first_observed_at == NOW  # first observation preserved


def _adh_delta(label):
    return extract_belief_deltas(
        discount_signals=None, adherence_outcomes=[_outcome("easy_discipline", label)]
    )[0]


def test_adherence_counts_accumulate():
    s = apply_delta(None, _adh_delta("acted_on"), NOW)
    s = apply_delta(s, _adh_delta("acted_on"), NOW)
    s = apply_delta(s, _adh_delta("ignored"), NOW)
    assert s.observation_count == 3
    assert s.value["acted"] == 2
    assert s.value["total"] == 3


def test_adherence_contradiction_resolves_in_place_not_two_rows():
    """Opposing observations evolve ONE belief's direction (contradiction
    resolved), they do not stack two contradictory beliefs."""
    s = apply_delta(None, _adh_delta("acted_on"), NOW)
    for _ in range(3):
        s = apply_delta(s, _adh_delta("ignored"), NOW)
    # mostly ignored now -> statement reflects NON-adherence, single belief
    assert s.value["acted"] == 1
    assert s.value["total"] == 4
    assert "not" in s.value["statement"].lower() or "mixed" in s.value["statement"].lower()


def test_adherence_high_adherence_statement():
    s = None
    for _ in range(4):
        s = apply_delta(s, _adh_delta("acted_on"), NOW)
    assert s.value["acted"] == 4
    assert s.confidence == "high"
    assert "easy" in s.value["statement"].lower()


def test_adherence_rolling_window_lets_recent_change_move_ratio():
    """A long run of non-adherence followed by genuine reform must move the belief
    OFF 'tends not to act' (the all-time tally would keep it buried)."""
    s = None
    for _ in range(8):
        s = apply_delta(s, _adh_delta("ignored"), NOW)
    assert "not" in s.value["statement"].lower()  # buried under ignores
    for _ in range(4):
        s = apply_delta(s, _adh_delta("acted_on"), NOW)
    # window decayed the old ignores; recent reform is now visible
    assert "not" not in s.value["statement"].lower()
    assert s.value["total"] <= 8  # rolling window bound


# --------------------------------------------------------------------------- #
# retrievability gate
# --------------------------------------------------------------------------- #

def _state(observation_count=2, kind="hr_confound", age_days=0):
    return BeliefState(
        kind=kind,
        key="heat",
        value={"statement": "x"},
        confidence=confidence_for(observation_count),
        observation_count=observation_count,
        first_observed_at=NOW - timedelta(days=age_days),
        last_reinforced_at=NOW - timedelta(days=age_days),
    )


def test_retrievable_requires_min_observations():
    assert is_retrievable(_state(observation_count=1), NOW) is False
    assert is_retrievable(_state(observation_count=MIN_OBSERVATIONS_FOR_RETRIEVAL), NOW) is True


def test_decayed_belief_not_retrievable():
    assert is_retrievable(_state(observation_count=3, age_days=200), NOW) is False
