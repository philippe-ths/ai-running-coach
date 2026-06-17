from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from app.schemas.coach import CoachReportRead


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


class NotificationRenderer(Protocol):
    """Rendering interface for a delivery channel (#333).

    A channel adapter that knows how to turn a coach report or a deterministic
    receipt into its own wire-shaped `Notification` implements this. It is the
    one place each channel handles BOTH report shapes (prose vs structured) and
    both stages (opener vs fuller), so the composer stays a thin channel
    dispatcher and a new output shape is handled in one place per adapter.

    Separate from `NotifierPort` (transport / `send`) on purpose: rendering is a
    pure transformation with no side effects, and the in-memory / no-op
    transports carry no rendering of their own. `render_*` are class methods on
    the real channel adapters so the composer can render for the active channel
    without constructing a transport (and independent of any `set_notifier`
    test override)."""

    def render_coach_report(
        self,
        *,
        report: "CoachReportRead",
        headline: str,
        distance_m: int,
        app_base_url: str,
        stage: str,
        to: str,
    ) -> Notification: ...

    def render_receipt(
        self,
        *,
        receipt_text: str,
        headline: str,
        activity_id: str,
        distance_m: int,
        app_base_url: str,
        to: str,
    ) -> Notification: ...
