import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from app.main import app
from app.config import get_settings, Settings


def override_settings():
    return Settings(api_key="test-key")


@pytest.fixture(autouse=True)
def apply_settings_override():
    app.dependency_overrides[get_settings] = override_settings
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_upload_requires_api_key(client):
    resp = client.post("/scan/upload")
    assert resp.status_code == 422  # missing header


def test_upload_rejects_wrong_key(client):
    resp = client.post("/scan/upload", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


def test_upload_returns_job_id(client, tmp_path):
    tif = tmp_path / "scan_001.pnm.tif"
    tif.write_bytes(b"FAKE")
    with patch("app.main.enqueue_job", new_callable=AsyncMock):
        resp = client.post(
            "/scan/upload",
            headers={"x-api-key": "test-key"},
            files=[("files", ("scan_001.pnm.tif", tif.open("rb"), "image/tiff"))],
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "queued"


def test_status_not_found(client):
    resp = client.get("/scan/status/nonexistent", headers={"x-api-key": "test-key"})
    assert resp.status_code == 404


def test_status_wrong_key(client):
    resp = client.get("/scan/status/anything", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401
