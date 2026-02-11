from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import health, auth, activities, webhooks, profile, trends

app = FastAPI(
    title="Running Coach",
    description="Local-first Strava Coach MVP",
    version="0.2.0",
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
app.include_router(activities.router, prefix="/api", tags=["Activities"])
app.include_router(webhooks.router, prefix="/api", tags=["Webhooks"])
app.include_router(trends.router, prefix="/api", tags=["Trends"])

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
