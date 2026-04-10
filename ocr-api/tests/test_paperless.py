import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.ai_metadata import AiMetadata
from app.config import Settings
from app.outputs.paperless import deliver_paperless, _lookup_or_create


def _make_settings(**kwargs):
    defaults = dict(
        api_key="test-key",
        paperless_url="https://paperless.example.com",
        paperless_token="my-token",
        enable_paperless=True,
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def _mock_sync_response(text="task-uuid-1234"):
    r = MagicMock()
    r.is_error = False
    r.raise_for_status = MagicMock()
    r.text = text
    return r


async def _fake_to_thread(fn):
    """Run the sync function directly in test (no thread)."""
    return fn()


# ---------------------------------------------------------------------------
# Existing behaviour (no AI metadata)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deliver_paperless_calls_api(tmp_path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    settings = _make_settings()

    with patch("app.outputs.paperless.get_settings", return_value=settings), \
         patch("app.outputs.paperless.asyncio.to_thread", side_effect=_fake_to_thread), \
         patch("httpx.Client.post", return_value=_mock_sync_response("task-uuid-1234")):
        result = await deliver_paperless(str(pdf), "myscan")

    assert result["paperless"]["status"] == "ok"
    assert result["paperless"]["task_id"] == "task-uuid-1234"


@pytest.mark.asyncio
async def test_deliver_paperless_correct_url(tmp_path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    settings = _make_settings(
        paperless_url="https://paperless.example.com/",
        paperless_token="secret-token",
    )
    captured = {}

    def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers", {})
        return _mock_sync_response("uuid-5678")

    with patch("app.outputs.paperless.get_settings", return_value=settings), \
         patch("app.outputs.paperless.asyncio.to_thread", side_effect=_fake_to_thread), \
         patch("httpx.Client.post", fake_post):
        result = await deliver_paperless(str(pdf), "myscan")

    assert captured["url"] == "https://paperless.example.com/api/documents/post_document/"
    assert captured["headers"]["Authorization"] == "Token secret-token"


@pytest.mark.asyncio
async def test_deliver_paperless_no_ai_meta(tmp_path):
    """ai_meta=None must not call GET for entity lookup."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    settings = _make_settings()

    with patch("app.outputs.paperless.get_settings", return_value=settings), \
         patch("app.outputs.paperless.asyncio.to_thread", side_effect=_fake_to_thread), \
         patch("httpx.Client.post", return_value=_mock_sync_response()), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
        result = await deliver_paperless(str(pdf), "myscan", ai_meta=None)

    mock_get.assert_not_called()
    assert result["paperless"]["status"] == "ok"


@pytest.mark.asyncio
async def test_deliver_paperless_multipart_contains_title(tmp_path):
    """Verify title is encoded as a multipart field in the upload."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    settings = _make_settings()
    captured = {}

    def fake_post(self, url, **kwargs):
        captured["files"] = kwargs.get("files", [])
        return _mock_sync_response()

    with patch("app.outputs.paperless.get_settings", return_value=settings), \
         patch("app.outputs.paperless.asyncio.to_thread", side_effect=_fake_to_thread), \
         patch("httpx.Client.post", fake_post):
        await deliver_paperless(str(pdf), "my-scan-title")

    field_keys = [f[0] for f in captured["files"]]
    assert "title" in field_keys
    assert "document" in field_keys


# ---------------------------------------------------------------------------
# AI metadata path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deliver_paperless_with_ai_meta_passes_ids(tmp_path):
    """When ai_meta is provided, entity IDs are resolved and sent as form fields."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    ai_meta = AiMetadata(
        topic="Invoice",
        korrespondent="Acme Corp",
        dokumenttyp="Rechnung",
        tags=["finance", "2024"],
        filename_stem="20240101_invoice_acme_corp",
    )
    settings = _make_settings()

    get_resp = MagicMock()
    get_resp.raise_for_status = MagicMock()
    get_resp.json = MagicMock(return_value={"results": [{"id": 7}]})

    captured = {}

    def fake_post_sync(self, url, **kwargs):
        captured["files"] = kwargs.get("files", [])
        return _mock_sync_response("task-ai-123")

    with patch("app.outputs.paperless.get_settings", return_value=settings), \
         patch("app.outputs.paperless.asyncio.to_thread", side_effect=_fake_to_thread), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=get_resp), \
         patch("httpx.Client.post", fake_post_sync):
        result = await deliver_paperless(str(pdf), "invoice", ai_meta=ai_meta)

    assert result["paperless"]["status"] == "ok"
    field_keys = [f[0] for f in captured["files"]]
    assert "correspondent" in field_keys
    assert "document_type" in field_keys
    assert field_keys.count("tags") == 2  # two tags


@pytest.mark.asyncio
async def test_deliver_paperless_with_ai_meta_creates_if_missing(tmp_path):
    """_lookup_or_create auto-creates entities when GET returns empty results."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    ai_meta = AiMetadata(
        topic="Contract",
        korrespondent="New Vendor",
        dokumenttyp="Vertrag",
        tags=[],
        filename_stem="20240101_contract_new_vendor",
    )
    settings = _make_settings()

    get_resp = MagicMock()
    get_resp.raise_for_status = MagicMock()
    get_resp.json = MagicMock(return_value={"results": []})  # not found

    create_resp = MagicMock()
    create_resp.raise_for_status = MagicMock()
    create_resp.json = MagicMock(return_value={"id": 42})

    async def fake_async_post(url, **kwargs):
        return create_resp

    with patch("app.outputs.paperless.get_settings", return_value=settings), \
         patch("app.outputs.paperless.asyncio.to_thread", side_effect=_fake_to_thread), \
         patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=get_resp), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=fake_async_post), \
         patch("httpx.Client.post", return_value=_mock_sync_response("task-xyz")):
        result = await deliver_paperless(str(pdf), "contract", ai_meta=ai_meta)

    assert result["paperless"]["status"] == "ok"


@pytest.mark.asyncio
async def test_deliver_paperless_http_error_raises(tmp_path):
    """An HTTP error response from Paperless propagates as an exception."""
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    settings = _make_settings()
    error_resp = MagicMock()
    error_resp.is_error = True
    error_resp.status_code = 403
    error_resp.text = "Forbidden"
    error_resp.raise_for_status = MagicMock(side_effect=Exception("403 Forbidden"))

    with patch("app.outputs.paperless.get_settings", return_value=settings), \
         patch("app.outputs.paperless.asyncio.to_thread", side_effect=_fake_to_thread), \
         patch("httpx.Client.post", return_value=error_resp):
        with pytest.raises(Exception, match="403"):
            await deliver_paperless(str(pdf), "myscan")


# ---------------------------------------------------------------------------
# _lookup_or_create unit tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lookup_or_create_returns_existing_id():
    client = MagicMock()
    get_resp = MagicMock()
    get_resp.raise_for_status = MagicMock()
    get_resp.json = MagicMock(return_value={"results": [{"id": 99}]})
    client.get = AsyncMock(return_value=get_resp)

    result = await _lookup_or_create(client, "https://pl.example.com", "correspondents", "Acme", {})

    assert result == 99
    client.get.assert_called_once()


@pytest.mark.asyncio
async def test_lookup_or_create_creates_when_not_found():
    client = MagicMock()
    get_resp = MagicMock()
    get_resp.raise_for_status = MagicMock()
    get_resp.json = MagicMock(return_value={"results": []})
    client.get = AsyncMock(return_value=get_resp)

    create_resp = MagicMock()
    create_resp.raise_for_status = MagicMock()
    create_resp.json = MagicMock(return_value={"id": 55})
    client.post = AsyncMock(return_value=create_resp)

    result = await _lookup_or_create(client, "https://pl.example.com", "tags", "finance", {})

    assert result == 55
    client.post.assert_called_once()


@pytest.mark.asyncio
async def test_lookup_or_create_returns_none_on_http_error():
    client = MagicMock()
    client.get = AsyncMock(side_effect=Exception("connection refused"))

    result = await _lookup_or_create(client, "https://pl.example.com", "tags", "broken", {})

    assert result is None

