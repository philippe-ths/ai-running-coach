from app.schemas.coach import CoachReportRead
from app.services.notifications._prose import (
    TELEGRAM_BODY_LIMIT,
    is_message_report,
    opener_body,
    truncate_at_paragraph,
)


def render_coach_report_telegram(
    *,
    report: CoachReportRead,
    headline: str,
    distance_m: int,
    app_base_url: str,
    stage: str = "fuller",
) -> tuple[str, str, str]:
    """Render title + plain-text body + activity URL for a Telegram message.

    The body is plain text (no markup); the Telegram adapter owns wire-format
    concerns (HTML escaping, the title in bold, the link). The URL is returned
    separately so the adapter can attach it out of band rather than inline.

    `stage` ("fuller" default, or "opener" for the A4 stage-one notification)
    selects which prose to render: the fuller `message` or the opener_message plus
    the reply-invite line. Default keeps every existing caller byte-stable.
    """
    # #338: the internal confidence rating is no longer surfaced to the runner,
    # including in the notification title (it still lives in report.meta).
    distance_km = round((distance_m or 0) / 1000.0, 1)
    activity_label = headline or "Activity"
    if distance_km > 0:
        title = f"{activity_label} — {distance_km}km"
    else:
        title = activity_label

    activity_url = f"{app_base_url.rstrip('/')}/activity/{report.activity_id}"

    content = report.report

    # A3 (ADR 0009): the prose message IS the product, so the Telegram body is the
    # message itself, truncated at a paragraph boundary under the 4096 limit. The
    # structured tail (chips, next steps) lives in the app, not the notification.
    if is_message_report(content):
        prose = opener_body(content) if stage == "opener" else content.message
        body = truncate_at_paragraph(prose, TELEGRAM_BODY_LIMIT)
        return title, body, activity_url

    lines: list[str] = ["Key takeaways:"]
    for t in content.key_takeaways:
        lines.append(f"• {t.text}")
    lines += ["", "Next steps:"]
    for s in content.next_steps:
        lines.append(f"• {s.action}: {s.details}")
        lines.append(f"   Why: {s.why}")
    if content.risks:
        lines += ["", "Risks:"]
        for r in content.risks:
            lines.append(f"• {r.flag}: {r.explanation}")
            lines.append(f"   Mitigation: {r.mitigation}")
    if content.questions:
        lines += ["", "Follow-up questions:"]
        for q in content.questions:
            lines.append(f"• {q.question} ({q.reason})")

    body = "\n".join(lines)
    return title, body, activity_url
