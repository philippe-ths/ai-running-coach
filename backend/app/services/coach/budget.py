"""Per-user LLM cost cap — the Ring 3 structural budget gate (P2.2, #122).

The owner's #1 going-live concern is that one user (abusive or buggy) can drain
the shared Anthropic budget. This is the structural cap: a Redis-backed running
spend counter checked BEFORE every coach generation and incremented after every
sub-call (so the multiplicative retry fan-out is counted, not just the first
call). At the cap, generation DEGRADES to the existing deterministic fallback
(`is_fallback=True`) rather than hard-blocking — the product still ships a
report, it just isn't the LLM one. One user hitting the cap never affects
another: the per-user counter is keyed by activity-owner `user_id`.

Windows: a daily and a monthly counter, per-user and global. Each ceiling is a
configurable dollar amount (0 = that window disabled). Over-budget is true when
ANY enabled window is at or over its ceiling.

Backend: Redis when reachable (cross-process — the worker generates reports),
else an in-process dict (local dev + the test suite, where Redis may be absent).
The gate degrades to a permissive no-op if the backend errors, so a Redis blip
never breaks generation — a cost cap must never become an availability risk.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Per-MTok (input, output) USD list price, kept in sync with the model the coach
# runs (COACH_MODEL_ID). Unknown models price as Opus (the most expensive tier)
# so a misconfiguration over-counts rather than under-counts.
PRICE_PER_MTOK: Dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-opus-4-5": (5.0, 25.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}
_DEFAULT_PRICE = (5.0, 25.0)  # conservative: assume Opus tier when unknown


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """List-price cost of one call in USD."""
    price_in, price_out = PRICE_PER_MTOK.get(model, _DEFAULT_PRICE)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _day_key(d: date) -> str:
    return d.isoformat()


def _month_key(d: date) -> str:
    return d.strftime("%Y-%m")


@dataclass
class _InMemoryBackend:
    """A process-local spend accumulator (tests + local dev without Redis)."""
    spend: Dict[str, float] = field(default_factory=dict)

    def incr(self, key: str, amount: float, ttl_seconds: int) -> None:
        self.spend[key] = self.spend.get(key, 0.0) + amount

    def get(self, key: str) -> float:
        return self.spend.get(key, 0.0)


class _RedisBackend:
    """Cross-process spend accumulator. INCRBYFLOAT + EXPIRE per window key."""

    def __init__(self, client) -> None:
        self._r = client

    def incr(self, key: str, amount: float, ttl_seconds: int) -> None:
        pipe = self._r.pipeline()
        pipe.incrbyfloat(key, amount)
        pipe.expire(key, ttl_seconds)
        pipe.execute()

    def get(self, key: str) -> float:
        raw = self._r.get(key)
        return float(raw) if raw is not None else 0.0


# Keep a day's and a month's counters alive a little past their window so a
# late-night call still sees the day's accrued spend.
_DAILY_TTL = 60 * 60 * 36          # 36h
_MONTHLY_TTL = 60 * 60 * 24 * 40   # 40d


class BudgetGate:
    """The spend gate. Reads ceilings from settings at call time so a config flip
    (or a test monkeypatch) takes effect without rebuilding the gate."""

    _PREFIX = "coach_budget"

    def __init__(self, backend) -> None:
        self._backend = backend

    # --- ceilings (read live from settings) ---
    @property
    def _user_daily(self) -> float:
        return settings.LLM_BUDGET_USER_DAILY_USD

    @property
    def _user_monthly(self) -> float:
        return settings.LLM_BUDGET_USER_MONTHLY_USD

    @property
    def _global_daily(self) -> float:
        return settings.LLM_BUDGET_GLOBAL_DAILY_USD

    @property
    def _global_monthly(self) -> float:
        return settings.LLM_BUDGET_GLOBAL_MONTHLY_USD

    def _key(self, scope: str, window: str) -> str:
        return f"{self._PREFIX}:{scope}:{window}"

    def over_budget(self, user_id) -> bool:
        """True when any enabled window is at or over its ceiling. Errors degrade
        to False (permissive) so the cap never becomes an availability risk."""
        today = _now().date()
        day, month = _day_key(today), _month_key(today)
        uid = str(user_id)
        try:
            checks = [
                (self._user_daily, self._key(f"user:{uid}", day)),
                (self._user_monthly, self._key(f"user:{uid}", month)),
                (self._global_daily, self._key("global", day)),
                (self._global_monthly, self._key("global", month)),
            ]
            for ceiling, key in checks:
                if ceiling > 0 and self._backend.get(key) >= ceiling:
                    logger.warning(
                        "llm_budget_exceeded", extra={"key": key, "ceiling": ceiling}
                    )
                    return True
            return False
        except Exception as exc:  # noqa: BLE001 — a cap must not break availability
            logger.warning("llm_budget_check_failed", extra={"error": str(exc)})
            return False

    def record(self, user_id, model: str, input_tokens: int, output_tokens: int) -> None:
        """Add one call's list-price cost to the user's and the global windows."""
        amount = cost_usd(model, input_tokens, output_tokens)
        if amount <= 0:
            return
        today = _now().date()
        day, month = _day_key(today), _month_key(today)
        uid = str(user_id)
        try:
            self._backend.incr(self._key(f"user:{uid}", day), amount, _DAILY_TTL)
            self._backend.incr(self._key(f"user:{uid}", month), amount, _MONTHLY_TTL)
            self._backend.incr(self._key("global", day), amount, _DAILY_TTL)
            self._backend.incr(self._key("global", month), amount, _MONTHLY_TTL)
        except Exception as exc:  # noqa: BLE001 — recording must never break generation
            logger.warning("llm_budget_record_failed", extra={"error": str(exc)})


# --- module-level gate (lazy, swappable in tests) --------------------------

_gate: Optional[BudgetGate] = None
_gate_lock = threading.Lock()


def _build_gate() -> BudgetGate:
    """A Redis-backed gate when REDIS_URL is reachable, else an in-process one."""
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=1)
        client.ping()
        return BudgetGate(_RedisBackend(client))
    except Exception as exc:  # noqa: BLE001 — no Redis -> degrade to in-process
        logger.info("llm_budget_using_in_memory_backend", extra={"reason": str(exc)})
        return BudgetGate(_InMemoryBackend())


def get_gate() -> BudgetGate:
    global _gate
    if _gate is None:
        with _gate_lock:
            if _gate is None:
                _gate = _build_gate()
    return _gate


def set_gate(gate: Optional[BudgetGate]) -> None:
    """Test seam: inject a controlled gate (None resets to lazy rebuild)."""
    global _gate
    with _gate_lock:
        _gate = gate


def new_in_memory_gate() -> BudgetGate:
    """A fresh in-process gate, for tests that drive a user over the cap."""
    return BudgetGate(_InMemoryBackend())


def cap_is_armed() -> bool:
    """True when at least one budget window has a positive ceiling.

    A window is enabled when its ceiling is > 0 (mirrors `over_budget`'s
    per-window check). All-zero ceilings mean the cost cap is OFF -- no window
    can ever trip, so generation is never degraded to the fallback on spend. The
    production boot guard (`assert_budget_cap_armed`) uses this to refuse a
    silently-uncapped prod deployment (#543).
    """
    return any(
        ceiling > 0
        for ceiling in (
            settings.LLM_BUDGET_USER_DAILY_USD,
            settings.LLM_BUDGET_USER_MONTHLY_USD,
            settings.LLM_BUDGET_GLOBAL_DAILY_USD,
            settings.LLM_BUDGET_GLOBAL_MONTHLY_USD,
        )
    )


def over_budget(user_id) -> bool:
    return get_gate().over_budget(user_id)


def record(user_id, model: str, input_tokens: int, output_tokens: int) -> None:
    get_gate().record(user_id, model, input_tokens, output_tokens)
