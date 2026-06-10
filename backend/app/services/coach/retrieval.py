"""Retrieval seam (A2b) — pull A2a's stored processed artifacts on demand.

The coach's working context is lean by default; this module is the seam that
fetches deeper, pre-digested detail only when a turn needs it (CONTEXT.md,
`Working context`: "small by default, deep on demand"). It reads the A2a
processed-artifacts layer — the consolidated stream view on `DerivedMetric` and
the exchange digests on `CoachReport` — never the raw store. Raw streams never
enter context; only their downsampled view does.

Every read degrades cleanly to None / an empty list, so a missing artifact never
breaks an exchange. Nothing here re-derives or overrides a `DerivedMetric`: the
seam only retrieves what ingestion already stored.
"""

from __future__ import annotations

import uuid
from typing import List, Optional

from sqlalchemy.orm import Session, undefer

from app.models import Activity
from app.models.coach_report import CoachReport
from app.models.derived_metric import DerivedMetric
from app.schemas.coach_context import PriorReportDigest
from app.services.coach.digest import build_report_digest

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
        seen.add(report.activity_id)
        digests.append(_resolve_digest(report, start_date))
        if len(digests) >= limit:
            break
    return digests


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
        seen.add(report.activity_id)
        digests.append(_resolve_digest(report, start_date))
        if len(digests) >= limit:
            break
    return digests


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
