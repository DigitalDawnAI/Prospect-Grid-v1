import os

from redis import Redis
from rq import Worker, Queue

import app  # noqa: F401


def main() -> None:
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL not configured")

    conn = Redis.from_url(redis_url)
    queue = Queue("default", connection=conn)
    Worker([queue], connection=conn).work()


if __name__ == "__main__":
    main()
