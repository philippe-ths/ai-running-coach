import logging
from html import escape

import httpx

from app.services.notifications.port import Notification

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20
_API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    """Telegram Bot API transport over HTTPS.

    Railway blocks outbound SMTP from deployed services, so coach reports are
    delivered as Telegram messages over the Bot API (port 443) instead. Pure
    transport: message content is rendered upstream; this adapter only formats
    the wire payload (HTML mode) and performs the send.
    """

    def __init__(
        self,
        *,
        bot_token: str,
        chat_id: str,
        api_base: str = _API_BASE,
        timeout: float = _TIMEOUT_SECONDS,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base = api_base
        self.timeout = timeout

    def send(self, notification: Notification) -> None:
        url = f"{self.api_base}/bot{self.bot_token}/sendMessage"
        body = {
            "chat_id": self.chat_id,
            "text": self._format(notification),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        reply_markup = self._reply_markup(notification)
        if reply_markup is not None:
            body["reply_markup"] = reply_markup
        response = httpx.post(url, json=body, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError(
                f"Telegram API rejected the message: {payload.get('description', 'unknown error')}"
            )

    def answer_callback(self, callback_query_id: str, *, text: str = "") -> None:
        """Acknowledge a tapped inline-keyboard button (I1b).

        Telegram shows the button as a spinner until the bot answers; this clears
        it (optionally with a brief toast). Best-effort by the caller's contract,
        but still raises on a hard transport/API error so failures are visible in
        logs rather than silently swallowed here."""
        url = f"{self.api_base}/bot{self.bot_token}/answerCallbackQuery"
        body: dict = {"callback_query_id": callback_query_id}
        if text:
            body["text"] = text
        response = httpx.post(url, json=body, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError(
                f"Telegram API rejected answerCallbackQuery: {payload.get('description', 'unknown error')}"
            )

    def edit_message_reply_markup(
        self, *, message_id: int, reply_markup: dict
    ) -> None:
        """Replace an existing message's inline keyboard (the #230 tap mark).

        Same error contract as answer_callback: best-effort by the caller, but
        raises on transport/API errors so failures land in logs."""
        url = f"{self.api_base}/bot{self.bot_token}/editMessageReplyMarkup"
        body = {
            "chat_id": self.chat_id,
            "message_id": message_id,
            "reply_markup": reply_markup,
        }
        response = httpx.post(url, json=body, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError(
                f"Telegram API rejected editMessageReplyMarkup: {payload.get('description', 'unknown error')}"
            )

    @staticmethod
    def _reply_markup(notification: Notification) -> dict | None:
        """Build an inline keyboard from the notification's actions, or None.

        One button per action, each on its own row (RPE/pain scales read better
        vertically on a phone), `callback_data` carrying the opaque token."""
        if not notification.actions:
            return None
        keyboard = [
            [{"text": action.label, "callback_data": action.token}]
            for action in notification.actions
        ]
        return {"inline_keyboard": keyboard}

    def _format(self, notification: Notification) -> str:
        """Build the HTML-mode message body: bold title, text, link."""
        parts = [f"<b>{escape(notification.subject)}</b>"]
        if notification.text:
            parts.append(escape(notification.text))
        if notification.url:
            link = escape(notification.url, quote=True)
            parts.append(f'<a href="{link}">View in app</a>')
        return "\n\n".join(parts)
