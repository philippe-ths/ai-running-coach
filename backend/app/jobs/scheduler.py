"""rq-scheduler bootstrap and long-lived runner.

Running this module as `python -m app.jobs.scheduler` first registers the
recurring polling schedule (idempotent across restarts) and then enters the
rq-scheduler poll loop so scheduled jobs actually fire. One process, one
command — suitable as a standalone process entrypoint.
"""

import logging
from datetime import datetime, timezone

from redis import Redis
from rq_scheduler import Scheduler

from app.core.config import settings
from app.core.observability import init_logging, init_sentry
from app.jobs.polling import poll_for_new_activities_job

logger = logging.getLogger(__name__)

_SCHEDULED_JOB_ID = "poll_for_new_activities_recurring"


def _build_scheduler() -> Scheduler:
    redis = Redis.from_url(settings.REDIS_URL)
    return Scheduler(connection=redis)


def register_polling_schedule(scheduler: Scheduler | None = None) -> None:
    """Idempotently install the recurring polling schedule into Redis."""
    scheduler = scheduler or _build_scheduler()

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


def run() -> None:
    """Register the schedule, then enter the rq-scheduler poll loop."""
    init_logging()
    init_sentry("scheduler")
    scheduler = _build_scheduler()
    register_polling_schedule(scheduler)
    logger.info("Entering rq-scheduler run loop")
    scheduler.run()


if __name__ == "__main__":
    run()
