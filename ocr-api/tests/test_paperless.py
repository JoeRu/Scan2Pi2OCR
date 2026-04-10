import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.config import Settings
from app.outputs.paperless import deliver_paperless


@pytest.mark.asyncio
async def test_deliver_paperless_calls_api(tmp_path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.text = "task-uuid-1234"

    settings = Settings(
        api_key="test-key",
        paperless_url="https://paperless.example.com",
        paperless_token="my-token",
        enable_paperless=True,
    )

    with patch("app.outputs.paperless.get_settings", return_value=settings), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response):
        result = await deliver_paperless(str(pdf), "myscan")

    assert result["paperless"]["status"] == "ok"
    assert result["paperless"]["task_id"] == "task-uuid-1234"


@pytest.mark.asyncio
async def test_deliver_paperless_correct_url(tmp_path):
    pdf = tmp_path / "test.pdf"
    pdf.write_bytes(b"%PDF-1.4 dummy")

    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.text = "uuid-5678"

    settings = Settings(
        api_key="test-key",
        paperless_url="https://paperless.example.com/",  # trailing slash
        paperless_token="secret-token",
        enable_paperless=True,
    )

    with patch("app.outputs.paperless.get_settings", return_value=settings), \
         patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_response) as mock_post:
        result = await deliver_paperless(str(pdf), "myscan")

    called_url = mock_post.call_args[0][0]
    assert called_url == "https://paperless.example.com/api/documents/post_document/"
    called_headers = mock_post.call_args[1]["headers"]
    assert called_headers["Authorization"] == "Token secret-token"
