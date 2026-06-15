"""Retrieval seam (A2b) — pull A2a's stored processed artifacts on demand.

The coach's working context is lean by default; this module is the seam that
fetches deeper, pre-digested detail only when a turn needs it (CONTEXT.md,
`Working context`: "small by default, deep on demand"). It reads the A2a
processed-artifacts layer — the consolidated stream view on `DerivedMetric`, the
exchange digests on `CoachReport`, and the stored exchange record itself
(`CoachReport.report`, for the M7 commitments that need their structured
next_steps) — never the raw store. Raw streams never enter context; only their
downsampled view does.

Every read degrades cleanly to None / an empty list, so a missing artifact never
breaks an exchange. Nothing here re-derives or overrides a `DerivedMetric`: the
seam only retrieves what ingestion already stored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session, undefer

from app.models import Activity
from app.models.coach_chat_message import CoachChatMessage
from app.models.coach_report import CoachReport
from app.models.derived_metric import DerivedMetric
from app.schemas.coach_context import PriorReportDigest
from app.services.coach.corpus import HOUSE_CORE, SCHOOLS, Corpus
from app.services.coach.digest import build_report_digest
from app.services.coach.output_contract import is_opener_only

# The working context carries the last 1-2 exchanges (design doc § 4 — "the last
# exchange's digest"). Two advances the prior narrative while keeping the pack
# from growing with history (the M4 token bound this seam inherits).
_MAX_PRIOR_DIGESTS = 2

# Defensive cap on rows scanned to find them. Reports are version-keyed (a few
# rows per activity at most), so this comfortably covers the two most recent
# distinct prior activities while keeping the read bounded as history grows.
_PRIOR_DIGEST_SCAN_LIMIT = 50


def _as_uuid(value) -> uuid.UUID:
    """Accept a UUID or its string form. SQLite (tests) will not implicitly cast
    a string to a UUID column the way Postgres does, so coerce up front."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def fetch_corpus(school_id: Optional[str]) -> Corpus:
    """Pull the coaching corpus (P1.2, ADR 0014): the always-present house core
    plus the keyed school of training thought.

    A pure keyed LOOKUP into the code-resident `corpus.py` — no DB read, since the
    corpus is house-authored and lives as code. This seam is the only place the
    rest of the system reads the corpus. An unknown or null `school_id` degrades to
    house-core-only (`school=None`), exactly as `narrative` degrades to null, so a
    missing/unresolved school never breaks an exchange.
    """
    school = SCHOOLS.get(school_id) if school_id else None
    return Corpus(house_core=HOUSE_CORE, school=school)


def fetch_stream_view(db: Session, activity_id) -> Optional[dict]:
    """Pull the consolidated stream view (A2a) for one activity, on demand.

    Loads the DEFERRED `DerivedMetric.stream_view` column in a single query via
    `undefer`, so the default context build — which never calls this seam — stays
    lean (the column is not loaded by the common `activity.metrics` path). Returns
    None when the activity has no metrics row or no stored view.
    """
    row = (
        db.query(DerivedMetric)
        .filter(DerivedMetric.activity_id == _as_uuid(activity_id))
        .options(undefer(DerivedMetric.stream_view))
        .first()
    )
    return row.stream_view if row is not None else None


def fetch_prior_digests(
    db: Session, activity: Activity, *, limit: int = _MAX_PRIOR_DIGESTS
) -> List[PriorReportDigest]:
    """Pull the digests of the runner's most recent non-fallback reports before
    this activity — the relationship's last-exchange memory.

    Reads the A2a stored `CoachReport.digest` artifact when present (the point of
    storing it: retrieve, don't re-project), falling back to re-projecting the
    full report body via `build_report_digest` for rows written before A2a. Both
    paths are byte-equal by construction (`digest.py` is the single shared
    projection and Strava start times are immutable), so the working context is
    unchanged whether or not a row predates A2a. Most recent first, one digest per
    prior activity (the latest report when an activity has several versioned
    rows), capped at `limit`. Empty for a runner's first activity.
    """
    rows = (
        db.query(CoachReport, Activity.start_date)
        .join(Activity, CoachReport.activity_id == Activity.id)
        .filter(
            Activity.user_id == activity.user_id,
            Activity.id != activity.id,
            Activity.is_deleted == False,  # noqa: E712
            Activity.start_date < activity.start_date,
            CoachReport.is_fallback == False,  # noqa: E712
        )
        # start_date orders by exchange recency; created_at picks the latest
        # version per activity; id is a stable final tiebreaker.
        .order_by(
            Activity.start_date.desc(),
            CoachReport.created_at.desc(),
            CoachReport.id.desc(),
        )
        .limit(_PRIOR_DIGEST_SCAN_LIMIT)
        .all()
    )

    digests: List[PriorReportDigest] = []
    seen: set = set()
    for report, start_date in rows:
        if report.activity_id in seen:
            continue  # keep only the latest report per prior activity
        if is_opener_only(report.report):
            # A4: an opener-only row is an incomplete exchange (the fuller turn
            # never landed). It carries no digest and an empty message, so it must
            # not feed the longitudinal memory — skip it. `seen` is NOT marked, so
            # an earlier COMPLETE exchange on the same activity is still considered.
            continue
        seen.add(report.activity_id)
        digests.append(_resolve_digest(report, start_date))
        if len(digests) >= limit:
            break
    return digests


@dataclass(frozen=True)
class PriorCommitments:
    """The most recent prior exchange's machine-readable commitments — the M7
    adherence read, pulled through the seam from the stored exchange record
    (CoachReport.report).

    The structured `next_steps` stay dicts (the A2d hard constraint: commitments
    stay machine-readable for the learning loop, since the M7 theme classifier
    reads their `action`/`details` fields). The source activity ref lets the
    caller scope the comparable-run window and the pushback scan. Carries ONLY the
    commitments + source ref, not the full report body, so the loop no longer
    holds the whole exchange in hand just to read its next_steps.
    """

    source_activity_id: uuid.UUID
    source_start_date: datetime
    next_steps: List[Dict[str, Any]]


def fetch_prior_commitments(db: Session, activity: Activity) -> Optional[PriorCommitments]:
    """Pull the single most recent prior non-fallback exchange's commitments —
    the input the M7 adherence loop judges the runner's later runs against.

    Reads the structured `next_steps` from the stored exchange record
    (`CoachReport.report`), the machine-readable commitments the brief requires the
    loop to keep. Selection matches `fetch_prior_digests`'s single-most-recent
    case exactly (exclude self, exclude soft-deleted, exclude fallback, strictly
    before this run, newest exchange then latest version). Returns None when the
    runner has no prior non-fallback report — the adherence section then degrades
    to empty.
    """
    rows = (
        db.query(CoachReport, Activity.id, Activity.start_date)
        .join(Activity, CoachReport.activity_id == Activity.id)
        .filter(
            Activity.user_id == activity.user_id,
            Activity.id != activity.id,
            Activity.is_deleted == False,  # noqa: E712
            Activity.start_date < activity.start_date,
            CoachReport.is_fallback == False,  # noqa: E712
        )
        .order_by(
            Activity.start_date.desc(),
            CoachReport.created_at.desc(),
            CoachReport.id.desc(),
        )
        .limit(_PRIOR_DIGEST_SCAN_LIMIT)
        .all()
    )
    for report, source_id, source_start_date in rows:
        # A4: skip opener-only rows — an incomplete exchange carries no commitments
        # and must not shadow an earlier COMPLETE exchange's next_steps.
        if is_opener_only(report.report):
            continue
        body = report.report or {}
        next_steps = [s for s in (body.get("next_steps") or []) if isinstance(s, dict)]
        return PriorCommitments(
            source_activity_id=source_id,
            source_start_date=source_start_date,
            next_steps=next_steps,
        )
    return None


def fetch_recent_user_digests(
    db: Session, user_id, *, limit: int = 5
) -> List[PriorReportDigest]:
    """Pull the digests of a runner's most recent non-fallback exchanges,
    user-scoped (not anchored to one activity) — the recent-history input the A2c
    Consolidation job re-grounds the narrative from.

    Like `fetch_prior_digests` but keyed on `user_id` and inclusive of the latest
    exchange, so consolidation triggered by an exchange sees that exchange. Most
    recent first, one digest per activity, capped at `limit`. Empty for a runner
    with no non-fallback reports yet (the job then has nothing to consolidate).
    """
    rows = (
        db.query(CoachReport, Activity.start_date)
        .join(Activity, CoachReport.activity_id == Activity.id)
        .filter(
            Activity.user_id == _as_uuid(user_id),
            Activity.is_deleted == False,  # noqa: E712
            CoachReport.is_fallback == False,  # noqa: E712
        )
        .order_by(
            Activity.start_date.desc(),
            CoachReport.created_at.desc(),
            CoachReport.id.desc(),
        )
        .limit(_PRIOR_DIGEST_SCAN_LIMIT)
        .all()
    )

    digests: List[PriorReportDigest] = []
    seen: set = set()
    for report, start_date in rows:
        if report.activity_id in seen:
            continue  # keep only the latest report per activity
        if is_opener_only(report.report):
            continue  # A4: incomplete exchange, not a consolidation input
        seen.add(report.activity_id)
        digests.append(_resolve_digest(report, start_date))
        if len(digests) >= limit:
            break
    return digests


def fetch_latest_user_reply(db: Session, activity_id) -> Optional[str]:
    """The text of the runner's most recent chat reply on this activity, or None.

    A4 fuller-turn continuity: a `CoachChatMessage` (role="user") the runner sent
    after the opener is the reply the fuller turn folds in. The check-in, if any,
    already rides the pack's `check_in`; this carries the chat half. Returns the
    latest user message text, or None when the runner sent no chat reply.
    """
    row = (
        db.query(CoachChatMessage.content)
        .filter(
            CoachChatMessage.activity_id == _as_uuid(activity_id),
            CoachChatMessage.role == "user",
        )
        .order_by(CoachChatMessage.created_at.desc(), CoachChatMessage.id.desc())
        .first()
    )
    return row[0] if row and row[0] else None


def _resolve_digest(report: CoachReport, start_date) -> PriorReportDigest:
    """Prefer the stored A2a digest; fall back to re-projecting the report body.

    Byte-equal between the two paths by construction. The stored artifact is
    validated back into the typed shape; a malformed legacy row falls through to
    re-projection rather than raising.
    """
    stored = report.digest
    if stored:
        try:
            return PriorReportDigest.model_validate(stored)
        except Exception:  # noqa: BLE001 — a malformed stored digest re-projects
            pass
    return build_report_digest(report.report or {}, start_date)
