"""ExchangeLifecycle — the single owner of legal Exchange state transitions (#494).

The `Exchange` row is a first-class two-stage lifecycle (A1/A4, ADR 0010) with a
receipt-cadence variant (#296, ADR 0018). Its state lives in five sentinels:

- `opened_at`     — the exchange has opened (the LLM opener generated under A4, or
                    the first deterministic receipt under #296). Anchors the reply
                    window, independent of notification delivery.
- `opener_sent_at`— the opener notification went out (at most once). Unused under
                    the receipt cadence (there is no LLM opener).
- `fuller_sent_at`— the fuller/full-report notification went out (at most once).
                    Set means the exchange is CLOSED and never re-fires.
- `done_at`       — the runner tapped "done" on a receipt (#296), the explicit
                    "session finished" signal that schedules the full report.

Plus a per-activity receipt dedup sentinel that lives on the Activity, not the
exchange, because the receipt is per-activity:

- `Activity.receipt_sent_at` — this activity's instant receipt has been sent.

Before this module the transitions were scattered across five helpers in
`jobs/process_new_activity.py`, each re-asserting the "never re-fire" guarantee and
the reply/done open-window guards. The recent prod bugs (#487, #482, #490) all
landed in that scattered lifecycle. This module concentrates every transition and
its guard in one place, so the at-most-once invariant is enforced — and tested —
once, and a new bug surfaces at the interface rather than in prod.

Boundary: this module owns ROW STATE only. SCHEDULING (RQ enqueue) and NOTIFICATION
(building/sending the channel message, and reading the report row) stay in the job
layer. A transition function never sends, never schedules, never generates; it reads
and mutates the Exchange/Activity sentinels under the at-most-once invariant and
commits.

Sentinel posture mirrors A4 exactly: a notification sentinel is set only on a
successful send (the job calls `mark_notification_sent` AFTER the send returns), left
null on failure so the stage stays re-sendable, and never reset by a `force=true`
regeneration (no transition here ever clears a sentinel).
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Activity, Exchange

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    """A datetime guaranteed tz-aware (naive reads from the DB are treated UTC)."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- state reads --------------------------------------------------------------


def is_closed(exchange: Exchange) -> bool:
    """The exchange is CLOSED: the fuller/full report has been sent. A closed
    exchange never re-fires and never re-opens (a late activity starts a new
    block)."""
    return exchange.fuller_sent_at is not None


def is_open(exchange: Exchange) -> bool:
    """The exchange is OPEN: it has opened (`opened_at` set, independent of delivery)
    and is not yet closed. Replies and "done" taps act only on an open exchange."""
    return exchange.opened_at is not None and not is_closed(exchange)


def within_reply_window(exchange: Exchange, *, now: Optional[datetime] = None) -> bool:
    """The opener is recent enough that a reply/done may still act on the exchange.
    A reply on a stale exchange (opener older than EXCHANGE_REPLY_WINDOW_SECONDS)
    must never spin one up (AC4). An unopened exchange has no window."""
    if exchange.opened_at is None:
        return False
    now = now or _now()
    age = (now - _aware(exchange.opened_at)).total_seconds()
    return age <= settings.EXCHANGE_REPLY_WINDOW_SECONDS


def can_fire_reply_fuller(exchange: Exchange, *, now: Optional[datetime] = None) -> bool:
    """Whether a runner reply may early-fire the fuller turn: the exchange is open,
    not closed, and within the reply window (A4 reply path)."""
    return is_open(exchange) and within_reply_window(exchange, now=now)


# --- state transitions (each guarded, idempotent, committing) -----------------


def open_exchange(db: Session, exchange: Exchange, *, now: Optional[datetime] = None) -> bool:
    """Open the exchange (set `opened_at`) if not already open. Idempotent: a second
    call on an already-opened exchange is a no-op. Returns True if it opened it now.

    Opening is independent of notification delivery (A4 posture), so the reply window
    anchors even under a NoOp local notifier."""
    if exchange.opened_at is not None:
        return False
    exchange.opened_at = now or _now()
    db.add(exchange)
    db.commit()
    return True


def record_receipt_sent(db: Session, activity: Activity, *, now: Optional[datetime] = None) -> None:
    """Mark this activity's instant receipt as sent (#296). The per-activity dedup
    sentinel; set only after a successful send so the receipt stays re-sendable on a
    pipeline retry. The caller has already confirmed `receipt_sent_at` was null."""
    activity.receipt_sent_at = now or _now()
    db.add(activity)
    db.commit()


def mark_done(db: Session, exchange: Exchange, *, now: Optional[datetime] = None) -> bool:
    """Record the runner's explicit "done" tap (#296). Idempotent: a second tap is a
    no-op on `done_at`. Defensively opens the exchange if the receipt somehow had not.
    Returns True if it recorded the completion now.

    The CALLER must already have checked the exchange is open and within window via
    `can_fire_reply_fuller` (or the equivalent) — this only writes state."""
    if exchange.done_at is not None:
        return False
    now = now or _now()
    exchange.done_at = now
    if exchange.opened_at is None:
        exchange.opened_at = now
    db.add(exchange)
    db.commit()
    return True


# --- the at-most-once notification invariant (one place) ----------------------

#: The notification sentinels and the row they live on. The opener/fuller stages
#: dedup on the block's Exchange row (A1); the single-shot rollback path dedups on
#: the Activity itself (per-activity dedup needs a per-activity store).
NOTIFICATION_SENTINELS = frozenset(
    {"opener_sent_at", "fuller_sent_at", "coach_notification_sent_at"}
)


def notification_already_sent(sentinel_obj, sentinel_attr: str) -> bool:
    """Whether this stage's notification has already gone out (the at-most-once read).

    `sentinel_obj` is the Exchange (two-stage) or the Activity (single-shot rollback);
    `sentinel_attr` names the dedup sentinel. The job checks this BEFORE building and
    sending, so a stage sends at most once per exchange (A4 AC5)."""
    if sentinel_attr not in NOTIFICATION_SENTINELS:
        raise ValueError(f"unknown notification sentinel {sentinel_attr!r}")
    return getattr(sentinel_obj, sentinel_attr) is not None


def mark_notification_sent(
    db: Session, sentinel_obj, sentinel_attr: str, *, now: Optional[datetime] = None
) -> None:
    """Set this stage's notification sentinel AFTER a successful send (the at-most-once
    write). Set only on success so a send failure leaves the stage re-sendable (#114).
    Never clears a sentinel, so a `force=true` regeneration never re-arms a stage."""
    if sentinel_attr not in NOTIFICATION_SENTINELS:
        raise ValueError(f"unknown notification sentinel {sentinel_attr!r}")
    setattr(sentinel_obj, sentinel_attr, now or _now())
    db.add(sentinel_obj)
    db.commit()
