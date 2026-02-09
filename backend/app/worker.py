import sys
from rq import Worker, Queue
from app.core.queue import redis_conn

listen = ['default']

if __name__ == '__main__':
    print("Worker initiating...")
    queues = [Queue(name, connection=redis_conn) for name in listen]
    worker = Worker(queues, connection=redis_conn)
    worker.work()
