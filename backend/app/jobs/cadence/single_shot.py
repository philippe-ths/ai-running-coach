"""The single-shot cadence: one report, one notification, inline on ingest.

The prior per-activity pipeline, served by any non-two-stage prompt
(`coach_message_v1`, the `coach_report_v*` chain). There is no exchange, opener,
or fuller turn, so every event after ingest is a no-op.
"""

import logging
from typing import Optional

from app.core.config import settings
from app.jobs import exchange_ops
from app.jobs.cadence.base import PostActivityCadence
from app.services.coach.service import (
    get_active_report_row,
    get_or_generate_coach_report,
)
from app.services.notifications import Notification

logger = logging.getLogger(__name__)


class SingleShotCadence(PostActivityCadence):
    """One report, one notification, inline on ingest, gated by the per-activity
    `coach_notification_sent_at` sentinel (per-activity dedup needs a per-activity
    store, so this path keeps the legacy Activity sentinel rather than the Exchange).

    `on_block_complete` also covers the AC6 rollback case — a two-stage block-complete
    check that fires after a flip back to a single-shot prompt — and no-ops BEFORE any
    DB work, as the prior gate did. `on_reply` and `on_done` inherit the base no-op:
    there is no exchange to advance and no "done" affordance."""

    async def on_ingest(self, *, db, activity, block, notifier) -> Optional[Notification]:
        return await self.run_single_shot(
            db=db,
            activity=activity,
            strava_activity_id=activity.strava_activity_id,
            notifier=notifier,
        )

    async def run_single_shot(
        self, *, db, activity, strava_activity_id: int, notifier
    ) -> Optional[Notification]:
        """The report-and-notify stage, named so the one event this cadence acts on
        stays readable and directly exercisable."""
        # #643: auto-generate a coach report only for runs (the single-shot cadence has
        # no receipt; a non-run is simply silent here). On-demand regeneration is a
        # separate ungated path.
        if not exchange_ops.is_run_for_auto_report(activity):
            logger.info(
                "Skipping single-shot report for activity %s: not a run (%s)",
                strava_activity_id, activity.type,
            )
            return None

        report = await get_or_generate_coach_report(db, str(activity.id))
        if report is None:
            logger.info(
                "Skipping notification for activity %s: no coach report", strava_activity_id
            )
            return None

        db.refresh(activity)
        # Read the *active*-version row: prior versions may be retained alongside it,
        # so a version-unaware query could gate notification on the wrong report.
        coach_row = get_active_report_row(db, activity.id)
        if coach_row is None or coach_row.is_fallback:
            logger.info(
                "Skipping notification for activity %s: report is fallback or missing",
                strava_activity_id,
            )
            return None

        return exchange_ops.notify_stage(
            db, activity, report=report, stage="fuller",
            sentinel_attr="coach_notification_sent_at", notifier=notifier,
        )

    async def on_block_complete(self, *, db, block_id, activity_id, notifier):
        logger.info(
            "Skipping block-complete check for block %s: active prompt %s is single-shot",
            block_id, settings.COACH_PROMPT_ID,
        )
        return None
