"""Per-user coach-report notification routing (P2.4, #120, ADR 0023).

Covers the routing FOUNDATION: the resolver (per-user Telegram chat vs the global
fallback), the composer threading `recipient` into `Notification.to`, and the
Telegram adapter honoring `to`. The linking flow + inbound multi-user callback
auth are the deferred follow-up (need P2.0's authenticated user).
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.core.config import settings
from app.models import User
from app.schemas.coach import (
    CoachMessageReport,
    CoachReportDebug,
    CoachReportMeta,
    CoachReportRead,
)
from app.services.notifications import (
    build_coach_notification,
    build_receipt_notification,
    resolve_owner_user,
    resolve_recipient,
)
from app.services.notifications.port import Notification
from app.services.notifications.telegram_adapter import TelegramNotifier


def _coach_report_read() -> CoachReportRead:
    """A minimal prose report, enough to render either Exchange stage."""
    return CoachReportRead(
        id=uuid4(),
        activity_id=uuid4(),
        report=CoachMessageReport(
            message="Solid effort on the hills today.",
            opener_message="Nice work getting that one in!",
            headline="Hilly moderate run",
        ),
        meta=CoachReportMeta(
            confidence="medium", model_id="claude-sonnet-4-6",
            prompt_id="coach_message_v2", schema_version="2.0",
            input_hash="hash123",
            generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        ),
        debug=CoachReportDebug(context_pack={}, system_prompt="", raw_llm_response=None),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _telegram_active(monkeypatch, *, chat_id="GLOBAL", owner_email=""):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "bot-token")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", chat_id)
    # Default to single-user mode (no owner concept) unless a test opts in, so the
    # #542 owner-only fallback is exercised explicitly where it matters.
    monkeypatch.setattr(settings, "OWNER_EMAIL", owner_email)


# --- resolver ------------------------------------------------------------------

def test_resolve_recipient_returns_bound_telegram_chat(monkeypatch):
    _telegram_active(monkeypatch)
    user = SimpleNamespace(telegram_chat_id="USER_CHAT_1", email="a@x.dev")
    assert resolve_recipient(user) == "USER_CHAT_1"


def test_resolve_recipient_unbound_no_owner_email_and_no_db_fails_closed(monkeypatch):
    # #600: safety no longer hinges on OWNER_EMAIL presence. With OWNER_EMAIL unset
    # and no db to prove the deployment is single-user, an unbound user resolves to
    # None (suppress) rather than the global owner chat — fail closed.
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="")
    assert resolve_recipient(SimpleNamespace(telegram_chat_id=None, email="a@x.dev")) is None
    assert resolve_recipient(None) is None


def test_resolve_recipient_unbound_single_user_db_falls_back_to_global(monkeypatch, db):
    # #600: OWNER_EMAIL unset but the db proves exactly one user exists => genuine
    # single-user deployment => the unbound user keeps the global fallback
    # (back-compat for the single-owner deployment).
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="")
    only = User(id=uuid4(), email="a@x.dev", telegram_chat_id=None)
    db.add(only)
    db.commit()
    assert resolve_recipient(only, db=db) == "GLOBAL"


def test_resolve_recipient_unbound_multi_user_no_owner_email_is_suppressed(monkeypatch, db):
    # #600: OWNER_EMAIL unset AND more than one user exists => the owner is not
    # identifiable, so an unbound runner fails closed to None instead of leaking to
    # the global owner chat. This is the leak #600 closes without keying on config.
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="")
    u1 = User(id=uuid4(), email="a@x.dev", telegram_chat_id=None)
    u2 = User(id=uuid4(), email="b@x.dev", telegram_chat_id=None)
    db.add_all([u1, u2])
    db.commit()
    assert resolve_recipient(u1, db=db) is None
    assert resolve_recipient(u2, db=db) is None


def test_resolve_recipient_owner_email_mistyped_still_fails_closed(monkeypatch, db):
    # #600: a mistyped OWNER_EMAIL that matches no user must NOT reopen the leak.
    # Every unbound runner (none matching the owner email) resolves to None.
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="typo@x.dev")
    u1 = User(id=uuid4(), email="a@x.dev", telegram_chat_id=None)
    u2 = User(id=uuid4(), email="b@x.dev", telegram_chat_id=None)
    db.add_all([u1, u2])
    db.commit()
    assert resolve_recipient(u1, db=db) is None
    assert resolve_recipient(u2, db=db) is None


def test_resolve_recipient_unbound_owner_falls_back_to_global(monkeypatch):
    # Multi-user (OWNER_EMAIL set): the owner, still unbound, keeps the global chat
    # so the owner's own notifications are unaffected.
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="owner@x.dev")
    owner = SimpleNamespace(telegram_chat_id=None, email="Owner@X.dev")  # case-insensitive
    assert resolve_recipient(owner) == "GLOBAL"


def test_resolve_recipient_unbound_non_owner_is_suppressed(monkeypatch):
    # The #542 fix: a non-owner runner who has not linked Telegram resolves to
    # None (suppress), never to the owner's global chat.
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="owner@x.dev")
    non_owner = SimpleNamespace(telegram_chat_id=None, email="runner@x.dev")
    assert resolve_recipient(non_owner) is None
    # A partial/None user in multi-user mode is also suppressed (no email to match).
    assert resolve_recipient(None) is None


def test_suppressed_unbound_runner_is_logged_loudly(monkeypatch, caplog):
    """#795: suppression is safe, but a runner receiving nothing must be visible.

    The leak ran a week partly because the affected runner had never received a
    single notification, so had nothing to report. Silence from a new signup is
    indistinguishable from "hasn't run yet" unless the server says otherwise.
    """
    import logging

    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="owner@x.dev")
    non_owner = SimpleNamespace(
        telegram_chat_id=None, email="runner@x.dev", id="user-123",
    )
    with caplog.at_level(logging.WARNING, logger="app.services.notifications"):
        assert resolve_recipient(non_owner) is None
    assert any("user-123" in r.getMessage() for r in caplog.records), (
        "a runner who receives nothing must be identifiable in the logs"
    )


def test_owner_and_bound_runners_are_not_logged_as_suppressed(monkeypatch, caplog):
    # The warning must stay rare enough to be worth reading: successful routing
    # never emits it, or an operator learns to scroll past it.
    import logging

    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="owner@x.dev")
    with caplog.at_level(logging.WARNING, logger="app.services.notifications"):
        resolve_recipient(SimpleNamespace(telegram_chat_id=None, email="owner@x.dev"))
        resolve_recipient(SimpleNamespace(telegram_chat_id="BOUND", email="r@x.dev"))
    assert not caplog.records


# --- resolve_owner_user (the identified deployment owner, #600/#608) -----------

def test_resolve_owner_user_by_email_case_insensitive(monkeypatch, db):
    monkeypatch.setattr(settings, "OWNER_EMAIL", "Owner@X.dev")
    owner = User(id=uuid4(), email="owner@x.dev")
    other = User(id=uuid4(), email="runner@x.dev")
    db.add_all([owner, other])
    db.commit()
    assert resolve_owner_user(db).id == owner.id


def test_resolve_owner_user_single_user_without_owner_email(monkeypatch, db):
    # OWNER_EMAIL unset + exactly one user => that user is the implicit owner.
    monkeypatch.setattr(settings, "OWNER_EMAIL", "")
    only = User(id=uuid4(), email="solo@x.dev")
    db.add(only)
    db.commit()
    assert resolve_owner_user(db).id == only.id


def test_resolve_owner_user_none_when_multi_user_and_no_owner_email(monkeypatch, db):
    # OWNER_EMAIL unset + more than one user => owner not identifiable => None.
    monkeypatch.setattr(settings, "OWNER_EMAIL", "")
    db.add_all([User(id=uuid4(), email="a@x.dev"), User(id=uuid4(), email="b@x.dev")])
    db.commit()
    assert resolve_owner_user(db) is None


def test_resolve_owner_user_none_when_owner_email_matches_nobody(monkeypatch, db):
    monkeypatch.setattr(settings, "OWNER_EMAIL", "ghost@x.dev")
    db.add_all([User(id=uuid4(), email="a@x.dev"), User(id=uuid4(), email="b@x.dev")])
    db.commit()
    assert resolve_owner_user(db) is None


def test_resolve_recipient_none_without_telegram_channel(monkeypatch):
    # No channel configured (email was removed, #595): the resolver returns None.
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    user = SimpleNamespace(telegram_chat_id="USER_CHAT_1", email="a@x.dev")
    assert resolve_recipient(user) is None


# --- composer threads recipient into Notification.to ---------------------------

def test_receipt_routes_to_per_user_recipient(monkeypatch):
    _telegram_active(monkeypatch)
    n = build_receipt_notification(
        receipt_text="Nice run", headline="Easy 5k", activity_id="act-1",
        distance_m=5000, app_base_url="http://app", recipient="USER_CHAT_1",
    )
    assert n.to == "USER_CHAT_1"  # the owner's chat, not the global


def test_receipt_telegram_single_user_falls_back_to_global(monkeypatch, db):
    # #795: the single-user global fallback survives, but it is now earned at the
    # RESOLVER, which has a db to prove the deployment really is single-user. The
    # builder no longer re-derives it. Previously this test built with no recipient
    # at all and asserted the builder invented "GLOBAL" — which pinned the leak.
    _telegram_active(monkeypatch, chat_id="GLOBAL", owner_email="")
    only = User(id=uuid4(), email="a@x.dev", telegram_chat_id=None)
    db.add(only)
    db.commit()
    n = build_receipt_notification(
        receipt_text="Nice run", headline="Easy 5k", activity_id="act-1",
        distance_m=5000, app_base_url="http://app",
        recipient=resolve_recipient(only, db=db),
    )
    assert n is not None and n.to == "GLOBAL"


def test_receipt_telegram_multiuser_suppressed_when_recipient_none(monkeypatch):
    # OWNER_EMAIL set => multi-user: a recipientless Telegram build is SUPPRESSED,
    # never routed to the global owner chat (#542). Defense in depth: holds at the
    # builder even if a caller bypassed resolve_recipient.
    _telegram_active(monkeypatch, chat_id="OWNER_CHAT", owner_email="owner@x.dev")
    n = build_receipt_notification(
        receipt_text="Nice run", headline="Easy 5k", activity_id="act-1",
        distance_m=5000, app_base_url="http://app",  # no recipient
    )
    assert n is None  # suppressed, not routed to OWNER_CHAT


def test_builder_suppresses_recipientless_build_when_owner_email_is_absent(monkeypatch):
    """#795: the leak. A recipientless build must NOT invent the global chat just
    because THIS process has no OWNER_EMAIL.

    This is the exact deployed configuration that leaked: `OWNER_EMAIL` was set on
    Railway's `web` service but not on `worker`, and `worker` is the process that
    sends. `resolve_recipient` correctly suppressed the non-owner runner (it asked
    the db and found more than one user), and the builder then turned that None
    back into the owner's global chat, because it re-derived "single-user" from
    `OWNER_EMAIL` alone — the one question it has no db to answer.

    The builder layer now has NO configuration-dependent recipient behaviour: no
    recipient means no notification, whatever config an individual process holds.
    """
    _telegram_active(monkeypatch, chat_id="OWNER_CHAT", owner_email="")
    assert build_receipt_notification(
        receipt_text="Someone else's run", headline="Easy run — 10.0km",
        activity_id="act-1", distance_m=10000, app_base_url="http://app",
    ) is None


def test_multi_user_leak_end_to_end_with_owner_email_absent(monkeypatch, db):
    """#795 end-to-end, at the layer that builds the notification.

    Multi-user deployment, `OWNER_EMAIL` absent from this process. The runner is
    not the owner and has no bound chat. Nothing may be built for them — the
    global owner chat is not a fallback, it is another tenant's inbox.

    The pre-#795 resolver-only tests could not catch this: they stopped at
    `resolve_recipient` returning None and never asked what the builder did with
    it.
    """
    _telegram_active(monkeypatch, chat_id="OWNER_CHAT", owner_email="")
    owner = User(id=uuid4(), email="owner@x.dev", telegram_chat_id=None)
    other = User(id=uuid4(), email="runner@x.dev", telegram_chat_id=None)
    db.add_all([owner, other])
    db.commit()

    for report_stage in ("opener", "fuller"):
        assert build_coach_notification(
            report=_coach_report_read(), headline="Hilly moderate run",
            distance_m=5100, app_base_url="http://app", stage=report_stage,
            recipient=resolve_recipient(other, db=db),
        ) is None, f"{report_stage} stage leaked another runner's report"

    assert build_receipt_notification(
        receipt_text="Got your run", headline="Easy run", activity_id="act-1",
        distance_m=10000, app_base_url="http://app",
        recipient=resolve_recipient(other, db=db),
    ) is None


def test_non_owner_unbound_receipt_is_not_built_end_to_end(monkeypatch):
    # The full #542 guarantee: a non-owner unbound runner produces NO Telegram
    # notification (the leak was their receipt going to the owner's chat).
    _telegram_active(monkeypatch, chat_id="OWNER_CHAT", owner_email="owner@x.dev")
    non_owner = SimpleNamespace(telegram_chat_id=None, email="runner@x.dev")
    n = build_receipt_notification(
        receipt_text="run", headline="h", activity_id="act-1",
        distance_m=0, app_base_url="http://app",
        recipient=resolve_recipient(non_owner),
    )
    assert n is None


# --- adapter honors Notification.to --------------------------------------------

def _ok_response():
    resp = MagicMock()
    resp.json.return_value = {"ok": True}
    resp.raise_for_status.return_value = None
    return resp


def test_telegram_adapter_sends_to_notification_recipient():
    n = Notification(to="USER_CHAT_1", subject="s", html="h", text="t")
    with patch("app.services.notifications.telegram_adapter.httpx.post",
               return_value=_ok_response()) as post:
        TelegramNotifier(bot_token="b", chat_id="GLOBAL").send(n)
    assert post.call_args.kwargs["json"]["chat_id"] == "USER_CHAT_1"


def test_telegram_adapter_falls_back_to_configured_chat_when_to_empty():
    n = Notification(to="", subject="s", html="h", text="t")
    with patch("app.services.notifications.telegram_adapter.httpx.post",
               return_value=_ok_response()) as post:
        TelegramNotifier(bot_token="b", chat_id="GLOBAL").send(n)
    assert post.call_args.kwargs["json"]["chat_id"] == "GLOBAL"


# --- tenant isolation (the point of the phase) ---------------------------------

def test_one_users_notification_never_routes_to_another_users_chat(monkeypatch):
    """A's notification carries A's chat, never B's — proven across the full
    resolve -> compose -> send chain."""
    _telegram_active(monkeypatch)
    user_a = SimpleNamespace(telegram_chat_id="A_CHAT", email="a@x.dev")
    user_b = SimpleNamespace(telegram_chat_id="B_CHAT", email="b@x.dev")

    recipient_a = resolve_recipient(user_a)
    assert recipient_a == "A_CHAT" and recipient_a != user_b.telegram_chat_id

    n = build_receipt_notification(
        receipt_text="run", headline="h", activity_id="act-a",
        distance_m=0, app_base_url="http://app", recipient=recipient_a,
    )
    with patch("app.services.notifications.telegram_adapter.httpx.post",
               return_value=_ok_response()) as post:
        TelegramNotifier(bot_token="b", chat_id="GLOBAL").send(n)
    sent_chat = post.call_args.kwargs["json"]["chat_id"]
    assert sent_chat == "A_CHAT"
    assert sent_chat != "B_CHAT"  # no cross-tenant leak
