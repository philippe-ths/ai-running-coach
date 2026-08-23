"""Owner-scoped reads and writes for period reports (#946).

Every function takes a REQUIRED `user_id` — the `services/schedule/store.py`
precedent applied to a second async-generated resource. The async lifecycle and
staleness guard mirror `TrainingPlan`/`schedule/store.py` exactly:
`create_generating_report` writes the row before the LLM call starts so a
crashed worker leaves a visible `generating` row rather than silence, and
`report_in_flight` treats a row stuck in `generating` past `STALE_AFTER` as
abandoned.

The request identity — `(user_id, period_start, period_end, disciplines_key,
prompt_id, schema_version)` — has no DB-level uniqueness constraint, the
`TrainingPlan` precedent (a PostgreSQL partial unique index is syntax the
SQLite test database cannot exercise, and unlike a training plan an old FAILED
row is allowed to sit behind a later successful retry for the same identity).
It is held here instead, but a DB constraint was never the only thing standing
between two concurrent identical requests and two paid generations: reading
`report_in_flight`/`find_ready` and then writing `create_generating_report` is
three separate statements with nothing atomic binding them together. `claim_identity`
closes that window with the `batch_chain.acquire_enqueue_slot` idiom
(`self_heal`'s atomic Redis `SET NX EX`, `degrade_open=False` — an unsent report
is recoverable, a double-spent generation is not) keyed on the FULL identity, not
just the user, so two distinct requests from the same runner never contend. The
API route acquires the claim before doing anything else.

`mark_ready`/`mark_failed` are themselves COMPARE-AND-SET: each only writes when
the row is still `generating`, expressed as an `UPDATE ... WHERE status =
'generating'` so the check and the write are one atomic statement rather than a
read-then-write an intervening writer could race. This is a SEPARATE race from
the one `claim_identity` closes: a genuinely slow job can still outlive
`STALE_AFTER`, at which point a legitimate retry (well outside the claim's own
short TTL) finds the row stale, marks it `failed`, and starts a second
generation. Without the CAS, the first job's eventual, real completion would
blindly overwrite that `failed` row back to `ready` — resurrecting a row someone
else already decided about and creating two `ready`-shaped answers to one
request. With it, a settled row simply discards a late result (logged, not
silently dropped) rather than being resurrected; `list_reports` also collapses
to at most one entry per identity so an old discarded attempt never reads as a
second report next to the one that actually landed.

Not fixed here, and deliberately not: there is no per-user rate limit over
DISTINCT identities. Fifty slightly different date ranges are fifty distinct
identities, each a full paid generation, gated only by the per-user dollar cap
(`turn.over_budget`) — itself a check-then-spend read, not atomic. That gap is a
pre-existing, system-wide property of every coaching turn (report/opener/fuller/
thread/schedule draft all share it), not something introduced here, so it is not
fixed on this branch. It is recorded here because this is the surface most
likely to make it matter: a runner-driven review over a wide pack, on
`COACH_PERIOD_MODEL_ID`, is the most expensive generation the product runs.
"""

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.period_report import PeriodReport

logger = logging.getLogger(__name__)

GENERATING = "generating"
READY = "ready"
FAILED = "failed"

# The `TrainingPlan.DRAFT_STALE_AFTER` precedent: generous next to a generation
# that runs a stronger model over a wider pack than the per-run report, but not
# so generous that a crashed worker wedges the feature for a runner with no way
# back but a DB edit.
STALE_AFTER = timedelta(minutes=20)

# Runner-facing failure categories. A category, never the gate's own text — the
# `TrainingPlan.failure_kind` precedent: the API chooses a sentence with a next
# step in it, and nothing internal travels with the category.
FAILURE_UNREACHABLE = "unreachable"
FAILURE_OVER_BUDGET = "over_budget"
FAILURE_POLICY = "policy_violation"
FAILURE_UNKNOWN = "unknown"

FAILURE_KINDS = frozenset(
    {FAILURE_UNREACHABLE, FAILURE_OVER_BUDGET, FAILURE_POLICY, FAILURE_UNKNOWN}
)

# How long a claim holds a request identity exclusively. Only needs to outlive
# the read-then-write this closes (report_in_flight -> find_ready ->
# create_generating_report, a handful of fast DB round trips), NOT the
# generation itself — the generation is a background job the claim has already
# let go of by the time it runs. Short on purpose: a legitimate new request for
# the same identity minutes later (a retry after failure, or just asking again)
# must never wait out a long cooldown, only the brief window a concurrent
# double-tap needs closed.
CLAIM_TTL_SECONDS = 30


def _claim_key(
    user_id: uuid.UUID,
    *,
    period_start: date,
    period_end: date,
    disciplines_key: str,
    prompt_id: str,
    schema_version: str,
) -> str:
    return (
        f"period_report_claim:{user_id}:{period_start.isoformat()}:"
        f"{period_end.isoformat()}:{disciplines_key}:{prompt_id}:{schema_version}"
    )


def claim_identity(
    user_id: uuid.UUID,
    *,
    period_start: date,
    period_end: date,
    disciplines_key: str,
    prompt_id: str,
    schema_version: str,
) -> bool:
    """At most one caller may hold this exact request identity at a time (#946
    review): the `batch_chain.acquire_enqueue_slot` idiom — an atomic Redis
    `SET NX EX` — keyed on the FULL identity rather than just the user, so two
    concurrent identical POSTs cannot both pass the
    report_in_flight/find_ready/create_generating_report sequence and both
    enqueue a paid generation.

    `degrade_open=False`: on a Redis outage this REFUSES rather than allowing a
    double generation through, the opposite posture from `self_heal`'s. A missed
    report is recoverable by asking again; a doubled generation is not, so this
    surface degrades CLOSED.
    """
    from app.jobs import batch_chain

    key = _claim_key(
        user_id,
        period_start=period_start,
        period_end=period_end,
        disciplines_key=disciplines_key,
        prompt_id=prompt_id,
        schema_version=schema_version,
    )
    return batch_chain.acquire_enqueue_slot(key, CLAIM_TTL_SECONDS, degrade_open=False)


def disciplines_key(disciplines: Optional[List[str]]) -> str:
    """The canonicalised identity form of a discipline list: sorted, lower-cased,
    de-duplicated, comma-joined; "all" for no filter. The ONE function that
    computes it, so two callers can never disagree on what counts as "the same
    request"."""
    if not disciplines:
        return "all"
    cleaned = sorted({d.strip().lower() for d in disciplines if d and d.strip()})
    return ",".join(cleaned) if cleaned else "all"


def _is_stale(report: PeriodReport, *, now: Optional[datetime] = None) -> bool:
    created = report.created_at
    if created is None:
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return created < (now - STALE_AFTER)


def report_in_flight(
    db: Session,
    user_id: uuid.UUID,
    *,
    period_start: date,
    period_end: date,
    disciplines_key: str,
    prompt_id: str,
    schema_version: str,
) -> Optional[PeriodReport]:
    """A generation currently running for this exact request, or None.

    A stale row (the worker died before finishing) is marked `failed` here and
    treated as not in flight, so a runner is never stuck behind a generation
    that will never complete.
    """
    row = (
        db.query(PeriodReport)
        .filter(
            PeriodReport.user_id == user_id,
            PeriodReport.period_start == period_start,
            PeriodReport.period_end == period_end,
            PeriodReport.disciplines_key == disciplines_key,
            PeriodReport.prompt_id == prompt_id,
            PeriodReport.schema_version == schema_version,
            PeriodReport.status == GENERATING,
        )
        .order_by(PeriodReport.created_at.desc(), PeriodReport.id.desc())
        .first()
    )
    if row is None:
        return None
    if _is_stale(row):
        mark_failed(db, row, "abandoned: still generating after the staleness window", kind=FAILURE_UNKNOWN)
        return None
    return row


def find_ready(
    db: Session,
    user_id: uuid.UUID,
    *,
    period_start: date,
    period_end: date,
    disciplines_key: str,
    prompt_id: str,
    schema_version: str,
) -> Optional[PeriodReport]:
    """An already-generated report for this exact request, or None.

    Identical requests are cheap to satisfy from the cache — the same
    "(activity_id, prompt_id, schema_version) retains rather than regenerates"
    principle `CoachReport` uses, applied to a period's identity.
    """
    return (
        db.query(PeriodReport)
        .filter(
            PeriodReport.user_id == user_id,
            PeriodReport.period_start == period_start,
            PeriodReport.period_end == period_end,
            PeriodReport.disciplines_key == disciplines_key,
            PeriodReport.prompt_id == prompt_id,
            PeriodReport.schema_version == schema_version,
            PeriodReport.status == READY,
        )
        .order_by(PeriodReport.created_at.desc(), PeriodReport.id.desc())
        .first()
    )


def create_generating_report(
    db: Session,
    user_id: uuid.UUID,
    *,
    period_start: date,
    period_end: date,
    disciplines: List[str],
    prompt_id: str,
    schema_version: str,
) -> PeriodReport:
    """An empty `generating` row, written before generation starts (#946
    requirement 2) — the same reason `create_drafting_plan` exists: a crashed
    worker still leaves the runner's client something to poll."""
    report = PeriodReport(
        user_id=user_id,
        period_start=period_start,
        period_end=period_end,
        disciplines=list(disciplines or []),
        disciplines_key=disciplines_key(disciplines),
        status=GENERATING,
        prompt_id=prompt_id,
        schema_version=schema_version,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def mark_ready(
    db: Session,
    report: PeriodReport,
    *,
    content: dict,
    context_pack: dict,
    model_id: str,
    meta: Optional[dict] = None,
) -> Optional[PeriodReport]:
    """Store a finished generation — COMPARE-AND-SET (#946 review): the write is
    an `UPDATE ... WHERE id = :id AND status = 'generating'`, so the check ("is
    this row still mine to settle") and the write happen as ONE atomic
    statement. A row that a stale-timeout retry already reassigned (marked
    `failed`, superseded by a fresh generation) is no longer `generating` by the
    time a genuinely slow original job finishes, so this returns None and
    discards the late result rather than resurrecting a row someone else already
    decided about — the alternative (a blind attribute-then-commit) is exactly
    what let a completing-late job flip a `failed` row back to `ready` behind a
    retry's back, producing two `ready`-shaped answers to one request.

    Returns the refreshed row on success, None when the result was discarded.
    """
    updated = (
        db.query(PeriodReport)
        .filter(PeriodReport.id == report.id, PeriodReport.status == GENERATING)
        .update(
            {
                PeriodReport.status: READY,
                PeriodReport.report: content,
                PeriodReport.context_pack: context_pack,
                PeriodReport.model_id: model_id,
                PeriodReport.meta: meta or {},
                PeriodReport.generated_at: datetime.now(timezone.utc),
            },
            synchronize_session=False,
        )
    )
    db.commit()
    if updated == 0:
        logger.warning(
            "period report %s: generation finished but the row was no longer "
            "'generating' (settled by another writer while this one ran); "
            "discarding the result instead of resurrecting it",
            report.id,
        )
        return None
    db.refresh(report)
    return report


def mark_failed(
    db: Session, report: PeriodReport, reason: str, *, kind: str = FAILURE_UNKNOWN
) -> Optional[PeriodReport]:
    """COMPARE-AND-SET, the `mark_ready` precedent: only writes `failed` over a
    row that is still `generating`. Called both by a generation's own failure
    paths and by `report_in_flight`'s staleness sweep, so this also protects
    against two concurrent stale-sweeps double-marking (harmless but logged) the
    same row.

    Returns the refreshed row on success, None when the row was already settled.
    """
    merged_meta = {
        **(report.meta or {}),
        "failure_reason": reason,
        "failure_kind": kind if kind in FAILURE_KINDS else FAILURE_UNKNOWN,
    }
    updated = (
        db.query(PeriodReport)
        .filter(PeriodReport.id == report.id, PeriodReport.status == GENERATING)
        .update(
            {PeriodReport.status: FAILED, PeriodReport.meta: merged_meta},
            synchronize_session=False,
        )
    )
    db.commit()
    if updated == 0:
        logger.warning(
            "period report %s: tried to mark failed (%s) but the row was no "
            "longer 'generating'; leaving the already-settled row alone",
            report.id,
            kind,
        )
        return None
    db.refresh(report)
    return report


def get_owned_report(
    db: Session, report_id: uuid.UUID, user_id: uuid.UUID
) -> Optional[PeriodReport]:
    return (
        db.query(PeriodReport)
        .filter(PeriodReport.id == report_id, PeriodReport.user_id == user_id)
        .first()
    )


def list_reports(
    db: Session, user_id: uuid.UUID, *, limit: int = 50
) -> List[PeriodReport]:
    """The runner's past period reports, newest first — at most ONE per request
    identity (#946 review). A retry after a failure (or a stale-timeout
    reassignment, see `mark_ready`) legitimately leaves more than one physical
    row for the same identity; only the newest is ever the one worth showing,
    since a newer row for an identity is only ever created when the previous one
    was not usable. Older rows behind it are earlier attempts, not other
    reports, and showing both reads as two reviews of the same stretch when
    there was only ever one request.
    """
    rows = (
        db.query(PeriodReport)
        .filter(PeriodReport.user_id == user_id)
        .order_by(PeriodReport.created_at.desc(), PeriodReport.id.desc())
        .all()
    )
    seen_identities: set = set()
    deduped: List[PeriodReport] = []
    for row in rows:
        identity = (
            row.period_start,
            row.period_end,
            row.disciplines_key,
            row.prompt_id,
            row.schema_version,
        )
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        deduped.append(row)
        if len(deduped) >= limit:
            break
    return deduped
