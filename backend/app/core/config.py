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
    # Secret for signing the Strava OAuth `state` token that carries the
    # signed-in user through the callback (#469). The callback is a direct
    # browser redirect from Strava with no Clerk session, so identity rides in a
    # server-verifiable HMAC state. Empty falls back to BASIC_AUTH_PASSWORD (set
    # in prod, #480) then CLERK_SECRET_KEY, so it is secure-by-default in prod
    # with no new env var; with no server secret at all (pure local dev) signing
    # is unauthenticated, which is fine since that path is single-owner and has
    # no adversary. Set this explicitly to rotate the OAuth state secret alone.
    STRAVA_OAUTH_STATE_SECRET: str = ""

    # Coach AI
    ANTHROPIC_API_KEY: str = ""
    COACH_MODEL_ID: str = "claude-sonnet-4-6"

    # Per-user LLM cost cap (P2.2, #122, Ring 3 of docs/vision/going-live-cost-control.md).
    # A Redis-backed running spend counter degrades coach generation to the
    # deterministic is_fallback path once a window is at or over its dollar
    # ceiling, so one user can never drain the shared Anthropic budget. Each
    # ceiling is a USD amount; 0 disables that window (so all-zero defaults are a
    # no-op until the owner sets real numbers — the owner owns the real dollars).
    # Per-user windows cap any single runner; the global windows cap the whole
    # deployment. See app/services/coach/budget.py.
    LLM_BUDGET_USER_DAILY_USD: float = 0.0
    LLM_BUDGET_USER_MONTHLY_USD: float = 0.0
    LLM_BUDGET_GLOBAL_DAILY_USD: float = 0.0
    LLM_BUDGET_GLOBAL_MONTHLY_USD: float = 0.0
    # The all-zero defaults mean NO cost cap. That is fine for local dev, but in
    # production a silently-uncapped deployment lets one user drain the shared
    # Anthropic budget (#543). Rather than crash the boot when prod is unconfigured
    # (#549: the #543 boot guard took prod fully DOWN on Railway, since the prior
    # deploy was REMOVED so a crashed boot left nothing serving — a strictly worse
    # failure than a missing cost cap), production falls back to the generous
    # safety ceiling below. Set this opt-out to run prod deliberately uncapped, so
    # "no cap in prod" stays a conscious, auditable choice.
    LLM_BUDGET_DISABLED: bool = False
    # The production safety backstop (#549). When APP_ENV=production and no
    # LLM_BUDGET_*_USD window above is explicitly set and the cap is not disabled,
    # the budget gate applies this global-daily ceiling so prod is NEVER silently
    # uncapped — without crashing the boot. Generous by design: it is a catastrophe
    # backstop, not a tuned cap (set an explicit window for that). 0 turns the
    # backstop off (prod would then run uncapped when otherwise unconfigured).
    # See app/services/coach/budget.py.
    LLM_BUDGET_PROD_DEFAULT_GLOBAL_DAILY_USD: float = 50.0
    # Minimum seconds between on-demand coach-report regenerations for one
    # activity (#122 / going-live landmine 1). The regenerate endpoint is the
    # single most exposed cost lever (it enqueues a full two-stage generation);
    # this cooldown plus a deterministic per-activity job id dedups rapid
    # re-taps so a stuck "Regenerate" button cannot fan out LLM spend.
    COACH_REGEN_COOLDOWN_SECONDS: int = 60
    # The active coach prompt. The default tracks the production prompt (see
    # project-context.md), NOT a legacy non-feature-bearing id: a prompt that
    # carries none of the runner-facing coaching features (voice/corpus/stance/
    # training-load/user-materials/volume) would silently disable that whole
    # surface, including the runner's declared voice (#424). A deploy that
    # resolves to an inert prompt is flagged loudly at startup by
    # warn_if_coach_prompt_inert (app/core/observability.py).
    COACH_PROMPT_ID: str = "coach_message_v8"

    # Coach-report notifications. Channel selection (see
    # app/services/notifications/__init__.py): Telegram when
    # TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID are both set, else no-op. The email
    # (SMTP) channel was removed (#595); the product is Telegram-only.

    # Telegram bot transport (#127). Railway blocks outbound SMTP, so the
    # deployed worker delivers coach reports over Telegram's HTTPS Bot API
    # instead. Both must be set for the channel to activate.
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    # The bot's public @username (without the leading @), used to build the
    # `https://t.me/<username>?start=<token>` deep link a runner taps to bind
    # their chat to their account (#477). Empty disables the link affordance (the
    # endpoint reports unconfigured); it is the bot's username, not a secret.
    TELEGRAM_BOT_USERNAME: str = ""
    # Secret for authenticating inbound Telegram callback_query webhooks (I1b,
    # #220). Set as Telegram's per-webhook `secret_token` at registration and
    # echoed back in the `X-Telegram-Bot-Api-Secret-Token` header on every
    # callback; the inbound endpoint rejects a request whose header does not
    # match (the stronger, secret-ish check, paired with a chat_id match).
    # Empty disables the secret check (local dev), leaving only the chat_id gate.
    TELEGRAM_WEBHOOK_SECRET: str = ""

    # User-triggered self-healing for missed Strava webhooks (#123, ADR 0006).
    # The time-based polling fallback was removed: Strava's rate limit is
    # per-application, so steady-state polling across many users exhausts the
    # shared budget. Instead, an authenticated app-open (or a Refresh tap) makes
    # ONE Strava call to detect activities the webhook missed. This bound caps how
    # often that check runs per user, so rapid refreshes do not multiply Strava
    # calls. Default: at most one check per user per minute.
    SELF_HEAL_MIN_INTERVAL_SECONDS: int = 60

    # Historical stream-analysis backfill (#110). A manually-triggered,
    # self-pacing job fetches streams + re-runs analysis for activities that
    # were imported summary-only. Each batch makes one Strava call per activity;
    # batch size over the pause must stay under Strava's 100-requests/15-min
    # ceiling alongside polling. Default 20 calls per 300s = 60/15min, plus
    # polling's ~7, stays well under 100. The backfill is one-time, so the daily
    # total is just the backlog size and converges to zero.
    BACKFILL_BATCH_SIZE: int = 20
    BACKFILL_BATCH_PAUSE_SECONDS: int = 300

    # Bulk re-analysis of already-analyzed history (#300). A self-pacing job
    # re-runs the analysis pipeline from streams ALREADY stored in the database
    # (no Strava calls), so a derivation change (e.g. #297 zone binning)
    # propagates to historical DerivedMetric rows. Unlike the stream backfill the
    # pacing is not rate-limit driven (re-analysis is local compute) — the pause
    # just yields the single worker to webhooks/polling between batches, so the
    # batch can be large and the pause short.
    REANALYZE_BATCH_SIZE: int = 100
    REANALYZE_BATCH_PAUSE_SECONDS: int = 5

    # Historical Strava import (#239). A self-pacing background job walks a
    # runner's Strava history backward in time, ingesting one page of activity
    # summaries per batch (no streams, no AI coach analysis, no notifications).
    # Each batch is a single Strava list call, far below the backfill's
    # per-activity stream cost; the pause only keeps the worker free for
    # webhooks/polling between batches. 50 activities per page (Strava's max).
    IMPORT_PAGE_SIZE: int = 50
    IMPORT_BATCH_PAUSE_SECONDS: int = 5

    # Global Strava API call budget (#544). Strava rate-limits per APPLICATION
    # (~100 req/15min, ~1000/day), shared across every connected athlete — not per
    # user. The per-call 429 backoff in the HTTP adapter defers a single call but
    # creates no capacity, so several runners onboarding at once (history backfill
    # costs one streams call PER ACTIVITY) can exhaust the shared ceiling and starve
    # everyone's webhook traffic. This Redis-backed global counter (clock-aligned to
    # Strava's own 15-min + daily reset windows) is recorded on every metered Strava
    # call and CHECKED only by the background jobs (import / stream backfill /
    # self-heal); live webhook ingestion never consults it, so a just-finished run is
    # always processed ("webhooks always win"). 0 here = no EXPLICIT ceiling for that
    # window; in production the safety backstop below still arms a default, so prod is
    # protected without the owner having to set these. Set either to the deployed
    # Strava app's CONFIRMED limit (below the real ceiling, to keep live headroom) to
    # override the backstop. See app/services/strava_ingestion/strava_budget.py.
    STRAVA_BUDGET_GLOBAL_PER_15MIN: int = 0
    STRAVA_BUDGET_GLOBAL_PER_DAY: int = 0
    # How long a background job waits before retrying when the global Strava budget
    # is exhausted: it re-enqueues itself after this delay instead of calling Strava,
    # yielding the shared ceiling to live webhook traffic.
    STRAVA_BUDGET_BACKOFF_SECONDS: int = 60
    # Production safety backstop (mirrors the LLM budget's #549 pattern). When
    # APP_ENV=production and the matching explicit ceiling above is 0 (unset) and the
    # budget is not disabled, the gate falls back to these values, so prod is
    # protected by DEFAULT without the owner having to arm it. Set BELOW Strava's
    # standard application limits (~100 req/15min, ~1000/day) so the gap between this
    # ceiling and Strava's real limit stays reserved for the ungated live webhook
    # path: once total recorded calls hit this ceiling only the background jobs pause,
    # leaving the remainder for live ingest. A single backfill paces under this
    # already (BACKFILL_BATCH_SIZE = ~60/15min), so the default only bites when
    # MULTIPLE background chains pile up (concurrent onboarding) — exactly the #544
    # case. Override a window with the confirmed limit, or set STRAVA_BUDGET_DISABLED
    # to run prod deliberately unthrottled. 0 turns a backstop window off. Outside
    # production these are inert (local/test never throttle).
    STRAVA_BUDGET_DISABLED: bool = False
    STRAVA_BUDGET_PROD_DEFAULT_PER_15MIN: int = 75
    STRAVA_BUDGET_PROD_DEFAULT_PER_DAY: int = 800

    # Receipt cadence (#296, ADR 0010/0011 delta). Orthogonal to COACH_PROMPT_ID:
    # when True AND the active prompt is a two-stage message prompt, the post-activity
    # touchpoint becomes an INSTANT deterministic per-activity receipt (RPE/pain +
    # "done" taps, no LLM, cannot fall back) plus a single full LLM report ~30 min
    # after the session (the block-complete debounce, or a "done" tap), retiring the
    # debounced LLM opener and the 3h fuller timer. The full report reuses the
    # configured prompt's fuller mode, so cadence and prompt content stay decoupled;
    # flipping this back restores the prior two-stage opener/fuller cadence with zero
    # code change. Inert under a single-shot prompt (there is no fuller mode to fire).
    COACH_RECEIPT_CADENCE: bool = False

    # (The M8 belief loop + A2c narrative durable-memory system and their kill
    # switches were retired in M4, ADR 0025, replaced by the runner memory profile
    # gated by COACH_MEMORY_ENABLED.)

    # Prior-report feedback kill switches. Like the retired belief/narrative loop,
    # the two prior-report-driven context sections were found to replay a stale
    # theme (the coach kept re-litigating "you ignored the rest-day advice" run
    # after run, because each new report read the previous report's rest-day
    # next_steps forward), so they are disabled pending the same recalibration.
    #
    # COACH_PRIOR_REPORTS_ENABLED gates the M4 longitudinal prior-report digest
    # (longitudinal.prior_reports — the last 1-2 reports' headlines + next_steps).
    # Off => prior_reports is empty; the numeric M2 baseline_trend half of the
    # longitudinal section is unaffected (it carries no report language to echo).
    #
    # COACH_ADHERENCE_ENABLED gates the M7 adherence section (did the runner act on
    # the last report's next_steps). Off => the section emits its empty form and the
    # coach says nothing about adherence. Re-enabling either is a pure config flip.
    COACH_PRIOR_REPORTS_ENABLED: bool = False
    COACH_ADHERENCE_ENABLED: bool = False

    # Coach-input kill switches (#522). Eleven independent, reversible gates that
    # REMOVE a named section / prompt part / input from what the coach receives,
    # plus grey the matching UI input. Each defaults to True (current behaviour:
    # the item is present), so the default code path is byte-identical and the test
    # suite stays green; the "off" state is reached by setting the flag False in the
    # environment. Orthogonal to COACH_PROMPT_ID, exactly like the kill switches
    # above. The gated builders DROP their section/sub-field byte-stably when the
    # flag is False (the PACK_SECTIONS drop registry), so a disabled item costs zero
    # tokens and disappears from the data-flow graph rather than emitting an empty
    # shell.
    COACH_USER_MATERIALS_ENABLED: bool = True       # corpus.user_materials
    COACH_RELATIONSHIP_ENABLED: bool = True          # read CoachingRelationship for voice + stance; off => defaults (receipt voicing falls to the house floor)
    COACH_HOUSE_SCHOOLS_ENABLED: bool = True         # corpus.school (the five named SCHOOLS); HOUSE_CORE is retained
    COACH_LONGITUDINAL_ENABLED: bool = True          # the whole pack.longitudinal section (both prior_reports and baseline_trend halves)
    COACH_PLAYBOOK_ENABLED: bool = True              # the activity-type playbook appended to the system prompt
    COACH_VOICE_BLOCK_ENABLED: bool = True           # the rendered per-runner voice block appended to the system prompt
    COACH_STOPS_ANALYSIS_ENABLED: bool = True        # focus.metrics.stops_analysis (pipeline still computes + stores it for non-coach use)
    COACH_SLEEP_QUALITY_ENABLED: bool = True         # the check-in sleep_quality input + its coach context (risk.py/flags.py keep their safe null-defaults)
    COACH_SALIENCE_ENABLED: bool = True              # the pack.salience section (the deterministic fuller-turn scheduling logic is untouched)
    COACH_CONTINUITY_ENABLED: bool = True            # the pack.continuity section
    COACH_PREVIOUS_30D_ENABLED: bool = True          # the previous_30d window inside pack.recent_training (the vs-prev comparisons are unaffected)
    # #561: an independent off-switch for the multi-year training-history section
    # (the LOD volume ladder + durability traits). Unlike the #522 switches above it
    # gates a PROMPT-version section and has no UI input, so it is NOT part of the
    # /api/coach/feature-flags surface; it only lets an operator drop just this
    # section without a full COACH_PROMPT_ID rollback. Off => the builder returns
    # None and PACK_SECTIONS drops it byte-stably.
    COACH_TRAINING_HISTORY_ENABLED: bool = True

    # Runner memory (ADR 0025), the rewrite-from-source replacement for the retired
    # belief + narrative loop. The conventional #522 default-True kill switch on top
    # of the prompt gate: the background memory update pass is enqueued, and the
    # `memory` pack section is read, ONLY when the active prompt is memory-aware
    # (coach_message_v13, M3) AND this is True. Off => no writer enqueue and the
    # section drops byte-stably. UI-visible (greys the future memory surface), so it
    # is on the /api/coach/feature-flags map (added in M3 alongside the read path).
    COACH_MEMORY_ENABLED: bool = True

    # Two-stage Exchange cadence (A4, ADR 0010). The opener fires immediately on a
    # finished activity; the conditional fuller turn fires on the runner's reply or
    # this timer, whichever is first. Only the two-stage prompt (coach_message_v2)
    # uses these; under a single-shot prompt the pipeline ignores them (AC8). Under
    # the receipt cadence (#296) the 3h timer is retired — the full report fires at
    # +BLOCK_GAP_SECONDS instead — so EXCHANGE_STAGE2_DELAY_SECONDS is then unused.
    EXCHANGE_STAGE2_DELAY_SECONDS: int = 10800  # 3h fuller-turn timer fallback
    # How long after the opener a reply (a check-in or chat) still belongs to the
    # open exchange and triggers the fuller turn early. A reply on an activity whose
    # opener is older than this never spins up a fresh exchange (AC4). 24h: a
    # same-day reply lands the fuller turn; a check-in on a stale run does not.
    EXCHANGE_REPLY_WINDOW_SECONDS: int = 86400

    # RQ death-penalty ceiling for worker jobs (#264). A two-stage coach generation
    # (adaptive thinking -> prose -> tail) runs ~120-360s, well past RQ's 180s
    # default, so a slow fuller turn / regeneration was being killed mid-generation
    # before it could store the report. 600s gives comfortable margin over the
    # worst case while still capping a genuinely stuck job. Applied as the queue
    # default_timeout (queue.enqueue paths) and as the explicit timeout on the
    # rq-scheduler enqueue_in calls (block-complete opener, scheduled fuller turn).
    RQ_JOB_TIMEOUT_SECONDS: int = 600

    # Block grouping (A1, ADR 0011): both the time-gap threshold that groups
    # temporally-contiguous activities into one Block AND the block-complete
    # debounce that gates the opener (the gap doubles as the trigger). An
    # activity starting within this many seconds of the previous block's end
    # joins it; a block with no new member for this long is complete.
    BLOCK_GAP_SECONDS: int = 1800

    # User materials (P4, #285, ADR 0017). The first untrusted-input surface, so
    # the ingestion guards are STRUCTURAL: a per-file byte cap and a per-user count
    # cap on non-archived materials. Generous for a single runner; tunable later.
    USER_MATERIAL_MAX_BYTES: int = 262144  # 256 KB per uploaded .md file
    USER_MATERIAL_MAX_COUNT: int = 50  # max non-archived materials per user

    # Phase 2 identity: social login via Clerk (ADR 0022). The frontend (Vercel)
    # owns the Clerk session; the backend VERIFIES the session token itself via
    # Clerk's JWKS and resolves user_id from the verified email, so it never
    # blindly trusts a header (the webhook routes are public). See
    # app/core/clerk_auth.py.
    #
    # CLERK_JWKS_URL is the only setting REQUIRED to turn auth on: when it is set
    # the backend enforces a verified session on every non-exempt /api route.
    # When it is empty AND APP_ENV != production, auth degrades to the single
    # local user (local dev + the test suite keep working with no Clerk); in
    # production an empty value fails closed, mirroring BasicAuthMiddleware.
    CLERK_JWKS_URL: str = ""
    # Clerk Backend API secret (sk_*). Used only as a fallback to fetch a
    # signed-in user's verified email when the session token carries no `email`
    # claim (the recommended setup adds `email` to the Clerk session-token
    # template, which avoids this call entirely). Never logged.
    CLERK_SECRET_KEY: str = ""
    # Expected token issuer. When empty it is derived from CLERK_JWKS_URL by
    # stripping the well-known JWKS path, which is correct for every Clerk
    # instance; set explicitly only to override.
    CLERK_ISSUER: str = ""
    # Optional comma-separated allowlist of authorized parties (the `azp` claim,
    # the frontend origin Clerk issued the token to). Empty disables the check.
    CLERK_AUTHORIZED_PARTIES: str = ""
    # Clock-skew tolerance (seconds) for token exp/nbf/iat checks.
    CLERK_LEEWAY_SECONDS: int = 30
    # The owner's Google email. On first sign-in the pre-Phase-2 single user
    # (whose email was backfilled to a placeholder by the email-non-null
    # migration) is reconciled to this address by adopting it, so the owner's
    # existing data is not orphaned behind a fresh empty account. Empty disables
    # reconciliation (every verified email gets/creates its own user).
    OWNER_EMAIL: str = ""

    # Dev-only ungated mode for local browser verification (#488). Setting this
    # True forces the Clerk degrade path (the single seeded local user) so the
    # seeded stack can be driven in a browser without a Clerk sign-in, WITHOUT
    # hand-editing CLERK_JWKS_URL out of .env (env_ignore_empty makes an empty
    # override impossible, but a non-empty `LOCAL_NO_AUTH=true` env var DOES
    # override .env). It is refused in production by `clerk_enabled`, so it can
    # never be a prod auth bypass: there the backend stays the enforcer and an
    # unconfigured Clerk fails closed.
    LOCAL_NO_AUTH: bool = False

    # Phase 1 deployment: HTTP basic auth in front of /api/*. Under Phase 2
    # (ADR 0022) this is REPURPOSED as the frontend-to-backend service secret
    # (defense in depth: "proves it is our frontend"), no longer as identity --
    # per-user identity rides on top via the verified Clerk token. Both must be
    # set for the middleware to enforce; either empty disables it (fails closed
    # in production).
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

    @property
    def clerk_issuer(self) -> str:
        """The expected token issuer.

        Explicit ``CLERK_ISSUER`` wins; otherwise derive it from the JWKS URL by
        stripping the ``/.well-known/jwks.json`` suffix (the Clerk convention,
        e.g. ``https://fine-octopus-89.clerk.accounts.dev``).
        """
        if self.CLERK_ISSUER:
            return self.CLERK_ISSUER.rstrip("/")
        url = self.CLERK_JWKS_URL
        marker = "/.well-known/"
        if marker in url:
            return url.split(marker, 1)[0].rstrip("/")
        return url.rstrip("/")

    @property
    def clerk_authorized_parties_list(self) -> list[str]:
        return [p.strip() for p in self.CLERK_AUTHORIZED_PARTIES.split(",") if p.strip()]

    @property
    def clerk_enabled(self) -> bool:
        """Whether the backend should enforce verified Clerk sessions.

        Auth turns on as soon as a JWKS URL is configured. With no JWKS URL,
        production fails closed (handled in the dependency) while non-production
        degrades to the single local user.

        The dev-only `LOCAL_NO_AUTH` switch (#488) forces the degrade path for
        local browser verification, but ONLY outside production: in production
        the flag is ignored, so it can never bypass the prod auth gate.
        """
        if self.LOCAL_NO_AUTH and self.APP_ENV != "production":
            return False
        return bool(self.CLERK_JWKS_URL)

    @property
    def coach_ui_feature_flags(self) -> dict[str, bool]:
        """The enabled-state of every coach input that has a UI surface (#522), so
        the frontend can grey the matching panel/field. Each value is True when the
        input is live and False when it is gated off. `voice` and `stance` are
        DERIVED: the voice panel is inert unless both the relationship is read and
        the rendered voice block is on; the stance panel is inert unless the
        relationship is read and the house schools (the selectable school) are on."""
        return {
            "voice": self.COACH_RELATIONSHIP_ENABLED and self.COACH_VOICE_BLOCK_ENABLED,
            "stance": self.COACH_RELATIONSHIP_ENABLED and self.COACH_HOUSE_SCHOOLS_ENABLED,
            "user_materials": self.COACH_USER_MATERIALS_ENABLED,
            "sleep_quality": self.COACH_SLEEP_QUALITY_ENABLED,
            "stops_analysis": self.COACH_STOPS_ANALYSIS_ENABLED,
            "memory": self.COACH_MEMORY_ENABLED,
        }

    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore"
    )

settings = Settings()
