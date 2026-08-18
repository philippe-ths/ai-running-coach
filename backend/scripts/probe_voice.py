"""Run one or more coach Voices against real stored baselines (#828).

Why
---
"Does this sound like The Roast" is not automatable and is not trying to be.
What IS automatable is everything around it: pull a real stored baseline, run
the production rewrite path over it under each voice, run the mechanical checks
(invented figures, the policy floor, the safety surface) over every result, and
write the baseline and the rewrite side by side where a human can read them.

The #822 defect -- The Cornerman softening a detraining verdict -- was found by
three scratch scripts that no longer exist. `HARD_CASES` in
`app/services/coach/voice_probe.py` is the committed replacement: the recorded
situations that break voices, each saying why it is in the set.

Usage
-----
    # the recorded hard cases, every character (needs ANTHROPIC_API_KEY)
    python scripts/probe_voice.py

    # one character, one stored report
    python scripts/probe_voice.py --voice roast --report-id <uuid>

    # the harness graded against a stub: no DB, no key, no network
    python scripts/probe_voice.py --self-test

Nothing is regenerated. A stored baseline is the input, so the cost is one
cheap voice-lane call per (case, voice) pair. Seed real data first with
`make seed-local`.

Exit codes
----------
    0  every pair applied cleanly, or the self-test passed
    1  a mechanical check fired, or the self-test failed
    2  nothing to probe (no baselines resolved)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.coach import voice_probe  # noqa: E402
from app.services.coach.voice import PRESETS  # noqa: E402

# `coach-*` under docs/audit is gitignored, which is the right home for working
# output: a probe result is something a human reads once while tuning, not a
# durable audit report.
_DEFAULT_OUT = BACKEND_DIR.parent / "docs" / "audit"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--voice",
        action="append",
        default=[],
        help=f"a character to probe, repeatable (default: all of {', '.join(sorted(PRESETS))})",
    )
    parser.add_argument(
        "--report-id",
        action="append",
        default=[],
        help="probe this stored report instead of the recorded hard cases, repeatable",
    )
    parser.add_argument(
        "--recent",
        type=int,
        default=0,
        help="probe the N most recent stored reports instead of the recorded hard cases",
    )
    parser.add_argument(
        "--out-dir",
        default=str(_DEFAULT_OUT),
        help="where to write the markdown and JSON (default: docs/audit)",
    )
    parser.add_argument("--no-write", action="store_true", help="print only, write nothing")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="grade the harness against scripted outcomes; no DB, no API key",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.self_test:
        ok, detail = voice_probe.run_self_test()
        print("voice-probe self-test:", "PASS" if ok else "FAIL")
        print(detail)
        raise SystemExit(0 if ok else 1)

    from app.db.session import SessionLocal, engine

    engine.echo = False  # a local .env may turn SQL echo on; this is a CLI

    try:
        voices = (
            {name: voice_probe.resolve_named_voice(name) for name in args.voice}
            if args.voice
            else voice_probe.all_named_voices()
        )
    except voice_probe.ProbeError as exc:
        sys.stderr.write(f"{exc}\n")
        raise SystemExit(1)

    db = SessionLocal()
    try:
        missing: list[str] = []
        if args.report_id or args.recent:
            baselines = voice_probe.load_baselines(
                db, report_ids=args.report_id, limit=args.recent
            )
        else:
            baselines, missing = voice_probe.load_recorded_cases(db)

        if not baselines:
            sys.stderr.write(
                "No baselines to probe. Seed real data with `make seed-local`, "
                "or pass --report-id / --recent.\n"
            )
            for note in missing:
                sys.stderr.write(f"  {note}\n")
            raise SystemExit(2)

        print(
            f"Probing {len(baselines)} baseline(s) under {len(voices)} voice(s) "
            f"= {len(baselines) * len(voices)} rewrite call(s)."
        )
        for note in missing:
            print(f"  unresolved: {note}")

        results = asyncio.run(
            voice_probe.probe(baselines=baselines, voices=voices)
        )
    finally:
        db.close()

    summary = voice_probe.summarise(results)
    print("\n" + json.dumps(summary, indent=2))

    for result in results:
        if not result.applied:
            print(f"  {result.voice} · {result.baseline.report_id}: {result.outcome_reason}")
        for name in result.failed_checks:
            status, detail = result.checks[name]
            print(f"  FAIL {name} · {result.voice} · {result.baseline.report_id}: {detail}")

    if not args.no_write:
        out_dir = Path(args.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = f"coach-voice-probe-{date.today().isoformat()}"
        md = out_dir / f"{stem}.md"
        js = out_dir / f"{stem}.json"
        md.write_text(voice_probe.render_markdown(results, missing=missing), encoding="utf-8")
        js.write_text(
            json.dumps(voice_probe.to_json(results, missing=missing), indent=2),
            encoding="utf-8",
        )
        print(f"\nWrote {md}\n      {js}")
        print("Read the markdown: the character judgement is the part no check makes.")

    failed = summary["harness_disagreements"] + summary["rejected_by_the_gate"]
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
