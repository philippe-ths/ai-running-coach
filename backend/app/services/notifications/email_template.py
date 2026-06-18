from html import escape

from app.schemas.coach import CoachReportRead
from app.services.notifications._prose import (
    is_message_report,
    message_paragraphs,
    opener_body,
)


def render_coach_report_email(
    *,
    report: CoachReportRead,
    headline: str,
    distance_m: int,
    app_base_url: str,
    stage: str = "fuller",
) -> tuple[str, str, str]:
    """Render subject + HTML body + plain-text body for a coach report email.

    `stage` ("fuller" default, or "opener" for the A4 stage-one email) selects the
    fuller `message` or the opener_message + reply-invite line. Default keeps every
    existing caller byte-stable."""
    # #338: the internal confidence rating is no longer surfaced to the runner,
    # including in the email subject and body (it still lives in report.meta).
    distance_km = round((distance_m or 0) / 1000.0, 1)
    activity_label = headline or "Activity"
    # Subject shape: "{label} — {dist}km" for activities with distance, "{label}"
    # for those without (indoor rides record 0m). The label already includes the
    # noun (e.g. "Easy Run", "Indoor Ride"), so we don't append it.
    if distance_km > 0:
        subject = f"{activity_label} — {distance_km}km"
    else:
        subject = activity_label

    activity_url = f"{app_base_url.rstrip('/')}/activity/{report.activity_id}"

    html = _render_html(report, activity_label, distance_km, activity_url, stage)
    text = _render_text(report, activity_label, distance_km, activity_url, stage)
    return subject, html, text


def _render_html(report, activity_label, distance_km, activity_url, stage="fuller") -> str:
    content = report.report
    heading_distance = f" · {distance_km}km" if distance_km > 0 else ""

    # A3 (ADR 0009): render the prose message as paragraphs; the message is the
    # product, the structured tail stays in the app. A4: the opener renders its
    # opener_message + the reply-invite line instead.
    if is_message_report(content):
        prose = opener_body(content) if stage == "opener" else content.message
        paragraphs = "".join(
            f"<p>{escape(p)}</p>" for p in message_paragraphs(prose)
        )
        return (
            "<!doctype html><html><body style=\"font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:16px;\">"
            f"<h2>{escape(activity_label)}{heading_distance}</h2>"
            f"{paragraphs}"
            f"<p><a href=\"{escape(activity_url)}\">View in app</a></p>"
            "</body></html>"
        )

    takeaways_items = "".join(
        f"<li>{escape(t.text)}</li>" for t in content.key_takeaways
    )
    next_steps_items = "".join(
        (
            "<li>"
            f"<strong>{escape(s.action)}</strong>"
            f"<div>{escape(s.details)}</div>"
            f"<div style=\"color:#666;\"><em>Why:</em> {escape(s.why)}</div>"
            "</li>"
        )
        for s in content.next_steps
    )
    risks_block = ""
    if content.risks:
        risks_items = "".join(
            (
                "<li>"
                f"<strong>{escape(r.flag)}</strong>: {escape(r.explanation)}<br/>"
                f"<em>Mitigation:</em> {escape(r.mitigation)}"
                "</li>"
            )
            for r in content.risks
        )
        risks_block = f"<h3>Risks</h3><ul>{risks_items}</ul>"

    questions_block = ""
    if content.questions:
        questions_items = "".join(
            (
                "<li>"
                f"<strong>{escape(q.question)}</strong><br/>"
                f"<em>{escape(q.reason)}</em>"
                "</li>"
            )
            for q in content.questions
        )
        questions_block = f"<h3>Follow-up questions</h3><ul>{questions_items}</ul>"

    return (
        "<!doctype html><html><body style=\"font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:16px;\">"
        f"<h2>{escape(activity_label)}{heading_distance}</h2>"
        "<h3>Key takeaways</h3>"
        f"<ul>{takeaways_items}</ul>"
        "<h3>Next steps</h3>"
        f"<ol>{next_steps_items}</ol>"
        f"{risks_block}"
        f"{questions_block}"
        f"<p><a href=\"{escape(activity_url)}\">View in app</a></p>"
        "</body></html>"
    )


def _render_text(report, activity_label, distance_km, activity_url, stage="fuller") -> str:
    content = report.report

    if distance_km > 0:
        header = f"{activity_label} — {distance_km}km"
    else:
        header = activity_label

    # A3 (ADR 0009): the plain-text body is the prose message. A4: the opener
    # renders its opener_message + the reply-invite line instead.
    if is_message_report(content):
        prose = opener_body(content) if stage == "opener" else content.message.strip()
        lines = [header, "", prose, "", f"View in app: {activity_url}"]
        return "\n".join(lines) + "\n"

    lines = [
        header,
        "",
        "Key takeaways:",
    ]
    for t in content.key_takeaways:
        lines.append(f"  - {t.text}")
    lines += ["", "Next steps:"]
    for s in content.next_steps:
        lines.append(f"  - {s.action}: {s.details}")
        lines.append(f"      Why: {s.why}")
    if content.risks:
        lines += ["", "Risks:"]
        for r in content.risks:
            lines.append(f"  - {r.flag}: {r.explanation}")
            lines.append(f"      Mitigation: {r.mitigation}")
    if content.questions:
        lines += ["", "Follow-up questions:"]
        for q in content.questions:
            lines.append(f"  - {q.question} ({q.reason})")
    lines += ["", f"View in app: {activity_url}"]
    return "\n".join(lines) + "\n"
