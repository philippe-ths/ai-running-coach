"""Global Strava API call budget — protect the shared per-application rate limit (#544).

Strava rate-limits per APPLICATION (~100 req / 15 min, ~1000 / day), shared across
every connected athlete, NOT per user. The per-call 429/Retry-After backoff in the
HTTP adapter defers a single call but creates no capacity; with several runners
onboarding at once (history backfill costs one streams call PER ACTIVITY) the shared
ceiling can be exhausted, starving steady webhook traffic for everyone.

This is the cross-user throttle the per-user pacing cannot provide: a Redis-backed
global running call count over Strava's own reset windows, recorded on every metered
Strava call (in the HTTP adapter) and CHECKED only by the background jobs (historical
import, stream backfill, self-heal). LIVE webhook ingestion never consults it, so a
just-finished run is always processed; only history backfill and the app-open safety
net yield when the shared budget is tight ("webhooks always win").

Windows are clock-aligned to Strava's accounting: a 15-minute bucket (Strava's short
window resets on the quarter-hour) and a UTC day. Ceilings are configurable; 0 = that
window disabled, so this is INERT until the owner arms it with the deployed app's
CONFIRMED limits (set them BELOW the real ceiling to leave headroom for the ungated
webhook/live path). Backend: Redis when reachable (cross-process — the worker makes
the calls), else an in-process dict (local dev + the test suite). The gate degrades
to permissive (not over budget) on any backend error: a throttle must never become an
availability risk, and the adapter's 429 backoff remains the hard floor underneath.

Deliberately parallel to app/services/coach/budget.py rather than shared: that meters
USD per user/global for LLM spend, this meters call counts global-only for an external
rate limit. The two have different units, scopes, and windows; collapsing them would
couple unrelated concerns.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Strava's short rate-limit window is a fixed 15 minutes aligned to the clock
# quarter-hour; bucketing on floor(epoch / 900) tracks the same boundaries Strava
# resets on, so the local count matches Strava's accounting rather than a private
# rolling window.
_WINDOW_15MIN_SECONDS = 900
# Keep each window's counter alive a little past the window so a call near the
# boundary still sees the accrued count.
_15MIN_TTL = _WINDOW_15MIN_SECONDS * 2  # 30m
_DAILY_TTL = 60 * 60 * 36  # 36h


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bucket_15min(now: datetime) -> int:
    return int(now.timestamp()) // _WINDOW_15MIN_SECONDS


def _day_key(d: date) -> str:
    return d.isoformat()


@dataclass
class _InMemoryBackend:
    """A process-local call accumulator (tests + local dev without Redis)."""

    counts: Dict[str, float] = field(default_factory=dict)

    def incr(self, key: str, amount: int, ttl_seconds: int) -> None:
        self.counts[key] = self.counts.get(key, 0.0) + amount

    def get(self, key: str) -> float:
        return self.counts.get(key, 0.0)


class _RedisBackend:
    """Cross-process call accumulator. INCRBY + EXPIRE per window key."""

    def __init__(self, client) -> None:
        self._r = client

    def incr(self, key: str, amount: int, ttl_seconds: int) -> None:
        pipe = self._r.pipeline()
        pipe.incrby(key, amount)
        pipe.expire(key, ttl_seconds)
        pipe.execute()

    def get(self, key: str) -> float:
        raw = self._r.get(key)
        return float(raw) if raw is not None else 0.0


class StravaBudgetGate:
    """The Strava call-rate gate. Reads ceilings from settings at call time so a
    config flip (or a test monkeypatch) takes effect without rebuilding the gate."""

    _PREFIX = "strava_budget"

    def __init__(self, backend) -> None:
        self._backend = backend

    # --- ceilings (read live from settings; 0 = window disabled) ---
    @property
    def _per_15min(self) -> int:
        return settings.STRAVA_BUDGET_GLOBAL_PER_15MIN

    @property
    def _per_day(self) -> int:
        return settings.STRAVA_BUDGET_GLOBAL_PER_DAY

    def _key(self, window: str) -> str:
        return f"{self._PREFIX}:global:{window}"

    def _keys(self, now: datetime) -> tuple[str, str]:
        return (
            self._key(f"15m:{_bucket_15min(now)}"),
            self._key(f"day:{_day_key(now.date())}"),
        )

    def over_budget(self) -> bool:
        """True when either enabled window is at or over its ceiling. Errors degrade
        to False (permissive) so the throttle never becomes an availability risk."""
        now = _now()
        key_15m, key_day = self._keys(now)
        try:
            checks = (
                (self._per_15min, key_15m),
                (self._per_day, key_day),
            )
            for ceiling, key in checks:
                if ceiling > 0 and self._backend.get(key) >= ceiling:
                    logger.warning(
                        "strava_budget_exceeded",
                        extra={"key": key, "ceiling": ceiling},
                    )
                    return True
            return False
        except Exception as exc:  # noqa: BLE001 — a throttle must not break availability
            logger.warning("strava_budget_check_failed", extra={"error": str(exc)})
            return False

    def record(self, calls: int = 1) -> None:
        """Add ``calls`` (default 1) to both global windows. Never raises."""
        if calls <= 0:
            return
        now = _now()
        key_15m, key_day = self._keys(now)
        try:
            self._backend.incr(key_15m, calls, _15MIN_TTL)
            self._backend.incr(key_day, calls, _DAILY_TTL)
        except Exception as exc:  # noqa: BLE001 — recording must never break a call
            logger.warning("strava_budget_record_failed", extra={"error": str(exc)})


# --- module-level gate (lazy, swappable in tests) --------------------------

_gate: Optional[StravaBudgetGate] = None
_gate_lock = threading.Lock()


def _build_gate() -> StravaBudgetGate:
    """A Redis-backed gate when REDIS_URL is reachable, else an in-process one."""
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        client.ping()
        return StravaBudgetGate(_RedisBackend(client))
    except Exception as exc:  # noqa: BLE001 — no Redis -> degrade to in-process
        logger.info("strava_budget_using_in_memory_backend", extra={"reason": str(exc)})
        return StravaBudgetGate(_InMemoryBackend())


def get_gate() -> StravaBudgetGate:
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                _gate = _build_gate()
    return _gate


def set_gate(gate: Optional[StravaBudgetGate]) -> None:
    """Test seam: inject a controlled gate (None resets to lazy rebuild)."""
    global _gate
    with _gate_lock:
        _gate = gate


def new_in_memory_gate() -> StravaBudgetGate:
    """A fresh in-process gate, for tests that drive the count over a ceiling."""
    return StravaBudgetGate(_InMemoryBackend())


def over_budget() -> bool:
    """True when the shared Strava call budget is exhausted for the current window.

    Consulted ONLY by background jobs; the live webhook path never calls this.
    """
    return get_gate().over_budget()


def record(calls: int = 1) -> None:
    """Record ``calls`` metered Strava request(s) against the global budget."""
    get_gate().record(calls)
