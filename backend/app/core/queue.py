from redis import Redis
from rq import Queue
from app.core.config import settings

# Establish Redis connection
# settings.REDIS_URL e.g. "redis://localhost:6379/0"
redis_conn = Redis.from_url(settings.REDIS_URL)

# Create default queue. default_timeout (#264) sets the RQ death-penalty ceiling
# for every job enqueued through this queue: a two-stage coach generation runs well
# past RQ's 180s default, so a slow fuller turn / regeneration was killed before it
# stored its report. The rq-scheduler enqueue_in calls set the same timeout
# explicitly (they create jobs through their own path, not this queue).
queue = Queue(
    'default', connection=redis_conn, default_timeout=settings.RQ_JOB_TIMEOUT_SECONDS
)
