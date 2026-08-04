from fastapi.testclient import TestClient

from marriage_ocr_api.main import create_app


def test_request_id_is_echoed_back_when_valid() -> None:
    client = TestClient(create_app())
    request_id = "f2c73976-2f39-46e9-bc79-376475fef45f"

    response = client.get("/health", headers={"X-Request-ID": request_id})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == request_id


def test_request_id_is_generated_when_invalid() -> None:
    client = TestClient(create_app())

    response = client.get("/health", headers={"X-Request-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] != "not-a-uuid"
