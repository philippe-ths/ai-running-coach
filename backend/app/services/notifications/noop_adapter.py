import logging

from app.services.notifications.port import Notification

logger = logging.getLogger(__name__)


class NoOpNotifier:
    """Notifier that drops sends. Used when no channel is configured."""

    def send(self, notification: Notification) -> None:
        logger.info(
            "Notifier no-op (no channel configured); would have sent to %s subject=%r",
            notification.to,
            notification.subject,
        )
