"""Notification rendering for the A3 prose-message shape (deliverable 6, backend).

A notification is one transmission of the prose message (CONTEXT.md: Notification).
These pin that the email and Telegram templates render the prose instead of
crashing on the absent key_takeaways, and that Telegram truncates at a paragraph
boundary under its 4096-char limit.
"""

from datetime import datetime, timezone
from uuid import uuid4

from app.schemas.coach import (
    CoachMessageReport,
    CoachNextStep,
    CoachReportDebug,
    CoachReportMeta,
    CoachReportRead,
)
from app.services.notifications._prose import truncate_at_paragraph
from app.services.notifications.email_template import render_coach_report_email
from app.services.notifications.telegram_template import render_coach_report_telegram


def _message_read(message: str) -> CoachReportRead:
    report = CoachMessageReport(
        message=message,
        headline="Solid easy run",
        next_steps=[CoachNextStep(action="Easy day", details="30 min", why="Recover")],
    )
    meta = CoachReportMeta(
        confidence="high", model_id="claude-sonnet-4-6", prompt_id="coach_message_v1",
        schema_version="2.0", input_hash="abc", generated_at=datetime.now(timezone.utc),
    )
    return CoachReportRead(
        id=uuid4(), activity_id=uuid4(), report=report, meta=meta,
        debug=CoachReportDebug(context_pack={}, system_prompt="", raw_llm_response=None),
        created_at=datetime.now(timezone.utc),
    )


class TestTruncateAtParagraph:
    def test_short_text_unchanged(self):
        assert truncate_at_paragraph("short note", 4096) == "short note"

    def test_truncates_at_paragraph_boundary(self):
        text = "First paragraph here." + "\n\n" + ("x" * 50) + "\n\n" + ("y" * 50)
        out = truncate_at_paragraph(text, 60)
        assert out.startswith("First paragraph here.")
        assert "open the app" in out
        assert len(out) <= 60

    def test_hard_limit_respected(self):
        out = truncate_at_paragraph("word " * 2000, 200)
        assert len(out) <= 200


class TestEmailMessageRender:
    def test_html_renders_prose_paragraphs(self):
        read = _message_read("Nice steady run.\n\nYour drift stayed low throughout.")
        subject, html, text = render_coach_report_email(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com",
        )
        assert "Nice steady run." in html
        assert "Your drift stayed low throughout." in html
        assert "<p>Nice steady run.</p>" in html
        # The form-shaped sections are gone for the prose family.
        assert "Key takeaways" not in html
        assert "Nice steady run." in text
        assert "Key takeaways" not in text

    def test_subject_unchanged(self):
        read = _message_read("Body.")
        subject, _, _ = render_coach_report_email(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com",
        )
        assert subject == "Easy Run — 5.0km · high confidence"


class TestTelegramMessageRender:
    def test_body_is_the_prose(self):
        read = _message_read("Great session today. You nailed the easy effort.")
        title, body, url = render_coach_report_telegram(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com",
        )
        assert body == "Great session today. You nailed the easy effort."
        assert "Key takeaways" not in body
        assert url.endswith(f"/activity/{read.activity_id}")

    def test_long_body_truncated_under_4096(self):
        long_message = "\n\n".join(f"Paragraph {i} " + ("w" * 200) for i in range(50))
        read = _message_read(long_message)
        _, body, _ = render_coach_report_telegram(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com",
        )
        assert len(body) <= 4096
        assert "open the app" in body


def _opener_read(opener_message: str) -> CoachReportRead:
    """An opener-state row: opener_message set, message empty (fuller not landed)."""
    report = CoachMessageReport(message="", opener_message=opener_message)
    meta = CoachReportMeta(
        confidence="medium", model_id="claude-sonnet-4-6", prompt_id="coach_message_v2",
        schema_version="2.0", input_hash="abc", generated_at=datetime.now(timezone.utc),
    )
    return CoachReportRead(
        id=uuid4(), activity_id=uuid4(), report=report, meta=meta,
        debug=CoachReportDebug(context_pack={}, system_prompt="", raw_llm_response=None),
        created_at=datetime.now(timezone.utc),
    )


class TestOpenerStageRender:
    """A4 D8: stage='opener' renders opener_message + the reply-invite line, not
    the (empty) fuller message."""

    def test_telegram_opener_renders_opener_prose_and_invite(self):
        from app.services.notifications._prose import OPENER_REPLY_LINE

        read = _opener_read("Nice work getting that one in!")
        _, body, url = render_coach_report_telegram(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com", stage="opener",
        )
        assert "Nice work getting that one in!" in body
        assert OPENER_REPLY_LINE in body
        assert url.endswith(f"/activity/{read.activity_id}")

    def test_email_opener_renders_opener_prose(self):
        from app.services.notifications._prose import OPENER_REPLY_LINE

        read = _opener_read("Quick reaction: solid steady effort.")
        _, html, text = render_coach_report_email(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com", stage="opener",
        )
        assert "Quick reaction: solid steady effort." in html
        assert "Quick reaction: solid steady effort." in text
        assert OPENER_REPLY_LINE in text

    def test_fuller_stage_default_unchanged_for_message_rows(self):
        # The default stage stays 'fuller' and renders message (not opener_message),
        # so every existing caller is byte-stable.
        read = _message_read("The fuller breakdown is here.")
        _, body, _ = render_coach_report_telegram(
            report=read, headline="Easy Run", distance_m=5000,
            app_base_url="https://app.example.com",
        )
        assert body == "The fuller breakdown is here."
