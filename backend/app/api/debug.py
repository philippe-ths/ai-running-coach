"""Diagnostic endpoints used to verify the observability stack end-to-end.

Behind the same basic-auth middleware as the rest of /api. Endpoints here
are intentionally never wired into the frontend or user-facing flows.

TODO(phase-2): consider gating these behind a feature flag or removing if
they outlive their usefulness past the first production deploy.
"""

from fastapi import APIRouter, HTTPException

from app.core.observability import sentry_capture_active

router = APIRouter()


class SentrySmokeError(RuntimeError):
    """Deliberate exception raised to verify the Sentry pipeline."""


@router.get("/_debug/sentry-test")
def trigger_sentry_test_exception() -> None:
    """Raise an exception so we can confirm Sentry captures it.

    Disabled unless Sentry capture is actually active. Capture needs both the
    optional `observability` extra installed and `SENTRY_DSN` set (logs-only by
    default; see issue #102), so a DSN alone is not enough. When capture is off
    this returns a clean 404 rather than raising an exception nothing records.
    """
    if not sentry_capture_active():
        raise HTTPException(status_code=404, detail="sentry capture not active")
    raise SentrySmokeError(
        "phase-1 step 3 smoke test — if you see this in Sentry, the pipeline works"
    )
