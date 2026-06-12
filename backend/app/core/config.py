from pydantic import field_validator
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
    # Active push-subscription id returned by Strava at registration. Strava
    # does not sign webhook payloads, so incoming events are authenticated by
    # matching this id (when set) and the connected athlete. 0 = unenforced
    # (local dev); set this to the live subscription id in production. See #100.
    STRAVA_WEBHOOK_SUBSCRIPTION_ID: int = 0

    # Coach AI
    ANTHROPIC_API_KEY: str = ""
    COACH_MODEL_ID: str = "claude-sonnet-4-6"
    COACH_PROMPT_ID: str = "coach_report_v10"

    # Coach-report notifications. Channel selection (see
    # app/services/notifications/__init__.py): Telegram when
    # TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are both set, else email when
    # SMTP_HOST + NOTIFY_TO are both set, else no-op.

    # Telegram bot transport (#127). Railway blocks outbound SMTP, so the
    # deployed worker delivers coach reports over Telegram's HTTPS Bot API
    # instead. Both must be set for the channel to activate.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    # Secret for authenticating inbound Telegram callback_query webhooks (I1b,
    # #220). Set as Telegram's per-webhook `secret_token` at registration and
    # echoed back in the `X-Telegram-Bot-Api-Secret-Token` header on every
    # callback; the inbound endpoint rejects a request whose header does not
    # match (the stronger, secret-ish check, paired with a chat_id match).
    # Empty disables the secret check (local dev), leaving only the chat_id gate.
    TELEGRAM_WEBHOOK_SECRET: str = ""

    # Email notifications (legacy/local channel; SMTP is unreachable from the
    # Railway worker, kept for local use and Pro-plan deployments).
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_STARTTLS: bool = True
    NOTIFY_TO: str = ""

    # Polling fallback for missed Strava webhooks
    POLLING_INTERVAL_SECONDS: int = 120
    # How far back each poll asks Strava for activities. Must exceed the
    # longest outage we expect the self-healing poll to recover from: any gap
    # older than this window can only be closed by a manual sync. Default 7
    # days. For a single recreational runner this stays within one page, so it
    # does not increase the steady-state Strava call budget. See #109.
    POLLING_LOOKBACK_SECONDS: int = 604800  # 7 days

    # Historical stream-analysis backfill (#110). A manually-triggered,
    # self-pacing job fetches streams + re-runs analysis for activities that
    # were imported summary-only. Each batch makes one Strava call per activity;
    # batch size over the pause must stay under Strava's 100-requests/15-min
    # ceiling alongside polling. Default 20 calls per 300s = 60/15min, plus
    # polling's ~7, stays well under 100. The backfill is one-time, so the daily
    # total is just the backlog size and converges to zero.
    BACKFILL_BATCH_SIZE: int = 20
    BACKFILL_BATCH_PAUSE_SECONDS: int = 300

    # Two-stage Exchange cadence (A4, ADR 0010). The opener fires immediately on a
    # finished activity; the conditional fuller turn fires on the runner's reply or
    # this timer, whichever is first. Only the two-stage prompt (coach_message_v2)
    # uses these; under a single-shot prompt the pipeline ignores them (AC8).
    EXCHANGE_STAGE2_DELAY_SECONDS: int = 10800  # 3h fuller-turn timer fallback
    # How long after the opener a reply (a check-in or chat) still belongs to the
    # open exchange and triggers the fuller turn early. A reply on an activity whose
    # opener is older than this never spins up a fresh exchange (AC4). 24h: a
    # same-day reply lands the fuller turn; a check-in on a stale run does not.
    EXCHANGE_REPLY_WINDOW_SECONDS: int = 86400

    # Block grouping (A1, ADR 0011): both the time-gap threshold that groups
    # temporally-contiguous activities into one Block AND the block-complete
    # debounce that gates the opener (the gap doubles as the trigger). An
    # activity starting within this many seconds of the previous block's end
    # joins it; a block with no new member for this long is complete.
    BLOCK_GAP_SECONDS: int = 1800

    # Phase 1 deployment: throwaway basic auth in front of /api/*.
    # Both must be set for the middleware to enforce; either empty disables it.
    # TODO(phase-2): remove when session auth lands.
    BASIC_AUTH_USER: str = ""
    BASIC_AUTH_PASSWORD: str = ""

    # Sentry DSN; empty disables Sentry init (Phase 1 step 3 wires the SDK).
    SENTRY_DSN: str = ""

    # CORS allowlist (comma-separated). Drives app.add_middleware(CORSMiddleware).
    CORS_ALLOWED_ORIGINS: str = "http://localhost:3000,http://localhost:8000"

    @field_validator("DATABASE_URL")
    @classmethod
    def _ensure_psycopg_driver(cls, v: str) -> str:
        """Normalise the DATABASE_URL to the psycopg driver SQLAlchemy needs.

        Railway's managed Postgres (and most providers) hand out a bare
        ``postgresql://`` URL, and some emit the legacy ``postgres://`` scheme.
        SQLAlchemy requires the explicit ``postgresql+psycopg://`` driver
        prefix. Normalising here means every consumer (the engine in
        ``db/session.py`` and Alembic in ``alembic/env.py``) inherits a
        driver-qualified URL. A URL that already names a driver
        (e.g. ``postgresql+psycopg://``) or a non-postgres URL is left as-is.
        """
        if v.startswith("postgresql+"):
            return v
        if v.startswith("postgresql://"):
            return "postgresql+psycopg://" + v[len("postgresql://"):]
        if v.startswith("postgres://"):
            return "postgresql+psycopg://" + v[len("postgres://"):]
        return v

    @property
    def cors_allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore"
    )

settings = Settings()
