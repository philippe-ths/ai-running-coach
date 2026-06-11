"""A4 two-stage Exchange live cutover rehearsal (done-decision evidence).

Run under the local seeded DB with COACH_PROMPT_ID=coach_message_v2 and a live
ANTHROPIC_API_KEY. For a bounded set of seeded activities it exercises the real
two-stage path with live LLM calls: generate_opener (stage one) then generate_fuller
(stage two, in-place on the same row), verifying the opener-state row, the schedule
decision, the in-place update preserving opener_message, the digest/learning-loop
invariants, and idempotency. Prints a summary; never writes to prod (local DB only).

Usage:
  cd backend && COACH_PROMPT_ID=coach_message_v2 .venv/bin/python -m scripts.a4_rehearsal [N]
"""

import asyncio
import sys

from sqlalchemy.orm import joinedload

from app.core.config import settings
from app.db.session import SessionLocal
from app.models import Activity
from app.models.coach_report import CoachReport
from app.services.coach.output_contract import is_opener_only
from app.services.coach.service import (
    generate_fuller,
    generate_opener,
    get_active_report_row,
    is_two_stage_prompt,
)


async def main(limit: int) -> int:
    assert is_two_stage_prompt(settings.COACH_PROMPT_ID), (
        f"COACH_PROMPT_ID must be coach_message_v2, got {settings.COACH_PROMPT_ID}"
    )
    db = SessionLocal()
    acts = (
        db.query(Activity)
        .options(joinedload(Activity.metrics))
        .filter(Activity.is_deleted == False)  # noqa: E712
        .order_by(Activity.start_date.desc())
        .all()
    )
    acts = [a for a in acts if a.metrics is not None][:limit]
    print(f"Rehearsing the two-stage Exchange on {len(acts)} seeded activities "
          f"(prompt={settings.COACH_PROMPT_ID}, model={settings.COACH_MODEL_ID})\n")

    ok = 0
    for i, act in enumerate(acts, 1):
        # Start from a clean slate for this activity's active-version row.
        existing = get_active_report_row(db, act.id)
        if existing is not None:
            db.delete(existing)
            db.commit()

        # Stage one: the opener.
        opener = await generate_opener(db, str(act.id))
        row = get_active_report_row(db, act.id)
        opener_ok = (
            opener is not None
            and row is not None
            and is_opener_only(row.report)
            and bool(row.report.get("opener_message"))
            and (row.report.get("message") or "") == ""
            and row.digest is None  # opener carries no digest
        )

        # Stage two: the fuller turn (in-place on the same row).
        fuller = await generate_fuller(db, str(act.id))
        row2 = get_active_report_row(db, act.id)
        fuller_ok = (
            fuller is not None
            and row2 is not None
            and row2.id == row.id  # SAME evolving row (cache identity preserved)
            and not is_opener_only(row2.report)
            and bool(row2.report.get("opener_message"))  # opener prose preserved
            and len((row2.report.get("message") or "").strip()) > 0
            and (row2.digest is not None if not row2.is_fallback else True)
        )

        # Idempotency: a second fuller is a cache hit (no new row, no error).
        again = await generate_fuller(db, str(act.id))
        idem_ok = again is not None and get_active_report_row(db, act.id).id == row.id

        passed = opener_ok and fuller_ok and idem_ok
        ok += 1 if passed else 0
        print(
            f"[{i}/{len(acts)}] {act.name[:32]:32} | "
            f"opener={'ok' if opener_ok else 'FAIL'} "
            f"schedule={opener.schedule_fuller_turn if opener else '?'} "
            f"fuller={'ok' if fuller_ok else 'FAIL'} "
            f"idem={'ok' if idem_ok else 'FAIL'} "
            f"fallback={row2.is_fallback if row2 else '?'}"
        )
        if opener and opener.report:
            om = (row.report.get("opener_message") or "")[:140]
            print(f"      opener: {om}")
        if row2 is not None:
            fm = (row2.report.get("message") or "")[:160]
            print(f"      fuller: {fm}\n")

    total = len(acts)
    print(f"\nRESULT: {ok}/{total} activities passed the full opener->fuller->idempotent cycle.")
    db.close()
    return 0 if ok == total and total > 0 else 1


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    raise SystemExit(asyncio.run(main(n)))
