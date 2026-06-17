"""Shared helpers for rendering the A3 prose-message report into notifications.

The A3 output is a human prose `message` (ADR 0009); a notification is one
transmission of it. These helpers let the email and Telegram templates render the
prose faithfully — paragraphs preserved, truncated at a paragraph boundary when a
channel has a hard length limit (Telegram's 4096) — and detect which output shape
a stored report carries.
"""

from __future__ import annotations

from app.schemas.coach import CoachMessageReport, CoachReportRead
from app.services.notifications.callback_token import encode as encode_callback_token
from app.services.notifications.port import NotificationAction

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


def opener_actions(report: CoachReportRead) -> tuple[NotificationAction, ...]:
    """Build tappable RPE/pain actions from a message report's questions (I1b).

    Mirrors the in-app contract (`CoachReportPanel.handleOptionTap`): only `rpe`
    and `pain` options are tappable, and a tap carries the option's numeric
    payload. Skips any option whose payload is not an integer (so a malformed
    option drops out rather than producing a dead button). Returns empty for the
    structured legacy shape or a report with no tappable options.

    A channel-agnostic affordance helper used by the channel adapters that carry
    buttons (Telegram); channels without buttons (email) ignore the actions."""
    content = report.report
    questions = getattr(content, "questions", None)
    if not questions:
        return ()
    actions: list[NotificationAction] = []
    for q in questions:
        for opt in getattr(q, "options", []) or []:
            if opt.kind not in ("rpe", "pain"):
                continue
            try:
                value = int(opt.payload)
            except (TypeError, ValueError):
                continue
            try:
                token = encode_callback_token(
                    kind=opt.kind,
                    activity_id=str(report.activity_id),
                    value=value,
                )
            except ValueError:
                continue
            actions.append(NotificationAction(label=opt.label, token=token))
    return tuple(actions)


def receipt_actions(activity_id: str) -> tuple[NotificationAction, ...]:
    """Build the deterministic receipt tap keyboard (#296): the fixed RPE/pain
    quick-replies plus the "done" control, each carrying its opaque callback
    token. A tap whose token cannot be encoded is dropped rather than producing a
    dead button (mirrors `opener_actions`)."""
    from app.services.coach.receipt import RECEIPT_TAPS

    actions: list[NotificationAction] = []
    for tap in RECEIPT_TAPS:
        try:
            token = encode_callback_token(
                kind=tap.kind, activity_id=str(activity_id), value=tap.value
            )
        except ValueError:
            continue
        actions.append(NotificationAction(label=tap.label, token=token))
    return tuple(actions)


def receipt_title(headline: str, distance_m: int) -> str:
    """The deterministic-receipt notification title: "{headline} — {dist}km",
    dropping the distance suffix when distance is 0 (e.g. an indoor session)."""
    distance_km = round((distance_m or 0) / 1000.0, 1)
    label = headline or "Activity"
    return f"{label} — {distance_km}km" if distance_km > 0 else label


def activity_url(app_base_url: str, activity_id) -> str:
    """The deep link to an activity, with a trailing slash on the base trimmed."""
    return f"{app_base_url.rstrip('/')}/activity/{activity_id}"
