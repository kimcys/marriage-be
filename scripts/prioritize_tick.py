"""Move the newest queued Celery task of one kind to the front of the queue.

DRY RUN BY DEFAULT. Same idea as prioritize_jobs.py (workers BRPOP from the
right end, so the right end is "next"), for tasks that have no job id -- by
default gemini_batch_tick, which submits prepared jobs to Gemini and finishes
completed batches. Only reorders; it can't interrupt a task already running.

  docker compose --env-file .env.production exec -T api \
      python - [--task gemini_batch_tick] [--execute] < scripts/prioritize_tick.py
"""

from __future__ import annotations

import argparse
import json
import os

QUEUE = "ocr_jobs"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", default="gemini_batch_tick", help="task name suffix to look for")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    import redis

    client = redis.Redis.from_url(os.environ.get("VALKEY_URL") or os.environ["CELERY_BROKER_URL"])
    messages = client.lrange(QUEUE, 0, -1)  # index 0 = newest, last = next to run
    matches = []
    for position, raw in enumerate(messages):
        try:
            task = json.loads(raw)["headers"].get("task", "")
        except (ValueError, KeyError, TypeError):
            continue
        if task.endswith(args.task):
            matches.append((position, raw))
    print(f"queue length {len(messages)}; {len(matches)} queued '{args.task}' task(s)")
    if not matches:
        return
    position, raw = matches[0]
    print(f"newest is {len(messages) - 1 - position} places from the front of the line")
    if not args.execute:
        print("[DRY RUN -- pass --execute to move it to the front]")
        return
    if client.lrem(QUEUE, 1, raw):
        client.rpush(QUEUE, raw)
        print("moved to the front")
    else:
        print("already consumed")


if __name__ == "__main__":
    main()
