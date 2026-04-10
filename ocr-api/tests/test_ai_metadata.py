import json
import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.ai_metadata import AiMetadata, extract_ai_metadata, sanitize_filename_part, _build_prompt
from app.config import Settings


# ---------------------------------------------------------------------------
# sanitize_filename_part
# ---------------------------------------------------------------------------

def test_sanitize_basic():
    assert sanitize_filename_part("Hello World") == "hello_world"


def test_sanitize_umlauts():
    assert sanitize_filename_part("Müller & Söhne GmbH") == "mueller_soehne_gmbh"


def test_sanitize_umlaut_ae():
    assert sanitize_filename_part("Hände") == "haende"


def test_sanitize_sharp_s():
    assert sanitize_filename_part("Straße") == "strasse"


def test_sanitize_consecutive_underscores():
    assert sanitize_filename_part("foo  bar") == "foo_bar"


def test_sanitize_special_chars():
    result = sanitize_filename_part("foo@bar#baz!")
    assert result == "foo_bar_baz"


def test_sanitize_leading_trailing_underscores():
    result = sanitize_filename_part("  hello  ")
    assert result == "hello"


def test_sanitize_truncation():
    long_str = "a" * 60
    result = sanitize_filename_part(long_str, max_len=40)
    assert len(result) == 40


def test_sanitize_custom_max_len():
    result = sanitize_filename_part("hello world", max_len=5)
    assert result == "hello"


def test_sanitize_empty():
    result = sanitize_filename_part("")
    assert result == ""


def test_sanitize_only_special():
    result = sanitize_filename_part("!@#$%")
    assert result == ""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SCAN_TS = datetime(2024, 3, 15, 10, 30, 0)

BASE_SETTINGS = Settings(
    api_key="test-key",
    enable_ai_metadata=True,
    openrouter_api_key="or-key",
    openrouter_model="test/model",
    ai_document_language="de",
)

VALID_RESPONSE_BODY = {
    "choices": [
        {
            "message": {
                "content": json.dumps({
                    "topic": "Rechnung Gas",
                    "korrespondent": "Stadtwerke München",
                    "dokumenttyp": "Rechnung",
                    "tags": ["energie", "gas"],
                })
            }
        }
    ]
}


def _make_mock_response(body: dict, status_code: int = 200):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = body
    return mock_resp


# ---------------------------------------------------------------------------
# extract_ai_metadata — happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_ai_metadata_valid(tmp_path):
    txt = tmp_path / "scan.txt"
    txt.write_text("Dies ist ein Test-Dokument.", encoding="utf-8")

    mock_resp = _make_mock_response(VALID_RESPONSE_BODY)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        result = await extract_ai_metadata(str(txt), SCAN_TS, BASE_SETTINGS, ["Rechnung", "Vertrag"])

    assert isinstance(result, AiMetadata)
    assert result.topic == "Rechnung Gas"
    assert result.korrespondent == "Stadtwerke München"
    assert result.dokumenttyp == "Rechnung"
    assert result.tags == ["energie", "gas"]
    assert result.filename_stem.startswith("20240315_103000_")
    assert "rechnung_gas" in result.filename_stem
    assert "stadtwerke_muenchen" in result.filename_stem


# ---------------------------------------------------------------------------
# extract_ai_metadata — network exception → returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_ai_metadata_network_error(tmp_path):
    txt = tmp_path / "scan.txt"
    txt.write_text("Some text.", encoding="utf-8")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=httpx_connect_error()):
        result = await extract_ai_metadata(str(txt), SCAN_TS, BASE_SETTINGS)

    assert result is None


def httpx_connect_error():
    import httpx
    return httpx.ConnectError("Connection refused")


# ---------------------------------------------------------------------------
# extract_ai_metadata — malformed JSON → returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_ai_metadata_malformed_json(tmp_path):
    txt = tmp_path / "scan.txt"
    txt.write_text("Some text.", encoding="utf-8")

    bad_body = {"choices": [{"message": {"content": "not valid json{"}}]}
    mock_resp = _make_mock_response(bad_body)

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        result = await extract_ai_metadata(str(txt), SCAN_TS, BASE_SETTINGS)

    assert result is None


# ---------------------------------------------------------------------------
# extract_ai_metadata — missing file → returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_ai_metadata_missing_file():
    result = await extract_ai_metadata("/nonexistent/path.txt", SCAN_TS, BASE_SETTINGS)
    assert result is None


# ---------------------------------------------------------------------------
# extract_ai_metadata — HTTP error response → returns None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_ai_metadata_http_error(tmp_path):
    txt = tmp_path / "scan.txt"
    txt.write_text("Some text.", encoding="utf-8")
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        "401 Unauthorized", request=MagicMock(), response=MagicMock()
    )
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        result = await extract_ai_metadata(str(txt), SCAN_TS, BASE_SETTINGS)
    assert result is None


# ---------------------------------------------------------------------------
# Language-aware prompt: de vs en
# ---------------------------------------------------------------------------
    prompt = _build_prompt("test text", "de", ["Rechnung", "Vertrag"])
    assert "Dokumentenklassifikator" in prompt
    assert "Rechnung, Vertrag" in prompt
    assert "OCR-Text (erste Seite)" in prompt


def test_prompt_en_contains_english_header():
    prompt = _build_prompt("test text", "en", ["Invoice", "Contract"])
    assert "document classifier" in prompt
    assert "Invoice, Contract" in prompt
    assert "OCR text (first page)" in prompt


def test_prompt_de_no_document_types():
    prompt = _build_prompt("text", "de", None)
    assert "keine vorgegeben" in prompt


def test_prompt_en_no_document_types():
    prompt = _build_prompt("text", "en", None)
    assert "none provided" in prompt


def test_prompt_unknown_language_falls_back_to_en():
    prompt = _build_prompt("text", "fr", ["Facture"])
    assert "document classifier" in prompt
    assert "Facture" in prompt


def test_prompt_includes_text():
    prompt = _build_prompt("my unique document content", "en", None)
    assert "my unique document content" in prompt
