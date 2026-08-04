from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import health, auth, activities, blocks, webhooks, profile, trends, coach, debug, strava_import, materials, account, threads
from app.core.auth import BasicAuthMiddleware
from app.core.body_size_limit import BodySizeLimitMiddleware
from app.services.strava_ingestion.port import StravaRateLimited
from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.core.observability import (
    assert_production_config,
    init_logging,
    init_sentry,
    log_budget_cap_status,
    warn_if_coach_prompt_inert,
    warn_notification_config,
)

init_logging()
init_sentry("web")
warn_if_coach_prompt_inert()
# Loud (NON-FATAL) warning if Telegram is the active channel but linking/owner
# routing config is incomplete (#600/#609). No-op outside production.
warn_notification_config()
# Crash the boot if a fail-closed production setting is unset, so the platform
# keeps the last healthy deploy serving instead of promoting one that 503s every
# route (the Phase 2 deploy-outran-config outage). No-op outside production.
assert_production_config()
# Log the LLM cost-cap posture (#549, NON-FATAL). The web process makes chat LLM
# calls, so an uncapped web is a spend risk -- but enforcement is a non-fatal
# safe default in production (budget.production_default_ceiling), not a boot crash
# (the #543 guard took prod down on Railway, #549).
log_budget_cap_status()

app = FastAPI(
    title="Running Coach",
    description="Local-first Strava Coach MVP",
    version="0.2.0",
)


@app.exception_handler(StravaRateLimited)
async def _strava_rate_limited_handler(request: Request, exc: StravaRateLimited):
    """Surface a Strava rate limit as HTTP 429 on the live request path (#602),
    so a contended sync returns a clean "retry shortly" instead of a 500. The
    true Retry-After (when Strava supplied one) rides the standard header."""
    headers = {}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(int(exc.retry_after))
    return JSONResponse(
        status_code=429,
        content={"detail": "Strava is rate-limiting requests right now. Please try again shortly."},
        headers=headers,
    )

# BasicAuthMiddleware is REPURPOSED under ADR 0022 as the frontend-to-backend
# service secret ("proves it is our frontend"), defense in depth beneath the
# per-user Clerk session verified by verify_clerk_session below. It is not
# removed. /api/auth/strava/callback is exempt because Strava redirects the
# user's browser there directly with an OAuth code; the code itself is the auth,
# and Strava has no way to send basic auth credentials.
app.add_middleware(
    BasicAuthMiddleware,
    exempt_prefixes=(
        "/api/health",
        "/api/webhooks",
        "/api/auth/strava/callback",
    ),
)

# Per-user identity (ADR 0022). Applied to every APPLICATION router below, so a
# new router is gated by default if it is added to that list. NOT applied to
# health, auth (OAuth handshakes), or webhooks (public, separately
# authenticated). The fence test (tests/test_clerk_auth.py) asserts every
# non-exempt route denies an unauthenticated request when Clerk is configured.
_require_session = [Depends(verify_clerk_session)]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Edge body-size cap (#705). Added LAST so it is the OUTERMOST middleware: an
# oversize body is rejected before auth/CORS do any work on it, and before the
# form parser spools it to disk.
app.add_middleware(
    BodySizeLimitMiddleware,
    max_body_bytes=settings.MAX_REQUEST_BODY_BYTES,
)

# Include routers. Public (no per-user session): health, auth, webhooks.
app.include_router(health.router, prefix="/api", tags=["System"])
app.include_router(auth.router, prefix="/api", tags=["Auth"])
app.include_router(webhooks.router, prefix="/api", tags=["Webhooks"])

# Application routers: every route requires a verified Clerk session.
app.include_router(profile.router, prefix="/api", tags=["Profile"], dependencies=_require_session)
app.include_router(account.router, prefix="/api", tags=["Account"], dependencies=_require_session)
app.include_router(activities.router, prefix="/api", tags=["Activities"], dependencies=_require_session)
app.include_router(strava_import.router, prefix="/api", tags=["Strava Import"], dependencies=_require_session)
app.include_router(blocks.router, prefix="/api", tags=["Blocks"], dependencies=_require_session)
app.include_router(trends.router, prefix="/api", tags=["Trends"], dependencies=_require_session)
app.include_router(coach.router, prefix="/api", tags=["Coach"], dependencies=_require_session)
app.include_router(threads.router, prefix="/api", tags=["Coach"], dependencies=_require_session)
app.include_router(materials.router, prefix="/api", tags=["Coach"], dependencies=_require_session)
app.include_router(debug.router, prefix="/api", tags=["Debug"], dependencies=_require_session)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
