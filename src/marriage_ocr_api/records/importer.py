from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

from marriage_ocr_api.records.repositories import create_record_if_missing


def import_records_from_json(session: Session, job_id: UUID, payload_path: Path) -> int:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    created = 0
    for item in records:
        if create_record_if_missing(session, job_id=job_id, payload=item):
            created += 1
    return created
