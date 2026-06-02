from html import escape

from app.schemas.coach import CoachReportRead


def render_login_email(*, verify_url: str, ttl_minutes: int) -> tuple[str, str, str]:
    """Render subject + HTML body + plain-text body for a magic-link email."""
    subject = "Your Running Coach sign-in link"
    safe_url = escape(verify_url)
    html = (
        "<!doctype html><html><body style=\"font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:16px;\">"
        "<h2>Sign in to Running Coach</h2>"
        "<p>Click the button below to sign in. This link works once and expires "
        f"in {ttl_minutes} minutes.</p>"
        f"<p><a href=\"{safe_url}\" style=\"display:inline-block;padding:10px 16px;"
        "background:#111;color:#fff;border-radius:6px;text-decoration:none;\">Sign in</a></p>"
        f"<p style=\"color:#666;font-size:13px;\">Or paste this link into your browser:<br/>{safe_url}</p>"
        "<p style=\"color:#666;font-size:13px;\">If you did not request this, you can ignore this email.</p>"
        "</body></html>"
    )
    text = (
        "Sign in to Running Coach\n\n"
        f"Open this link to sign in (works once, expires in {ttl_minutes} minutes):\n"
        f"{verify_url}\n\n"
        "If you did not request this, you can ignore this email.\n"
    )
    return subject, html, text


def render_coach_report_email(
    *,
    report: CoachReportRead,
    activity_class: str,
    distance_m: int,
    app_base_url: str,
) -> tuple[str, str, str]:
    """Render subject + HTML body + plain-text body for a coach report email."""
    distance_km = round((distance_m or 0) / 1000.0, 1)
    confidence = report.meta.confidence
    activity_label = activity_class or "Activity"
    # Subject shape: "{label} — {dist}km · {conf} confidence" for activities
    # with distance, "{label} — {conf} confidence" for those without (indoor
    # rides record 0m). The label already includes the noun (e.g. "Easy Run",
    # "Indoor Ride"), so we don't append it.
    if distance_km > 0:
        subject = f"{activity_label} — {distance_km}km · {confidence} confidence"
    else:
        subject = f"{activity_label} — {confidence} confidence"

    activity_url = f"{app_base_url.rstrip('/')}/activity/{report.activity_id}"

    html = _render_html(report, activity_label, distance_km, confidence, activity_url)
    text = _render_text(report, activity_label, distance_km, confidence, activity_url)
    return subject, html, text


def _render_html(report, activity_label, distance_km, confidence, activity_url) -> str:
    content = report.report

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

    heading_distance = f" · {distance_km}km" if distance_km > 0 else ""
    return (
        "<!doctype html><html><body style=\"font-family:-apple-system,sans-serif;max-width:600px;margin:0 auto;padding:16px;\">"
        f"<h2>{escape(activity_label)}{heading_distance}</h2>"
        f"<p style=\"color:#666;\">Confidence: <strong>{escape(confidence)}</strong></p>"
        "<h3>Key takeaways</h3>"
        f"<ul>{takeaways_items}</ul>"
        "<h3>Next steps</h3>"
        f"<ol>{next_steps_items}</ol>"
        f"{risks_block}"
        f"{questions_block}"
        f"<p><a href=\"{escape(activity_url)}\">View in app</a></p>"
        "</body></html>"
    )


def _render_text(report, activity_label, distance_km, confidence, activity_url) -> str:
    content = report.report

    if distance_km > 0:
        header = f"{activity_label} — {distance_km}km · {confidence} confidence"
    else:
        header = f"{activity_label} — {confidence} confidence"
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
