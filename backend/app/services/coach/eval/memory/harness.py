"""Aggregation + self-test + stored-profile scan for the runner-memory eval (#658).

Mirrors the coach-report eval's `harness.py`: a `MemoryScorecard` that
micro-averages assertion pass rates, a `run_self_test()` that validates the
rubric against the synthetic good/bad fixtures with NO DB and NO API key, and a
`scan_stored_profiles()` that runs the profile-only verdict floor over the stored
`runner_memory` rows (needs a DB, no key).
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.services.coach.eval.memory.fixtures import (
    deliberately_bad_memory,
    known_good_memory,
)
from app.services.coach.eval.memory.rubric import (
    MEMORY_ASSERTIONS,
    MemoryScore,
    score_memory,
    score_stored_profile,
)
from app.services.coach.eval.rubric import AssertionStatus

logger = logging.getLogger(__name__)


@dataclass
class MemoryScorecard:
    scores: List[MemoryScore] = field(default_factory=list)
    errors: int = 0

    def assertion_summary(self) -> Dict[str, Dict[str, Any]]:
        """Per-assertion {applicable, passed, failed, pass_rate}, micro-averaged,
        skipping NOT_APPLICABLE — the coach-eval shape."""
        summary: Dict[str, Dict[str, Any]] = {}
        for score in self.scores:
            for a in score.assertions:
                bucket = summary.setdefault(
                    a.name, {"applicable": 0, "passed": 0, "failed": 0}
                )
                if a.status is AssertionStatus.NOT_APPLICABLE:
                    continue
                bucket["applicable"] += 1
                if a.status is AssertionStatus.PASS:
                    bucket["passed"] += 1
                elif a.status is AssertionStatus.FAIL:
                    bucket["failed"] += 1
        for bucket in summary.values():
            applicable = bucket["applicable"]
            bucket["pass_rate"] = (bucket["passed"] / applicable) if applicable else 1.0
        return summary

    @property
    def overall_pass_rate(self) -> float:
        applicable = sum(s.applicable_count for s in self.scores)
        passed = sum(s.passed_count for s in self.scores)
        return (passed / applicable) if applicable else 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "profiles_scored": len(self.scores),
            "errors": self.errors,
            "overall_pass_rate": self.overall_pass_rate,
            "assertions": self.assertion_summary(),
            "scores": [s.to_dict() for s in self.scores],
        }


def _all_assertion_names() -> set:
    """Every assertion name in the full rubric (derived by running it once on the
    good fixture, so the set never drifts from `MEMORY_ASSERTIONS`)."""
    sources, candidates, profile = known_good_memory()
    return {a.name for a in score_memory(sources, candidates, profile).assertions}


def run_self_test() -> bool:
    """Validate the rubric against its synthetic fixtures. No DB, no API key.

    The good fixture must fail nothing (and apply at least one assertion); the bad
    fixture must FAIL every assertion name. Returns True on success. This is the
    inverted-oracle gate: it proves each assertion can both pass a clean write and
    catch its violation, so a later real-data scan is trustworthy."""
    ok = True

    good = score_memory(*known_good_memory())
    if good.applicable_count == 0:
        logger.error("memory_selftest_good_no_applicable_assertions")
        ok = False
    if good.failed_count != 0:
        failed = [a.name for a in good.assertions if a.status is AssertionStatus.FAIL]
        logger.error("memory_selftest_good_unexpected_failures", extra={"failed": failed})
        ok = False

    bad = score_memory(*deliberately_bad_memory())
    failed_names = {a.name for a in bad.assertions if a.status is AssertionStatus.FAIL}
    expected = _all_assertion_names()
    missing = expected - failed_names
    if missing:
        logger.error(
            "memory_selftest_bad_did_not_fail_all", extra={"not_failed": sorted(missing)}
        )
        ok = False

    return ok


def scan_stored_profiles(db) -> MemoryScorecard:
    """Run the profile-only verdict floor over every stored `runner_memory` profile.

    A real-data sensor with no candidates/sources on hand, so only the ADR 0025
    rule-1 verdict floor (`no_inferred_verdict`) applies. Each row is guarded so one
    malformed profile becomes an error, never a crash."""
    from app.models.runner_memory import RunnerMemory
    from app.schemas.coach_memory import RunnerMemoryProfile

    scorecard = MemoryScorecard()
    for row in db.query(RunnerMemory).all():
        if not row.profile:
            continue
        try:
            profile = RunnerMemoryProfile(**row.profile)
        except Exception:
            logger.exception("memory_scan_unparseable_profile", extra={"user_id": str(row.user_id)})
            scorecard.errors += 1
            continue
        scorecard.scores.append(
            score_stored_profile(profile, label=f"user:{row.user_id}")
        )
    return scorecard
