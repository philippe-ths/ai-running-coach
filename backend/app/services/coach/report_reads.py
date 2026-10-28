"""
Coach report reads: which stored report row is active or displayable for an
activity, and its read shape. Split from `service.py` (#1159); `service` re-exports
every name here.
"""

import logging
import uuid
from typing import Optional

from sqlalchemy.orm import Session, load_only

from app.core.config import settings
from app.models import Activity, Block
from app.models.coach_report import CoachReport
from app.schemas.coach import (
    CoachMessageReport,
    CoachReportContent,
    CoachReportDebug,
    CoachReportMeta,
    CoachReportRead,
)
from app.services.coach.digest import build_report_digest
from app.services.coach.output_contract import is_opener_only
from app.services.coach.prompts import PROMPT_VERSIONS
from app.services.coach.turn import TurnKind

logger = logging.getLogger(__name__)

# Schema version is keyed by prompt FAMILY (ADR 0009): the legacy structured
# CoachReportContent output is schema 1.2; the A3 prose-message CoachMessageReport
# output is schema 2.0. The active version is resolved from the active prompt id's
# prefix, so a config flip of COACH_PROMPT_ID flips both the prompt and the cache
# identity together. `SCHEMA_VERSION` is retained as the legacy-family default for
# callers that import it.
SCHEMA_VERSION = "1.2"
SCHEMA_VERSION_BY_FAMILY = {
    "coach_report": "1.2",
    "coach_message": "2.0",
}


def active_schema_version(prompt_id: str) -> str:
    """The schema version for a prompt id, resolved by family prefix.

    Unknown prefixes fall back to the legacy structured family (1.2) so an
    unrecognised prompt id can never silently mint a new schema.
    """
    for family, version in SCHEMA_VERSION_BY_FAMILY.items():
        if prompt_id.startswith(family):
            return version
    return SCHEMA_VERSION


def get_active_report_row(db: Session, activity_id) -> Optional[CoachReport]:
    """Return the cached report row for the *active* version of this activity.

    The active version is the current (COACH_PROMPT_ID, SCHEMA_VERSION) pair.
    Rows from older prompt/schema versions are retained but ignored here, so a
    version bump causes a clean cache miss and regeneration rather than serving
    a stale shape.
    """
    activity_uuid = _coerce_uuid(activity_id)
    prompt_id = settings.COACH_PROMPT_ID
    return (
        db.query(CoachReport)
        .filter(
            CoachReport.activity_id == activity_uuid,
            CoachReport.prompt_id == prompt_id,
            CoachReport.schema_version == active_schema_version(prompt_id),
            # #646: only the CURRENT row is the active report; superseded audit copies
            # of the same key are never served.
            CoachReport.superseded_at.is_(None),
        )
        .first()
    )


def get_displayable_report_row(db: Session, activity_id) -> Optional[CoachReport]:
    """A report row suitable for DISPLAY: the active-version row if present, else
    the most recently created cached report of ANY (prompt_id, schema_version).

    This is the read/display fallback (#261) that keeps a COACH_PROMPT_ID flip from
    turning every historical activity into a cache-miss-that-regenerates: after a
    flip (e.g. activating P1.1 voice coach_message_v3), prior-version reports still
    render instead of 404-ing or triggering a synchronous regeneration that exceeds
    the gateway timeout (#260). It is a read concern ONLY — it never affects the M0
    versioned-cache identity used for regeneration (get_active_report_row and
    get_or_generate_coach_report are unchanged), so the background pipeline still
    generates the active version for new activities, and an explicit force=True
    regeneration still targets the active version.
    """
    active = get_active_report_row(db, activity_id)
    if active is not None:
        return active
    activity_uuid = _coerce_uuid(activity_id)
    return (
        db.query(CoachReport)
        .filter(
            CoachReport.activity_id == activity_uuid,
            # #646: never fall back to a superseded audit copy; only current rows
            # (of any prompt/schema version) are displayable.
            CoachReport.superseded_at.is_(None),
        )
        .order_by(CoachReport.created_at.desc())
        .first()
    )


def _lead_sentence(row: CoachReport, activity: Activity) -> Optional[str]:
    """This report's opening claim, as one line.

    Prefers the STORED digest (written for every non-fallback report since A2a)
    and re-projects through the shared `build_report_digest` only for older rows,
    mirroring `retrieval._resolve_digest` — so the line shown in a list is the same
    text the longitudinal read treats as this report's lead, never a second
    projection that could drift from it.
    """
    stored = row.digest if isinstance(row.digest, dict) else None
    if stored:
        lead = stored.get("lead_argument")
        if isinstance(lead, str) and lead.strip():
            return lead.strip()
    report = row.report if isinstance(row.report, dict) else None
    if not report:
        return None
    try:
        return (build_report_digest(report, activity.start_date).lead_argument or "").strip() or None
    except Exception:  # a malformed stored row must never break the activity list
        logger.exception("Could not project a lead sentence for report %s", row.id)
        return None


def get_displayable_report_leads(db: Session, activities) -> dict:
    """`{activity_id: lead sentence}` for a page of activities (#797).

    Telegram is the only channel that ever ANNOUNCES a report, so a runner who
    declines to link one had no way to discover their runs were coached at all —
    the reports were written, stored and visible, and nothing said so. This is the
    activity list's way of saying so, without inventing read/unread state.

    Batched deliberately: one query for the whole page. The obvious
    `get_displayable_report_row` per row would be N+1 on the home page's default
    20 activities, on a path a runner hits on every app open.

    Selection mirrors `get_displayable_report_row` — the active
    (prompt_id, schema_version) row when one exists, else the most recent current
    row of any version — so the list and the detail page cannot show leads from
    two different reports. Fallback reports are excluded: `is_fallback` marks a
    generation that FAILED, and its apology text is not coaching to advertise.

    Scoped to each activity's OWN report, deliberately NOT the #482 block-primary
    fallback: a session's report is generated once per block, so borrowing it for
    every member would print the same sentence against a walk and the run beside
    it. In a multi-activity block only the primary carries the line.
    """
    by_id = {a.id: a for a in activities}
    if not by_id:
        return {}

    prompt_id = settings.COACH_PROMPT_ID
    active_schema = active_schema_version(prompt_id)

    def _is_active(row: CoachReport) -> bool:
        return row.prompt_id == prompt_id and row.schema_version == active_schema

    rows = (
        db.query(CoachReport)
        # #1137: only what selection and the lead need, never the heavy
        # `context_pack` / `raw_llm_response` / `meta`. `report` stays: a row
        # written before the stored digest (A2a) re-projects its lead from it,
        # and loading it here keeps this one query rather than adding a second.
        .options(load_only(
            CoachReport.id,
            CoachReport.activity_id,
            CoachReport.prompt_id,
            CoachReport.schema_version,
            CoachReport.digest,
            CoachReport.report,
            CoachReport.created_at,
        ))
        .filter(
            CoachReport.activity_id.in_(list(by_id)),
            # #646: current rows only, never a superseded audit copy.
            CoachReport.superseded_at.is_(None),
            CoachReport.is_fallback.is_(False),
        )
        .order_by(CoachReport.created_at.desc())
        .all()
    )

    chosen: dict = {}
    for row in rows:  # newest first, so the first of any class is the newest of it
        held = chosen.get(row.activity_id)
        if held is None or (_is_active(row) and not _is_active(held)):
            chosen[row.activity_id] = row

    leads = {}
    for activity_id, row in chosen.items():
        lead = _lead_sentence(row, by_id[activity_id])
        if lead:
            leads[activity_id] = lead
    return leads


def get_block_primary_report_row(db: Session, activity_id) -> Optional[CoachReport]:
    """Block-aware DISPLAY fallback (#482): the displayable report of this
    activity's block PRIMARY, when the activity itself owns none.

    The session report is generated once per block, keyed to the primary activity
    (the run, else the longest member). A non-primary member therefore has zero
    coach_reports rows, so its page 404s and the panel spins forever. This lets the
    read path show the session's report on any member instead.

    Read-only, no generation: the M0 versioned-cache identity used for regeneration
    (get_active_report_row / get_or_generate_coach_report) is unchanged, so the
    per-activity "Re-run" still mints a member-specific report. Returns None when the
    activity is not in a block, IS its block's primary (it would have its own report),
    or the primary has no displayable report yet.
    """
    activity_uuid = _coerce_uuid(activity_id)
    activity = (
        db.query(Activity).filter(Activity.id == activity_uuid).first()
    )
    if activity is None or activity.block_id is None:
        return None
    block = db.query(Block).filter(Block.id == activity.block_id).first()
    if block is None or block.primary_activity_id == activity_uuid:
        return None
    return get_displayable_report_row(db, block.primary_activity_id)


def cached_row_is_servable(
    existing: Optional[CoachReport], *, kind: TurnKind, force: bool
) -> bool:
    """Whether a cached row answers this request without a fresh LLM call.

    One predicate with the per-kind rule stated, rather than three cache checks
    that read as if they disagree (they did, and each disagreement was load
    bearing):

    - REPORT (single-shot): any active-version row is the whole report.
    - FULLER: an OPENER-ONLY row is half a report, so it never serves; the fuller
      turn's job is to fill it in place.
    - OPENER: any active-version row serves, complete or not, because the opener
      is idempotent re-entry — a later fuller must not be re-opened. The caller
      still recomputes the schedule decision from the stored row.

    `force` is a runner-initiated "Re-run" and overrides the first two. It does
    NOT reach the opener, which has no force path: re-opening a delivered
    exchange is not a thing a runner can ask for.
    """
    if existing is None:
        return False
    if kind is TurnKind.OPENER:
        return True
    if force:
        return False
    if kind is TurnKind.FULLER:
        return not is_opener_only(existing.report)
    return True


def _coerce_uuid(value) -> uuid.UUID:
    """Accept either a UUID or its string form. Postgres tolerates the latter
    via implicit cast; SQLite (used in tests) does not, so coerce up front."""
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


def _to_read(db_report: CoachReport) -> CoachReportRead:
    """Convert a DB CoachReport row into the read schema.

    The stored report is validated against the model for its schema-version family
    (ADR 0009): the A3 prose CoachMessageReport for 2.x rows, the legacy structured
    CoachReportContent otherwise. Keying off the stored row's own schema_version
    (not the active config) means old rows always read back in their own shape, so
    a config flip never mis-parses history (AC7).
    """
    meta = CoachReportMeta.model_validate(db_report.meta)
    if str(db_report.schema_version or "").startswith("2"):
        report = CoachMessageReport.model_validate(db_report.report)
    else:
        report = CoachReportContent.model_validate(db_report.report)
    return CoachReportRead(
        id=db_report.id,
        activity_id=db_report.activity_id,
        report=report,
        meta=meta,
        debug=CoachReportDebug(
            context_pack=db_report.context_pack or {},
            system_prompt=PROMPT_VERSIONS.get(meta.prompt_id, "unknown"),
            raw_llm_response=db_report.raw_llm_response,
        ),
        created_at=db_report.created_at,
    )
