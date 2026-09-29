"""Register the inbound Telegram webhook when the web process boots (#1024).

Telegram only delivers button taps and `/start` to a URL the bot has registered,
and revoking the bot token drops that registration. Registering from the running
service means a token rotation needs nothing beyond updating the env var and
redeploying: the token and secret are used where they already live and never
have to be handled by hand.
"""

import logging
import os
from collections.abc import Mapping

from app.core.config import Settings
from app.services.notifications.telegram_adapter import TelegramNotifier

logger = logging.getLogger(__name__)

WEBHOOK_PATH = "/api/webhooks/telegram"


def webhook_url(settings: Settings, environ: Mapping[str, str] = os.environ) -> str | None:
    """This backend's public webhook URL, or None when it has no public https
    address. `API_BASE_URL` is the declared backend base; Railway's own
    `RAILWAY_PUBLIC_DOMAIN` is the fallback when that was left at its default."""
    base = settings.API_BASE_URL.rstrip("/")
    if base.startswith("https://") and "localhost" not in base:
        return base + WEBHOOK_PATH
    domain = environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if domain:
        return f"https://{domain}{WEBHOOK_PATH}"
    return None


def register_webhook(settings: Settings, environ: Mapping[str, str] = os.environ) -> bool:
    """Register the webhook in production; never raises, so a Telegram outage
    cannot stop the web process booting. Returns whether it registered."""
    if settings.APP_ENV != "production":
        return False
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_WEBHOOK_SECRET:
        return False
    url = webhook_url(settings, environ)
    if url is None:
        logger.warning("telegram_webhook_not_registered: no public https address for this service")
        return False
    notifier = TelegramNotifier(bot_token=settings.TELEGRAM_BOT_TOKEN, chat_id="")
    try:
        notifier.set_webhook(url=url, secret_token=settings.TELEGRAM_WEBHOOK_SECRET)
    except Exception as exc:  # TelegramAPIError carries no token; see the adapter
        logger.warning("telegram_webhook_not_registered: %s", exc)
        return False
    logger.info("telegram_webhook_registered url=%s", url)
    return True
