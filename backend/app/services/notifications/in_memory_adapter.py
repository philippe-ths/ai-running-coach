from app.services.notifications.port import Notification


class InMemoryNotifier:
    """In-memory notifier for tests. Captures sends in a list."""

    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        self.sent.append(notification)
