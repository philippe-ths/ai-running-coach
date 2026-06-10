"""Exchange digest projection (A2a processed-artifacts layer).

The single, shared projection from a stored CoachReport's `report` JSON down to
its token-bounded longitudinal digest: when it was, its verdict label, its
strongest claim, and the next-steps (commitments) it recommended. Deliberately
excludes the full body (key_takeaways, thesis, risks, questions, evidence) so
the digest does not grow with history.

This is THE projection. Two callers share it so the stored digest is byte-equal
to the recomputed one:
  - the read-time M4 longitudinal builder (context._digest_from_report), and
  - the write-time persistence on CoachReport.digest (service.py).

Pure function: a report dict + the source activity's start_date in, a
PriorReportDigest out. No DB, no I/O.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.schemas.coach_context import PriorReportDigest


def build_report_digest(report: Dict[str, Any], activity_start_date) -> PriorReportDigest:
    """Project a stored report dict down to its longitudinal digest fields.

    `activity_start_date` is the SOURCE activity's start_date (a join, not a
    report field); a stored digest snapshots it. Strava activity start times are
    immutable, so the snapshot stays equal to the recomputed value.
    """
    lead = report.get("lead_argument")
    if isinstance(lead, dict):
        lead_text = lead.get("text")
    elif isinstance(lead, str):
        lead_text = lead
    else:
        lead_text = None

    next_steps: list[str] = []
    for step in (report.get("next_steps") or []):
        if not isinstance(step, dict):
            continue
        # Coerce to str defensively: the write path validates these as strings,
        # but a hand-edited or future-shape row must not raise here.
        action = str(step.get("action") or "").strip()
        details = str(step.get("details") or "").strip()
        if action and details:
            next_steps.append(f"{action} ({details})")
        elif action:
            next_steps.append(action)

    return PriorReportDigest(
        activity_date=activity_start_date.isoformat() if activity_start_date else "",
        headline=report.get("headline"),
        lead_argument=lead_text,
        next_steps=next_steps,
    )
