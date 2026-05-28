from unittest.mock import MagicMock, patch

from app.services.notifications import Notification
from app.services.notifications.smtp_adapter import SMTPNotifier


def _build_notification() -> Notification:
    return Notification(
        to="runner@example.com",
        subject="Easy run — 8.2km · medium confidence",
        html="<html><body><h1>Coach report</h1></body></html>",
        text="Coach report\n",
    )


def test_smtp_notifier_sends_via_starttls():
    notifier = SMTPNotifier(
        host="smtp.example.com",
        port=587,
        username="user",
        password="pw",
        from_addr="coach@example.com",
        use_starttls=True,
    )
    fake_server = MagicMock()
    with patch("smtplib.SMTP", return_value=fake_server) as smtp_cls:
        notifier.send(_build_notification())

    smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=20)
    enter_calls = [c[0] for c in fake_server.method_calls]
    # Order matters: starttls before login, login before send_message
    assert enter_calls.index("starttls") < enter_calls.index("login")
    assert enter_calls.index("login") < enter_calls.index("send_message")
    fake_server.login.assert_called_once_with("user", "pw")
    fake_server.send_message.assert_called_once()
    sent_message = fake_server.send_message.call_args.args[0]
    assert sent_message["From"] == "coach@example.com"
    assert sent_message["To"] == "runner@example.com"
    assert sent_message["Subject"] == "Easy run — 8.2km · medium confidence"
    # Multipart/alternative with both text and html parts
    parts = list(sent_message.iter_parts())
    content_types = {p.get_content_type() for p in parts}
    assert content_types == {"text/plain", "text/html"}
    fake_server.quit.assert_called_once()


def test_smtp_notifier_uses_ssl_when_starttls_false():
    notifier = SMTPNotifier(
        host="smtp.example.com",
        port=465,
        username="user",
        password="pw",
        from_addr="coach@example.com",
        use_starttls=False,
    )
    fake_server = MagicMock()
    with patch("smtplib.SMTP_SSL", return_value=fake_server) as ssl_cls:
        notifier.send(_build_notification())

    ssl_cls.assert_called_once_with("smtp.example.com", 465, timeout=20)
    fake_server.starttls.assert_not_called()
    fake_server.login.assert_called_once_with("user", "pw")
    fake_server.send_message.assert_called_once()


def test_smtp_notifier_skips_login_when_no_credentials():
    notifier = SMTPNotifier(
        host="smtp.example.com",
        port=587,
        username="",
        password="",
        from_addr="coach@example.com",
        use_starttls=True,
    )
    fake_server = MagicMock()
    with patch("smtplib.SMTP", return_value=fake_server):
        notifier.send(_build_notification())
    fake_server.login.assert_not_called()
    fake_server.send_message.assert_called_once()
