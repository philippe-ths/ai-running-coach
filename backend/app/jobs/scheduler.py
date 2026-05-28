"""rq-scheduler bootstrap: register the recurring polling job.

Run this once at startup of the scheduler process to install the recurring
schedule, then run `rqscheduler --url $REDIS_URL` as a long-lived process
to actually fire scheduled jobs.
"""

import logging
from datetime import datetime, timezone

from redis import Redis
from rq_scheduler import Scheduler

from app.core.config import settings
from app.jobs.polling import poll_for_new_activities_job

logger = logging.getLogger(__name__)

_SCHEDULED_JOB_ID = "poll_for_new_activities_recurring"


def register_polling_schedule() -> None:
    """Idempotently install the recurring polling schedule into Redis."""
    redis = Redis.from_url(settings.REDIS_URL)
    scheduler = Scheduler(connection=redis)

    # Cancel any existing instances to avoid duplicates after restarts.
    for job in list(scheduler.get_jobs()):
        if job.id == _SCHEDULED_JOB_ID:
            scheduler.cancel(job)

    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc),
        func=poll_for_new_activities_job,
        interval=settings.POLLING_INTERVAL_SECONDS,
        repeat=None,
        id=_SCHEDULED_JOB_ID,
        result_ttl=3600,
    )
    logger.info(
        "Registered polling schedule every %ss as job %s",
        settings.POLLING_INTERVAL_SECONDS,
        _SCHEDULED_JOB_ID,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    register_polling_schedule()
