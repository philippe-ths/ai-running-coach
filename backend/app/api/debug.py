"""Diagnostic endpoints used to verify the observability stack end-to-end.

Behind the same basic-auth middleware as the rest of /api. Endpoints here
are intentionally never wired into the frontend or user-facing flows.

TODO(phase-2): consider gating these behind a feature flag or removing if
they outlive their usefulness past the first production deploy.
"""

from fastapi import APIRouter, HTTPException

from app.core.config import settings

router = APIRouter()


class SentrySmokeError(RuntimeError):
    """Deliberate exception raised to verify the Sentry pipeline."""


@router.get("/_debug/sentry-test")
def trigger_sentry_test_exception() -> None:
    """Raise an exception so we can confirm Sentry captures it.

    Disabled when SENTRY_DSN is unset so calling this in a misconfigured
    environment produces a clean 404 rather than a meaningless 500.
    """
    if not settings.SENTRY_DSN:
        raise HTTPException(status_code=404, detail="sentry not configured")
    raise SentrySmokeError(
        "phase-1 step 3 smoke test — if you see this in Sentry, the pipeline works"
    )
