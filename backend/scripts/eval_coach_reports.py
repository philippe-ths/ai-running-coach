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

Exit codes: 0 = ok, 1 = self-test failed or a regression was detected, 2 = no
reports found to score.
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
    compare_scorecards,
    run_self_test,
    score_db_reports,
)


def _print_summary(card: Scorecard) -> None:
    data = card.to_dict()
    print("Coach-report eval scorecard")
    print("=" * 60)
    print(f"reports scored:    {data['reports_scored']}")
    print(f"skipped (fallback): {data['skipped_fallback']}")
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
    finally:
        db.close()

    _print_summary(card)
    data = card.to_dict()

    if args.output:
        with open(args.output, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        print(f"Wrote scorecard to {args.output}")

    if not card.report_scores:
        if card.errors:
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
