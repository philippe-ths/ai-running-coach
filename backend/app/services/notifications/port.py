from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Notification:
    to: str
    subject: str
    html: str
    text: str


@runtime_checkable
class NotifierPort(Protocol):
    """Transport interface for outbound notifications. Pure side effects."""

    def send(self, notification: Notification) -> None: ...
