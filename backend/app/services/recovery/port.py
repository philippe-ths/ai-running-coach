"""The contract a recovery source meets (#555)."""

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol


class RecoveryAuthError(Exception):
    """The source refused our credentials (expired or revoked token).

    Distinct from a transient failure: retrying will not help, the owner has to
    mint a new token. The message must never contain the token.
    """


@dataclass(frozen=True)
class RecoveryReading:
    """One night as the device reported it. A None field means NOT MEASURED."""

    day: date
    sleep_duration_s: Optional[int] = None
    sleep_score: Optional[int] = None
    hrv_avg_ms: Optional[float] = None
    hrv_status: Optional[str] = None
    hrv_baseline_low_ms: Optional[float] = None
    hrv_baseline_high_ms: Optional[float] = None
    resting_hr: Optional[int] = None

    def has_signal(self) -> bool:
        return any(
            v is not None
            for v in (
                self.sleep_duration_s,
                self.sleep_score,
                self.hrv_avg_ms,
                self.resting_hr,
            )
        )


class RecoverySource(Protocol):
    """A device or service that can say how a given night went."""

    #: Stored on every row; part of the (user, day, source) key.
    source: str

    def fetch_day(self, day: date) -> RecoveryReading:
        """Return the reading for the night ending on ``day``.

        Raises RecoveryAuthError when the credentials are no longer accepted. Any
        other exception is treated as a transient failure for that day.
        """
        ...
