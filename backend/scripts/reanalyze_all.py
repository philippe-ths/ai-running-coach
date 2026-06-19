"""Re-run the analysis pipeline over all stored history, run-to-completion.

Synchronous, local equivalent of the async re-analysis job (#300): it reuses the
SAME batch function, `reanalyze_history_batch`, so both paths share one
re-analysis contract (eligibility, ordering, baseline-once) and cannot drift
apart (#326). Used by the coach-report eval real-data rebuild
(`docs/testing/coach-report-eval.md`): it rebuilds historical `DerivedMetric`
rows from the streams already in the database — no Strava calls — and blocks
until the whole eligible set is drained (no queue, no pacing), which is exactly
what the offline eval workflow needs.

    python -m scripts.reanalyze_all            # all eligible activities
    python -m scripts.reanalyze_all --limit 50 # bound the run (newest first)

Eligible = non-deleted activities that have stored streams (the only rows that
can be re-derived offline). Idempotent: `analyze` upserts the `DerivedMetric`, so
re-running — or resuming after an interruption — is always safe.
"""
import argparse
from typing import Optional

from app.core.config import settings
from app.db.session import SessionLocal
from app.jobs.reanalyze_history import reanalyze_history_batch


def reanalyze_all(
    db, *, limit: Optional[int] = None, batch_size: Optional[int] = None
) -> int:
    """Re-analyze eligible stored history to completion; return the count processed.

    Walks the same eligible set as the async job (non-deleted, stream-bearing),
    newest first, by looping `reanalyze_history_batch` until the cursor drains —
    so the re-analysis contract lives in one place. `limit` bounds the TOTAL
    number of activities processed (None means all); `batch_size` defaults to the
    configured `REANALYZE_BATCH_SIZE`.
    """
    batch_size = batch_size or settings.REANALYZE_BATCH_SIZE
    processed = 0
    cursor: Optional[int] = None
    while limit is None or processed < limit:
        this_limit = (
            batch_size if limit is None else min(batch_size, limit - processed)
        )
        result = reanalyze_history_batch(db, limit=this_limit, cursor=cursor)
        processed += len(result.processed)
        if result.next_cursor is None:  # set drained
            break
        cursor = result.next_cursor
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-analyze stored activities from their stored streams (run to completion)."
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="max activities to process (newest first)"
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        count = reanalyze_all(db, limit=args.limit)
        print(f"Re-analyzed {count} activities.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
