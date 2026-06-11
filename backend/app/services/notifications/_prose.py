"""Shared helpers for rendering the A3 prose-message report into notifications.

The A3 output is a human prose `message` (ADR 0009); a notification is one
transmission of it. These helpers let the email and Telegram templates render the
prose faithfully — paragraphs preserved, truncated at a paragraph boundary when a
channel has a hard length limit (Telegram's 4096) — and detect which output shape
a stored report carries.
"""

from __future__ import annotations

from app.schemas.coach import CoachMessageReport

# Telegram messages cap at 4096 characters; leave headroom for the bold title and
# the link the adapter attaches out of band.
TELEGRAM_BODY_LIMIT = 3500

_TRUNCATION_SUFFIX = "\n\n… (open the app for the full note)"


def is_message_report(content) -> bool:
    """True when a CoachReportRead.report is the A3 prose shape."""
    return isinstance(content, CoachMessageReport)


def truncate_at_paragraph(text: str, limit: int) -> str:
    """Truncate `text` to at most `limit` characters at a paragraph boundary.

    Prefers the last paragraph break (blank line) before the limit; falls back to
    the last sentence end, then the last space, then a hard cut. A suffix points
    the reader to the full note in the app. Text already within the limit is
    returned unchanged.
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text

    budget = max(0, limit - len(_TRUNCATION_SUFFIX))
    window = text[:budget]
    # Prefer a paragraph boundary, then a sentence end, then a word boundary.
    cut = window.rfind("\n\n")
    if cut < budget * 0.5:  # too early to be a useful paragraph cut
        sentence = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
        cut = sentence + 1 if sentence != -1 else window.rfind(" ")
    if cut <= 0:
        cut = budget
    return text[:cut].rstrip() + _TRUNCATION_SUFFIX


def message_paragraphs(message: str) -> list[str]:
    """Split a prose message into non-empty paragraphs (blank-line separated)."""
    return [p.strip() for p in (message or "").split("\n\n") if p.strip()]


# The A4 opener's closing line, inviting the runner to reply — which brings the
# fuller turn sooner. Inline-keyboard tap delivery of the RPE/pain options is I1;
# for now the opener notification is prose + this plain-text line + the app link.
OPENER_REPLY_LINE = "Let me know how it felt and I'll follow up with the full breakdown."


def opener_body(content: CoachMessageReport) -> str:
    """The A4 opener notification body: the opener prose + the reply-invite line."""
    prose = (content.opener_message or "").strip()
    return f"{prose}\n\n{OPENER_REPLY_LINE}" if prose else OPENER_REPLY_LINE
