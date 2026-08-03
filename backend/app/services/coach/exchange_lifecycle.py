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

Two categories live here, and the difference is the transaction (#740):

- STATE TRANSITIONS (`open_exchange`, `mark_done`, `claim_fuller`, …) advance a live
  exchange and COMMIT, because each is a standalone at-most-once decision that must
  land before the caller does anything irreversible with the answer.
- STRUCTURAL changes (`create_exchange_for_block`, `absorb_exchange`,
  `delete_exchange_for_block`) construct/merge/remove the row itself and DO NOT
  commit — they participate in the caller's transaction. The block-correction paths
  in `services/blocks.py` are each one transaction (membership, bounds, primary, and
  the exchange row land together or not at all), so a structural function that
  committed mid-correction would change their failure semantics.

Sentinel posture mirrors A4 exactly: a notification sentinel is set only on a
successful send (the job calls `mark_notification_sent` AFTER the send returns), left
null on failure so the stage stays re-sendable, and never reset by a `force=true`
regeneration (no transition here ever clears a sentinel).
"""

import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Activity, Block, Exchange

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
    """Whether a runner action (a reply OR a "done" tap) may early-fire the fuller/full
    report: the exchange is open, not closed, and within the reply window. The SINGLE
    act-guard shared by the A4 reply path and the #296 "done" path, so "reply/done may
    act only on an open, in-window exchange" has one implementation, not two."""
    return is_open(exchange) and within_reply_window(exchange, now=now)


# --- block -> exchange resolution ---------------------------------------------


def get_exchange_for_block(db: Session, block_id) -> Optional[Exchange]:
    """The Exchange owning this block, or None (A1: one exchange per block, `block_id`
    UNIQUE). The single block->exchange lookup, so the job layer never re-implements the
    query at each call site."""
    return db.query(Exchange).filter(Exchange.block_id == block_id).first()


def require_exchange_for_block(db: Session, block_id) -> Exchange:
    """The Exchange owning this block, raising `NoResultFound` when there is none.

    The strict resolver for the block-CORRECTION paths (split/merge), where a missing
    exchange is a broken invariant rather than a state to degrade around — every block
    is created with its exchange. Raising (rather than returning None and crashing later
    on an attribute) keeps the correction endpoints' failure mode exactly as it was: the
    API layer catches only `ValueError`, so this surfaces as a 500, not a spurious 422.
    """
    return db.query(Exchange).filter(Exchange.block_id == block_id).one()


def ensure_exchange_for_block(db: Session, block: Block) -> Exchange:
    """The block's Exchange, creating one if it is somehow missing (defensive: blocks are
    created WITH their exchange in `blocks.py`, A1). Idempotent get-or-create, so a
    block-complete check that finds no row still resolves an exchange rather than crashing.

    The one COMMITTING creator: the job layer calls it outside any correction transaction,
    so the recovered row must be durable before the block-complete check proceeds.
    """
    exchange = get_exchange_for_block(db, block.id)
    if exchange is None:
        exchange = create_exchange_for_block(db, block)
        db.commit()
    return exchange


# --- structural row changes (the caller's transaction, no commit) -------------

#: The stage sentinels a block CORRECTION carries across, so a split or merge can
#: never re-open delivery: whatever had already been sent stays marked as sent on
#: every resulting exchange (ADR 0011 — corrections never re-fire).
#:
#: `done_at` is deliberately absent, preserving the behaviour these corrections have
#: always had: it is the runner's completion tap (#296), not a delivery stage, and a
#: dropped tap cannot cause a re-send (the `fuller_sent_at` inheritance is what stops
#: a CLOSED exchange re-firing). Adding it would be a behaviour change — tracked
#: separately rather than smuggled into a behaviour-preserving refactor.
STAGE_SENTINELS = ("opened_at", "opener_sent_at", "fuller_sent_at")


def create_exchange_for_block(
    db: Session, block: Block, *, inherit_from: Optional[Exchange] = None
) -> Exchange:
    """Construct the Exchange row for a block (A1: created WITH the block, so the
    one-exchange-per-block invariant is kept atomically at block creation).

    `inherit_from` carries the `STAGE_SENTINELS` across from another exchange — the
    split correction, where the new half must start out already knowing what has been
    delivered so the correction cannot re-open delivery.

    Structural: adds to the session but does NOT commit, so it lands with the rest of
    the caller's block work (or not at all).
    """
    exchange = Exchange(user_id=block.user_id, block_id=block.id)
    if inherit_from is not None:
        for sentinel in STAGE_SENTINELS:
            setattr(exchange, sentinel, getattr(inherit_from, sentinel))
    db.add(exchange)
    return exchange


def absorb_exchange(db: Session, surviving: Exchange, absorbed: Exchange) -> Exchange:
    """Fold one exchange into another and delete the absorbed row (the merge correction).

    The survivor takes the MOST-ADVANCED value of each stage sentinel from either side,
    so a merge can never re-fire a stage that either block had already delivered.

    Structural: no commit — the merge is one transaction with the membership and bounds
    changes. Returns the survivor.
    """
    for sentinel in STAGE_SENTINELS:
        setattr(
            surviving,
            sentinel,
            _latest(getattr(surviving, sentinel), getattr(absorbed, sentinel)),
        )
    db.delete(absorbed)
    return surviving


def delete_exchange_for_block(db: Session, block_id) -> bool:
    """Delete the block's Exchange row, if it has one. Returns True if one was deleted.

    Used when a block is emptied (its last activity was soft-deleted, #238): the row is
    REMOVED rather than reset, so the at-most-once delivery guarantee is preserved by
    absence — no notification can re-fire against a row that no longer exists.

    Structural: no commit, and no flush — the caller sequences the flush, because the
    Exchange must be gone before the Block row it references is deleted (FK order).
    """
    exchange = get_exchange_for_block(db, block_id)
    if exchange is None:
        return False
    db.delete(exchange)
    return True


def _latest(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    """The most-advanced sentinel: non-null wins; both set, the later."""
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)


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
    """Atomically record the runner's explicit "done" tap (#296). Idempotent: a second
    tap is a no-op on `done_at`. Defensively opens the exchange if the receipt somehow
    had not. Returns True if THIS call recorded the completion now.

    The CALLER must already have checked the exchange is open and within window via
    `can_fire_reply_fuller` (or the equivalent) — this only writes state. The caller
    schedules the full report only when this returns True, so exactly one tap schedules.

    #514: the prior guard was a non-atomic check-then-act (`done_at` read, then the
    write). The web service is multi-process and the Telegram "done" callback hits it, so
    two SIMULTANEOUS taps could both read `done_at` null, both transition, and both
    schedule the full report. This is a conditional UPDATE that sets `done_at` only WHERE
    it is still NULL and inspects the rowcount, mirroring `claim_fuller`: the database
    serializes the two taps so exactly one wins (rowcount 1) and the loser (rowcount 0)
    learns it lost and does NOT schedule. `opened_at` is set in the SAME UPDATE only
    where it is still null, so the defensive open stays atomic with the claim and never
    moves an existing reply-window anchor."""
    stamp = now or _now()
    result = db.execute(
        update(Exchange)
        .where(Exchange.id == exchange.id, Exchange.done_at.is_(None))
        .values(
            done_at=stamp,
            opened_at=func.coalesce(Exchange.opened_at, stamp),
        )
    )
    db.commit()
    won = result.rowcount == 1
    if won:
        # Keep the in-memory instance consistent with the row the winner just wrote.
        exchange.done_at = stamp
        if exchange.opened_at is None:
            exchange.opened_at = stamp
    return won


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


# --- the atomic fuller-turn claim (#506) --------------------------------------


def claim_fuller(db: Session, exchange: Exchange, *, now: Optional[datetime] = None) -> bool:
    """Atomically CLAIM the fuller turn for this exchange, so exactly one of a racing
    timer-fired and reply-fired trigger proceeds to generate + notify (#506).

    The prior guard was a non-atomic check-then-act (`is_closed` read, then a long
    `generate_fuller`, then the notification sentinel set). Under multiple workers both
    triggers could pass the read, both generate, and both notify — two notifications and
    a wasted second generation. This is a conditional UPDATE that sets `fuller_sent_at`
    only WHERE it is still NULL and inspects the rowcount, so the database serializes the
    two triggers: the winner gets rowcount 1 and owns the turn, the loser gets 0 and
    bails BEFORE generating.

    The claim doubles as the at-most-once notification sentinel (`fuller_sent_at` set =
    CLOSED), so the winner does NOT re-mark it after the send. To preserve the
    re-sendable-on-failure posture (#114), the caller MUST `release_fuller_claim` if the
    generation falls back, produces no report, or the send fails — leaving the sentinel
    null so the stage can be retried. Returns True if this caller won the claim."""
    stamp = now or _now()
    result = db.execute(
        update(Exchange)
        .where(Exchange.id == exchange.id, Exchange.fuller_sent_at.is_(None))
        .values(fuller_sent_at=stamp)
    )
    db.commit()
    won = result.rowcount == 1
    if won:
        # Keep the in-memory instance consistent with the row the winner just claimed.
        exchange.fuller_sent_at = stamp
    return won


class FullerClaim:
    """The outcome of one `fuller_claim` attempt: whether this caller WON the turn, and
    whether the completed work earned the right to KEEP the claim.

    `won` is False for a losing concurrent trigger (or a late timer after an early reply
    already closed the exchange) — it must bail without generating. The winner calls
    `keep()` only once a notification has genuinely gone out; anything else (no report, a
    fallback report, no channel, a send failure, a raised exception) leaves the claim
    unkept and `fuller_claim` releases it on the way out.
    """

    __slots__ = ("won", "kept")

    def __init__(self, won: bool) -> None:
        self.won = won
        self.kept = False

    def keep(self) -> None:
        """Keep the claim: the send succeeded, so `fuller_sent_at` stays set and the
        exchange stays CLOSED. Irreversible by design — the exchange has spoken."""
        self.kept = True


@contextmanager
def fuller_claim(db: Session, exchange: Exchange) -> Iterator[FullerClaim]:
    """Claim the fuller turn for the length of a block, releasing it on every exit that
    did not end in a successful send (#740).

    The claim/release DANCE, owned here rather than hand-written at the call site. The
    rule it enforces is subtle and was learned the hard way (#506/#114): `claim_fuller`
    sets `fuller_sent_at`, which doubles as the CLOSED / at-most-once sentinel, so EVERY
    path between the claim and a genuinely-successful send must put it back — not only
    the clean early returns but a RAISED exception too (an RQ JobTimeoutException over
    the ~120-360s generation window, a context-build or network error). Miss one and the
    exchange exits CLOSED with no notification, and the RQ retry's claim finds the
    sentinel set and bails, stranding the turn — strictly worse than the pre-claim
    null-on-crash posture.

    A LOSING caller never releases: the sentinel it found set belongs to the winner, and
    clearing it would re-arm a turn somebody else is running.

    The caller keeps SENDING (building the message, reading the report row) — this owns
    only the row state, per the module boundary.
    """
    claim = FullerClaim(claim_fuller(db, exchange))
    try:
        yield claim
    finally:
        if claim.won and not claim.kept:
            release_fuller_claim(db, exchange)


def release_fuller_claim(db: Session, exchange: Exchange) -> None:
    """Release a fuller-turn claim taken by `claim_fuller` when the turn did not complete
    (a fallback/empty generation, missing report, or a send failure), so the stage stays
    re-sendable (#114). Only ever clears a sentinel this same caller set; never called on
    a legitimately-closed exchange. The mirror of `claim_fuller`'s pre-send set."""
    db.execute(
        update(Exchange)
        .where(Exchange.id == exchange.id)
        .values(fuller_sent_at=None)
    )
    db.commit()
    exchange.fuller_sent_at = None
