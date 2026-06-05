"""Offline coach-report evaluation harness (M5) — THE GATE.

This package is the precondition for the long-horizon learning milestones
(M7-M10). It scores coach reports against a deterministic, human-authored rubric
so "better than a cold AI" and "gets better over time" become measurable and
preference-drift becomes visible.

Deliberately deterministic in v1: an LLM-judge would reintroduce the very drift
the gate exists to detect. Each report is scored independently from its own
content plus its stored ``context_pack`` (no cross-row joins, no re-read of
mutable analysis state), so the score is order-independent and byte-stable.
"""

from app.services.coach.eval.rubric import (
    AssertionResult,
    AssertionStatus,
    ReportScore,
    score_report,
)

__all__ = [
    "AssertionResult",
    "AssertionStatus",
    "ReportScore",
    "score_report",
]
