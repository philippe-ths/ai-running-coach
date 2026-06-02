"""Redis-backed rate limiting for the magic-link request endpoint (ADR 0005).

Two independent fixed windows cap the email-spam vector: per-email-per-hour and
per-IP-per-minute. The limiter fails open if Redis is unavailable: login
availability outweighs the rate cap, and the residual abuse window is still
bounded by SMTP throughput. Outages are logged.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.core.config import settings

logger = logging.getLogger(__name__)


class _RedisLike(Protocol):
    def incr(self, name: str) -> int: ...
    def expire(self, name: str, seconds: int) -> bool: ...


class RateLimiter:
    def __init__(self, redis_client: _RedisLike) -> None:
        self._redis = redis_client

    def _under_limit(self, key: str, limit: int, window_seconds: int) -> bool:
        try:
            count = self._redis.incr(key)
            if count == 1:
                self._redis.expire(key, window_seconds)
            return count <= limit
        except Exception:  # noqa: BLE001 - any Redis failure fails open
            logger.warning("rate_limit_backend_unavailable key=%s; failing open", key)
            return True

    def allow_login_request(self, *, email: str, ip: str, now_epoch: float) -> bool:
        """Count this request against both windows; allow only if both are under
        their limit. Both counters are always incremented so each dimension
        independently reflects attempt volume."""
        email_key = f"loginrate:email:{email}:{int(now_epoch // 3600)}"
        ip_key = f"loginrate:ip:{ip}:{int(now_epoch // 60)}"
        email_ok = self._under_limit(email_key, settings.LOGIN_RATE_PER_EMAIL_HOUR, 3600)
        ip_ok = self._under_limit(ip_key, settings.LOGIN_RATE_PER_IP_MINUTE, 60)
        return email_ok and ip_ok


def get_rate_limiter() -> RateLimiter:
    """Default limiter wrapping the shared Redis connection."""
    from app.core.queue import redis_conn

    return RateLimiter(redis_conn)
