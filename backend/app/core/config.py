from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Core
    APP_ENV: str = "local"
    APP_BASE_URL: str = "http://localhost:3000"
    API_BASE_URL: str = "http://localhost:8000"
    SECRET_KEY: str = "dev-only-secret"
    
    # Database
    # Defaulting to a sensible local docker default if not provided, 
    # but strictly it should come from .env
    DATABASE_URL: str
    
    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # Strava (will be used later, but good to have in config definition)
    STRAVA_CLIENT_ID: str = ""
    STRAVA_CLIENT_SECRET: str = ""
    STRAVA_REDIRECT_URI: str = "http://localhost:8000/api/auth/strava/callback"
    STRAVA_WEBHOOK_VERIFY_TOKEN: str = ""
    STRAVA_WEBHOOK_CALLBACK_URL: str = "http://localhost:8000/api/webhooks/strava"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore"
    )

settings = Settings()
