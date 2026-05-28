from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, auth, activities, webhooks, profile, trends, coach
from app.core.auth import BasicAuthMiddleware
from app.core.config import settings

app = FastAPI(
    title="Running Coach",
    description="Local-first Strava Coach MVP",
    version="0.2.0",
)

# TODO(phase-2): remove BasicAuthMiddleware when session auth from ADR 0005 lands.
app.add_middleware(
    BasicAuthMiddleware,
    exempt_prefixes=("/api/health", "/api/webhooks"),
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
