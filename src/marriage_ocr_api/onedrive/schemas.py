from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class OneDriveLinkCreateRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {"url": "https://1drv.ms/f/s!AbCdEfGhIjKlMnOp"},
            ]
        }
    )

    url: str
