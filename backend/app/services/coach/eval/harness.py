"""The offline eval harness (M5): load coach reports, score them against the
rubric, aggregate into a repeatable scorecard, and flag regressions across runs.

Default scope is the current ``(prompt_id, schema_version)`` pair — exactly the
versioned-cache identity M0 introduced — so a scorecard is comparable across
prompt/model iterations.

A stored pack that no longer parses has TWO meanings and the scorecard keeps
them apart (#810). A pack written under a prompt id declared beyond the pack
readability cutoff (`UNREADABLE_PACK_PROMPT_IDS`) is settled history: it is
counted in `skipped_unreadable_pack`, listed in `unreadable_packs`, and scores
nothing. Any other parse failure is live drift and stays in `errors`, loud. A
row in either bucket contributes no assertions at all, so it can never be
mistaken for a report that parsed and scored badly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.coach_report import CoachReport
from app.schemas.coach import CoachMessageReport, CoachReportContent
from app.schemas.coach_context import StoredPackUnreadable, load_stored_pack
from app.services.coach.eval.fixtures import (
    deliberately_bad_message_report,
    deliberately_bad_report,
    known_good_message_report,
    known_good_report,
)
from app.services.coach.eval.judge import (
    JUDGE_CRITERIA,
    JudgeReportScore,
    StructuredClient,
    judge_report,
    summarize_judge_scores,
)
from app.services.coach.eval.rubric import (
    ASSERTIONS,
    AssertionStatus,
    ReportScore,
    score_report,
)
from app.services.coach.output_contract import is_opener_only
from app.services.coach.service import active_schema_version


def _validate_report(schema_version, report_json):
    """Parse a stored report against the model for its schema-version family
    (ADR 0009): the A3 prose CoachMessageReport for 2.x rows, the legacy
    structured CoachReportContent otherwise."""
    if str(schema_version or "").startswith("2"):
        return CoachMessageReport.model_validate(report_json)
    return CoachReportContent.model_validate(report_json)


def _assertion_names() -> List[str]:
    # The rubric assertion order is the source of truth for column order.
    # Function names map 1:1 to assertion result names (assert_<name> -> <name>).
    return [fn.__name__.replace("assert_", "", 1) for fn in ASSERTIONS]


@dataclass
class Scorecard:
    report_scores: List[ReportScore]
    skipped_fallback: int = 0
    errors: List[Dict[str, Any]] = field(default_factory=list)
    # A3: how many scored reports stored a degraded tail (a real prose message but
    # no usable structured tail). Operational health, not a rubric assertion: a
    # rising count means the tail call is failing even when the coaching is fine.
    tail_degraded: int = 0
    # A4: how many rows were skipped as not-yet-complete opener-only exchanges (the
    # fuller turn never landed). The harness scores the fuller turn only; an
    # opener row has no message to score. Operational health, not a rubric assertion.
    skipped_opener_only: int = 0
    # #810: rows whose stored context_pack does not parse under the CURRENT schema
    # AND was written under a prompt id declared beyond the readability cutoff.
    # A KNOWN, counted, non-scoring outcome (the `skipped_opener_only` idiom), not
    # an error: those packs are settled history that is deliberately neither
    # migrated nor rescued by widening the schema. Each entry carries the row's
    # prompt id and the first line of the rejection, so the census is inspectable
    # rather than a bare number. An unreadable pack under any other prompt id
    # stays in `errors`.
    unreadable_packs: List[Dict[str, Any]] = field(default_factory=list)
    # #164 semantic-judge layer (OPT-IN, advisory only). Empty unless the judge was
    # explicitly run. The judge NEVER affects the deterministic pass/fail above; its
    # scores are aggregated into a separate, clearly-labelled `judge` section.
    judge_scores: List[JudgeReportScore] = field(default_factory=list)
    judge_errors: List[Dict[str, Any]] = field(default_factory=list)

    def assertion_summary(self) -> Dict[str, Dict[str, Any]]:
        summary: Dict[str, Dict[str, Any]] = {}
        for name in _assertion_names():
            applicable = passed = failed = 0
            for rs in self.report_scores:
                for a in rs.assertions:
                    if a.name != name:
                        continue
                    if a.status is AssertionStatus.NOT_APPLICABLE:
                        continue
                    applicable += 1
                    if a.status is AssertionStatus.PASS:
                        passed += 1
                    elif a.status is AssertionStatus.FAIL:
                        failed += 1
            summary[name] = {
                "applicable": applicable,
                "passed": passed,
                "failed": failed,
                "pass_rate": (passed / applicable) if applicable else 1.0,
            }
        return summary

    @property
    def overall_pass_rate(self) -> float:
        # Micro-average over all applicable assertions (assertion-weighted, not
        # report-weighted): a report with more applicable assertions counts more.
        passed = sum(rs.passed_count for rs in self.report_scores)
        applicable = sum(rs.applicable_count for rs in self.report_scores)
        return (passed / applicable) if applicable else 1.0

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "reports_scored": len(self.report_scores),
            "skipped_fallback": self.skipped_fallback,
            "skipped_opener_only": self.skipped_opener_only,
            # #810: the known figure, always present so a scorecard states it even
            # when it is zero. Zero is the reassuring reading; a surprise is what
            # the issue was filed about.
            "skipped_unreadable_pack": len(self.unreadable_packs),
            "unreadable_packs": self.unreadable_packs,
            "tail_degraded": self.tail_degraded,
            "errors": self.errors,
            "overall_pass_rate": self.overall_pass_rate,
            "assertion_summary": self.assertion_summary(),
            "reports": [rs.to_dict() for rs in self.report_scores],
        }
        # The judge section is present ONLY when the opt-in judge layer ran, so a
        # default deterministic scorecard is byte-identical to the pre-#164 shape.
        if self.judge_scores or self.judge_errors:
            data["judge"] = {
                "reports_judged": len(self.judge_scores),
                "judge_errors": self.judge_errors,
                "criterion_summary": summarize_judge_scores(self.judge_scores),
                "reports": [js.to_dict() for js in self.judge_scores],
            }
        return data


def score_db_reports(
    db: Session,
    *,
    prompt_id: Optional[str] = None,
    schema_version: Optional[str] = None,
    all_versions: bool = False,
    include_fallback: bool = False,
) -> Scorecard:
    """Score every matching CoachReport row currently in the database.

    By default only the current ``(prompt_id, schema_version)`` is scored so the
    result is comparable across prompt/model versions. Pass ``all_versions=True``
    to score every row (older packs that no longer parse become errors)."""
    if prompt_id is None:
        prompt_id = settings.COACH_PROMPT_ID
    if schema_version is None:
        # Score whichever family the active prompt selects, so `make eval` follows
        # the active output shape across the cutover (AC5).
        schema_version = active_schema_version(prompt_id)

    # #646: score CURRENT rows only; superseded audit copies would double-count the
    # same activity's regenerated report into the scorecard.
    query = db.query(CoachReport).filter(CoachReport.superseded_at.is_(None))
    if not all_versions:
        query = query.filter(
            CoachReport.prompt_id == prompt_id,
            CoachReport.schema_version == schema_version,
        )
    rows = query.order_by(CoachReport.created_at, CoachReport.id).all()

    scores: List[ReportScore] = []
    skipped_fallback = 0
    skipped_opener_only = 0
    tail_degraded = 0
    errors: List[Dict[str, Any]] = []
    unreadable_packs: List[Dict[str, Any]] = []
    for row in rows:
        if row.is_fallback and not include_fallback:
            skipped_fallback += 1
            continue
        # A4: an opener-only row is a not-yet-complete exchange (the fuller turn
        # never landed). The rubric scores the fuller-turn message; skip it BEFORE
        # parse/score so an empty message never reaches the assertions. In-band
        # signal only (the row's own report), preserving the harness's
        # self-containment — no Activity-sentinel coupling.
        if is_opener_only(row.report):
            skipped_opener_only += 1
            continue
        try:
            # Parse (drifted/corrupt pack) and score (a pathological assertion)
            # both guarded: one bad report becomes an error, never a crashed run.
            content = _validate_report(row.schema_version, row.report)
            pack = load_stored_pack(row.context_pack, prompt_id=row.prompt_id)
            score = score_report(
                content, pack,
                report_id=str(row.id),
                activity_id=str(row.activity_id),
                prompt_id=row.prompt_id,
                schema_version=row.schema_version,
            )
        except StoredPackUnreadable as exc:
            # #810: settled history, not a failure. Counted and listed, never
            # scored, and deliberately BEFORE the generic handler below so it can
            # never be swallowed into the error bucket.
            unreadable_packs.append({
                "report_id": str(row.id),
                "activity_id": str(row.activity_id),
                "prompt_id": row.prompt_id,
                "schema_version": row.schema_version,
                "detail": exc.detail,
            })
            continue
        except Exception as exc:
            # Keep the summary to one line; pydantic ValidationError is multi-line.
            summary = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            errors.append({"report_id": str(row.id), "error": f"{type(exc).__name__}: {summary}"})
            continue
        if getattr(content, "tail_degraded", False):
            tail_degraded += 1
        scores.append(score)

    return Scorecard(
        report_scores=scores,
        skipped_fallback=skipped_fallback,
        skipped_opener_only=skipped_opener_only,
        tail_degraded=tail_degraded,
        errors=errors,
        unreadable_packs=unreadable_packs,
    )


async def judge_db_reports(
    db: Session,
    client: StructuredClient,
    *,
    prompt_id: Optional[str] = None,
    schema_version: Optional[str] = None,
    all_versions: bool = False,
    include_fallback: bool = False,
    limit: Optional[int] = None,
) -> tuple[List[JudgeReportScore], List[Dict[str, Any]]]:
    """Run the opt-in semantic judge over the SAME scoreable rows the deterministic
    scorer covers, returning (judge_scores, judge_errors).

    This walks the rows independently of ``score_db_reports`` rather than refactoring
    it, so the deterministic gate stays byte-for-byte unchanged (the conservative
    choice — do not touch the working floor to bolt on an advisory layer). The row
    filter, the fallback skip, and the opener-only skip mirror ``score_db_reports``
    exactly so the two sections describe the same population. One malformed verdict or
    transport error is recorded per-report and the run continues, never crashing — the
    judge is advisory.
    """
    if prompt_id is None:
        prompt_id = settings.COACH_PROMPT_ID
    if schema_version is None:
        schema_version = active_schema_version(prompt_id)

    # #646: score CURRENT rows only; superseded audit copies would double-count the
    # same activity's regenerated report into the scorecard.
    query = db.query(CoachReport).filter(CoachReport.superseded_at.is_(None))
    if not all_versions:
        query = query.filter(
            CoachReport.prompt_id == prompt_id,
            CoachReport.schema_version == schema_version,
        )
    rows = query.order_by(CoachReport.created_at, CoachReport.id).all()

    judged: List[JudgeReportScore] = []
    errors: List[Dict[str, Any]] = []
    for row in rows:
        if limit is not None and len(judged) >= limit:
            break
        if row.is_fallback and not include_fallback:
            continue
        if is_opener_only(row.report):
            continue
        try:
            content = _validate_report(row.schema_version, row.report)
            pack = load_stored_pack(row.context_pack, prompt_id=row.prompt_id)
            verdict = await judge_report(client, content, pack)
        except StoredPackUnreadable:
            # #810: settled history. The deterministic scorecard carries the census;
            # the advisory judge simply has nothing to judge, so it says nothing
            # rather than filing a pack the policy already accounts for as an error.
            continue
        except Exception as exc:
            summary = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
            errors.append(
                {"report_id": str(row.id), "error": f"{type(exc).__name__}: {summary}"}
            )
            continue
        judged.append(
            JudgeReportScore(
                verdict=verdict,
                report_id=str(row.id),
                activity_id=str(row.activity_id),
                prompt_id=row.prompt_id,
                schema_version=row.schema_version,
            )
        )

    return judged, errors


def compare_judge_sections(
    before: Dict[str, Any],
    after: Dict[str, Any],
    *,
    min_drop: float = 0.5,
) -> List[str]:
    """Flag a meaningful drop in any judge criterion's mean between two scorecards.

    Advisory, not a hard gate: an LLM judge is not perfectly reproducible, so a small
    wobble is noise. Only a drop of at least ``min_drop`` (half a point on the 1-5
    scale, default) in a criterion mean is surfaced, and only when BOTH scorecards
    carry a judge section. Returns human-readable lines; empty means nothing notable.
    """
    before_judge = before.get("judge")
    after_judge = after.get("judge")
    if not before_judge or not after_judge:
        return []

    regressions: List[str] = []
    before_summary = before_judge.get("criterion_summary", {})
    after_summary = after_judge.get("criterion_summary", {})
    for name in JUDGE_CRITERIA:
        before_mean = (before_summary.get(name) or {}).get("mean")
        after_mean = (after_summary.get(name) or {}).get("mean")
        if before_mean is None or after_mean is None:
            continue
        if after_mean <= before_mean - min_drop:
            regressions.append(
                f"judge {name} mean dropped {before_mean:.2f} -> {after_mean:.2f}"
            )
    return regressions


def compare_scorecards(
    before: Dict[str, Any],
    after: Dict[str, Any],
    *,
    epsilon: float = 1e-9,
) -> List[str]:
    """Flag regressions between two scorecard dicts: a drop in the overall pass
    rate, a drop in any single assertion's pass rate, or an assertion that stops
    being evaluated. Returns human-readable lines; empty list means no regression.

    A gate that silently stops checking a rule is worse than no gate, so this
    iterates the union of assertion names and treats a disappeared assertion or a
    collapse to zero applicable reports as a regression, not as a vacuous pass."""
    regressions: List[str] = []

    before_overall = before.get("overall_pass_rate", 1.0)
    after_overall = after.get("overall_pass_rate", 1.0)
    if after_overall < before_overall - epsilon:
        regressions.append(
            f"overall pass rate dropped {before_overall:.3f} -> {after_overall:.3f}"
        )

    # #810: the unreadable-pack census is a figure, so a RISE in it is a regression
    # the same way a pass-rate drop is: more of the corpus has stopped being
    # readable than the declared cutoff accounts for. Compared only when BOTH
    # scorecards carry the key (the judge-section idiom), so a scorecard written
    # before this field existed does not read as a jump from zero.
    before_unreadable = before.get("skipped_unreadable_pack")
    after_unreadable = after.get("skipped_unreadable_pack")
    if before_unreadable is not None and after_unreadable is not None:
        if after_unreadable > before_unreadable:
            regressions.append(
                f"unreadable stored packs rose {before_unreadable} -> {after_unreadable}"
            )

    before_summary = before.get("assertion_summary", {})
    after_summary = after.get("assertion_summary", {})
    for name in sorted(set(before_summary) | set(after_summary)):
        before_stats = before_summary.get(name, {})
        before_applicable = before_stats.get("applicable", 0)
        before_rate = before_stats.get("pass_rate", 1.0)

        if name not in after_summary:
            if before_applicable > 0:
                regressions.append(
                    f"{name}: no longer evaluated (was {before_rate:.3f} over {before_applicable})"
                )
            continue

        after_stats = after_summary[name]
        after_applicable = after_stats.get("applicable", 0)
        after_rate = after_stats.get("pass_rate", 1.0)

        if before_applicable > 0 and after_applicable == 0:
            regressions.append(
                f"{name}: coverage dropped to zero applicable reports (was {before_applicable})"
            )
        elif after_rate < before_rate - epsilon:
            regressions.append(
                f"{name} pass rate dropped {before_rate:.3f} -> {after_rate:.3f}"
            )

    return regressions


def run_self_test() -> tuple[bool, str]:
    """Validate the harness against its own synthetic fixtures (the brief's
    deliberately-bad-fails / known-good-passes check), for BOTH output shapes —
    the legacy structured report and the A3 prose message. Returns (ok, report)."""
    lines: List[str] = []
    ok = True

    for label, good_fn, bad_fn in (
        ("structured", known_good_report, deliberately_bad_report),
        ("message", known_good_message_report, deliberately_bad_message_report),
    ):
        good_content, good_pack = good_fn()
        good = score_report(good_content, good_pack, report_id=f"self-test-good-{label}")
        if good.failed_count != 0 or good.applicable_count == 0:
            ok = False
            lines.append(
                f"FAIL known-good {label} fixture: failed={good.failed_count} "
                f"applicable={good.applicable_count} (expected 0 failed, >0 applicable)"
            )
        else:
            lines.append(
                f"OK   known-good {label} fixture passes all {good.applicable_count} applicable assertions"
            )

        bad_content, bad_pack = bad_fn()
        bad = score_report(bad_content, bad_pack, report_id=f"self-test-bad-{label}")
        failed_names = {a.name for a in bad.assertions if a.status is AssertionStatus.FAIL}
        expected = set(_assertion_names())
        missing = expected - failed_names
        if missing:
            ok = False
            lines.append(f"FAIL deliberately-bad {label} fixture did not fail: {sorted(missing)}")
        else:
            lines.append(f"OK   deliberately-bad {label} fixture fails all {len(expected)} assertions")

    return ok, "\n".join(lines)
