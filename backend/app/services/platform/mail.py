"""The deployment's mail server, when it has one (``SMTP_*``, ``docs/CONFIGURATION.md``).

Nothing is sent unless ``SMTP_HOST`` is set: ``enabled`` answers that, and
every caller asks before it queues a message, so a deployment without SMTP
never waits on mail. ``smtp_send`` sends one message, bounded by
``SMTP_TIMEOUT_SECONDS``; it is called off the request path (the Manager's
brief loop, the outbox processor) and raises ``OSError`` or
``smtplib.SMTPException`` for its caller to record and retry.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from config import settings


def enabled() -> bool:
    return bool((settings.SMTP_HOST or "").strip())


def sender() -> str:
    return settings.SMTP_FROM or settings.SMTP_USER or "la-fabrique@localhost"


def smtp_send(message: EmailMessage) -> None:
    """Send *message* through the configured SMTP server."""
    host, port = settings.SMTP_HOST.strip(), settings.SMTP_PORT
    timeout = settings.SMTP_TIMEOUT_SECONDS
    if settings.SMTP_SSL:
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
    with server:
        if settings.SMTP_STARTTLS and not settings.SMTP_SSL:
            server.starttls(context=ssl.create_default_context())
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(message)
