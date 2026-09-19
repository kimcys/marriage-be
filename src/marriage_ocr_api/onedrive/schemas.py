from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from marriage_ocr_api.batches.status import DocumentType


class OneDriveLinkCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"url": "https://1drv.ms/f/s!AbCdEfGhIjKlMnOp"},
            ]
        }
    )

    url: str


class SkippedFileClassifyRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"filename": "image00001.jpg", "document_type": "HANDWRITTEN_REGISTER"},
            ]
        }
    )

    filename: str
    document_type: DocumentType
