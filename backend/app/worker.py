"""RQ worker entrypoint.

Invoked as `python -m app.worker` so we get a Python startup hook for the
observability layer (Sentry, structured logging) before handing control to
RQ's worker loop.
"""

import logging

from rq import Queue, Worker

from app.core.observability import init_logging, init_sentry
from app.core.queue import redis_conn

LISTEN = ("default",)

logger = logging.getLogger(__name__)


def main() -> None:
    init_logging()
    init_sentry("worker")
    logger.info("Worker booting; listening on %s", ",".join(LISTEN))
    queues = [Queue(name, connection=redis_conn) for name in LISTEN]
    worker = Worker(queues, connection=redis_conn)
    # with_scheduler: RQ's retry intervals (#215 PIPELINE_RETRY) park retried jobs
    # in the ScheduledJobRegistry, which only a worker-embedded scheduler thread
    # moves back onto the queue. Without it an interval retry is stranded forever.
    # Independent of the separate rq-scheduler process (polling/fuller timers).
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
