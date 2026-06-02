from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, auth, activities, webhooks, profile, trends, coach, debug
from app.core.config import settings
from app.core.observability import init_logging, init_sentry
from app.core.session_auth import SessionAuthMiddleware

init_logging()
init_sentry("web")

app = FastAPI(
    title="Running Coach",
    description="Local-first Strava Coach MVP",
    version="0.2.0",
)

# Session gate (ADR 0005). The whole /api/auth/* surface is exempt: the
# magic-link request/verify/logout endpoints serve unauthenticated callers, and
# the Strava OAuth callback is hit by a browser redirect carrying the OAuth code
# as its own auth. /api/webhooks/* is server-to-server (authenticated in-handler)
# and /api/health must stay open for probes.
# PR-A: this middleware still falls back to basic auth so the not-yet-cutover
# frontend keeps working. PR-B removes that fallback and deletes app/core/auth.py.
app.add_middleware(
    SessionAuthMiddleware,
    exempt_prefixes=(
        "/api/health",
        "/api/webhooks",
        "/api/auth",
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api", tags=["System"])
app.include_router(auth.router, prefix="/api", tags=["Auth"])
app.include_router(profile.router, prefix="/api", tags=["Profile"])
app.include_router(activities.router, prefix="/api", tags=["Activities"])
app.include_router(webhooks.router, prefix="/api", tags=["Webhooks"])
app.include_router(trends.router, prefix="/api", tags=["Trends"])
app.include_router(coach.router, prefix="/api", tags=["Coach"])
app.include_router(debug.router, prefix="/api", tags=["Debug"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
