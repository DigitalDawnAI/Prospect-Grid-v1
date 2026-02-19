"""
RQ worker entrypoint.

Spawns multiple worker processes so campaigns run concurrently.
Each worker picks one job at a time from the queue. With N workers,
N campaigns process simultaneously.

Config (env vars on Railway worker service):
    WORKER_CONCURRENCY  — number of RQ worker processes (default: 5)
    PROCESSING_WORKERS  — threads per campaign for property parallelism (default: 5)
    GEMINI_RPM          — Gemini rate limit; Redis throttle coordinates across all workers
"""

import os
import signal
import sys
import multiprocessing
import logging
import uuid

from redis import Redis
from rq import Worker, Queue
from sqlalchemy import select

import app  # noqa: F401  — registers process_campaign so RQ can find it
from app import SessionLocal, Campaign, process_campaign

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def resume_stuck_campaigns(queue: Queue) -> None:
    """Re-enqueue any campaigns stuck in 'processing' state from a previous worker crash."""
    db = SessionLocal()
    try:
        stuck = db.execute(
            select(Campaign).where(Campaign.status == "processing")
        ).scalars().all()

        if not stuck:
            logger.info("No stuck campaigns found on startup.")
            return

        for campaign in stuck:
            queue.enqueue(process_campaign, str(campaign.id), job_timeout=14400)
            logger.info(f"Auto-resumed stuck campaign {campaign.id} on worker startup")
    except Exception as e:
        logger.error(f"Error checking for stuck campaigns: {e}", exc_info=True)
    finally:
        db.close()


def run_single_worker(worker_id: int) -> None:
    """Run one RQ worker process. Blocks until terminated."""
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL not configured")

    conn = Redis.from_url(redis_url)
    queues = [Queue("default", connection=conn)]

    startup_uid = uuid.uuid4().hex[:6]
    logger.info(f"Worker-{worker_id} starting (PID {os.getpid()})")
    w = Worker(queues, connection=conn, name=f"worker-{worker_id}-{startup_uid}")
    w.work()


def main() -> None:
    concurrency = int(os.getenv("WORKER_CONCURRENCY", "10"))
    logger.info(f"Starting {concurrency} RQ worker process(es)")

    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL not configured")
    startup_conn = Redis.from_url(redis_url)
    startup_queue = Queue("default", connection=startup_conn)
    resume_stuck_campaigns(startup_queue)

    if concurrency <= 1:
        run_single_worker(0)
        return

    processes: list[multiprocessing.Process] = []

    def shutdown(signum, frame):
        logger.info(f"Received signal {signum}, shutting down workers...")
        for p in processes:
            if p.is_alive():
                p.terminate()
        for p in processes:
            p.join(timeout=10)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    for i in range(concurrency):
        p = multiprocessing.Process(target=run_single_worker, args=(i,), daemon=True)
        p.start()
        processes.append(p)
        logger.info(f"Worker-{i} spawned (PID {p.pid})")

    # Monitor and restart crashed workers
    while True:
        for i, p in enumerate(processes):
            if not p.is_alive():
                exit_code = p.exitcode
                logger.warning(
                    f"Worker-{i} (PID {p.pid}) exited with code {exit_code}, restarting..."
                )
                new_p = multiprocessing.Process(
                    target=run_single_worker, args=(i,), daemon=True
                )
                new_p.start()
                processes[i] = new_p
                logger.info(f"Worker-{i} restarted (PID {new_p.pid})")

        try:
            multiprocessing.active_children()
            signal.pause()
        except (InterruptedError, ChildProcessError):
            pass


if __name__ == "__main__":
    main()
