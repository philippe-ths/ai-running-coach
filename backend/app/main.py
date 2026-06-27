from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, auth, activities, blocks, webhooks, profile, trends, coach, debug, strava_import, materials, account
from app.core.auth import BasicAuthMiddleware
from app.core.clerk_auth import verify_clerk_session
from app.core.config import settings
from app.core.observability import (
    assert_production_config,
    init_logging,
    init_sentry,
    log_budget_cap_status,
    warn_if_coach_prompt_inert,
)

init_logging()
init_sentry("web")
warn_if_coach_prompt_inert()
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
app.include_router(materials.router, prefix="/api", tags=["Coach"], dependencies=_require_session)
app.include_router(debug.router, prefix="/api", tags=["Debug"], dependencies=_require_session)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
