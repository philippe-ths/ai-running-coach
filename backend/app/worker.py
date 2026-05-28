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
    worker.work()


if __name__ == "__main__":
    main()
