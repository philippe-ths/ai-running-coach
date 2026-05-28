from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Core
    APP_ENV: str = "local"
    APP_BASE_URL: str = "http://localhost:3000"
    API_BASE_URL: str = "http://localhost:8000"

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

    # Coach AI
    ANTHROPIC_API_KEY: str = ""
    COACH_MODEL_ID: str = "claude-sonnet-4-20250514"
    COACH_PROMPT_ID: str = "coach_report_v1"

    # Email notifications (feature off when SMTP_HOST is empty)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_STARTTLS: bool = True
    NOTIFY_TO: str = ""

    # Polling fallback for missed Strava webhooks
    POLLING_INTERVAL_SECONDS: int = 120

    # Phase 1 deployment: throwaway basic auth in front of /api/*.
    # Both must be set for the middleware to enforce; either empty disables it.
    # TODO(phase-2): remove when session auth lands.
    BASIC_AUTH_USER: str = ""
    BASIC_AUTH_PASSWORD: str = ""

    # Sentry DSN; empty disables Sentry init (Phase 1 step 3 wires the SDK).
    SENTRY_DSN: str = ""

    # CORS allowlist (comma-separated). Drives app.add_middleware(CORSMiddleware).
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:8000"

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore"
    )

settings = Settings()
