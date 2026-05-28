import logging

from app.services.notifications.port import Notification

logger = logging.getLogger(__name__)


class NoOpNotifier:
    """Notifier that drops sends. Used when SMTP is not configured."""

    def send(self, notification: Notification) -> None:
        logger.info(
            "Notifier no-op (SMTP not configured); would have sent to %s subject=%r",
            notification.to,
            notification.subject,
        )
