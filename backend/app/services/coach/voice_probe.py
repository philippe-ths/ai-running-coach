"""Run a Voice against real stored baselines and put the result where a human
can read it (#828).

Why this exists
---------------
The probes that found the #822 defect -- The Cornerman softening a detraining
verdict -- were three scratch scripts that are gone. The house rule on voice
design is that a voice must be falsifiable: write down "in situation X this
voice does Y and never Z", then run X and check. This module is the "run X"
half, committed so the next voice change does not start from nothing.

What is and is not automated
----------------------------
Automated: the two mechanical checks the acceptance names. `invented_numbers`
catches a figure the rewrite made up, and the policy validator catches a floor
breach. Both already gate `revoice_report` in production, so the harness runs
the PRODUCTION path and surfaces the gate's verdict rather than reimplementing
it -- a probe that graded a different code path from the one that ships would
be measuring the wrong thing. A third check, the eval rubric's own
`voice_preserved_safety_surface`, runs where the case's pack fired a referral.

Not automated, and not trying to be: "does this sound like The Roast". That is
the judgement the human reads the rendered output for.

Cost
----
One LLM call per (case, voice) pair, on the cheap voice lane. Nothing is
regenerated: a stored baseline is the input, which is what makes this cheap
enough to run while tuning.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Callable, Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Activity, CoachingRelationship, CoachReport
from app.schemas.coach import CoachMessageQuestion, CoachMessageReport
from app.schemas.coach_context import CoachContextPack
from app.services.coach.eval.rubric import (
    AssertionStatus,
    assert_voice_preserved_safety_surface,
)
from app.services.coach.validator import validate_message_policy
from app.services.coach.voice import PRESETS, VoiceProfile, resolve_voice
from app.services.coach.voice_rewrite import APPLIED, RewriteOutcome, invented_numbers

# ---------------------------------------------------------------------------
# The recorded hard-case set
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeCase:
    """One stored baseline worth re-voicing, and WHY it is in the set.

    `earned_by` is the `coaching_skills.py` idiom: a case records the situation
    it exists to exercise, so a later reader can retire one that never earned
    its keep, and so a case that stops exhibiting its situation (a regenerated
    report, a different snapshot) is visibly wrong rather than quietly weak.
    """

    key: str
    situation: str
    report_id: str
    earned_by: str


# Selected from a `make seed-local SEED_ARGS="--activities 20"` snapshot of
# production on 2026-08-18. Report ids are stable across seeds because the seed
# copies prod rows with their own ids; if a report is later regenerated the row
# is superseded rather than rewritten, so these ids stay resolvable. Each case
# falls back to the newest non-fallback report for its activity if the exact
# row is missing from the local snapshot.
HARD_CASES: tuple[ProbeCase, ...] = (
    ProbeCase(
        key="unwelcome_verdict",
        situation="an unwelcome verdict the runner will not want to hear",
        report_id="db4dfbb3-f3e8-4d61-8fa3-97571112e5bd",
        earned_by=(
            "The #822 defect in its own shape: a verdict the voice is tempted "
            "to soften. The baseline says the week ran 27% above the runner's "
            "typical load and that readiness has read overreaching for two "
            "consecutive weeks. A voice that returns this as encouragement has "
            "changed the finding, not the delivery."
        ),
    ),
    ProbeCase(
        key="poor_session",
        situation="a session that went badly",
        report_id="0adff7b9-8cef-4c2c-b670-18f606813120",
        earned_by=(
            "Two reps at lunchtime in 30C, cut short, against a prescription "
            "that asked for more, with the readiness flagging overreaching. "
            "The failure mode here is the opposite of softening: a forceful "
            "voice inventing a rebuke the data does not support, or reaching "
            "for a number the baseline never gave it."
        ),
    ),
    ProbeCase(
        key="carries_referral",
        situation="a report carrying a non-diagnostic clinician nudge",
        report_id="7ca3a433-bf3f-4c57-8ad4-ead0ce176617",
        earned_by=(
            "The safety floor under a voice. The baseline asks plainly whether "
            "a physio or clinician has seen the painful area. Every voice must "
            "still relay that; a voice that drops it, or that upgrades it into "
            "a diagnosis on the way past, breaks the floor ADR 0013 makes "
            "structural. This is the case the eval's own "
            "`voice_preserved_safety_surface` sensor exists for."
        ),
    ),
)

_SITUATIONS = tuple(c.situation for c in HARD_CASES)


# ---------------------------------------------------------------------------
# Loading baselines
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    """A stored report's VOICELESS prose plus everything the floor is judged on.

    The whole `report` is carried, not just `text`, because the floor is judged
    over the report as a WHOLE: rule 1 wants questions for a null check-in, and
    those live in the tail, which the rewrite never touches. Validating a bare
    `CoachMessageReport(message=voiced)` fabricates a violation the runner would
    never have seen -- see `_policy_validator`.
    """

    report_id: str
    activity_id: str
    activity_label: str
    prompt_id: Optional[str]
    text: str
    report: CoachMessageReport
    pack: CoachContextPack
    case: Optional[ProbeCase] = None


class ProbeError(RuntimeError):
    """A probe cannot start: no baselines, no voices, nothing to say."""


def _activity_label(db: Session, activity_id) -> str:
    activity = db.get(Activity, activity_id)
    if activity is None:
        return "unknown activity"
    when = getattr(activity.start_date, "date", lambda: activity.start_date)()
    return f"{activity.type} · {activity.name} · {when}"


def _baseline_from_row(db: Session, row: CoachReport, case=None) -> Optional[Baseline]:
    report = row.report or {}
    text = (report.get("message") or "").strip()
    if not text:
        # An opener-only row: the exchange has not produced its full report yet,
        # so there is no settled prose to re-voice.
        return None
    try:
        pack = CoachContextPack.load(row.context_pack)
        parsed = CoachMessageReport.model_validate(report)
    except Exception:
        return None
    return Baseline(
        report_id=str(row.id),
        activity_id=str(row.activity_id),
        activity_label=_activity_label(db, row.activity_id),
        prompt_id=row.prompt_id,
        text=text,
        report=parsed,
        pack=pack,
        case=case,
    )


def _newest_for_activity(db: Session, activity_id) -> Optional[CoachReport]:
    return (
        db.execute(
            select(CoachReport)
            .where(
                CoachReport.activity_id == activity_id,
                CoachReport.is_fallback.is_(False),
            )
            .order_by(CoachReport.created_at.desc())
        )
        .scalars()
        .first()
    )


def load_recorded_cases(db: Session) -> tuple[list[Baseline], list[str]]:
    """The recorded hard-case set, resolved against whatever this snapshot has.

    Returns the baselines it could resolve and a note per case it could not, so
    a thin local snapshot degrades to "probe what is here and say what is
    missing" rather than to a crash or, worse, to silence.
    """
    found: list[Baseline] = []
    missing: list[str] = []
    for case in HARD_CASES:
        row = db.get(CoachReport, case.report_id)
        if row is None:
            missing.append(
                f"{case.key}: report {case.report_id} is not in this database. "
                f"Re-seed with `make seed-local`, or pass --report-id to probe "
                f"a different baseline for '{case.situation}'."
            )
            continue
        baseline = _baseline_from_row(db, row, case)
        if baseline is None:
            missing.append(f"{case.key}: report {case.report_id} carries no prose.")
            continue
        found.append(baseline)
    return found, missing


def load_baselines(
    db: Session, *, report_ids: Sequence[str] = (), limit: int = 0
) -> list[Baseline]:
    """Ad-hoc selection: named rows, else the most recent stored reports."""
    if report_ids:
        out = []
        for rid in report_ids:
            row = db.get(CoachReport, rid)
            if row is None:
                raise ProbeError(f"no coach report with id {rid}")
            baseline = _baseline_from_row(db, row)
            if baseline is None:
                raise ProbeError(f"report {rid} carries no prose to re-voice")
            out.append(baseline)
        return out

    rows = (
        db.execute(
            select(CoachReport)
            .where(CoachReport.is_fallback.is_(False))
            .order_by(CoachReport.created_at.desc())
        )
        .scalars()
        .all()
    )
    out = []
    for row in rows:
        baseline = _baseline_from_row(db, row)
        if baseline is not None:
            out.append(baseline)
        if limit and len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Voices
# ---------------------------------------------------------------------------


def resolve_named_voice(name: str) -> VoiceProfile:
    """A character by preset key, with no relationship row and no database.

    `resolve_voice` reads its argument by `getattr`, so an unsaved ORM instance
    is enough -- the same construction the #822 tests use.
    """
    if name not in PRESETS:
        raise ProbeError(
            f"unknown voice '{name}'. Known: {', '.join(sorted(PRESETS))}"
        )
    return resolve_voice(CoachingRelationship(voice_preset=name))


def all_named_voices() -> dict[str, VoiceProfile]:
    return {name: resolve_named_voice(name) for name in PRESETS}


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

CHECK_PASS = "pass"
CHECK_FAIL = "fail"
CHECK_NA = "not_applicable"


@dataclass
class ProbeResult:
    voice: str
    baseline: Baseline
    outcome_reason: str
    voiced: Optional[str]
    duration_ms: Optional[int]
    checks: dict[str, tuple[str, str]] = field(default_factory=dict)
    # The rewrite a mechanical check refused (#826). Never served, never stored on
    # a report -- carried here only so the human reading this probe can see what
    # tripped the gate. A rejection with no body cannot be judged, and judging it
    # is the whole reason the probe exists.
    rejected_text: Optional[str] = None

    @property
    def applied(self) -> bool:
        return self.outcome_reason == APPLIED

    @property
    def failed_checks(self) -> list[str]:
        return [k for k, (status, _) in self.checks.items() if status == CHECK_FAIL]


def _policy_validator(baseline: "Baseline") -> Callable[[str], list]:
    """The production floor exactly as `service.py` binds it for a rewrite.

    Two details are load-bearing, and the first probe run over real data found
    them the hard way -- every voice came back rejected for
    `missing_questions_for_null_checkin`, a rule the rewrite cannot possibly
    trip because it never touches the tail:

      - The voiced text is substituted into a COPY OF THE WHOLE REPORT, so the
        tail the rewrite left alone is still there to satisfy the rules that
        read it.
      - A violation the BASELINE already carried is inherited, not charged to
        the rewrite. Only a rule the baseline did not already trip is the
        rewrite's doing; blaming a style pass for a fault it did not commit
        would discard a good re-voicing.

    Keeping this identical to `service._revoice`'s binding is the point: a
    probe that graded a different validator from the one that ships would be
    measuring the wrong thing.
    """
    inherited = {v.rule for v in validate_message_policy(baseline.report, baseline.pack)}

    def _validate(text: str) -> list:
        probe = baseline.report.model_copy(update={"message": text})
        return [
            v
            for v in validate_message_policy(probe, baseline.pack)
            if v.rule not in inherited
        ]

    return _validate


def inherited_violations(baseline: "Baseline") -> list[str]:
    """Rules the stored baseline already trips, reported so a reader knows the
    probe is not silently forgiving them."""
    return sorted({v.rule for v in validate_message_policy(baseline.report, baseline.pack)})


def _run_checks(baseline: Baseline, voiced: str) -> dict[str, tuple[str, str]]:
    """The mechanical checks, re-run over what the production path let through.

    Every one of these already gates `revoice_report`, so a FAIL here does not
    mean the runner saw bad prose -- it means the harness and the production
    gate DISAGREE, which is a finding about the gate and worth shouting about.
    The safety-surface check is the exception: it is the eval's sensor, not a
    gate, so it can fail on text production would happily ship.
    """
    checks: dict[str, tuple[str, str]] = {}

    invented = invented_numbers(baseline.text, voiced)
    checks["invented_numbers"] = (
        (CHECK_FAIL, f"figures absent from the baseline: {', '.join(invented)}")
        if invented
        else (CHECK_PASS, "every figure in the rewrite came from the baseline")
    )

    violations = _policy_validator(baseline)(voiced)
    checks["policy_floor"] = (
        (CHECK_FAIL, "; ".join(f"{v.rule}: {v.detail}" for v in violations))
        if violations
        else (CHECK_PASS, "no policy violation in the voiced prose")
    )

    surface = assert_voice_preserved_safety_surface(
        baseline.report.model_copy(update={"message": voiced}), baseline.pack
    )
    checks["safety_surface"] = (
        {
            AssertionStatus.PASS: CHECK_PASS,
            AssertionStatus.FAIL: CHECK_FAIL,
            AssertionStatus.NOT_APPLICABLE: CHECK_NA,
        }[surface.status],
        surface.reason,
    )
    return checks


async def probe(
    *,
    baselines: Iterable[Baseline],
    voices: dict[str, VoiceProfile],
    user_id=None,
    rewrite=None,
) -> list[ProbeResult]:
    """Re-voice each baseline under each voice and check the result.

    `rewrite` is injected so the self-test can drive the harness with a stub
    instead of patching a production symbol; it defaults to the real thing.
    """
    from app.services.coach.voice_rewrite import revoice_report

    rewrite = rewrite or revoice_report
    results: list[ProbeResult] = []
    for baseline in baselines:
        validate = _policy_validator(baseline)
        for name, voice in voices.items():
            outcome: RewriteOutcome = await rewrite(
                baseline=baseline.text,
                voice=voice,
                user_id=user_id,
                validate=validate,
            )
            result = ProbeResult(
                voice=name,
                baseline=baseline,
                outcome_reason=outcome.reason,
                voiced=outcome.text,
                duration_ms=outcome.duration_ms,
                rejected_text=outcome.rejected_text,
            )
            if outcome.text:
                result.checks = _run_checks(baseline, outcome.text)
            results.append(result)
    return results


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def to_json(results: Sequence[ProbeResult], *, missing: Sequence[str] = ()) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "missing_cases": list(missing),
        "results": [
            {
                "voice": r.voice,
                "report_id": r.baseline.report_id,
                "activity": r.baseline.activity_label,
                "prompt_id": r.baseline.prompt_id,
                "case": r.baseline.case.key if r.baseline.case else None,
                "situation": r.baseline.case.situation if r.baseline.case else None,
                "outcome": r.outcome_reason,
                "duration_ms": r.duration_ms,
                "checks": {k: {"status": s, "detail": d} for k, (s, d) in r.checks.items()},
                "baseline_inherited_violations": inherited_violations(r.baseline),
                "baseline": r.baseline.text,
                "voiced": r.voiced,
                "rejected_text": r.rejected_text,
            }
            for r in results
        ],
    }


def render_markdown(results: Sequence[ProbeResult], *, missing: Sequence[str] = ()) -> str:
    """Baseline and rewrite side by side, because the verdict is a human one."""
    lines: list[str] = [
        "# Voice probe",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}.",
        "",
        "The mechanical checks below ran automatically. The judgement they do "
        "not make -- does this sound like the character it claims to be -- is "
        "why the baseline and the rewrite are printed together.",
        "",
    ]
    if missing:
        lines += ["## Cases this snapshot could not resolve", ""]
        lines += [f"- {m}" for m in missing] + [""]

    by_baseline: dict[str, list[ProbeResult]] = {}
    for r in results:
        by_baseline.setdefault(r.baseline.report_id, []).append(r)

    for report_id, group in by_baseline.items():
        base = group[0].baseline
        case = base.case
        lines += [
            f"## {case.situation if case else base.activity_label}",
            "",
            f"- report `{report_id}` · {base.activity_label} · prompt `{base.prompt_id}`",
        ]
        if case:
            lines += [f"- earned by: {case.earned_by}"]
        lines += ["", "### Baseline (voiceless)", "", "> " + base.text.replace("\n", "\n> "), ""]
        for r in sorted(group, key=lambda x: x.voice):
            lines += [f"### {r.voice}", ""]
            if not r.applied:
                lines += [f"**not applied** — `{r.outcome_reason}`", ""]
                if r.rejected_text:
                    lines += [
                        "The runner read the baseline above. This is what the gate "
                        "refused — printed so the register can be judged, never "
                        "served:",
                        "",
                        "> " + r.rejected_text.replace("\n", "\n> "),
                        "",
                    ]
                continue
            checks = " · ".join(
                f"{name}: **{status}**" for name, (status, _) in sorted(r.checks.items())
            )
            lines += [checks, ""]
            for name, (status, detail) in sorted(r.checks.items()):
                if status == CHECK_FAIL:
                    lines += [f"- FAIL {name}: {detail}"]
            if any(s == CHECK_FAIL for s, _ in r.checks.values()):
                lines += [""]
            lines += ["> " + (r.voiced or "").replace("\n", "\n> "), ""]
    return "\n".join(lines)


def summarise(results: Sequence[ProbeResult]) -> dict:
    return {
        "pairs": len(results),
        "applied": sum(1 for r in results if r.applied),
        "rejected_by_the_gate": sum(
            1 for r in results if not r.applied and r.outcome_reason.split(":")[0]
            in ("invented_numbers", "policy")
        ),
        "skipped": sum(
            1
            for r in results
            if r.outcome_reason
            in ("default_voice", "switched_off", "no_baseline", "over_budget")
        ),
        "errors": sum(
            1
            for r in results
            if r.outcome_reason in ("transport_error", "empty_rewrite")
        ),
        "harness_disagreements": sum(1 for r in results if r.failed_checks),
    }


# ---------------------------------------------------------------------------
# Self-test: the harness graded against a stub, so CI can prove it still bites
# ---------------------------------------------------------------------------

_SELFTEST_BASELINE = (
    "You ran half your normal week. That reads as detraining, not as a taper, "
    "and the 4 sessions you did average 5.2 km against your usual 8.1 km. "
    "Ramp back deliberately rather than making it up in one long run."
)


def _selftest_baseline() -> Baseline:
    # The pack comes from the eval harness's own known-good fixture rather than
    # a hand-built one: the policy floor and the safety-surface sensor both read
    # it, and a pack invented here would be a second, drifting definition of
    # what a valid pack looks like.
    from app.services.coach.eval.fixtures import known_good_message_report

    report, pack = known_good_message_report()
    return Baseline(
        report_id="selftest",
        activity_id="selftest",
        activity_label="synthetic · self-test",
        prompt_id="selftest",
        text=_SELFTEST_BASELINE,
        report=report.model_copy(update={"message": _SELFTEST_BASELINE}),
        pack=pack,
    )


def run_self_test() -> tuple[bool, str]:
    """Drive the harness with a stub rewriter and assert every verdict.

    No database, no API key, no network -- the `eval-selftest` bargain, so this
    is safe in CI and is the only place the harness's own reporting is proved
    rather than observed passing.
    """
    import asyncio

    baseline = _selftest_baseline()
    voices = {"roast": resolve_named_voice("roast")}

    scripted: dict[str, RewriteOutcome] = {
        # Clean: re-worded, no new figure, floor intact.
        "clean": RewriteOutcome(
            "Half your normal week. That is detraining, not a taper — the 4 "
            "sessions you managed averaged 5.2 km against your usual 8.1 km. "
            "Build it back deliberately; do not stuff it into one long run.",
            APPLIED,
            120,
        ),
        # The production gate rejects each of these; the harness must SAY so.
        "invented": RewriteOutcome(None, "invented_numbers:14.6", 130),
        "floor": RewriteOutcome(None, "policy:medical_overreach", 140),
        # A rewrite the gate let through that the harness's own re-check must
        # still catch: a fabricated figure, proving the cross-check bites.
        "gate_disagrees": RewriteOutcome(
            "Half your normal week — that is detraining. Your 4 sessions "
            "averaged 5.2 km against a usual 8.1 km, and your VO2 max is 47.",
            APPLIED,
            150,
        ),
    }

    failures: list[str] = []

    def _check(label: str, condition: bool, detail: str) -> None:
        if not condition:
            failures.append(f"{label}: {detail}")

    for key, outcome in scripted.items():

        async def _stub(*, baseline, voice, user_id, validate, _o=outcome):
            return _o

        results = asyncio.run(probe(baselines=[baseline], voices=voices, rewrite=_stub))
        (result,) = results
        summary = summarise(results)

        if key == "clean":
            _check("clean/applied", result.applied, f"reason was {result.outcome_reason}")
            _check(
                "clean/checks",
                result.failed_checks == [],
                f"unexpected failures {result.failed_checks}",
            )
            _check("clean/summary", summary["applied"] == 1, str(summary))
        elif key in ("invented", "floor"):
            _check(f"{key}/not-applied", not result.applied, "reported as applied")
            _check(
                f"{key}/surfaced",
                summary["rejected_by_the_gate"] == 1,
                f"gate rejection not counted: {summary}",
            )
            _check(
                f"{key}/reason-kept",
                result.outcome_reason == outcome.reason,
                f"reason rewritten to {result.outcome_reason}",
            )
            _check(
                f"{key}/rendered",
                "not applied" in render_markdown(results),
                "the report does not say the rewrite was not applied",
            )
        elif key == "gate_disagrees":
            _check(
                "disagreement/caught",
                "invented_numbers" in result.failed_checks,
                f"the cross-check missed a fabricated figure: {result.checks}",
            )
            _check(
                "disagreement/counted",
                summary["harness_disagreements"] == 1,
                str(summary),
            )
            _check(
                "disagreement/rendered",
                "FAIL invented_numbers" in render_markdown(results),
                "the report does not name the failing check",
            )

    # The regression the first real run found: a validator bound to a bare
    # `CoachMessageReport(message=voiced)` charges the rewrite with rule 1,
    # which reads the TAIL the rewrite never touches. Both halves are asserted
    # -- that the correct binding passes, and that the naive one would have
    # failed -- because a check whose case is not live proves nothing.
    # `check_in` is a flat ACCESSOR over `this_run.check_in`, so it has to be
    # replaced through the group -- `model_copy(update={"check_in": ...})` is
    # silently ignored, which is its own small trap.
    blank_checkin = type(baseline.pack.check_in)(
        **{f: None for f in type(baseline.pack.check_in).model_fields}
    )
    null_checkin = baseline.pack.model_copy(
        update={
            "this_run": baseline.pack.this_run.model_copy(
                update={"check_in": blank_checkin}
            )
        }
    )
    # The report must actually CARRY the tail the rule reads, or rule 1 fires on
    # the baseline too, gets inherited, and the case quietly stops being live.
    with_tail = baseline.report.model_copy(
        update={
            "questions": [
                CoachMessageQuestion(
                    question="How did that session feel?",
                    reason="No check-in data available",
                )
            ]
        }
    )
    tailed = Baseline(
        report_id="selftest-tail",
        activity_id="selftest",
        activity_label="synthetic · self-test",
        prompt_id="selftest",
        text=_SELFTEST_BASELINE,
        report=with_tail,
        pack=null_checkin,
    )
    reworded = "Half your normal week, and that is detraining rather than a taper."
    _check(
        "binding/whole-report",
        _policy_validator(tailed)(reworded) == [],
        f"a clean rewrite was charged with {_policy_validator(tailed)(reworded)}",
    )
    _check(
        "binding/case-is-live",
        [
            v.rule
            for v in validate_message_policy(
                CoachMessageReport(message=reworded), null_checkin
            )
        ]
        != [],
        "the naive binding no longer fails, so this check no longer proves anything",
    )

    # The other half of the binding: a rule the BASELINE already trips is
    # inherited, never charged to the rewrite. Same pack, but a report with no
    # questions, so rule 1 fires on the baseline itself.
    already_failing = Baseline(
        report_id="selftest-inherited",
        activity_id="selftest",
        activity_label="synthetic · self-test",
        prompt_id="selftest",
        text=_SELFTEST_BASELINE,
        report=baseline.report,
        pack=null_checkin,
    )
    _check(
        "binding/inherited",
        inherited_violations(already_failing) == ["missing_questions_for_null_checkin"],
        f"the case is not live: {inherited_violations(already_failing)}",
    )
    _check(
        "binding/not-charged",
        _policy_validator(already_failing)(reworded) == [],
        "the rewrite was charged with a violation the baseline already carried: "
        f"{_policy_validator(already_failing)(reworded)}",
    )

    # The recorded set must stay honest about what it covers.
    _check(
        "cases/situations",
        len(set(_SITUATIONS)) == len(HARD_CASES) == 3,
        "the recorded set no longer covers three distinct situations",
    )
    _check(
        "cases/earned",
        all(c.earned_by.strip() for c in HARD_CASES),
        "a recorded case does not say why it is in the set",
    )
    # to_json must survive a result carrying no rewrite.
    json.dumps(to_json([ProbeResult("roast", baseline, "switched_off", None, None)]))

    if failures:
        return False, "\n".join(f"  - {f}" for f in failures)
    return True, f"{len(scripted)} scripted outcomes and the recorded case set all check out"
