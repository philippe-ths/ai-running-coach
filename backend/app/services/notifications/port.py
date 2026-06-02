from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Notification:
    to: str
    subject: str
    html: str
    text: str
    # Deep link to the activity. Email embeds it in the body; channels that
    # carry it out of band (e.g. Telegram) read it from here. Optional so the
    # field stays backward-compatible with email-only callers.
    url: str | None = None


@runtime_checkable
class NotifierPort(Protocol):
    """Transport interface for outbound notifications. Pure side effects."""

    def send(self, notification: Notification) -> None: ...
