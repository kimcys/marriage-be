from __future__ import annotations

from pathlib import Path

import yaml

from marriage_ocr_api.jobs.celery_app import MAINTENANCE_QUEUE, celery_app

ROOT = Path(__file__).resolve().parents[2]


def test_scheduler_tasks_run_on_the_maintenance_queue() -> None:
    schedule = celery_app.conf.beat_schedule
    for name in (
        "gemini-batch-tick",
        "recover-stale-ocr-jobs",
        "recover-stale-onedrive-submissions",
        "clean-local-copies",
    ):
        assert schedule[name]["options"] == {"queue": MAINTENANCE_QUEUE}, name
    # Everything else keeps the default queue.
    assert "options" not in schedule["cleanup-stale-exports"]
    assert celery_app.conf.task_default_queue == "ocr_jobs"


def test_every_worker_listens_to_the_maintenance_queue_by_default() -> None:
    # If scheduled tasks are routed to a queue no worker consumes, batches are
    # never submitted or collected -- so the compose default must include it.
    for compose in ("docker-compose.production.yml", "docker-compose.yml"):
        command = yaml.safe_load((ROOT / compose).read_text())["services"]["worker"]["command"][-1]
        assert "-Q ${WORKER_QUEUES:-ocr_jobs,ocr_maintenance}" in command, compose
