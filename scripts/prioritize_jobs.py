"""Move already-queued run_ocr_job tasks to the front of the Celery queue.

DRY RUN BY DEFAULT. Celery's Redis transport LPUSHes new tasks and workers
BRPOP from the right end, so the right end is "next". This finds the queued
`run_ocr_job` messages for the given job ids and, with --execute, removes each
one (LREM) and puts the same message back at the right end (RPUSH). Nothing is
duplicated or deleted; a job that is not queued is reported and left alone.

It only reorders the queue: tasks already running keep their worker slot, so a
job still waits for a free slot.

Pipe it into the api container (scripts/ is not in the image):
  docker compose --env-file .env.production exec -T api \
      python - <job-id> [<job-id> ...] [--execute] < scripts/prioritize_jobs.py
"""

from __future__ import annotations

import argparse
import json
import os

QUEUE = "ocr_jobs"
TASK = "marriage_ocr_api.jobs.run_ocr_job"


def _client():
    import redis

    url = os.environ.get("VALKEY_URL") or os.environ["CELERY_BROKER_URL"]
    return redis.Redis.from_url(url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("job_ids", nargs="+")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    wanted = set(args.job_ids)

    client = _client()
    found: dict[str, bytes] = {}
    for raw in client.lrange(QUEUE, 0, -1):
        try:
            headers = json.loads(raw)["headers"]
        except (ValueError, KeyError, TypeError):
            continue
        if headers.get("task") != TASK:
            continue
        for job_id in wanted:
            if job_id in str(headers.get("argsrepr", "")):
                found[job_id] = raw
    print(f"queue length {client.llen(QUEUE)}; {len(found)} of {len(wanted)} job(s) found queued")
    for job_id in sorted(wanted - set(found)):
        print("  not queued (running, finished or never enqueued):", job_id)
    if not args.execute:
        print("[DRY RUN -- pass --execute to move them to the front]")
        return
    moved = 0
    for job_id, raw in found.items():
        if client.lrem(QUEUE, 1, raw):
            client.rpush(QUEUE, raw)
            moved += 1
        else:
            print("  already consumed:", job_id)
    print(f"moved {moved} job(s) to the front; queue length now {client.llen(QUEUE)}")


if __name__ == "__main__":
    main()
