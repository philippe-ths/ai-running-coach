import logging
from html import escape
from typing import TYPE_CHECKING

import httpx

from app.services.notifications.port import Notification

if TYPE_CHECKING:
    from app.schemas.coach import CoachReportRead

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20
_API_BASE = "https://api.telegram.org"


class TelegramNotifier:
    """Telegram Bot API transport over HTTPS.

    Railway blocks outbound SMTP from deployed services, so coach reports are
    delivered as Telegram messages over the Bot API (port 443) instead. The
    adapter owns BOTH halves of the channel: rendering (the `render_*` class
    methods turn a coach report or a deterministic receipt into a Telegram-shaped
    Notification, including the tappable inline keyboard) and transport (`send`
    formats the wire payload in HTML mode and POSTs it). Rendering is stateless,
    so `render_*` are class methods the composer calls without a transport.
    """

    @classmethod
    def render_coach_report(
        cls,
        *,
        report: "CoachReportRead",
        headline: str,
        distance_m: int,
        app_base_url: str,
        stage: str,
        to: str,
    ) -> Notification:
        """Render a coach report into a Telegram Notification (#333).

        The body is the channel template's plain-text rendering of the report
        (prose or structured, opener or fuller). Only the opener carries tappable
        RPE/pain buttons; the fuller turn is the response to that input, so it has
        no quick-reply affordances. HTML/title/link wire formatting stays in
        `send`."""
        from app.services.notifications._prose import opener_actions
        from app.services.notifications.telegram_template import (
            render_coach_report_telegram,
        )

        subject, text, url = render_coach_report_telegram(
            report=report,
            headline=headline,
            distance_m=distance_m,
            app_base_url=app_base_url,
            stage=stage,
        )
        actions = opener_actions(report) if stage == "opener" else ()
        return Notification(
            to=to, subject=subject, html="", text=text, url=url, actions=actions
        )

    @classmethod
    def render_receipt(
        cls,
        *,
        receipt_text: str,
        headline: str,
        activity_id: str,
        distance_m: int,
        app_base_url: str,
        to: str,
    ) -> Notification:
        """Render a deterministic receipt (#296) into a Telegram Notification.

        Carries the fixed RPE/pain/done tap keyboard; the body is the receipt
        prose verbatim, the title the activity headline + distance."""
        from app.services.notifications._prose import (
            activity_url,
            receipt_actions,
            receipt_title,
        )

        return Notification(
            to=to,
            subject=receipt_title(headline, distance_m),
            html="",
            text=receipt_text,
            url=activity_url(app_base_url, activity_id),
            actions=receipt_actions(activity_id),
        )

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
