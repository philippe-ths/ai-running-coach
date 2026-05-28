"""Sentry SDK init and structured JSON logging.

Called from the entrypoint of each process group (web, worker, scheduler) so
unhandled exceptions and error-level logs from any of them reach Sentry, and
so log output is uniformly structured for ingestion by `flyctl logs` or any
future log aggregator.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.logging import LoggingIntegration
from sentry_sdk.integrations.rq import RqIntegration

from app.core.config import settings


_STANDARD_LOG_RECORD_FIELDS = frozenset(
    {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    }
)


class JSONFormatter(logging.Formatter):
    """Render a LogRecord as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = value
        return json.dumps(payload, default=str)


def init_logging() -> None:
    """Replace root handlers with a single stdout handler.

    Emits JSON when APP_ENV is "production"; pretty otherwise. Idempotent —
    repeated calls only ever leave one handler attached.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    if settings.APP_ENV == "production":
        handler.setFormatter(JSONFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
        )
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def init_sentry(component: str) -> None:
    """Initialise the Sentry SDK. No-op when SENTRY_DSN is unset.

    `component` is the Fly process group ("web", "worker", "scheduler") and is
    attached to every event as a tag so the Sentry UI can filter by process.
    """
    if not settings.SENTRY_DSN:
        return
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.APP_ENV,
        traces_sample_rate=0.0,
        integrations=[
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
            RqIntegration(),
        ],
    )
    sentry_sdk.set_tag("component", component)
