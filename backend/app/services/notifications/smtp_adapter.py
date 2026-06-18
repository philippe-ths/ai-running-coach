import logging
import smtplib
from email.message import EmailMessage
from html import escape
from typing import TYPE_CHECKING

from app.services.notifications.port import Notification

if TYPE_CHECKING:
    from app.schemas.coach import CoachReportRead

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20


class SMTPNotifier:
    """SMTP transport. STARTTLS by default; SSL when use_starttls=False.

    The adapter owns BOTH halves of the email channel: rendering (the `render_*`
    class methods turn a coach report or a deterministic receipt into an
    email-shaped Notification with subject + HTML + plaintext) and transport
    (`send` builds the MIME message and delivers it). Email carries no tappable
    affordances, so `render_*` leave `actions` empty. Rendering is stateless, so
    `render_*` are class methods the composer calls without a transport."""

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
        """Render a coach report into an email Notification (#333).

        Delegates the subject/HTML/plaintext bodies to the email channel template
        (prose or structured, opener or fuller). No tappable affordances."""
        from app.services.notifications.email_template import (
            render_coach_report_email,
        )

        subject, html, text = render_coach_report_email(
            report=report,
            headline=headline,
            distance_m=distance_m,
            app_base_url=app_base_url,
            stage=stage,
        )
        return Notification(to=to, subject=subject, html=html, text=text)

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
        """Render a deterministic receipt (#296) into an email Notification.

        Email cannot tap, so the receipt renders as prose + the activity link in
        the body, with no affordances."""
        from app.services.notifications._prose import activity_url, receipt_title

        return Notification(
            to=to,
            subject=receipt_title(headline, distance_m),
            html=f"<p>{escape(receipt_text)}</p>",
            text=receipt_text,
            url=activity_url(app_base_url, activity_id),
        )

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        use_starttls: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.use_starttls = use_starttls

    def send(self, notification: Notification) -> None:
        message = EmailMessage()
        message["From"] = self.from_addr
        message["To"] = notification.to
        message["Subject"] = notification.subject
        message.set_content(notification.text)
        message.add_alternative(notification.html, subtype="html")

        if self.use_starttls:
            server = smtplib.SMTP(self.host, self.port, timeout=_TIMEOUT_SECONDS)
        else:
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=_TIMEOUT_SECONDS)
        try:
            server.ehlo()
            if self.use_starttls:
                server.starttls()
                server.ehlo()
            if self.username and self.password:
                server.login(self.username, self.password)
            server.send_message(message)
        finally:
            server.quit()
