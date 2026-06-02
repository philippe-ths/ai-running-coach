"""Structured JSON logging, with optional Sentry error tracking.

Called from the entrypoint of each process group (web, worker, scheduler) so
log output is uniformly structured for ingestion by the hosting platform's log
collector. Error tracking via Sentry is optional and logs-only by default (see
issue #102): `sentry_sdk` is not a runtime dependency and `init_sentry` is a
no-op unless both the SDK is installed (`pip install -e ".[observability]"`)
and `SENTRY_DSN` is set.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

try:
    import sentry_sdk
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.rq import RqIntegration

    _SENTRY_AVAILABLE = True
except ImportError:
    _SENTRY_AVAILABLE = False

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


def sentry_capture_active() -> bool:
    """True only when Sentry error capture is actually live.

    Capture requires *both* the optional `observability` extra installed (so
    `sentry_sdk` imported) *and* `SENTRY_DSN` set. A DSN alone is not enough
    (see issue #102: logs-only by default). This is the single source of truth
    for "is error tracking on", so no path can advertise capture that is not
    actually wired up.
    """
    return _SENTRY_AVAILABLE and bool(settings.SENTRY_DSN)


def init_sentry(component: str) -> None:
    """Initialise the Sentry SDK. No-op unless the SDK is installed and
    SENTRY_DSN is set.

    Sentry is optional (logs-only by default; see issue #102). When the
    `observability` extra is not installed, `sentry_sdk` is absent and this is
    a no-op regardless of SENTRY_DSN.

    `component` is the process group ("web", "worker", "scheduler") and is
    attached to every event as a tag so the Sentry UI can filter by process.

    Failures inside `sentry_sdk.init` (malformed DSN, startup network policy,
    bad integration kwarg) are caught and logged. They must not prevent the
    process from booting; the same image runs `alembic upgrade head` as the
    deploy's release command, so an import-time crash here can block deploys.
    """
    if not sentry_capture_active():
        return
    try:
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
    except Exception:
        logging.getLogger(__name__).exception(
            "sentry_init_failed",
            extra={"component": component},
        )
