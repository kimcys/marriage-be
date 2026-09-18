from __future__ import annotations

from enum import StrEnum


class RecordStatus(StrEnum):
    PENDING_REVIEW = "PENDING_REVIEW"
    APPROVED = "APPROVED"


# Below this, the OCR/Gemini extraction itself signaled low confidence in what
# it read -- a record needs a reviewer's eyes even when every field has a
# value. Kept alongside RecordStatus since it's the other half of "does this
# record need review" (see records/repositories.py::_initial_status_for and
# records/service.py::apply_correction).
LOW_CONFIDENCE_THRESHOLD = 0.70
