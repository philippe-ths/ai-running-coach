"""Offline voiced-receipt-template generation (#296, the trust-boundary slice).

Pins the containment + voice-floor discipline: structured-output-only coercion,
per-variant slot whitelisting (no fabricated facts) and medical-scope filtering
(voice flexes delivery only), the voice fingerprint (regen detection), and the
store/idempotency/default-clears behaviour of refresh_receipt_templates.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.models import User
from app.models.coaching_relationship import CoachingRelationship
from app.services.coach import receipt_voice as RV
from app.services.coach.llm import Usage
from app.services.coach.voice import resolve_voice


def _client(payload):
    c = AsyncMock()
    c.model = "claude-haiku-4-5"
    c.generate_structured = AsyncMock(return_value=payload)
    c.generate_structured_with_usage = AsyncMock(
        return_value=(payload, Usage(input_tokens=100, output_tokens=50))
    )
    return c


def _declared_voice():
    # A non-default voice (a preset + a dial nudge + freetext).
    rel = SimpleNamespace(
        voice_preset="roast", voice_warmth=3, voice_humor=5,
        voice_force=5, voice_energy=5, voice_freetext="be cheeky",
    )
    return resolve_voice(rel)


def _seed_relationship(db, *, preset=None, freetext=None) -> CoachingRelationship:
    user = User(email=f"u-{uuid4()}@example.com")
    db.add(user)
    db.commit()
    rel = CoachingRelationship(user_id=user.id, voice_preset=preset, voice_freetext=freetext)
    db.add(rel)
    db.commit()
    db.refresh(rel)
    return rel


# --- generation + containment -------------------------------------------------


@pytest.mark.asyncio
async def test_generate_happy_path_returns_cleaned_set():
    payload = {
        "first": ["Boom, that {type}'s in. How'd it feel?"],
        "second": ["Two now: {prev_type} then {type}. How was it?"],
        "multi": ["{count} today ({sequence}). How'd the {type} feel?"],
    }
    out = await RV.generate_receipt_templates(_declared_voice(), client=_client(payload))
    assert set(out.keys()) == {"first", "second", "multi"}
    assert out["first"] == payload["first"]


@pytest.mark.asyncio
async def test_generate_drops_unknown_slot_and_medical_variants():
    payload = {
        "first": [
            "Nice {type}! How'd it feel?",                          # kept
            "Your HR drift was {drift}% today — how'd it feel?",    # unknown slot -> drop
            "Take 400mg ibuprofen for that niggle. How'd it feel?", # medical -> drop
        ],
        "second": [],
        "multi": [],
    }
    out = await RV.generate_receipt_templates(_declared_voice(), client=_client(payload))
    assert out["first"] == ["Nice {type}! How'd it feel?"]
    # situations with no surviving variant are omitted -> the house floor stands.
    assert "second" not in out and "multi" not in out


@pytest.mark.asyncio
async def test_generate_drops_overlong_variant():
    payload = {"first": ["x" * 5000 + " {type}"], "second": [], "multi": []}
    out = await RV.generate_receipt_templates(_declared_voice(), client=_client(payload))
    assert out == {}  # everything dropped -> floor everywhere


@pytest.mark.asyncio
async def test_generate_off_shape_output_fails_to_floor():
    # A rogue extra key fails strict coercion -> None (the floor stands), nothing stored.
    bad = {"first": ["ok {type}?"], "second": [], "multi": [], "evil": ["x"]}
    out = await RV.generate_receipt_templates(_declared_voice(), client=_client(bad))
    assert out is None


@pytest.mark.asyncio
async def test_generate_returns_none_without_api_key(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "")
    out = await RV.generate_receipt_templates(_declared_voice(), client=None)
    assert out is None


# --- fingerprint --------------------------------------------------------------


def test_fingerprint_default_is_sentinel():
    assert RV.voice_fingerprint(resolve_voice(None)) == "default"


def test_fingerprint_stable_and_voice_sensitive():
    v = _declared_voice()
    assert RV.voice_fingerprint(v) == RV.voice_fingerprint(v)
    other = resolve_voice(SimpleNamespace(
        voice_preset="sage", voice_warmth=4, voice_humor=2,
        voice_force=2, voice_energy=1, voice_freetext=None,
    ))
    assert RV.voice_fingerprint(v) != RV.voice_fingerprint(other)


# --- refresh_receipt_templates (store + idempotency + default) ----------------


@pytest.mark.asyncio
async def test_refresh_stores_and_is_idempotent(db):
    rel = _seed_relationship(db, preset="roast", freetext="be cheeky")
    payload = {
        "first": ["Boom {type}! How'd it feel?"],
        "second": ["{prev_type} then {type} — how was it?"],
        "multi": ["{count} today: {sequence}. How'd the {type} feel?"],
    }
    await RV.refresh_receipt_templates(db, rel.user_id, client=_client(payload))
    db.refresh(rel)
    assert rel.receipt_templates is not None
    assert rel.receipt_templates["first"] == payload["first"]
    assert rel.receipt_templates_voice_key not in (None, "default")
    assert rel.receipt_templates_generated_at is not None

    # Idempotent: same voice -> no regeneration (client untouched).
    c2 = _client(payload)
    await RV.refresh_receipt_templates(db, rel.user_id, client=c2)
    c2.generate_structured_with_usage.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_records_haiku_spend_for_the_user(db):
    """#472: the receipt-voice generation's Haiku spend is recorded for the user."""
    from unittest.mock import MagicMock, patch

    rel = _seed_relationship(db, preset="roast", freetext="be cheeky")
    payload = {
        "first": ["Boom {type}! How'd it feel?"],
        "second": ["{prev_type} then {type} — how was it?"],
        "multi": ["{count} today: {sequence}. How'd the {type} feel?"],
    }
    with patch.object(RV, "budget_record", MagicMock()) as rec:
        await RV.refresh_receipt_templates(db, rel.user_id, client=_client(payload))

    rec.assert_called_once_with(
        rel.user_id,
        "claude-haiku-4-5",
        100,
        50,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )


@pytest.mark.asyncio
async def test_refresh_default_voice_clears_to_floor(db):
    rel = _seed_relationship(db, preset=None)  # undeclared -> default voice
    c = _client({"first": ["x {type}?"], "second": [], "multi": []})
    await RV.refresh_receipt_templates(db, rel.user_id, client=c)
    db.refresh(rel)
    assert rel.receipt_templates is None  # floor stands
    assert rel.receipt_templates_voice_key == "default"
    assert rel.receipt_templates_generated_at is not None
    c.generate_structured_with_usage.assert_not_called()  # no LLM spent on the default voice


def test_lazy_refresh_skips_default_and_already_generated(db, monkeypatch):
    calls = []
    monkeypatch.setattr(RV, "enqueue_receipt_template_refresh", lambda uid: calls.append(uid))

    # default voice -> no enqueue
    default_rel = _seed_relationship(db, preset=None)
    RV.maybe_enqueue_lazy_refresh(default_rel)
    assert calls == []

    # declared voice, never generated -> enqueue
    rel = _seed_relationship(db, preset="roast")
    RV.maybe_enqueue_lazy_refresh(rel)
    assert calls == [rel.user_id]

    # already generated -> no further enqueue
    from datetime import datetime, timezone
    rel.receipt_templates_generated_at = datetime.now(timezone.utc)
    RV.maybe_enqueue_lazy_refresh(rel)
    assert calls == [rel.user_id]


# --- #607: soft over-budget entry gate (PAUSE, not overshoot; non-fatal) -------


def _arm_over_budget(monkeypatch, user_id):
    """Drive the real budget gate over a per-user daily ceiling for `user_id`."""
    from app.core.config import settings
    from app.services.coach import budget as B

    B.set_gate(B.new_in_memory_gate())
    monkeypatch.setattr(settings, "LLM_BUDGET_USER_DAILY_USD", 0.01)
    B.record(user_id, "claude-opus-4-8", 1_000_000, 0)  # ~$5.00 >> $0.01 ceiling


@pytest.mark.asyncio
async def test_generate_over_budget_skips_llm_returns_none(monkeypatch):
    """Over budget: generation PAUSES — no Haiku call, returns None so the caller
    keeps the house-default floor."""
    from app.services.coach import budget as B

    uid = uuid4()
    _arm_over_budget(monkeypatch, uid)
    try:
        client = _client({"first": ["boom {type}? feel?"], "second": [], "multi": []})
        out = await RV.generate_receipt_templates(_declared_voice(), client=client, user_id=uid)
        assert out is None
        client.generate_structured_with_usage.assert_not_called()
    finally:
        B.set_gate(None)


@pytest.mark.asyncio
async def test_refresh_over_budget_leaves_templates_regenerable(db, monkeypatch):
    """Over budget through refresh: no call, provenance is left UNSTAMPED and the
    stored set untouched, so the lazy path re-attempts once spend rolls over."""
    from app.services.coach import budget as B

    rel = _seed_relationship(db, preset="roast", freetext="be cheeky")
    _arm_over_budget(monkeypatch, rel.user_id)
    try:
        client = _client({"first": ["boom {type}? feel?"], "second": [], "multi": []})
        out = await RV.refresh_receipt_templates(db, rel.user_id, client=client)
        client.generate_structured_with_usage.assert_not_called()
        assert out is not None
        assert out.receipt_templates_generated_at is None  # retryable: not stamped
        assert out.receipt_templates is None
        # the lazy path still sees "never generated" and will re-enqueue
        assert RV.resolve_voice(out).is_default is False
    finally:
        B.set_gate(None)


@pytest.mark.asyncio
async def test_generate_under_budget_with_user_proceeds(monkeypatch):
    """Under budget: passing a user_id does not gate — the call proceeds as before."""
    from app.services.coach import budget as B

    uid = uuid4()
    B.set_gate(B.new_in_memory_gate())  # fresh gate, no ceiling armed -> not over
    try:
        client = _client({"first": ["boom {type}? feel?"], "second": [], "multi": []})
        out = await RV.generate_receipt_templates(_declared_voice(), client=client, user_id=uid)
        client.generate_structured_with_usage.assert_called_once()
        assert out is not None
    finally:
        B.set_gate(None)


# ---------------------------------------------------------------------------
# The fingerprint covers what the generator actually reads (#827)
# ---------------------------------------------------------------------------


def test_the_fingerprint_moves_when_a_characters_exemplars_are_rewritten():
    """A preset is a name over a body of text, and this generator reads the text.

    `_render_voice_data` puts the preset's example messages into the generation
    prompt, so rewriting an exemplar changes what the templates are generated
    FROM. A fingerprint over the preset KEY alone would report fresh while every
    runner sat on templates written from prose that no longer exists.
    """
    from dataclasses import replace as dc_replace

    from app.services.coach.voice import PRESETS, resolve_voice
    from app.services.coach.receipt_voice import voice_fingerprint

    voice = resolve_voice(SimpleNamespace(
        voice_preset="sage", voice_warmth=None, voice_humor=None,
        voice_force=None, voice_energy=None, voice_length=None, voice_freetext=None,
    ))
    before = voice_fingerprint(voice)

    # Same key, same dials, one exemplar rewritten — the state a house-wide
    # rewrite of the cast leaves every stored template set in.
    rewritten = dc_replace(
        PRESETS["sage"], example_bad="Something else entirely, in the same voice."
    )
    after = voice_fingerprint(dc_replace(voice, preset=rewritten))

    assert before != after, (
        "rewriting a character's exemplars left the fingerprint unchanged, so no "
        "runner's receipt templates would ever be regenerated from the new prose"
    )
