# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Scan2Pi2OCR connects a Raspberry Pi scanner to a containerized OCR pipeline. A button press triggers `insaned` → `raspi/scan.sh` → `raspi/ocrit.sh`, which POSTs scanned pages to the OCR API. The API runs OCR, optionally classifies with an LLM, then delivers the PDF to one or more destinations.

## Commands

All commands run from `ocr-api/` unless noted.

```bash
# Run tests
python3 -m pytest tests/ -q

# Run a single test
python3 -m pytest tests/test_ocr.py::test_is_blank_page_true -v

# Run tests matching a pattern
python3 -m pytest tests/ -k "tesseract" -v

# Install Python deps
pip3 install --break-system-packages -r requirements.txt
pip3 install --break-system-packages -r requirements-dev.txt

# Start the full stack
docker compose up -d          # from repo root

# Rebuild after code changes
docker compose up -d --build
```

## Architecture

### Request lifecycle

```
POST /scan/upload
  → saves files to tempdir → enqueue_job() → returns {job_id}

worker_loop() (background asyncio task)
  → process_scan()      [ocr.py]      — blank removal, contrast fix, OCR
  → extract_ai_metadata()             — optional LLM classification
  → deliver_*() in parallel           — filesystem / paperless / rclone / mail
  → updates _status[job_id]

GET /scan/status/{job_id}
  → reads _status dict → returns {status, outputs}
```

### OCR pipeline (`ocr-api/app/ocr.py`)

`process_scan()` runs three steps:
1. **Blank page removal** — ImageMagick histogram → skips pages with < 1% black pixels
2. **Contrast cleanup** — ImageMagick brightness-contrast on each page in-place
3. **OCR** — calls `get_backend(settings.ocr_engine).run(pages, language)` → plain text; then `build_searchable_pdf()` creates the PDF

### Pluggable OCR backends (`ocr-api/app/ocr_backends/`)

- `base.py` — `OcrBackend` Protocol: `run(pages: list[Path], language: str) -> str`
- `tesseract.py` — wraps Tesseract CLI subprocess, reads `_ocr_out.txt`
- `paddleocr.py` — PaddleOCR Python API (lazy import; `try/except ImportError` at module level for mockability)
- `gcv.py` — Google Cloud Vision stub (raises `NotImplementedError`)
- `build_pdf.py` — `build_searchable_pdf()` creates PDFs via `fpdf2`: TIF images as pages + invisible white text layer for Ctrl+F searchability
- `__init__.py` — `get_backend(engine: str)` factory with lazy per-branch imports

Switch engine via `OCR_ENGINE=paddleocr` (env var). Default: `tesseract`. Valid values are enforced by `Literal["tesseract", "paddleocr", "gcv"]` in config.

**Known limitation:** all OCR text goes on page 1 of multi-page PDFs. Per-page placement requires `OcrBackend.run()` to return `list[str]` instead of `str`.

### AI metadata (`ocr-api/app/ai_metadata.py`)

When `ENABLE_AI_METADATA=true`, the first ~3000 chars of OCR text are sent to an OpenRouter LLM. Returns `AiMetadata(topic, korrespondent, dokumenttyp, tags, filename_stem)`. The filename_stem drives output file naming (`YYYYMMDD_HHMMSS_<topic>_<korrespondent>.pdf`) and Paperless enrichment. Failures are non-fatal.

### Output destinations (`ocr-api/app/outputs/`)

All `deliver_*()` functions are async, return a dict merged into job status, and are launched concurrently via `asyncio.gather()`. Enabled by `ENABLE_FILESYSTEM`, `ENABLE_PAPERLESS`, `ENABLE_RCLONE`, `ENABLE_MAIL`.

### Configuration (`ocr-api/app/config.py`)

Pydantic `Settings` loaded from `.env`. `get_settings()` is `lru_cache`-wrapped — in tests, override it via `app.dependency_overrides[get_settings] = lambda: Settings(api_key="test")`.

### In-memory job state

`_status` and `_queue` in `worker.py` are module-level globals (no persistence). Jobs are lost on restart. Status values: `queued` → `processing` → `done` / `done_with_errors` / `failed`.

## Test layout

- `tests/test_api.py` — FastAPI routes via `TestClient`
- `tests/test_ocr.py` — `is_blank_page()` + `process_scan()` (mocks backend + build_pdf)
- `tests/test_ocr_backends.py` — Protocol, `build_searchable_pdf`, `TesseractBackend`, `PaddleOcrBackend`, `GoogleCloudVisionBackend`, `get_backend()` factory
- `tests/test_worker.py` — worker queue and job lifecycle
- `tests/test_ai_metadata.py` — prompt building and JSON parsing
- `tests/test_paperless.py` — Paperless delivery and entity lookup

Two tests in `test_paperless.py` are pre-existing failures unrelated to the OCR backend work.

## Key files on the Raspberry Pi side

- `raspi/scan.sh` — triggered by insaned; calls ocrit.sh
- `raspi/ocrit.sh` — POSTs `.pnm` pages to `/scan/upload` via curl, polls `/scan/status/{job_id}`
- `raspi/ocrit.env` — `API_HOST` and `API_KEY` (not committed)
- `insaned/events/scan` — insaned hook that launches scan.sh
