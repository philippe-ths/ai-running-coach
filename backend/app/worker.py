"""RQ worker entrypoint.

Invoked as `python -m app.worker` so we get a Python startup hook for the
observability layer (Sentry, structured logging) before handing control to
RQ's worker loop.
"""

import logging

from rq import Queue, Worker

from app.core.observability import init_logging, init_sentry, warn_if_coach_prompt_inert
from app.core.queue import redis_conn

LISTEN = ("default",)

logger = logging.getLogger(__name__)


def main() -> None:
    init_logging()
    init_sentry("worker")
    warn_if_coach_prompt_inert()
    logger.info("Worker booting; listening on %s", ",".join(LISTEN))
    queues = [Queue(name, connection=redis_conn) for name in LISTEN]
    worker = Worker(queues, connection=redis_conn)
    # with_scheduler: the worker-embedded scheduler thread drains the
    # ScheduledJobRegistry — both RQ's retry intervals (#215 PIPELINE_RETRY) and
    # every deferred queue.enqueue_in job (block-complete opener, fuller-turn timer,
    # backfill/reanalyze/import batches). Without it those are stranded forever.
    # Since #123/ADR 0006 there is no separate rq-scheduler process: removing the
    # polling fallback let all deferred jobs move to RQ-native scheduling here.
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
