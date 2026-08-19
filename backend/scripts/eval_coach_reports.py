"""Offline coach-report eval harness CLI (M5) — THE GATE.

Scores frozen coach reports against the deterministic rubric and prints a
repeatable scorecard. The default mode scores reports already in the local DB
(seed them first with `make seed-local`); no LLM calls, no API key, free and
deterministic.

    # score the reports already in the seeded local DB (current version only)
    python -m scripts.eval_coach_reports

    # validate the harness itself against its good/bad fixtures (no DB, no key)
    python -m scripts.eval_coach_reports --self-test

    # write a scorecard, then later flag regressions against it
    python -m scripts.eval_coach_reports --output before.json
    python -m scripts.eval_coach_reports --compare before.json

    # regenerate reports under the current prompt before scoring (needs ANTHROPIC_API_KEY)
    python -m scripts.eval_coach_reports --regenerate --activities 20

    # ALSO run the opt-in advisory semantic judge (#164; needs ANTHROPIC_API_KEY)
    python -m scripts.eval_coach_reports --semantic-judge --judge-limit 20

The deterministic rubric is the gate; the ``--semantic-judge`` layer is OFF by
default, advisory only (it never changes the exit code), and aggregated into a
separate, clearly-labelled section of the scorecard. The deterministic policy
validator remains the only safety floor.

Exit codes: 0 = ok, 1 = self-test failed or a deterministic regression was detected,
2 = no reports found to score.
"""

import argparse
import asyncio
import json
import sys
from typing import Optional

from app.db.session import SessionLocal, engine
from app.models import Activity, DerivedMetric
from app.services.coach.eval.harness import (
    Scorecard,
    compare_judge_sections,
    compare_scorecards,
    judge_db_reports,
    run_self_test,
    score_db_reports,
)
from app.services.coach.eval.judge import JUDGE_CRITERIA


def _print_summary(card: Scorecard) -> None:
    data = card.to_dict()
    print("Coach-report eval scorecard")
    print("=" * 60)
    print(f"reports scored:    {data['reports_scored']}")
    print(f"skipped (fallback): {data['skipped_fallback']}")
    # #810: always printed, including the zero. The point of the census is that the
    # figure is stated rather than discovered.
    print(f"skipped (unreadable pack, retired prompt): {data['skipped_unreadable_pack']}")
    for row in data["unreadable_packs"]:
        print(f"  - {row['report_id']} [{row['prompt_id']}]: {row['detail']}")
    if data["errors"]:
        print(f"errors (unparseable): {len(data['errors'])}")
        for err in data["errors"]:
            print(f"  - {err['report_id']}: {err['error']}")
    print(f"overall pass rate: {data['overall_pass_rate']:.3f}")
    print("-" * 60)
    for name, stats in data["assertion_summary"].items():
        print(
            f"  {name:<26} {stats['passed']}/{stats['applicable']} "
            f"(pass rate {stats['pass_rate']:.3f}, {stats['failed']} failed)"
        )
    print("=" * 60)
    if "judge" in data:
        _print_judge_section(data["judge"])


def _print_judge_section(judge: dict) -> None:
    """Print the opt-in semantic-judge section, clearly labelled as advisory and
    separate from the deterministic gate above."""
    print()
    print("Semantic judge (ADVISORY — does NOT gate; LLM-as-judge, #164)")
    print("=" * 60)
    print(f"reports judged:    {judge['reports_judged']}")
    if judge["judge_errors"]:
        print(f"judge errors:      {len(judge['judge_errors'])}")
        for err in judge["judge_errors"]:
            print(f"  - {err['report_id']}: {err['error']}")
    print("-" * 60)
    summary = judge["criterion_summary"]
    for name in JUDGE_CRITERIA:
        stats = summary.get(name, {})
        mean = stats.get("mean")
        if mean is None:
            print(f"  {name:<26} (no reports judged)")
        else:
            print(
                f"  {name:<26} mean {mean:.2f}  "
                f"(min {stats['min']}, max {stats['max']}, n={stats['count']})"
            )
    print("=" * 60)


async def _run_judge(db, card: Scorecard, args) -> None:
    """Run the opt-in semantic judge over the scored reports and attach the results to
    the scorecard. Needs ANTHROPIC_API_KEY; mutates ``card`` in place."""
    from app.core.config import settings
    from app.services.coach.llm import AnthropicClient

    if not settings.ANTHROPIC_API_KEY:
        print(
            "\n--semantic-judge requires ANTHROPIC_API_KEY (the deterministic gate "
            "above ran without it). Skipping the judge.",
            file=sys.stderr,
        )
        return
    model = args.judge_model or settings.COACH_MODEL_ID
    client = AnthropicClient(api_key=settings.ANTHROPIC_API_KEY, model=model)
    judge_scores, judge_errors = await judge_db_reports(
        db,
        client,
        prompt_id=args.prompt_id,
        schema_version=args.schema_version,
        all_versions=args.all_versions,
        include_fallback=args.include_fallback,
        limit=args.judge_limit,
    )
    card.judge_scores = judge_scores
    card.judge_errors = judge_errors


async def _regenerate(db, limit: Optional[int]) -> int:
    """Force-regenerate reports under the current prompt for activities that have
    metrics. Returns the count regenerated."""
    from app.services.coach.service import get_or_generate_coach_report

    q = (
        db.query(Activity.id)
        .join(DerivedMetric, DerivedMetric.activity_id == Activity.id)
        .order_by(Activity.start_date.desc())
    )
    if limit:
        q = q.limit(limit)
    ids = [row[0] for row in q.all()]
    count = 0
    for activity_id in ids:
        try:
            result = await get_or_generate_coach_report(db, str(activity_id), force=True)
            if result is not None:
                count += 1
        except Exception as exc:  # keep going; the scorer reports gaps
            db.rollback()
            print(f"  regenerate error on {activity_id}: {exc}", file=sys.stderr)
    return count


def main() -> int:
    # This is a reporting CLI, not the app: keep SQL echo (on locally via
    # APP_ENV) out of the scorecard. The engine's per-engine echo flag forces
    # logging regardless of logger level, so turn it off on the engine itself.
    engine.echo = False

    parser = argparse.ArgumentParser(description="Offline coach-report eval harness.")
    parser.add_argument("--prompt-id", default=None, help="prompt id to score (default: current configured prompt)")
    parser.add_argument("--schema-version", default=None, help="schema version to score (default: current)")
    parser.add_argument("--all-versions", action="store_true", help="score every report regardless of version")
    parser.add_argument("--include-fallback", action="store_true", help="include is_fallback reports in scoring")
    parser.add_argument("--regenerate", action="store_true", help="force-regenerate reports first (needs ANTHROPIC_API_KEY)")
    parser.add_argument("--activities", type=int, default=None, help="max activities to regenerate")
    parser.add_argument("--output", default=None, help="write the scorecard JSON to this path")
    parser.add_argument("--compare", default=None, help="compare against a prior scorecard JSON and flag regressions")
    parser.add_argument("--self-test", action="store_true", help="validate the harness against its good/bad fixtures and exit")
    parser.add_argument("--semantic-judge", action="store_true", help="ALSO run the opt-in advisory LLM-as-judge layer (#164; needs ANTHROPIC_API_KEY). OFF by default; never gates.")
    parser.add_argument("--judge-model", default=None, help="model id for the semantic judge (default: COACH_MODEL_ID)")
    parser.add_argument("--judge-limit", type=int, default=None, help="max reports to send to the semantic judge (cost control)")
    args = parser.parse_args()

    if args.self_test:
        ok, report = run_self_test()
        print(report)
        return 0 if ok else 1

    db = SessionLocal()
    try:
        if args.regenerate:
            n = asyncio.run(_regenerate(db, args.activities))
            print(f"Regenerated {n} report(s).")

        card = score_db_reports(
            db,
            prompt_id=args.prompt_id,
            schema_version=args.schema_version,
            all_versions=args.all_versions,
            include_fallback=args.include_fallback,
        )

        # Opt-in, advisory, OFF by default: never runs unless explicitly requested,
        # and never affects the deterministic pass/fail computed above.
        if args.semantic_judge:
            asyncio.run(_run_judge(db, card, args))
    finally:
        db.close()

    _print_summary(card)
    data = card.to_dict()

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        print(f"Wrote scorecard to {args.output}")

    if not card.report_scores:
        if card.unreadable_packs and not card.errors:
            print(
                f"\n{len(card.unreadable_packs)} report(s) found but none could be scored: every "
                "stored context pack was written under a prompt id declared beyond the pack "
                "readability cutoff (ADR 0032). Those packs are not migrated by design; target a "
                "readable version via --prompt-id/--schema-version, or regenerate with "
                "--regenerate.",
                file=sys.stderr,
            )
        elif card.errors:
            print(
                f"\n{len(card.errors)} report(s) found but none could be scored: the stored "
                "context packs do not match the current schema (version drift). Regenerate "
                "under the current prompt with --regenerate, or target the stored version via "
                "--prompt-id/--schema-version.",
                file=sys.stderr,
            )
        else:
            print("No reports found to score. Did you run `make seed-local` first?", file=sys.stderr)
        return 2

    if args.compare:
        with open(args.compare) as fh:
            previous = json.load(fh)
        regressions = compare_scorecards(previous, data)
        if regressions:
            print("\nREGRESSIONS DETECTED:")
            for line in regressions:
                print(f"  - {line}")
            return 1
        print("\nNo regressions against the baseline scorecard.")

        # Advisory judge drift: surfaced as a SIGNAL, never an exit-code failure (the
        # deterministic gate above owns the gate). Only flags when both scorecards
        # carry a judge section.
        judge_drift = compare_judge_sections(previous, data)
        if judge_drift:
            print("\nSEMANTIC-JUDGE DRIFT (advisory, not a gate):")
            for line in judge_drift:
                print(f"  - {line}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
