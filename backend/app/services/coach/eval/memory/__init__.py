"""Offline runner-memory eval harness (#658) — the durable-memory counterpart to
the M5 coach-report eval.

It scores a runner-memory WRITE against deterministic ADR 0025 assertions, so a
change to the memory writer (sources, prompt, or graduation) has a repeatable
quality gate instead of a one-off human read — the same way ``make eval`` guards
the coach reports.

Deterministic in v1, on purpose: an LLM judge would reintroduce the drift the
gate exists to catch. The semantic assertions #658 lists (a genuine elliptical
commitment IS captured; a reworded verdict; newer-supersedes-older) need a judge
and are a documented opt-in follow-up, not part of this deterministic core.
"""

from app.services.coach.eval.memory.rubric import (
    MEMORY_ASSERTIONS,
    MemoryScore,
    score_memory,
    score_stored_profile,
)

__all__ = [
    "MEMORY_ASSERTIONS",
    "MemoryScore",
    "score_memory",
    "score_stored_profile",
]
