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
from app.services.coach.voice import resolve_voice


def _client(payload):
    c = AsyncMock()
    c.generate_structured = AsyncMock(return_value=payload)
    return c


def _declared_voice():
    # A non-default voice (a preset + a dial nudge + freetext).
    rel = SimpleNamespace(
        voice_preset="roast", voice_warmth=3, voice_humor=5,
        voice_directness=5, voice_energy=5, voice_freetext="be cheeky",
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
        voice_directness=2, voice_energy=1, voice_freetext=None,
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
    c2.generate_structured.assert_not_called()


@pytest.mark.asyncio
async def test_refresh_default_voice_clears_to_floor(db):
    rel = _seed_relationship(db, preset=None)  # undeclared -> default voice
    c = _client({"first": ["x {type}?"], "second": [], "multi": []})
    await RV.refresh_receipt_templates(db, rel.user_id, client=c)
    db.refresh(rel)
    assert rel.receipt_templates is None  # floor stands
    assert rel.receipt_templates_voice_key == "default"
    assert rel.receipt_templates_generated_at is not None
    c.generate_structured.assert_not_called()  # no LLM spent on the default voice


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
