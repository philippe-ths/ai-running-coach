import sys
from rq import Worker, Queue, Connection
from app.core.queue import redis_conn

listen = ['default']

if __name__ == '__main__':
    with Connection(redis_conn):
        print("Worker initiating...")
        worker = Worker(map(Queue, listen))
        worker.work()
