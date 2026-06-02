from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.notifications import Notification, NotifierPort
from app.services.notifications.telegram_adapter import TelegramNotifier


def _notifier() -> TelegramNotifier:
    return TelegramNotifier(bot_token="123:ABC", chat_id="42")


def _notification() -> Notification:
    return Notification(
        to="42",
        subject="Easy Run — 8.2km · medium confidence",
        html="",
        text="Key takeaways:\n• Stayed in zone 2.",
        url="https://app.example.com/activity/abc",
    )


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True, "result": {"message_id": 1}}
    return response


def test_satisfies_notifier_port_protocol():
    assert isinstance(_notifier(), NotifierPort)


def test_send_posts_expected_payload_to_bot_api():
    with patch(
        "app.services.notifications.telegram_adapter.httpx.post",
        return_value=_ok_response(),
    ) as post:
        _notifier().send(_notification())

    post.assert_called_once()
    called_url = post.call_args.args[0]
    assert called_url == "https://api.telegram.org/bot123:ABC/sendMessage"
    body = post.call_args.kwargs["json"]
    assert body["chat_id"] == "42"
    assert body["parse_mode"] == "HTML"
    # Title is bolded, body carried, link attached.
    assert "<b>Easy Run — 8.2km · medium confidence</b>" in body["text"]
    assert "Stayed in zone 2." in body["text"]
    assert '<a href="https://app.example.com/activity/abc">View in app</a>' in body["text"]
    assert post.call_args.kwargs["timeout"] == 20


def test_send_escapes_html_special_characters_in_content():
    notification = Notification(
        to="42",
        subject="5 < 6 & rising",
        html="",
        text="splits: 4:30 < 4:45 & dropping",
        url=None,
    )
    with patch(
        "app.services.notifications.telegram_adapter.httpx.post",
        return_value=_ok_response(),
    ) as post:
        _notifier().send(notification)

    text = post.call_args.kwargs["json"]["text"]
    # Raw angle brackets / ampersands from content must be escaped so they are
    # not parsed as Telegram HTML entities.
    assert "5 &lt; 6 &amp; rising" in text
    assert "4:30 &lt; 4:45 &amp; dropping" in text


def test_send_raises_on_http_error_status():
    response = MagicMock()
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401", request=MagicMock(), response=MagicMock()
    )
    with patch(
        "app.services.notifications.telegram_adapter.httpx.post",
        return_value=response,
    ):
        with pytest.raises(httpx.HTTPStatusError):
            _notifier().send(_notification())


def test_send_raises_when_api_reports_not_ok():
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": False, "description": "chat not found"}
    with patch(
        "app.services.notifications.telegram_adapter.httpx.post",
        return_value=response,
    ):
        with pytest.raises(RuntimeError, match="chat not found"):
            _notifier().send(_notification())
