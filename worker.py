import os

from redis import Redis
from rq import Worker, Queue, Connection

import app  # noqa: F401


def main() -> None:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL not configured")

    conn = Redis.from_url(redis_url)
    with Connection(conn):
        worker = Worker([Queue("default")])
        worker.work()


if __name__ == "__main__":
    main()
