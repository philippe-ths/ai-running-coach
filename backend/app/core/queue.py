from redis import Redis
from rq import Queue
from app.core.config import settings

# Establish Redis connection
# settings.REDIS_URL e.g. "redis://localhost:6379/0"
redis_conn = Redis.from_url(settings.REDIS_URL)

# Create default queue
queue = Queue('default', connection=redis_conn)
