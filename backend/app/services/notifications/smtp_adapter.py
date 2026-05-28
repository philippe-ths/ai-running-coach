import logging
import smtplib
from email.message import EmailMessage

from app.services.notifications.port import Notification

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20


class SMTPNotifier:
    """SMTP transport. STARTTLS by default; SSL when use_starttls=False."""

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
