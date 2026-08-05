"""RQ worker entrypoint.

Invoked as `python -m app.worker` so we get a Python startup hook for the
observability layer (Sentry, structured logging) before handing control to
RQ's worker loop.
"""

import logging

from redis import Redis
from rq import Queue, Worker
from rq.worker_pool import WorkerPool

from app.core.config import Settings, settings
from app.core.observability import (
    init_logging,
    init_sentry,
    log_budget_cap_status,
    warn_if_coach_prompt_inert,
    warn_notification_config,
)
from app.core.queue import redis_conn

LISTEN = ("default",)

logger = logging.getLogger(__name__)


def build_worker_runtime(
    settings: Settings, connection: Redis
) -> Worker | WorkerPool:
    """Select the worker runtime from ``WORKER_POOL_SIZE`` (#594).

    Size 1 (default) returns a single in-process ``Worker`` on the "default"
    queue -- byte-identical to the pre-#594 behaviour. Size > 1 returns an RQ
    ``WorkerPool`` of that many forked workers over the same queue. The pool is
    safe to run with the scheduler: each spawned worker runs the embedded
    scheduler (``run_worker`` hardcodes ``with_scheduler=True``) and RQ's
    scheduler takes an exclusive per-queue Redis lock, so only one is ever
    active.
    """
    if settings.WORKER_POOL_SIZE <= 1:
        queues = [Queue(name, connection=connection) for name in LISTEN]
        return Worker(queues, connection=connection)
    return WorkerPool(
        list(LISTEN), connection=connection, num_workers=settings.WORKER_POOL_SIZE
    )


def run_worker_runtime(runtime: Worker | WorkerPool) -> None:
    """Run the selected runtime with the scheduler enabled (blocking).

    A single ``Worker`` runs ``with_scheduler=True`` explicitly; a ``WorkerPool``
    enables the scheduler per spawned worker internally, so ``start()`` takes no
    scheduler flag.
    """
    if isinstance(runtime, WorkerPool):
        runtime.start()
    else:
        runtime.work(with_scheduler=True)


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
    # #795: the worker is the ONLY process that sends notifications, so it is the
    # one that must vouch for its own notification config. Railway env vars are
    # per-service; #609 wired this warning into `web` alone, so `OWNER_EMAIL`
    # missing HERE — the config that produced the #795 cross-user leak — booted
    # silently. Worker-scoped: TELEGRAM_BOT_USERNAME is a web-only concern.
    warn_notification_config("worker")
    logger.info(
        "Worker booting; listening on %s (WORKER_POOL_SIZE=%d)",
        ",".join(LISTEN),
        settings.WORKER_POOL_SIZE,
    )
    # with_scheduler (enabled inside run_worker_runtime): the worker-embedded
    # scheduler thread drains the ScheduledJobRegistry — both RQ's retry intervals
    # (#215 PIPELINE_RETRY) and every deferred queue.enqueue_in job (block-complete
    # opener, fuller-turn timer, backfill/reanalyze/import batches). Without it
    # those are stranded forever. Since #123/ADR 0006 there is no separate
    # rq-scheduler process: removing the polling fallback let all deferred jobs
    # move to RQ-native scheduling here. Under a WorkerPool (#594) each spawned
    # worker runs the scheduler; RQ's exclusive per-queue lock keeps one active.
    runtime = build_worker_runtime(settings, redis_conn)
    run_worker_runtime(runtime)


if __name__ == "__main__":
    main()
