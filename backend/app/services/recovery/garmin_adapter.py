"""Garmin Connect as a recovery source, through the unofficial `garminconnect` library (#555).

OWNER-ONLY and unofficial: Garmin's official Health API is closed to new
applicants and aggregators break the no-runaway-spend rule, so this reads the
owner's own account the way the Garmin Connect web app does. It can break
without notice, which is why everything provider-specific lives in THIS file and
the store, endpoint and job speak only `RecoveryReading`.

Credentials: no Garmin password ever reaches this app. The owner mints a token
dump once with `scripts/garmin_login.py` and sets it as the worker secret
`GARMIN_TOKENS`. The library refreshes its short-lived token itself; the
refreshed dump is kept in Redis only (never the DB, never logged) and preferred
over the env value next run, because a refresh can rotate the refresh token and
the env copy would then be stale. When the long-lived refresh token finally
expires the library raises an authentication error; this adapter turns it into
`RecoveryAuthError`, the sync stops and logs, and the owner re-runs the script.

Response shapes: the library returns Garmin's raw JSON and does not model it, so
the field names below come from Garmin Connect's wellness endpoints as seen in
the library's own methods (`get_sleep_data`, `get_hrv_data`, `get_rhr_day`) and
community use of them. They are NOT verified against a live account in this
repo (no credentials), so every accessor is defensive: a missing or oddly typed
field becomes None (not measured), never an exception and never a zero.
"""

import logging
import time
from datetime import date
from typing import Any, Callable, Optional

from app.services.recovery.port import RecoveryAuthError, RecoveryReading

logger = logging.getLogger(__name__)

SOURCE = "garmin"
TOKENS_REDIS_KEY = "garmin:tokens"
# The library treats a tokenstore shorter than this as a file PATH, so a shorter
# value is a misconfiguration, not a token dump.
_MIN_TOKEN_DUMP_CHARS = 513

# Politeness toward an unofficial endpoint during the 30-day backfill.
_PAUSE_BETWEEN_DAYS_S = 0.5


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _positive_int(value: Any) -> Optional[int]:
    n = _num(value)
    return int(round(n)) if n is not None and n > 0 else None


def _positive_float(value: Any) -> Optional[float]:
    n = _num(value)
    return n if n is not None and n > 0 else None


def _dig(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def parse_garmin_day(
    day: date,
    sleep: Any,
    hrv: Any,
    rhr: Any,
) -> RecoveryReading:
    """Fold the three Garmin payloads for one night into a RecoveryReading.

    Zero or negative values are treated as not measured: Garmin reports 0 for a
    night the watch was off, and 0 hours of sleep is not a fact about the runner.
    """
    sleep_s = _positive_int(_dig(sleep, "dailySleepDTO", "sleepTimeSeconds"))
    score = _positive_int(_dig(sleep, "dailySleepDTO", "sleepScores", "overall", "value"))

    hrv_avg = _positive_float(_dig(hrv, "hrvSummary", "lastNightAvg"))
    if hrv_avg is None:
        hrv_avg = _positive_float(_dig(sleep, "avgOvernightHrv"))
    status = _dig(hrv, "hrvSummary", "status") or _dig(sleep, "hrvStatus")
    status = status if isinstance(status, str) and status else None
    base_low = _positive_float(_dig(hrv, "hrvSummary", "baseline", "balancedLow"))
    base_high = _positive_float(_dig(hrv, "hrvSummary", "baseline", "balancedUpper"))

    resting: Optional[int] = None
    series = _dig(rhr, "allMetrics", "metricsMap", "WELLNESS_RESTING_HEART_RATE")
    if isinstance(series, list) and series:
        resting = _positive_int(_dig(series[0], "value"))
    if resting is None:
        resting = _positive_int(_dig(sleep, "restingHeartRate"))

    return RecoveryReading(
        day=day,
        sleep_duration_s=sleep_s,
        sleep_score=score,
        hrv_avg_ms=hrv_avg,
        hrv_status=status,
        hrv_baseline_low_ms=base_low,
        hrv_baseline_high_ms=base_high,
        resting_hr=resting,
    )


class GarminRecoverySource:
    """`RecoverySource` over a logged-in `garminconnect.Garmin` client."""

    source = SOURCE

    def __init__(
        self,
        env_tokens: str,
        redis: Any,
        *,
        client_factory: Optional[Callable[[], Any]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._env_tokens = env_tokens
        self._redis = redis
        # Injected in tests; in production the real library, imported lazily so
        # the rest of the app (and the test suite) never needs it installed.
        self._client_factory = client_factory or self._default_factory
        self._sleep = sleep
        self._api: Any = None

    @staticmethod
    def _default_factory() -> Any:
        from garminconnect import Garmin  # lazy: optional dependency

        return Garmin()

    def _candidate_tokens(self) -> list[str]:
        out: list[str] = []
        try:
            cached = self._redis.get(TOKENS_REDIS_KEY)
        except Exception:  # noqa: BLE001 - Redis down: fall back to the env value
            cached = None
        if isinstance(cached, bytes):
            cached = cached.decode()
        if cached:
            out.append(cached)
        if self._env_tokens and self._env_tokens not in out:
            out.append(self._env_tokens)
        return out

    def _login(self) -> Any:
        candidates = self._candidate_tokens()
        if not candidates:
            raise RecoveryAuthError("no Garmin token configured (GARMIN_TOKENS unset)")
        for dump in candidates:
            if len(dump) < _MIN_TOKEN_DUMP_CHARS:
                logger.error("GARMIN_TOKENS is not a token dump (too short); re-run garmin_login.py")
                continue
            api = self._client_factory()
            try:
                api.login(tokenstore=dump)
            except Exception as exc:  # noqa: BLE001
                # Never include str(exc): library errors can echo request detail.
                logger.warning("garmin login with a stored token failed: %s", type(exc).__name__)
                continue
            self._persist(api)
            return api
        raise RecoveryAuthError("Garmin token rejected or expired; re-run garmin_login.py")

    def _persist(self, api: Any) -> None:
        """Keep a refreshed token dump in Redis only, so the next run starts from it."""
        try:
            self._redis.set(TOKENS_REDIS_KEY, api.client.dumps())
        except Exception as exc:  # noqa: BLE001 - losing the refresh is not fatal
            logger.warning("could not cache refreshed Garmin token: %s", type(exc).__name__)

    def fetch_day(self, day: date) -> RecoveryReading:
        if self._api is None:
            self._api = self._login()
        cdate = day.isoformat()
        try:
            payloads = []
            for call in (self._api.get_sleep_data, self._api.get_hrv_data, self._api.get_rhr_day):
                try:
                    payloads.append(call(cdate))
                except Exception as exc:  # noqa: BLE001
                    if _is_auth_error(exc):
                        raise
                    # One endpoint missing for a night (404 when the watch was off)
                    # must not discard the others.
                    logger.info("garmin %s unavailable for %s: %s", call.__name__, cdate, type(exc).__name__)
                    payloads.append(None)
        except Exception as exc:  # noqa: BLE001
            if _is_auth_error(exc):
                raise RecoveryAuthError("Garmin rejected the session; re-run garmin_login.py") from None
            raise
        self._sleep(_PAUSE_BETWEEN_DAYS_S)
        return parse_garmin_day(day, *payloads)


def _is_auth_error(exc: BaseException) -> bool:
    # By name, so this module never has to import the optional library. A rate
    # limit (TooManyRequests) is deliberately NOT auth: the token is fine, the
    # day just fails transiently and the next run retries it.
    return type(exc).__name__ == "GarminConnectAuthenticationError"
