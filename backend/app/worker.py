"""RQ worker entrypoint.

Invoked as `python -m app.worker` so we get a Python startup hook for the
observability layer (Sentry, structured logging) before handing control to
RQ's worker loop.
"""

import logging

from rq import Queue, Worker

from app.core.observability import (
    init_logging,
    init_sentry,
    log_budget_cap_status,
    warn_if_coach_prompt_inert,
)
from app.core.queue import redis_conn

LISTEN = ("default",)

logger = logging.getLogger(__name__)


def main() -> None:
    init_logging()
    init_sentry("worker")
    warn_if_coach_prompt_inert()
    # No assert_production_config() here: the preflight guards the web process's
    # fail-closed HTTP settings (CLERK_JWKS_URL, BASIC_AUTH_*), which are web-only
    # (docs/deployment/topology.md). The worker serves no HTTP, so requiring them
    # would crash a correctly-configured worker. Its hard deps (DATABASE_URL) are
    # already required by Settings and crash the boot on their own.
    # The budget cap IS a worker concern, though: reports generate here, so an
    # uncapped prod worker is exactly the spend risk #543/#549 address. Enforcement
    # is a non-fatal safe default in production (budget.production_default_ceiling),
    # not a boot crash -- the #543 guard took prod down on Railway (#549). This
    # only logs the resulting posture.
    log_budget_cap_status()
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
