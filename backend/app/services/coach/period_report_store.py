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
It is held here instead: `report_in_flight` and `find_ready` are the only reads
that check it, and every write path goes through this module.
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
) -> PeriodReport:
    report.status = READY
    report.report = content
    report.context_pack = context_pack
    report.model_id = model_id
    report.meta = meta or {}
    report.generated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(report)
    return report


def mark_failed(
    db: Session, report: PeriodReport, reason: str, *, kind: str = FAILURE_UNKNOWN
) -> PeriodReport:
    report.status = FAILED
    report.meta = {
        **(report.meta or {}),
        "failure_reason": reason,
        "failure_kind": kind if kind in FAILURE_KINDS else FAILURE_UNKNOWN,
    }
    db.commit()
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
    return (
        db.query(PeriodReport)
        .filter(PeriodReport.user_id == user_id)
        .order_by(PeriodReport.created_at.desc(), PeriodReport.id.desc())
        .limit(limit)
        .all()
    )
