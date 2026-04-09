import logging
import sys
from rq import Worker, Queue
from app.core.queue import redis_conn

listen = ['default']
logger = logging.getLogger(__name__)

if __name__ == '__main__':
    logger.info("Worker initiating...")
    queues = [Queue(name, connection=redis_conn) for name in listen]
    worker = Worker(queues, connection=redis_conn)
    worker.work()
