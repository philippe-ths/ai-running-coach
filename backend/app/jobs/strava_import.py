"""Historical Strava import: backfill a runner's past activities on demand (#239).

A self-pacing background job (modelled on the stream backfill, #110). Each run
fetches one page of the runner's Strava history in the window from `since_date`
to today, upserts the activity summaries, runs deterministic analysis, and
advances the page cursor.

Crucially it imports *raw data only*: summaries and deterministic analysis
(distance/time/effort that power trends), never the AI coach report, opener, or
any Telegram/email notification. Firing the coach on a bulk historical import
would generate thousands of LLM calls and flood the runner's channel with
analyses of runs weeks or years old. Coach analysis stays opt-in (the existing
per-activity path).

State lives entirely in a `StravaImport` row:
  - `cursor_page` is the next page to fetch (1-based), with a fixed `after` lower
    bound. Strava returns results oldest-first when `after` is set, so paginating
    by page number walks the whole window regardless of sort order; the cursor
    advances by one per full page and an interruption resumes from it. (An
    earlier `before`-epoch cursor was wrong: with `after` set the first page is
    the *oldest* 50 activities, so `before = oldest` excluded everything and the
    import stopped after one page.)
  - dedup by `strava_activity_id` (via `upsert_activity`) makes any replayed page
    idempotent, so the import is safely re-runnable.

Pacing: when a full page comes back there is likely more history, so the job
schedules its own successor via rq-scheduler after `IMPORT_BATCH_PAUSE_SECONDS`,
keeping the single worker free for webhooks and self-heal between batches. Each
batch is a single Strava list call (no per-activity stream calls), so the call
rate stays comfortably under the 100-requests/15-min ceiling.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.queue import queue
from app.db.session import SessionLocal
from app.models import StravaAccount, StravaImport
from app.services.analysis import analyze
from app.services.blocks import (
    assign_block_guarded,
    reconcile_unassigned_activities,
)
from app.services.strava_ingestion import (
    ensure_valid_access_token,
    get_strava_port,
    upsert_activity,
)
from app.services.strava_ingestion.port import StravaRateLimited

logger = logging.getLogger(__name__)


@dataclass
class ImportBatchResult:
    imported: int = 0
    done: bool = True


async def run_import_batch(
    db: Session, import_obj: StravaImport, *, limit: int
) -> ImportBatchResult:
    """Fetch one page (since_date.., page=cursor_page), upsert + analyze each.

    Advances `cursor_page` by one when a full page comes back; marks the import
    complete when a short page signals the window is exhausted. Commits progress
    as it goes so an interruption resumes. Never notifies.
    """
    account = (
        db.query(StravaAccount)
        .filter(StravaAccount.user_id == import_obj.user_id)
        .first()
    )
    if account is None:
        import_obj.status = "failed"
        import_obj.error = "No linked Strava account found."
        db.add(import_obj)
        db.commit()
        return ImportBatchResult(imported=0, done=True)

    port = get_strava_port()
    since = datetime.combine(import_obj.since_date, time.min, tzinfo=timezone.utc)
    after = int(since.timestamp())
    page = import_obj.cursor_page

    try:
        access_token = await ensure_valid_access_token(db, account, port)
        try:
            batch = await port.list_activities_page(
                access_token, after=after, page=page, per_page=limit
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 401:
                raise
            logger.warning(
                "strava_401_mid_flight: force-refreshing token and retrying import page"
            )
            access_token = await ensure_valid_access_token(db, account, port, force=True)
            batch = await port.list_activities_page(
                access_token, after=after, page=page, per_page=limit
            )
    except StravaRateLimited:
        # A rate limit is transient: propagate it so the job reschedules after the
        # budget backoff (status left "running"), rather than marking the whole
        # import permanently failed and losing the cursor (#602 / mirrors #601).
        raise
    except Exception as exc:  # noqa: BLE001 - surface the failure on the row, don't crash the worker
        import_obj.status = "failed"
        import_obj.error = f"Strava list failed: {exc}"
        db.add(import_obj)
        db.commit()
        logger.error("Historical import %s failed listing: %s", import_obj.id, exc)
        return ImportBatchResult(imported=0, done=True)

    imported = 0
    for raw in batch:
        try:
            activity = upsert_activity(db, raw, account.user_id)
            db.commit()
            assign_block_guarded(db, activity)
            # Deterministic analysis only (no streams, no LLM, no notification);
            # a failure here must not abort the import of the rest of the page.
            try:
                analyze(db, str(activity.id))
            except Exception as exc:  # noqa: BLE001
                db.rollback()
                logger.error(
                    "Import analysis failed for activity %s: %s",
                    raw.get("id"),
                    exc,
                )
            imported += 1
        except Exception as exc:  # noqa: BLE001 - one bad activity must not sink the batch
            db.rollback()
            logger.error("Error importing activity %s: %s", raw.get("id"), exc)

    # Once per batch (not per activity): recover any activity an earlier ingest
    # committed but left block-less because its guarded assignment raised (#515).
    reconcile_unassigned_activities(db, account.user_id)

    import_obj.activities_imported += imported
    # A short page means we have reached the end of the window: done. A full page
    # means there is likely more history; advance to the next page.
    done = len(batch) < limit
    if done:
        import_obj.status = "completed"
    else:
        import_obj.cursor_page = page + 1
    db.add(import_obj)
    db.commit()

    logger.info(
        "Historical import %s batch: page %d imported %d (total %d), done=%s",
        import_obj.id,
        page,
        imported,
        import_obj.activities_imported,
        done,
    )
    return ImportBatchResult(imported=imported, done=done)


def _schedule_next_batch(import_id: str) -> None:
    """Schedule the next batch after the configured pause, so the single worker
    stays free for webhooks between batches.

    Uses RQ-native deferred scheduling (drained by the worker's `with_scheduler`),
    so no separate rq-scheduler process is needed (#123/ADR 0006)."""
    from app.core.queue import queue

    queue.enqueue_in(
        timedelta(seconds=settings.IMPORT_BATCH_PAUSE_SECONDS),
        strava_import_job,
        import_id,
    )


def _reschedule_after_budget(import_id: str) -> None:
    """Re-enqueue this import batch after the Strava-budget backoff (#544), when
    the shared budget is exhausted, so the walk resumes once capacity frees."""
    from app.core.queue import queue

    queue.enqueue_in(
        timedelta(seconds=settings.STRAVA_BUDGET_BACKOFF_SECONDS),
        strava_import_job,
        import_id,
    )


def strava_import_job(import_id: str) -> None:
    """RQ entrypoint. Runs one batch; if work remains, schedules the next.

    Yields to the shared Strava budget (#544): a runner's full-history walk is the
    onboarding pressure this protects against, so when the global budget is
    exhausted the batch defers (re-enqueues after the backoff, status left
    ``running``) instead of calling Strava and starving live webhook traffic.
    """
    from app.services.strava_ingestion.strava_budget import over_budget

    if over_budget():
        _reschedule_after_budget(str(import_id))
        logger.info(
            "Historical import %s deferred: Strava budget exhausted; retrying in %ds",
            import_id,
            settings.STRAVA_BUDGET_BACKOFF_SECONDS,
        )
        return

    db = SessionLocal()
    try:
        import_obj = db.get(StravaImport, UUID(str(import_id)))
        if import_obj is None:
            logger.warning("Historical import %s not found; skipping", import_id)
            return
        if import_obj.status != "running":
            logger.info(
                "Historical import %s is %s; not running a batch",
                import_id,
                import_obj.status,
            )
            return
        try:
            result = asyncio.run(
                run_import_batch(db, import_obj, limit=settings.IMPORT_PAGE_SIZE)
            )
        except StravaRateLimited as exc:
            # Shared window hit mid-walk: defer after the budget backoff, leaving
            # status "running" so the walk resumes with its cursor intact (#602).
            _reschedule_after_budget(str(import_id))
            logger.info(
                "Historical import %s deferred: Strava rate limited (retry_after=%s); "
                "retrying in %ds",
                import_id,
                exc.retry_after,
                settings.STRAVA_BUDGET_BACKOFF_SECONDS,
            )
            return
    finally:
        db.close()

    if not result.done:
        _schedule_next_batch(str(import_id))


def enqueue_import(db: Session, account: StravaAccount, since_date) -> StravaImport:
    """Create the import-state row and enqueue the first batch.

    Idempotent: if an import is already running for this user, return it rather
    than starting a second concurrent walk.
    """
    existing = (
        db.query(StravaImport)
        .filter(
            StravaImport.user_id == account.user_id,
            StravaImport.status == "running",
        )
        .order_by(StravaImport.created_at.desc())
        .first()
    )
    if existing is not None:
        return existing

    import_obj = StravaImport(
        user_id=account.user_id,
        since_date=since_date,
        status="running",
        cursor_page=1,
        activities_imported=0,
    )
    db.add(import_obj)
    db.commit()
    db.refresh(import_obj)

    queue.enqueue(
        strava_import_job,
        str(import_obj.id),
        job_id=f"strava_import_{import_obj.id}",
        result_ttl=3600,
    )
    return import_obj
