"""Receipt delivery (#296): the 'done' callback token and the deterministic
receipt Notification (taps over Telegram, the only channel)."""

import pytest

from app.core.config import settings
from app.services.notifications import build_receipt_notification, set_notifier
from app.services.notifications.callback_token import decode, encode


# --- done token codec ---------------------------------------------------------


def test_done_token_round_trips():
    aid = "11111111-2222-3333-4444-555555555555"
    token = encode(kind="done", activity_id=aid)
    action = decode(token)
    assert action is not None
    assert action.kind == "done"
    assert action.activity_id == aid
    assert action.value is None
    assert action.field is None


def test_rpe_token_still_round_trips():
    aid = "11111111-2222-3333-4444-555555555555"
    action = decode(encode(kind="rpe", activity_id=aid, value=7))
    assert action.kind == "rpe"
    assert action.value == 7
    assert action.field == "rpe"


def test_pain_token_still_round_trips():
    aid = "11111111-2222-3333-4444-555555555555"
    action = decode(encode(kind="pain", activity_id=aid, value=4))
    assert action.field == "pain_score"
    assert action.value == 4


def test_done_token_with_extra_parts_is_rejected():
    # A 'done' token must be exactly 3 parts; a value-bearing done is malformed.
    assert decode("cb1|done|abc|9") is None


def test_unknown_kind_rejected():
    assert decode("cb1|bogus|abc") is None
    assert decode("cb1|bogus|abc|3") is None


# --- receipt notification -----------------------------------------------------


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "12345")
    set_notifier(None)
    yield
    set_notifier(None)


@pytest.fixture
def no_channel(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")


def _aid():
    return "11111111-2222-3333-4444-555555555555"


def test_receipt_notification_telegram_carries_taps(telegram):
    n = build_receipt_notification(
        receipt_text="Got your run — how did it feel?",
        headline="Steady aerobic run",
        activity_id=_aid(),
        distance_m=5000,
        app_base_url="https://app.example.com",
        # Routing is stated, not inferred (#795): this test pins the tap keyboard.
        recipient="42",
    )
    assert n is not None
    assert n.text == "Got your run — how did it feel?"
    assert "Steady aerobic run" in n.subject and "5.0km" in n.subject
    assert n.url.endswith(f"/activity/{_aid()}")
    kinds = [decode(a.token).kind for a in n.actions]
    assert "rpe" in kinds and "pain" in kinds and "done" in kinds
    # every action token resolves to THIS activity
    assert all(decode(a.token).activity_id == _aid() for a in n.actions)


def test_receipt_notification_none_without_channel(no_channel):
    assert build_receipt_notification(
        receipt_text="x", headline="y", activity_id=_aid(), distance_m=1000,
        app_base_url="https://app.example.com",
    ) is None
