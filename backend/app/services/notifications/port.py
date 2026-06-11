from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class NotificationAction:
    """A channel-agnostic tappable affordance carried on a Notification (I1b,
    #220). `label` is the button text; `token` is an opaque, self-contained
    payload a channel echoes back when tapped so the inbound handler can resolve
    the action without server-side state. The Telegram adapter renders these as
    an inline keyboard (token -> callback_data); channels without buttons (email)
    ignore them."""

    label: str
    token: str


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
    # Tappable quick-reply affordances (I1b). Empty for every existing caller;
    # populated only for the A4 opener so its RPE/pain options become buttons.
    actions: tuple["NotificationAction", ...] = field(default_factory=tuple)


@runtime_checkable
class NotifierPort(Protocol):
    """Transport interface for outbound notifications. Pure side effects."""

    def send(self, notification: Notification) -> None: ...
