"""render_login_email (ADR 0005)."""

from app.services.notifications.email_template import render_login_email


def test_login_email_contains_link_and_ttl():
    url = "http://localhost:3000/api/auth/verify?token=abc123"
    subject, html, text = render_login_email(verify_url=url, ttl_minutes=15)

    assert "sign-in" in subject.lower()
    assert url in text
    assert url in html
    assert "15 minutes" in text
    assert "15 minutes" in html


def test_login_email_escapes_url_in_html():
    url = "http://localhost:3000/api/auth/verify?token=a&b=<script>"
    _subject, html, _text = render_login_email(verify_url=url, ttl_minutes=15)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
