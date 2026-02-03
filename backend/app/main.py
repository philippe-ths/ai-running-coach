from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import health, auth, activities, webhooks, demo, profile, chat, verdict_v3

app = FastAPI(
    title="AI Running Coach",
    description="Local-first Strava Coach MVP",
    version="0.1.0",
)

# CORS Configuration
origins = [
    "http://localhost:3000",
    "http://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api", tags=["System"])
app.include_router(auth.router, prefix="/api", tags=["Auth"])
app.include_router(profile.router, prefix="/api", tags=["Profile"])
app.include_router(chat.router, prefix="/api", tags=["Chat"])
app.include_router(activities.router, prefix="/api", tags=["Activities"])
app.include_router(webhooks.router, prefix="/api", tags=["Webhooks"])
app.include_router(verdict_v3.router, prefix="/api", tags=["Coach Verdict V3"])

# Conditionally include demo router to keep swagger clean in 'prod'
if settings.DEMO_MODE:
    app.include_router(demo.router, prefix="/api", tags=["Demo"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
