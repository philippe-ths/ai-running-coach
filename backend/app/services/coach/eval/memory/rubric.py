"""The deterministic runner-memory rubric (#658).

The unit scored is the triple ``(sources, candidates, profile)``:

- ``sources`` (``MemorySources``): what the writer was handed this pass.
- ``candidates`` (``list[MemoryCandidate]``): the writer's RAW proposed lines,
  pre-graduation, each carrying its ``supporting_source_ids`` citations and the
  ``safety_relevant`` flag. This is where provenance lives.
- ``profile`` (``RunnerMemoryProfile``): what ``apply_graduation`` stored.

The stored profile keeps only line text (graduation discards provenance), so the
grounding / anti-echo sensors read the candidates, matched back to a profile line
by ``(section, text)``. This mirrors the coach-report eval scoring ``(content,
pack)``: ``candidates`` are the memory analogue of the LLM ``content``, ``sources``
the analogue of the ``pack``.

Each assertion returns an ``AssertionResult`` (PASS / FAIL / NOT_APPLICABLE),
reusing the coach-eval vocabulary so both harnesses speak one language.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from app.schemas.coach_memory import MEMORY_SECTION_FIELDS, RunnerMemoryProfile
from app.services.coach.eval.rubric import AssertionResult, AssertionStatus
from app.services.coach.memory_update import MemoryCandidate, MemorySources

# ADR 0025 §1-4 durable sections: a line here must rest on the runner's OWN words.
# `lately` is the probationary holding pen and may rest on any source (incl. a
# coach digest), so it is excluded from the grounding sensors.
_PLAIN_DURABLE_SECTIONS = ("who_you_are", "limits_and_constraints", "what_works_for_you")
_PLAN_SECTION = "goals_and_plans"

# Narrow, high-precision markers of an INFERRED BEHAVIOURAL VERDICT about the
# runner's TRAINING COMPLIANCE (ADR 0025 rule 1 — the rest-day-fixation incident's
# symptom). Memory holds STATED facts and soft character only; whether the runner
# follows advice or trains easy enough is re-derived live from data and is NEVER a
# memory line. Deliberately narrow (like the coach nag sensor), so a legitimately
# STATED preference or limit ("no morning runs", "gels make me sick", "prefers
# evening runs") never trips it — only an unambiguous compliance judgment does.
_VERDICT_MARKERS = (
    "ignores easy",
    "ignores rest",
    "ignores advice",
    "ignores the plan",
    "ignores coaching",
    "doesn't follow",
    "does not follow",
    "won't follow",
    "never follows",
    "doesn't run easy",
    "does not run easy",
    "won't run easy",
    "overcooks easy",
    "overcooks the easy",
    "runs easy days too hard",
    "runs easy too hard",
    "fixated on rest",
    "fixated on",
    "consistently ignores",
    "keeps ignoring",
    "keeps blowing past",
    "tends to overcook",
    "struggles to run easy",
    "can't run easy",
    "refuses to run easy",
)


def _candidate_index(
    candidates: List[MemoryCandidate],
) -> Dict[tuple, MemoryCandidate]:
    """Map (section, text) -> candidate, so a profile line can be traced back to
    the candidate (and thus the source ids) that produced it. Graduation preserves
    candidate text verbatim, so the match is exact."""
    return {(c.section, c.text.strip()): c for c in candidates}


# --------------------------------------------------------------------------- #
# Assertions
# --------------------------------------------------------------------------- #
def assert_no_inferred_verdict(
    sources: MemorySources,
    candidates: List[MemoryCandidate],
    profile: RunnerMemoryProfile,
) -> AssertionResult:
    """ADR 0025 rule 1: no section holds an inferred behavioural verdict about the
    runner's training compliance (the rest-day-fixation floor). FAIL on any
    `_VERDICT_MARKERS` hit; PASS otherwise. Always applicable — this is the floor.
    Reads the profile text only, so it also scores a stored profile with no
    sources/candidates on hand."""
    hits: List[Dict[str, Any]] = []
    for section in MEMORY_SECTION_FIELDS:
        for line in getattr(profile, section):
            low = line.lower()
            for marker in _VERDICT_MARKERS:
                if marker in low:
                    hits.append({"section": section, "line": line, "marker": marker})
    if hits:
        return AssertionResult(
            "no_inferred_verdict",
            AssertionStatus.FAIL,
            "A memory line renders an inferred behavioural verdict about the runner's "
            "training compliance — the retired belief loop's symptom. Memory holds "
            "stated facts and soft character only; compliance is re-derived live.",
            {"hits": hits},
        )
    return AssertionResult(
        "no_inferred_verdict",
        AssertionStatus.PASS,
        "No section renders an inferred behavioural verdict about the runner.",
    )


def assert_durable_lines_grounded(
    sources: MemorySources,
    candidates: List[MemoryCandidate],
    profile: RunnerMemoryProfile,
) -> AssertionResult:
    """ADR 0025 rule 6 (anti-echo): every line in a plain durable section
    (who_you_are / limits_and_constraints / what_works_for_you) must rest on at
    least one of the runner's OWN (durable) sources. A line grounded only on the
    coach's words, or on no matching candidate at all, FAILS — that is the coach's
    opinion echoed as the runner's stated fact. NOT_APPLICABLE when there are no
    such lines."""
    durable_ids = sources.durable_source_ids
    index = _candidate_index(candidates)
    lines = [
        (section, line)
        for section in _PLAIN_DURABLE_SECTIONS
        for line in getattr(profile, section)
    ]
    if not lines:
        return AssertionResult(
            "durable_lines_grounded",
            AssertionStatus.NOT_APPLICABLE,
            "No durable character/limit/preference lines to check.",
        )
    bad: List[Dict[str, Any]] = []
    for section, line in lines:
        candidate = index.get((section, line.strip()))
        if candidate is None:
            bad.append({"section": section, "line": line, "reason": "no matching candidate (fabricated)"})
            continue
        if not (set(candidate.supporting_source_ids) & durable_ids):
            bad.append({"section": section, "line": line, "reason": "grounded only on non-durable (coach) sources"})
    if bad:
        return AssertionResult(
            "durable_lines_grounded",
            AssertionStatus.FAIL,
            "A durable memory line does not rest on the runner's own words (it is "
            "ungrounded or echoes a coach turn as the runner's stated fact).",
            {"violations": bad},
        )
    return AssertionResult(
        "durable_lines_grounded",
        AssertionStatus.PASS,
        "Every durable character/limit/preference line rests on the runner's own words.",
    )


def assert_plan_from_commitment(
    sources: MemorySources,
    candidates: List[MemoryCandidate],
    profile: RunnerMemoryProfile,
) -> AssertionResult:
    """The #657 regression sensor: every `goals_and_plans` line must trace to a
    runner COMMITMENT — a candidate grounded on at least one durable (runner)
    source. A plan the coach only PROPOSED (grounded on coach turns alone), or a
    plan the runner merely weighed, must not graduate. NOT_APPLICABLE when there
    are no plan lines."""
    durable_ids = sources.durable_source_ids
    index = _candidate_index(candidates)
    plans = profile.goals_and_plans
    if not plans:
        return AssertionResult(
            "plan_from_commitment",
            AssertionStatus.NOT_APPLICABLE,
            "No goals_and_plans lines to check.",
        )
    bad: List[Dict[str, Any]] = []
    for line in plans:
        candidate = index.get((_PLAN_SECTION, line.strip()))
        if candidate is None:
            bad.append({"line": line, "reason": "no matching candidate (fabricated)"})
            continue
        if not (set(candidate.supporting_source_ids) & durable_ids):
            bad.append({"line": line, "reason": "no runner commitment — coach-proposed / weighed only"})
    if bad:
        return AssertionResult(
            "plan_from_commitment",
            AssertionStatus.FAIL,
            "A goals_and_plans line has no runner commitment behind it (a coach "
            "proposal or an option the runner only weighed graduated as a plan).",
            {"violations": bad},
        )
    return AssertionResult(
        "plan_from_commitment",
        AssertionStatus.PASS,
        "Every plan rests on a runner commitment.",
    )


def assert_safety_limit_held(
    sources: MemorySources,
    candidates: List[MemoryCandidate],
    profile: RunnerMemoryProfile,
) -> AssertionResult:
    """ADR 0025 rule 4: a `safety_relevant` limit grounded on the runner's own
    words is HELD in limits_and_constraints on a single mention (never silently
    dropped). FAIL when such a grounded safety candidate is missing from the stored
    limits. NOT_APPLICABLE when there is no grounded safety candidate to hold."""
    durable_ids = sources.durable_source_ids
    grounded_safety = [
        c
        for c in candidates
        if c.safety_relevant and (set(c.supporting_source_ids) & durable_ids)
    ]
    if not grounded_safety:
        return AssertionResult(
            "safety_limit_held",
            AssertionStatus.NOT_APPLICABLE,
            "No grounded safety-relevant limit to hold.",
        )
    stored = {line.strip() for line in profile.limits_and_constraints}
    dropped = [c.text for c in grounded_safety if c.text.strip() not in stored]
    if dropped:
        return AssertionResult(
            "safety_limit_held",
            AssertionStatus.FAIL,
            "A safety-relevant limit grounded on the runner's own words was dropped "
            "from limits_and_constraints instead of being held on first mention.",
            {"dropped": dropped},
        )
    return AssertionResult(
        "safety_limit_held",
        AssertionStatus.PASS,
        "Every grounded safety-relevant limit is held in limits_and_constraints.",
    )


# The full triple-scored rubric.
MEMORY_ASSERTIONS: List[
    Callable[[MemorySources, List[MemoryCandidate], RunnerMemoryProfile], AssertionResult]
] = [
    assert_no_inferred_verdict,
    assert_durable_lines_grounded,
    assert_plan_from_commitment,
    assert_safety_limit_held,
]

# The subset scoreable from a stored profile alone (no candidates/sources): the
# verdict floor reads profile text only, so a real-data scan can run it.
PROFILE_ONLY_ASSERTIONS: List[
    Callable[[MemorySources, List[MemoryCandidate], RunnerMemoryProfile], AssertionResult]
] = [assert_no_inferred_verdict]


@dataclass
class MemoryScore:
    assertions: List[AssertionResult]
    label: Optional[str] = None

    @property
    def applicable_count(self) -> int:
        return sum(1 for a in self.assertions if a.status is not AssertionStatus.NOT_APPLICABLE)

    @property
    def passed_count(self) -> int:
        return sum(1 for a in self.assertions if a.status is AssertionStatus.PASS)

    @property
    def failed_count(self) -> int:
        return sum(1 for a in self.assertions if a.status is AssertionStatus.FAIL)

    @property
    def pass_rate(self) -> float:
        """Passed over applicable. Vacuously 1.0 when nothing applies."""
        applicable = self.applicable_count
        if applicable == 0:
            return 1.0
        return self.passed_count / applicable

    def to_dict(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "applicable_count": self.applicable_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "pass_rate": self.pass_rate,
            "assertions": [a.to_dict() for a in self.assertions],
        }


def score_memory(
    sources: MemorySources,
    candidates: List[MemoryCandidate],
    profile: RunnerMemoryProfile,
    *,
    label: Optional[str] = None,
) -> MemoryScore:
    """Score one memory WRITE (the full triple) against the deterministic rubric."""
    results = [assertion(sources, candidates, profile) for assertion in MEMORY_ASSERTIONS]
    return MemoryScore(assertions=results, label=label)


def score_stored_profile(
    profile: RunnerMemoryProfile, *, label: Optional[str] = None
) -> MemoryScore:
    """Score a STORED profile with the profile-only subset (no candidates/sources
    on hand). Only the ADR 0025 rule-1 verdict floor applies here."""
    empty_sources = MemorySources()
    results = [assertion(empty_sources, [], profile) for assertion in PROFILE_ONLY_ASSERTIONS]
    return MemoryScore(assertions=results, label=label)
