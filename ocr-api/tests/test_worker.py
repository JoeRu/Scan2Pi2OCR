"""Tests for app.worker — job queuing, processing, and AI metadata integration."""
import asyncio
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import app.worker as worker_mod
from app.ai_metadata import AiMetadata
from app.config import Settings
from app.worker import (
    enqueue_job,
    get_job_status,
    _fetch_paperless_document_types,
    _process_job,
)


def _make_settings(**kwargs):
    defaults = dict(
        api_key="test-key",
        enable_filesystem=False,
        enable_paperless=False,
        enable_rclone=False,
        enable_mail=False,
        enable_ai_metadata=False,
    )
    defaults.update(kwargs)
    return Settings(**defaults)


def _now():
    return datetime.now(timezone.utc)


@pytest.fixture(autouse=True)
def reset_worker_state():
    """Isolate module-level _status dict and _queue between tests."""
    original_status = worker_mod._status.copy()
    original_queue = worker_mod._queue
    worker_mod._status.clear()
    worker_mod._queue = None
    yield
    worker_mod._status.clear()
    worker_mod._status.update(original_status)
    worker_mod._queue = original_queue


# ---------------------------------------------------------------------------
# enqueue_job / get_job_status
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_job_sets_status_queued():
    await enqueue_job("job-1", "/tmp/x", "scan_001", _now())
    assert get_job_status("job-1") == {"status": "queued"}


@pytest.mark.asyncio
async def test_enqueue_job_puts_tuple_on_queue():
    ts = _now()
    await enqueue_job("job-2", "/tmp/y", "scan_002", ts)
    q = worker_mod._get_queue()
    item = q.get_nowait()
    assert item == ("job-2", "/tmp/y", "scan_002", ts)


def test_get_job_status_unknown_returns_none():
    assert get_job_status("does-not-exist") is None


def test_get_job_status_known_returns_dict():
    worker_mod._status["known"] = {"status": "done"}
    assert get_job_status("known") == {"status": "done"}


# ---------------------------------------------------------------------------
# _fetch_paperless_document_types
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_document_types_success():
    settings = _make_settings(
        paperless_url="http://pl.example.com",
        paperless_token="tok",
    )
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"results": [{"name": "Rechnung"}, {"name": "Vertrag"}]})

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=resp):
        result = await _fetch_paperless_document_types(settings)

    assert result == ["Rechnung", "Vertrag"]


@pytest.mark.asyncio
async def test_fetch_document_types_network_error_returns_none():
    settings = _make_settings(
        paperless_url="http://pl.example.com",
        paperless_token="tok",
    )
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=Exception("timeout")):
        result = await _fetch_paperless_document_types(settings)

    assert result is None


# ---------------------------------------------------------------------------
# _process_job — success paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_job_no_outputs_succeeds(tmp_path):
    """With all outputs disabled, job completes with no deliveries."""
    settings = _make_settings()
    ocr_result = {"pdf": str(tmp_path / "out.pdf"), "txt": str(tmp_path / "out.txt")}

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, return_value=ocr_result), \
         patch("shutil.rmtree"):
        await _process_job("j1", str(tmp_path), "scan_001", _now())

    assert worker_mod._status["j1"]["status"] == "done"
    assert worker_mod._status["j1"]["outputs"] == {}


@pytest.mark.asyncio
async def test_process_job_filesystem_delivery(tmp_path):
    settings = _make_settings(enable_filesystem=True)
    ocr_result = {"pdf": str(tmp_path / "out.pdf"), "txt": str(tmp_path / "out.txt")}
    fs_result = {"filesystem": {"status": "ok", "path": "/out/scan.pdf"}}

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, return_value=ocr_result), \
         patch("app.worker.deliver_filesystem", new_callable=AsyncMock, return_value=fs_result), \
         patch("shutil.rmtree"):
        await _process_job("j2", str(tmp_path), "scan_001", _now())

    assert worker_mod._status["j2"]["status"] == "done"
    assert worker_mod._status["j2"]["outputs"]["filesystem"]["status"] == "ok"


@pytest.mark.asyncio
async def test_process_job_delivery_error_recorded(tmp_path):
    """A delivery exception is captured in errors, job ends with done_with_errors."""
    settings = _make_settings(enable_filesystem=True)
    ocr_result = {"pdf": str(tmp_path / "out.pdf"), "txt": str(tmp_path / "out.txt")}

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, return_value=ocr_result), \
         patch("app.worker.deliver_filesystem", new_callable=AsyncMock, side_effect=OSError("disk full")), \
         patch("shutil.rmtree"):
        await _process_job("j3", str(tmp_path), "scan_001", _now())

    status = worker_mod._status["j3"]
    assert status["status"] == "done_with_errors"
    assert "filesystem" in status["errors"]
    assert "disk full" in status["errors"]["filesystem"]


@pytest.mark.asyncio
async def test_process_job_ocr_failure_sets_failed(tmp_path):
    """If OCR raises, the job ends with status 'failed'."""
    settings = _make_settings()

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, side_effect=RuntimeError("tesseract crashed")), \
         patch("shutil.rmtree"):
        await _process_job("j4", str(tmp_path), "scan_001", _now())

    status = worker_mod._status["j4"]
    assert status["status"] == "failed"
    assert "tesseract crashed" in status["error"]


# ---------------------------------------------------------------------------
# _process_job — AI metadata integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_process_job_ai_metadata_renames_file(tmp_path):
    """When AI metadata succeeds, filename_stem replaces the original file_name."""
    ai_meta = AiMetadata(
        topic="Invoice",
        korrespondent="Acme",
        dokumenttyp="Rechnung",
        tags=[],
        filename_stem="20240101_000000_invoice_acme",
    )
    settings = _make_settings(
        enable_ai_metadata=True,
        enable_filesystem=True,
        openrouter_api_key="sk-or-test",
    )
    ocr_result = {"pdf": str(tmp_path / "out.pdf"), "txt": str(tmp_path / "out.txt")}
    fs_result = {"filesystem": {"status": "ok"}}

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, return_value=ocr_result), \
         patch("app.worker.extract_ai_metadata", new_callable=AsyncMock, return_value=ai_meta), \
         patch("app.worker.deliver_filesystem", new_callable=AsyncMock, return_value=fs_result) as mock_fs, \
         patch("shutil.rmtree"):
        await _process_job("j5", str(tmp_path), "scan_001", _now())

    # deliver_filesystem must have been called with the AI-derived stem
    called_name = mock_fs.call_args[0][1]
    assert called_name == "20240101_000000_invoice_acme"
    assert worker_mod._status["j5"]["status"] == "done"


@pytest.mark.asyncio
async def test_process_job_ai_metadata_fallback_uses_timestamp(tmp_path):
    """When AI metadata returns None, filename falls back to YYYYMMDD-HHMM timestamp."""
    settings = _make_settings(
        enable_ai_metadata=True,
        enable_filesystem=True,
        openrouter_api_key="sk-or-test",
    )
    ocr_result = {"pdf": str(tmp_path / "out.pdf"), "txt": str(tmp_path / "out.txt")}
    fs_result = {"filesystem": {"status": "ok"}}
    ts = datetime(2024, 3, 15, 9, 5, 42, tzinfo=timezone.utc)

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, return_value=ocr_result), \
         patch("app.worker.extract_ai_metadata", new_callable=AsyncMock, return_value=None), \
         patch("app.worker.deliver_filesystem", new_callable=AsyncMock, return_value=fs_result) as mock_fs, \
         patch("shutil.rmtree"):
        await _process_job("j6", str(tmp_path), "scan_original", ts)

    called_name = mock_fs.call_args[0][1]
    assert called_name == "20240315-0905"


@pytest.mark.asyncio
async def test_process_job_ai_disabled_uses_timestamp(tmp_path):
    """When AI metadata is disabled, filename is YYYYMMDD-HHMM rather than the scan name."""
    settings = _make_settings(enable_filesystem=True)
    ocr_result = {"pdf": str(tmp_path / "out.pdf"), "txt": str(tmp_path / "out.txt")}
    fs_result = {"filesystem": {"status": "ok"}}
    ts = datetime(2024, 6, 1, 14, 30, 0, tzinfo=timezone.utc)

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, return_value=ocr_result), \
         patch("app.worker.deliver_filesystem", new_callable=AsyncMock, return_value=fs_result) as mock_fs, \
         patch("shutil.rmtree"):
        await _process_job("j-ts", str(tmp_path), "scan_001", ts)

    called_name = mock_fs.call_args[0][1]
    assert called_name == "20240601-1430"


@pytest.mark.asyncio
async def test_process_job_ai_fetches_doc_types_when_paperless_enabled(tmp_path):
    """Document types are fetched from Paperless and passed to extract_ai_metadata."""
    settings = _make_settings(
        enable_ai_metadata=True,
        enable_paperless=True,
        paperless_url="http://pl.example.com",
        paperless_token="tok",
        openrouter_api_key="sk-or-test",
    )
    ocr_result = {"pdf": str(tmp_path / "out.pdf"), "txt": str(tmp_path / "out.txt")}
    paperless_result = {"paperless": {"status": "ok", "task_id": "t1"}}

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, return_value=ocr_result), \
         patch("app.worker._fetch_paperless_document_types",
               new_callable=AsyncMock, return_value=["Rechnung", "Vertrag"]) as mock_fetch, \
         patch("app.worker.extract_ai_metadata", new_callable=AsyncMock, return_value=None), \
         patch("app.worker.deliver_paperless", new_callable=AsyncMock, return_value=paperless_result), \
         patch("shutil.rmtree"):
        await _process_job("j7", str(tmp_path), "scan_001", _now())

    mock_fetch.assert_called_once()


@pytest.mark.asyncio
async def test_process_job_ai_passes_metadata_to_paperless(tmp_path):
    """ai_meta is forwarded to deliver_paperless when AI is enabled."""
    ai_meta = AiMetadata(
        topic="Contract",
        korrespondent="Vendor",
        dokumenttyp="Vertrag",
        tags=["legal"],
        filename_stem="20240101_000000_contract_vendor",
    )
    settings = _make_settings(
        enable_ai_metadata=True,
        enable_paperless=True,
        paperless_url="http://pl.example.com",
        paperless_token="tok",
        openrouter_api_key="sk-or-test",
    )
    ocr_result = {"pdf": str(tmp_path / "out.pdf"), "txt": str(tmp_path / "out.txt")}
    paperless_result = {"paperless": {"status": "ok", "task_id": "t2"}}

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, return_value=ocr_result), \
         patch("app.worker._fetch_paperless_document_types",
               new_callable=AsyncMock, return_value=["Vertrag"]), \
         patch("app.worker.extract_ai_metadata", new_callable=AsyncMock, return_value=ai_meta), \
         patch("app.worker.deliver_paperless",
               new_callable=AsyncMock, return_value=paperless_result) as mock_pl, \
         patch("shutil.rmtree"):
        await _process_job("j8", str(tmp_path), "scan_001", _now())

    _, called_name, called_meta = mock_pl.call_args[0]
    assert called_name == "20240101_000000_contract_vendor"
    assert called_meta is ai_meta
