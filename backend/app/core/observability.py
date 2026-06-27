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
import re
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


# Telegram bot tokens look like `bot<numeric-id>:<35-char secret>` and ride in
# the Bot API URL path, which httpx logs at INFO. Mask the secret part, keeping
# the (non-secret) numeric bot id so the log line stays useful. See #131.
_TELEGRAM_TOKEN_RE = re.compile(r"(bot\d+):[A-Za-z0-9_-]+")
_TELEGRAM_TOKEN_REPLACEMENT = r"\1:<redacted>"


def _redact_secrets(value: Any) -> Any:
    """Mask known secrets in a log value, preserving type when nothing matches.

    Operates on the string form so it also catches non-str args (e.g. an
    `httpx.URL`). Returns the original object unchanged when no secret is
    present, so normal log formatting (and arg types) are untouched.
    """
    text = value if isinstance(value, str) else str(value)
    redacted = _TELEGRAM_TOKEN_RE.sub(_TELEGRAM_TOKEN_REPLACEMENT, text)
    return redacted if redacted != text else value


class SecretRedactingFilter(logging.Filter):
    """Strip secrets from log records before they are formatted.

    Attached to the root handler (not a logger) so it also sees records that
    propagate up from third-party loggers such as `httpx`. Mutating the record
    is safe here: the record is only emitted by this one handler.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _redact_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact_secrets(v) for k, v in record.args.items()}
            else:
                record.args = tuple(_redact_secrets(a) for a in record.args)
        return True


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
    handler.addFilter(SecretRedactingFilter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def warn_if_coach_prompt_inert() -> None:
    """Log a loud WARNING when the active coach prompt carries no runner-facing features.

    The voice block, corpus, stance, training-load, user-materials, and volume
    sections are all gated on COACH_PROMPT_ID. If a deploy resolves to a prompt
    that carries none of them (a legacy ``coach_report_*`` id, ``coach_message_v1``,
    a two-stage-only id, or an unknown id), that whole coaching surface goes inert
    with no error. Rather than degrade silently (#424), surface it at startup so a
    misconfigured deploy is visible in the logs instead of producing flat reports.
    Called once per process group right after ``init_logging``.
    """
    # Imported lazily so this pure-logging module stays free of a coach-layer
    # import at module load.
    from app.services.coach.prompt_features import carries_runner_facing_features

    prompt_id = settings.COACH_PROMPT_ID
    if carries_runner_facing_features(prompt_id):
        return
    logging.getLogger(__name__).warning(
        "coach_prompt_inert: active COACH_PROMPT_ID=%r carries no runner-facing "
        "coaching features (voice/corpus/stance/training-load/user-materials/volume); "
        "the entire prompt-gated coaching surface is disabled. Set COACH_PROMPT_ID to a "
        "feature-bearing prompt (e.g. coach_message_v8).",
        prompt_id,
        extra={"coach_prompt_id": prompt_id},
    )


class ProductionConfigError(RuntimeError):
    """A setting that fails closed in production is unset at startup."""


# Settings whose request-time code FAILS CLOSED in production: an empty value
# does not stop the process booting, but every application route then returns
# 503. CLERK_JWKS_URL empty -> verify_clerk_session 503s every app route
# (clerk_auth.py); BASIC_AUTH_USER/PASSWORD empty -> BasicAuthMiddleware 503s
# every non-exempt route (auth.py). /api/health is auth-exempt and only checks
# process + DB, so it stays green and the platform promotes the broken deploy
# (the Phase 2 outage). Asserting these at boot turns that silent "up but
# serving errors" deploy into a boot crash, so the platform keeps the previous
# healthy deploy live instead of cutting over to one that 503s. These are the
# WEB process's HTTP gate settings only (web-only per docs/deployment/topology.md);
# the worker serves no HTTP and is intentionally not gated by this (see worker.py).
_REQUIRED_IN_PRODUCTION = (
    "CLERK_JWKS_URL",
    "BASIC_AUTH_USER",
    "BASIC_AUTH_PASSWORD",
)


def assert_production_config() -> None:
    """Refuse to boot when a fail-closed production setting is unset.

    No-op unless ``APP_ENV == "production"``: local dev and the test suite run
    with these unset and degrade to the single local user, so they must not
    raise. In production a missing (or whitespace-only) value raises
    ``ProductionConfigError``, which crashes the process before it serves
    traffic; the hosting platform then keeps the last healthy deploy running
    rather than promoting one that would 503 every request. The failure
    direction is safe -- a false positive can only block a new deploy, never
    take down the running one. Called once per process group right after
    ``init_logging``.

    SCOPE NOTE (#549): the "platform keeps the last healthy deploy serving on a
    boot crash" assumption above proved FALSE on Railway -- prior deploys were
    ``REMOVED``, so a crashed boot took prod fully down. The budget guard that
    shared this pattern was made non-fatal for that reason (see
    ``log_budget_cap_status``). This guard is left crashing-on-boot deliberately:
    the settings it guards (Clerk + basic-auth) make EVERY route 503 when unset,
    so booting anyway just trades a boot crash for a fully-broken-but-"up" deploy
    -- the tradeoff is defensible here in a way it was not for a cost cap prod
    runs fine without. Revisiting it (a non-fatal/preflight form) is tracked
    separately rather than duplicating #549's fix.
    """
    if settings.APP_ENV != "production":
        return
    missing = [
        name
        for name in _REQUIRED_IN_PRODUCTION
        if not str(getattr(settings, name, "") or "").strip()
    ]
    if not missing:
        return
    logging.getLogger(__name__).critical(
        "production_config_incomplete",
        extra={"missing": missing},
    )
    raise ProductionConfigError(
        "Refusing to boot: required production settings are unset: "
        + ", ".join(missing)
        + ". The frontend-facing routes would fail closed (503) without them. "
        "Set them in the deploy environment and redeploy."
    )


def log_budget_cap_status() -> None:
    """Log the effective LLM cost-cap posture at boot. NON-FATAL (#549).

    Replaces the #543 crash-on-boot guard (``assert_budget_cap_armed``). That
    guard refused to boot when production ran with no ``LLM_BUDGET_*_USD`` window
    set, on the assumption that a boot crash only blocks a NEW deploy while the
    platform keeps the last healthy one serving. On Railway that did not hold: the
    prior deploys were ``REMOVED``, so the crashed boot left nothing serving and
    prod went fully DOWN -- turning a missing cost cap (a setting prod runs FINE
    without; uncapped means uncapped spend, not a broken app) into a production
    outage, a strictly worse failure than the thing it guarded.

    Enforcement moved to a non-fatal safe default instead: in production an
    unconfigured, non-disabled cap falls back to a generous global-daily ceiling
    (``budget.production_default_ceiling`` / ``LLM_BUDGET_PROD_DEFAULT_GLOBAL_DAILY_USD``),
    so prod is never silently uncapped AND never crashes on this. This function
    only makes the resulting posture visible in the logs; it never raises. Called
    once per process group (web + worker) right after ``init_logging`` -- spend is
    incurred on whichever process calls the model.
    """
    if settings.APP_ENV != "production":
        return
    from app.services.coach.budget import (
        cap_is_armed,
        production_default_ceiling,
    )

    log = logging.getLogger(__name__)
    if settings.LLM_BUDGET_DISABLED:
        log.warning(
            "llm_budget_cap_disabled: LLM_BUDGET_DISABLED=true in production -- "
            "coach generation runs UNCAPPED. One user can drain the shared "
            "Anthropic budget. This is a deliberate waiver; set an "
            "LLM_BUDGET_*_USD window to arm the cap."
        )
        return
    backstop = production_default_ceiling()
    if backstop > 0:
        log.warning(
            "llm_budget_cap_default_backstop: no LLM_BUDGET_*_USD window set in "
            "production; applying the safety backstop of $%.2f global/day "
            "(LLM_BUDGET_PROD_DEFAULT_GLOBAL_DAILY_USD). Set an explicit window "
            "for a tuned cap.",
            backstop,
        )
        return
    if cap_is_armed():
        log.info("llm_budget_cap_armed: an explicit LLM_BUDGET_*_USD window is set.")
        return
    # Reachable only if the backstop default itself is set to 0 in production --
    # a deliberate "off" with no explicit window. Surface it loudly but never crash.
    log.warning(
        "llm_budget_cap_unarmed: production is running UNCAPPED (no window set and "
        "the safety backstop is 0). Set an LLM_BUDGET_*_USD window or a positive "
        "LLM_BUDGET_PROD_DEFAULT_GLOBAL_DAILY_USD."
    )


def sentry_capture_active() -> bool:
    """True only when Sentry error capture is actually live.

    Capture requires *both* the optional `observability` extra installed (so
    `sentry_sdk` imported) *and* `SENTRY_DSN` set. A DSN alone is not enough
    (see issue #102: logs-only by default). This is the single source of truth
    for "is error tracking on", so no path can advertise capture that is not
    actually wired up.
    """
    return _SENTRY_AVAILABLE and bool(settings.SENTRY_DSN)


def capture_exception(exc: BaseException) -> None:
    """Route a caught exception to Sentry. No-op unless capture is active.

    For a best-effort guard that swallows an exception so it never breaks the
    caller (e.g. the runner-baseline / readiness recomputes), an ERROR-level
    ``logger.exception`` already reaches Sentry via the ``LoggingIntegration``
    (``event_level=logging.ERROR``). This helper makes that capture explicit and
    unconditional at the swallow site so a genuine internal failure is visible as
    its own event rather than indistinguishable from a normal "not enough data"
    abstention. It is a no-op when Sentry is not wired up (the optional
    ``observability`` extra absent or ``SENTRY_DSN`` unset; logs-only by default,
    see issue #102), so it is safe to call from any code path.
    """
    if not sentry_capture_active():
        return
    try:
        sentry_sdk.capture_exception(exc)
    except Exception:  # pragma: no cover - capture must never break the caller
        logging.getLogger(__name__).exception("sentry_capture_failed")


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
