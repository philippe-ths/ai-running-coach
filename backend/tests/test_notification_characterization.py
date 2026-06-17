"""Characterization snapshots for the notifications package (#333).

These pin the EXACT rendered output of every (channel x report-shape x stage)
combination plus the deterministic receipt path, captured against the current
code. They are the behaviour-preservation oracle for the #333 refactor that
moves report-shape rendering behind the notifier seam: the rendered bytes must
be identical before and after.

Trust level: the inputs are SYNTHETIC test setup (level 5) — hand-built report
fixtures that exercise each branch. The ORACLE, however, is trust level 3: the
exact output the current (pre-refactor) code produces on those inputs, captured
here as literal expected strings. A failing snapshot after the refactor is a
signal the refactor changed behaviour, never a signal to edit the snapshot.

The matrix covered (channel x shape x stage + receipt + no-channel):
  - Telegram x prose (CoachMessageReport) x fuller
  - Telegram x prose x opener
  - Telegram x prose x fuller, long-message truncation path
  - Telegram x structured (CoachReportContent)        [stage defaults fuller]
  - Email    x prose x fuller
  - Email    x prose x opener
  - Email    x structured
  - Telegram receipt (tap keyboard)
  - Email    receipt (no taps)
  - No channel -> None  (both build_coach_notification and build_receipt_notification)

For Telegram, both the composer-level Notification (subject/text/url/actions)
AND the adapter wire payload (the HTML body + inline keyboard) are pinned, since
the inline keyboard is assembled in the adapter.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import UUID

import pytest

from app.core.config import settings
from app.schemas.coach import (
    CoachMessageQuestion,
    CoachMessageReport,
    CoachNextStep,
    CoachQuestion,
    CoachReportContent,
    CoachReportDebug,
    CoachReportMeta,
    CoachReportRead,
    CoachRisk,
    CoachTakeaway,
    TappableOption,
)
from app.services.notifications import (
    build_coach_notification,
    build_receipt_notification,
    set_notifier,
)
from app.services.notifications.telegram_adapter import TelegramNotifier

# A fixed activity id so the deep-link URL and callback tokens are deterministic.
_AID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
_BASE = "https://app.example.com"


# --- synthetic fixtures (test setup, level 5) ---------------------------------


def _meta(prompt_id: str, schema_version: str) -> CoachReportMeta:
    return CoachReportMeta(
        confidence="medium",
        model_id="claude-sonnet-4-6",
        prompt_id=prompt_id,
        schema_version=schema_version,
        input_hash="hash123",
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _read(report, prompt_id: str, schema_version: str) -> CoachReportRead:
    return CoachReportRead(
        id=UUID("11111111-1111-1111-1111-111111111111"),
        activity_id=_AID,
        report=report,
        meta=_meta(prompt_id, schema_version),
        debug=CoachReportDebug(context_pack={}, system_prompt="", raw_llm_response=None),
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _prose_fuller() -> CoachReportRead:
    report = CoachMessageReport(
        message="Strong steady effort today.\n\nYour drift held under 3% the whole way.",
        headline="Solid easy run",
        next_steps=[CoachNextStep(action="Easy day", details="30 min", why="Recover")],
        questions=[
            CoachMessageQuestion(
                question="How did it feel?",
                reason="Calibrate effort.",
                options=[
                    TappableOption(id="rpe6", label="RPE 6", kind="rpe", payload=6),
                    TappableOption(id="pain0", label="No pain", kind="pain", payload=0),
                ],
            )
        ],
    )
    return _read(report, "coach_message_v1", "2.0")


def _prose_opener() -> CoachReportRead:
    report = CoachMessageReport(
        message="",
        opener_message="Nice work getting that one in!",
        questions=[
            CoachMessageQuestion(
                question="How did it feel?",
                reason="Calibrate effort.",
                options=[
                    TappableOption(id="rpe6", label="RPE 6", kind="rpe", payload=6),
                    TappableOption(id="pain0", label="No pain", kind="pain", payload=0),
                ],
            )
        ],
    )
    return _read(report, "coach_message_v2", "2.0")


def _prose_long() -> CoachReportRead:
    long_message = "\n\n".join(f"Paragraph {i} " + ("w" * 200) for i in range(50))
    report = CoachMessageReport(message=long_message, headline="Long one")
    return _read(report, "coach_message_v1", "2.0")


def _structured() -> CoachReportRead:
    report = CoachReportContent(
        key_takeaways=[
            CoachTakeaway(text="Effort stayed in zone 2 throughout."),
            CoachTakeaway(text="HR drift was minimal at 2.1%."),
        ],
        next_steps=[
            CoachNextStep(
                action="Add a tempo run mid-week",
                details="Target 4km at threshold pace.",
                why="To extend lactate clearance.",
            )
        ],
        risks=[
            CoachRisk(
                flag="cadence_low",
                explanation="Cadence averaged 162 spm.",
                mitigation="Consider strides 1-2x/week.",
            )
        ],
        questions=[
            CoachQuestion(
                question="How did sleep feel last night?",
                reason="Helps calibrate effort guidance.",
            )
        ],
    )
    return _read(report, "coach_report_v10", "1.2")


# --- channel fixtures ---------------------------------------------------------


@pytest.fixture
def telegram(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123:ABC")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "NOTIFY_TO", "")
    set_notifier(None)
    yield
    set_notifier(None)


@pytest.fixture
def email(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "NOTIFY_TO", "runner@example.com")
    set_notifier(None)
    yield
    set_notifier(None)


@pytest.fixture
def no_channel(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "NOTIFY_TO", "")


def _coach(report: CoachReportRead, stage: str = "fuller", distance_m: int = 8200):
    return build_coach_notification(
        report=report,
        headline="Easy Run",
        distance_m=distance_m,
        app_base_url=_BASE,
        stage=stage,
    )


def _telegram_wire(notification) -> dict:
    """Capture the exact Telegram Bot API payload the adapter would POST."""
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True, "result": {"message_id": 1}}
    with patch(
        "app.services.notifications.telegram_adapter.httpx.post",
        return_value=response,
    ) as post:
        TelegramNotifier(bot_token="123:ABC", chat_id="42").send(notification)
    return post.call_args.kwargs["json"]


# --- Telegram x prose ---------------------------------------------------------


class TestTelegramProse:
    def test_fuller_composer_output(self, telegram):
        n = _coach(_prose_fuller(), stage="fuller")
        assert n is not None
        assert n.to == "42"
        assert n.subject == "Easy Run — 8.2km · medium confidence"
        assert n.html == ""
        assert n.text == (
            "Strong steady effort today.\n\n"
            "Your drift held under 3% the whole way."
        )
        assert n.url == f"{_BASE}/activity/{_AID}"
        # The fuller turn carries no tappable affordances.
        assert n.actions == ()

    def test_fuller_telegram_wire(self, telegram):
        n = _coach(_prose_fuller(), stage="fuller")
        body = _telegram_wire(n)
        assert body["chat_id"] == "42"
        assert body["parse_mode"] == "HTML"
        assert body["disable_web_page_preview"] is True
        assert body["text"] == (
            "<b>Easy Run — 8.2km · medium confidence</b>\n\n"
            "Strong steady effort today.\n\n"
            "Your drift held under 3% the whole way.\n\n"
            f'<a href="{_BASE}/activity/{_AID}">View in app</a>'
        )
        assert "reply_markup" not in body

    def test_opener_composer_output(self, telegram):
        n = _coach(_prose_opener(), stage="opener")
        assert n is not None
        assert n.subject == "Easy Run — 8.2km · medium confidence"
        assert n.text == (
            "Nice work getting that one in!\n\n"
            "Let me know how it felt and I'll follow up with the full breakdown."
        )
        assert n.url == f"{_BASE}/activity/{_AID}"
        # The opener carries the tappable RPE/pain options as actions.
        assert tuple((a.label, a.token) for a in n.actions) == (
            ("RPE 6", f"cb1|rpe|{_AID}|6"),
            ("No pain", f"cb1|pain|{_AID}|0"),
        )

    def test_opener_telegram_wire(self, telegram):
        n = _coach(_prose_opener(), stage="opener")
        body = _telegram_wire(n)
        # The adapter HTML-escapes the body text, so the apostrophe in "I'll"
        # is encoded as &#x27; on the wire (current behaviour, pinned).
        assert body["text"] == (
            "<b>Easy Run — 8.2km · medium confidence</b>\n\n"
            "Nice work getting that one in!\n\n"
            "Let me know how it felt and I&#x27;ll follow up with the full breakdown.\n\n"
            f'<a href="{_BASE}/activity/{_AID}">View in app</a>'
        )
        assert body["reply_markup"] == {
            "inline_keyboard": [
                [{"text": "RPE 6", "callback_data": f"cb1|rpe|{_AID}|6"}],
                [{"text": "No pain", "callback_data": f"cb1|pain|{_AID}|0"}],
            ]
        }

    def test_long_message_truncated(self, telegram):
        n = _coach(_prose_long(), stage="fuller")
        assert len(n.text) <= 3500
        assert n.text.endswith("… (open the app for the full note)")
        assert n.text.startswith("Paragraph 0 ")


# --- Telegram x structured ----------------------------------------------------


class TestTelegramStructured:
    def test_composer_output(self, telegram):
        n = _coach(_structured())
        assert n is not None
        assert n.subject == "Easy Run — 8.2km · medium confidence"
        assert n.html == ""
        assert n.text == (
            "Key takeaways:\n"
            "• Effort stayed in zone 2 throughout.\n"
            "• HR drift was minimal at 2.1%.\n"
            "\n"
            "Next steps:\n"
            "• Add a tempo run mid-week: Target 4km at threshold pace.\n"
            "   Why: To extend lactate clearance.\n"
            "\n"
            "Risks:\n"
            "• cadence_low: Cadence averaged 162 spm.\n"
            "   Mitigation: Consider strides 1-2x/week.\n"
            "\n"
            "Follow-up questions:\n"
            "• How did sleep feel last night? (Helps calibrate effort guidance.)"
        )
        assert n.url == f"{_BASE}/activity/{_AID}"
        # The legacy structured family never produces tappable actions
        # (actions only fire for the opener stage, which is a prose row).
        assert n.actions == ()


# --- Email x prose ------------------------------------------------------------


class TestEmailProse:
    def test_fuller_composer_output(self, email):
        n = _coach(_prose_fuller(), stage="fuller")
        assert n is not None
        assert n.to == "runner@example.com"
        assert n.subject == "Easy Run — 8.2km · medium confidence"
        assert n.actions == ()
        assert n.html == (
            '<!doctype html><html><body style="font-family:-apple-system,sans-serif;'
            'max-width:600px;margin:0 auto;padding:16px;">'
            "<h2>Easy Run · 8.2km</h2>"
            '<p style="color:#666;">Confidence: <strong>medium</strong></p>'
            "<p>Strong steady effort today.</p>"
            "<p>Your drift held under 3% the whole way.</p>"
            f'<p><a href="{_BASE}/activity/{_AID}">View in app</a></p>'
            "</body></html>"
        )
        assert n.text == (
            "Easy Run — 8.2km · medium confidence\n"
            "\n"
            "Strong steady effort today.\n\n"
            "Your drift held under 3% the whole way.\n"
            "\n"
            f"View in app: {_BASE}/activity/{_AID}\n"
        )

    def test_opener_composer_output(self, email):
        n = _coach(_prose_opener(), stage="opener")
        assert n is not None
        assert n.html == (
            '<!doctype html><html><body style="font-family:-apple-system,sans-serif;'
            'max-width:600px;margin:0 auto;padding:16px;">'
            "<h2>Easy Run · 8.2km</h2>"
            '<p style="color:#666;">Confidence: <strong>medium</strong></p>'
            "<p>Nice work getting that one in!</p>"
            "<p>Let me know how it felt and I&#x27;ll follow up with the full breakdown.</p>"
            f'<p><a href="{_BASE}/activity/{_AID}">View in app</a></p>'
            "</body></html>"
        )
        assert n.text == (
            "Easy Run — 8.2km · medium confidence\n"
            "\n"
            "Nice work getting that one in!\n\n"
            "Let me know how it felt and I'll follow up with the full breakdown.\n"
            "\n"
            f"View in app: {_BASE}/activity/{_AID}\n"
        )


# --- Email x structured -------------------------------------------------------


class TestEmailStructured:
    def test_composer_output(self, email):
        n = _coach(_structured())
        assert n is not None
        assert n.subject == "Easy Run — 8.2km · medium confidence"
        assert n.html == (
            '<!doctype html><html><body style="font-family:-apple-system,sans-serif;'
            'max-width:600px;margin:0 auto;padding:16px;">'
            "<h2>Easy Run · 8.2km</h2>"
            '<p style="color:#666;">Confidence: <strong>medium</strong></p>'
            "<h3>Key takeaways</h3>"
            "<ul><li>Effort stayed in zone 2 throughout.</li>"
            "<li>HR drift was minimal at 2.1%.</li></ul>"
            "<h3>Next steps</h3>"
            "<ol><li><strong>Add a tempo run mid-week</strong>"
            "<div>Target 4km at threshold pace.</div>"
            '<div style="color:#666;"><em>Why:</em> To extend lactate clearance.</div>'
            "</li></ol>"
            "<h3>Risks</h3>"
            "<ul><li><strong>cadence_low</strong>: Cadence averaged 162 spm.<br/>"
            "<em>Mitigation:</em> Consider strides 1-2x/week.</li></ul>"
            "<h3>Follow-up questions</h3>"
            "<ul><li><strong>How did sleep feel last night?</strong><br/>"
            "<em>Helps calibrate effort guidance.</em></li></ul>"
            f'<p><a href="{_BASE}/activity/{_AID}">View in app</a></p>'
            "</body></html>"
        )
        assert n.text == (
            "Easy Run — 8.2km · medium confidence\n"
            "\n"
            "Key takeaways:\n"
            "  - Effort stayed in zone 2 throughout.\n"
            "  - HR drift was minimal at 2.1%.\n"
            "\n"
            "Next steps:\n"
            "  - Add a tempo run mid-week: Target 4km at threshold pace.\n"
            "      Why: To extend lactate clearance.\n"
            "\n"
            "Risks:\n"
            "  - cadence_low: Cadence averaged 162 spm.\n"
            "      Mitigation: Consider strides 1-2x/week.\n"
            "\n"
            "Follow-up questions:\n"
            "  - How did sleep feel last night? (Helps calibrate effort guidance.)\n"
            "\n"
            f"View in app: {_BASE}/activity/{_AID}\n"
        )


# --- Receipt path -------------------------------------------------------------


_RID = "11111111-2222-3333-4444-555555555555"


def _expected_receipt_actions() -> tuple[tuple[str, str], ...]:
    """(label, token) per receipt tap, derived from the real RECEIPT_TAPS.

    The tap labels/values are owned by services/coach/receipt.py (out of #333's
    scope); what this pins is the notifications package's ASSEMBLY of those taps
    into Notification actions + the wire callback_data."""
    from app.services.coach.receipt import RECEIPT_TAPS
    from app.services.notifications.callback_token import encode

    return tuple(
        (tap.label, encode(kind=tap.kind, activity_id=_RID, value=tap.value))
        for tap in RECEIPT_TAPS
    )


class TestReceipt:
    def test_telegram_carries_taps(self, telegram):
        n = build_receipt_notification(
            receipt_text="Got your run — how did it feel?",
            headline="Steady aerobic run",
            activity_id=_RID,
            distance_m=5000,
            app_base_url=_BASE,
        )
        assert n is not None
        assert n.to == "42"
        assert n.subject == "Steady aerobic run — 5.0km"
        assert n.html == ""
        assert n.text == "Got your run — how did it feel?"
        assert n.url == f"{_BASE}/activity/{_RID}"
        assert (
            tuple((a.label, a.token) for a in n.actions)
            == _expected_receipt_actions()
        )

    def test_telegram_receipt_wire(self, telegram):
        n = build_receipt_notification(
            receipt_text="Got your run — how did it feel?",
            headline="Steady aerobic run",
            activity_id=_RID,
            distance_m=5000,
            app_base_url=_BASE,
        )
        body = _telegram_wire(n)
        assert body["text"] == (
            "<b>Steady aerobic run — 5.0km</b>\n\n"
            "Got your run — how did it feel?\n\n"
            f'<a href="{_BASE}/activity/{_RID}">View in app</a>'
        )
        assert body["reply_markup"] == {
            "inline_keyboard": [
                [{"text": label, "callback_data": token}]
                for label, token in _expected_receipt_actions()
            ]
        }

    def test_email_no_taps(self, email):
        n = build_receipt_notification(
            receipt_text="Got your run — how did it feel?",
            headline="Steady run",
            activity_id=_RID,
            distance_m=0,
            app_base_url=_BASE,
        )
        assert n is not None
        assert n.to == "runner@example.com"
        assert n.subject == "Steady run"
        assert n.html == "<p>Got your run — how did it feel?</p>"
        assert n.text == "Got your run — how did it feel?"
        assert n.url == f"{_BASE}/activity/{_RID}"
        assert n.actions == ()


# --- No channel ---------------------------------------------------------------


class TestNoChannel:
    def test_coach_notification_none(self, no_channel):
        set_notifier(None)
        try:
            assert _coach(_prose_fuller()) is None
        finally:
            set_notifier(None)

    def test_receipt_notification_none(self, no_channel):
        set_notifier(None)
        try:
            assert (
                build_receipt_notification(
                    receipt_text="x",
                    headline="y",
                    activity_id=_RID,
                    distance_m=1000,
                    app_base_url=_BASE,
                )
                is None
            )
        finally:
            set_notifier(None)
