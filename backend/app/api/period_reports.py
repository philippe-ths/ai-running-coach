"""The period-report API (#946): the coach reviewing a runner-chosen stretch of
training, across the disciplines the runner chose.

The kill switch is a ROUTER-level dependency, the `SCHEDULE_ENABLED` precedent.
Every path parameter that names an owned resource resolves through its
`deps.get_owned_*` dependency, which `tests/test_route_ownership_802.py`
enforces structurally.

Async lifecycle, the `services/schedule` precedent: `POST` writes a `generating`
row and enqueues the worker job, returning 202 immediately — a stronger-model
pack over a runner-chosen stretch can run well past the request's gateway
timeout. `GET /{id}` is the status poll the client reads until the row leaves
`generating`.
"""

import logging
from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import CurrentUser, DbSession, OwnedPeriodReport
from app.core.config import settings
from app.schemas.period_report import (
    PeriodReportContent,
    PeriodReportCreate,
    PeriodReportListItem,
    PeriodReportRead,
)
from app.services.coach import period_report_store as store
from app.services.coach.period_report import PROMPT_ID, SCHEMA_VERSION
from app.jobs.generate_period_report import enqueue_period_report

logger = logging.getLogger(__name__)


def require_period_report_enabled() -> None:
    """#946: the period-report surface's kill switch, applied to the whole router."""
    if not settings.COACH_PERIOD_REPORT_ENABLED:
        raise HTTPException(
            status_code=503,
            detail=(
                "Period reports are temporarily unavailable. Your per-run reports "
                "still arrive after each session."
            ),
        )


router = APIRouter(
    prefix="/coach/period-reports",
    dependencies=[Depends(require_period_report_enabled)],
)

_FAILURE_MESSAGES = {
    store.FAILURE_UNREACHABLE: (
        "Your coach could not be reached, so no report was written. Nothing has "
        "changed — try again in a few minutes."
    ),
    store.FAILURE_OVER_BUDGET: (
        "You have used this period's coaching allowance, so no report was "
        "written. Ask again once the allowance resets."
    ),
    store.FAILURE_POLICY: (
        "Your coach could not write a report that stayed inside its coaching "
        "guardrails, so nothing was saved. Try a different period, or ask again."
    ),
    store.FAILURE_UNKNOWN: (
        "Your coach could not write this report. Nothing has changed — ask "
        "again, or try a different period."
    ),
}

_STATUS_MESSAGES = {
    store.GENERATING: "Your coach is reviewing this stretch. This can take a minute or two.",
    store.READY: "Your report is ready.",
}


def _failure_message(report) -> str:
    kind = (report.meta or {}).get("failure_kind") or store.FAILURE_UNKNOWN
    return _FAILURE_MESSAGES.get(kind, _FAILURE_MESSAGES[store.FAILURE_UNKNOWN])


def _read(report) -> PeriodReportRead:
    content = None
    status = report.status
    message = _STATUS_MESSAGES.get(status)
    if status == store.FAILED:
        message = _failure_message(report)
    if status == store.READY and report.report:
        try:
            content = PeriodReportContent.model_validate(report.report)
        except Exception:  # noqa: BLE001 — a malformed stored row degrades, not 500s
            logger.exception("period report %s: stored report is unreadable", report.id)
            # Display-safe (the `get_displayable_report_row` precedent): the
            # stored row stays `ready` untouched, but a runner reading this
            # response sees a clear failure rather than a "ready" report with
            # no content and no explanation.
            status = store.FAILED
            message = _FAILURE_MESSAGES[store.FAILURE_UNKNOWN]
    return PeriodReportRead(
        id=report.id,
        period_start=report.period_start,
        period_end=report.period_end,
        disciplines=report.disciplines or [],
        status=status,
        generated_at=report.generated_at,
        created_at=report.created_at,
        report=content,
        message=message,
    )


@router.post("", response_model=PeriodReportRead, status_code=202)
def create_period_report(
    body: PeriodReportCreate, db: DbSession, user: CurrentUser
) -> PeriodReportRead:
    """Ask the coach to review this stretch. Returns immediately; the client
    polls `GET /{id}`.

    Idempotent for an identical in-flight or already-generated request (the
    `POST /api/schedule/draft` precedent): a second identical POST returns the
    existing row rather than starting a second generation.
    """
    key = store.disciplines_key(body.disciplines)

    in_flight = store.report_in_flight(
        db,
        user.id,
        period_start=body.period_start,
        period_end=body.period_end,
        disciplines_key=key,
        prompt_id=PROMPT_ID,
        schema_version=SCHEMA_VERSION,
    )
    if in_flight is not None:
        return _read(in_flight)

    ready = store.find_ready(
        db,
        user.id,
        period_start=body.period_start,
        period_end=body.period_end,
        disciplines_key=key,
        prompt_id=PROMPT_ID,
        schema_version=SCHEMA_VERSION,
    )
    if ready is not None:
        return _read(ready)

    report = store.create_generating_report(
        db,
        user.id,
        period_start=body.period_start,
        period_end=body.period_end,
        disciplines=body.disciplines,
        prompt_id=PROMPT_ID,
        schema_version=SCHEMA_VERSION,
    )
    enqueue_period_report(user.id, report.id)
    return _read(report)


@router.get("", response_model=List[PeriodReportListItem])
def list_period_reports(db: DbSession, user: CurrentUser) -> List[PeriodReportListItem]:
    """The runner's past period reports, newest first."""
    reports = store.list_reports(db, user.id)
    out = []
    for report in reports:
        headline = None
        if report.status == store.READY and isinstance(report.report, dict):
            headline = report.report.get("headline") or (
                (report.report.get("message") or "").split("\n", 1)[0][:140] or None
            )
        out.append(
            PeriodReportListItem(
                id=report.id,
                period_start=report.period_start,
                period_end=report.period_end,
                disciplines=report.disciplines or [],
                status=report.status,
                generated_at=report.generated_at,
                created_at=report.created_at,
                headline=headline,
            )
        )
    return out


@router.get("/{report_id}", response_model=PeriodReportRead)
def read_period_report(report: OwnedPeriodReport) -> PeriodReportRead:
    """The status poll + the finished report."""
    return _read(report)
