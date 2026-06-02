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
        response = httpx.post(
            url,
            json={
                "chat_id": self.chat_id,
                "text": self._format(notification),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok", False):
            raise RuntimeError(
                f"Telegram API rejected the message: {payload.get('description', 'unknown error')}"
            )

    def _format(self, notification: Notification) -> str:
        """Build the HTML-mode message body: bold title, text, link."""
        parts = [f"<b>{escape(notification.subject)}</b>"]
        if notification.text:
            parts.append(escape(notification.text))
        if notification.url:
            link = escape(notification.url, quote=True)
            parts.append(f'<a href="{link}">View in app</a>')
        return "\n\n".join(parts)
