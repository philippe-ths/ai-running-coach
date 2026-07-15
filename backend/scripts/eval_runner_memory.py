"""Offline runner-memory eval harness CLI (#658).

The durable-memory counterpart to `scripts/eval_coach_reports.py`. Two modes:

  --self-test   Validate the rubric against its synthetic good/bad fixtures.
                No DB, no API key. Exit 0 on pass, 1 on failure. This is the
                CI-friendly gate (the `make eval-memory-selftest` target).

  --scan        Score every STORED runner_memory profile against the profile-only
                verdict floor (ADR 0025 rule 1). Needs a DB (no key). Exit 0, or
                1 if any profile fails the floor.

The full triple-scored rubric (grounding / anti-echo / plan-commitment / safety-
hold) needs the writer's raw candidates + sources, which are not stored, so it is
exercised offline via the fixtures (self-test) and — as a follow-up — a
`--regenerate` path that runs the real writer over a conversation fixture set,
mirroring `eval_coach_reports.py --regenerate`. See docs/testing/coach-memory-eval.md.
"""

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline runner-memory eval harness (#658)")
    parser.add_argument("--self-test", action="store_true", help="validate the rubric against synthetic fixtures (no DB/key)")
    parser.add_argument("--scan", action="store_true", help="score stored runner_memory profiles' verdict floor (needs DB)")
    parser.add_argument("--output", help="write the JSON scorecard to this path")
    args = parser.parse_args()

    if args.self_test:
        from app.services.coach.eval.memory.harness import run_self_test

        ok = run_self_test()
        print(f"runner-memory eval self-test: {'PASS' if ok else 'FAIL'}")
        return 0 if ok else 1

    if args.scan:
        from app.db.session import SessionLocal
        from app.services.coach.eval.memory.harness import scan_stored_profiles

        db = SessionLocal()
        try:
            scorecard = scan_stored_profiles(db)
        finally:
            db.close()
        payload = scorecard.to_dict()
        text = json.dumps(payload, indent=2, default=str)
        if args.output:
            with open(args.output, "w") as fh:
                fh.write(text)
        print(text)
        floor = payload["assertions"].get("no_inferred_verdict", {})
        return 0 if floor.get("failed", 0) == 0 else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
