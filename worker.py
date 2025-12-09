#!/usr/bin/env python3
from __future__ import annotations

import os

from redis import Redis
from rq import Queue, Worker

from litreel import create_app


def main():
    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    queue_names = [
        name.strip()
        for name in (os.getenv("WORK_QUEUE_NAME", "litreel-tasks")).split(",")
        if name.strip()
    ]
    if not queue_names:
        queue_names = ["litreel-tasks"]

    redis_connection = Redis.from_url(redis_url)
    app = create_app()

    with app.app_context():
        queues = [Queue(name, connection=redis_connection) for name in queue_names]
        worker = Worker(queues, connection=redis_connection)
        worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
