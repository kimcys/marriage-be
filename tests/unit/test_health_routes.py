from fastapi.testclient import TestClient

from marriage_ocr_api.main import create_app


def test_health_route_is_liveness_only() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "Marriage OCR API"}
