"""The deterministic, block-aware receipt core (#296).

Pins the situation taxonomy, the fact-slot fill, friendly-type humanising,
deterministic variant choice, and the voiced-set-overrides-with-house-floor
fallback. Pure module — no DB/LLM.
"""

import pytest

from app.services.coach import receipt as R


# --- situation taxonomy + facts ----------------------------------------------


def test_first_situation_solo_block():
    sit, facts = R.derive_facts(["Run"], 0)
    assert sit == R.SITUATION_FIRST
    assert facts.type == "run"
    assert facts.prev_type is None
    assert facts.ordinal == 1
    assert facts.count == 1
    assert facts.sequence == "run"


def test_second_situation_names_prior_type():
    sit, facts = R.derive_facts(["Walk", "Run"], 1)
    assert sit == R.SITUATION_SECOND
    assert facts.type == "run"
    assert facts.prev_type == "walk"
    assert facts.ordinal == 2
    assert facts.count == 2
    assert facts.sequence == "walk → run"


def test_multi_situation_renders_full_sequence():
    sit, facts = R.derive_facts(["Walk", "Run", "Ride"], 2)
    assert sit == R.SITUATION_MULTI
    assert facts.type == "ride"
    assert facts.count == 3
    assert facts.sequence == "walk → run → ride"


def test_situation_keys_on_subject_ordinal_not_block_size():
    # Out-of-order: a 2nd-arriving activity in a 3-member block still frames as
    # "second" off its own ordinal, not "multi".
    sit, facts = R.derive_facts(["Walk", "Run", "Ride"], 1)
    assert sit == R.SITUATION_SECOND
    assert facts.prev_type == "walk"


def test_friendly_type_maps_known_and_degrades_unknown():
    assert R.friendly_type("EBikeRide") == "ride"
    assert R.friendly_type("WeightTraining") == "strength session"
    assert R.friendly_type("Kitesurf") == "kitesurf"  # unknown -> lowercased raw
    assert R.friendly_type(None) == "activity"


def test_derive_facts_rejects_empty_block():
    with pytest.raises(ValueError):
        R.derive_facts([], 0)


# --- slot fill ----------------------------------------------------------------


def test_fill_template_fills_all_slots():
    _, facts = R.derive_facts(["Walk", "Run", "Ride"], 2)
    out = R.fill_template(
        "{ordinal} today ({sequence}) — the {type} after the {prev_type}, {count} total",
        facts,
    )
    assert out == "third today (walk → run → ride) — the ride after the run, 3 total"


def test_fill_template_tolerates_missing_or_null_slot():
    # A template referencing prev_type on a first-activity receipt must not crash;
    # the null slot renders empty while the surrounding literal text stays.
    _, facts = R.derive_facts(["Run"], 0)
    assert R.fill_template("after the {prev_type} mark", facts) == "after the  mark"
    # An unknown slot renders empty rather than raising.
    assert R.fill_template("x{nope}y", facts) == "xy"


# --- variant choice (deterministic) ------------------------------------------


def test_choose_variant_is_deterministic_by_seed():
    variants = ["a", "b", "c"]
    assert R.choose_variant(variants, 0) == "a"
    assert R.choose_variant(variants, 4) == "b"
    assert R.choose_variant(variants, 4) == "b"  # stable


# --- build_receipt: house floor + voiced override ----------------------------


def test_build_receipt_uses_house_floor_without_templates():
    r = R.build_receipt(["Run"], 0, seed=0)
    assert r.situation == R.SITUATION_FIRST
    assert "run" in r.text
    assert r.text  # non-empty floor


def test_build_receipt_prefers_voiced_set_for_situation():
    voiced = {R.SITUATION_FIRST: ["Boom — {type} banked. Talk to me, how'd it go?"]}
    r = R.build_receipt(["Run"], 0, seed=0, templates=voiced)
    assert r.text == "Boom — run banked. Talk to me, how'd it go?"


def test_build_receipt_falls_back_to_floor_for_missing_situation():
    # Voiced set only covers "first"; a second-activity receipt falls to the floor.
    voiced = {R.SITUATION_FIRST: ["voiced first"]}
    r = R.build_receipt(["Walk", "Run"], 1, seed=0, templates=voiced)
    assert r.situation == R.SITUATION_SECOND
    assert r.text in [R.fill_template(t, r.facts) for t in R.HOUSE_DEFAULT_TEMPLATES[R.SITUATION_SECOND]]


def test_build_receipt_ignores_empty_voiced_variants():
    voiced = {R.SITUATION_FIRST: ["", "   "]}  # malformed: all blank
    r = R.build_receipt(["Run"], 0, seed=0, templates=voiced)
    # Falls back to the floor rather than rendering an empty receipt.
    assert r.text.strip()
