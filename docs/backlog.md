# Backlog

Enhancement ideas captured for later. Not scheduled.

## Per-page searchable text layer

**Problem:** `build_searchable_pdf()` writes all OCR text onto page 1 only
(`ocr-api/app/ocr_backends/build_pdf.py:38`, `if i == 0`). On multi-page
documents, page 1 is searchable but pages 2+ are image-only (no text layer).
This is the "known limitation" noted in CLAUDE.md.

**Direction:** Change the `OcrBackend.run()` contract to return `list[str]`
(one string per page) instead of a single `str`, then have
`build_searchable_pdf()` place each page's text on its own page. Touches:
- `ocr-api/app/ocr_backends/base.py` (Protocol signature)
- `tesseract.py`, `paddleocr.py`, `gcv.py` (return per-page lists)
- `ocr.py` `process_scan()` (pass through the list)
- `build_pdf.py` (loop text per page, drop the `if i == 0` special case)
- tests in `tests/test_ocr_backends.py`, `tests/test_ocr.py`

**Value:** Higher — fixes real searchability loss on every multi-page scan.

## Switch PaddleOCR to the lightweight (mobile) detection model

**Problem:** Even with the detection cap at `PADDLE_DET_LIMIT_SIDE_LEN=960`,
PaddleOCR runs ~2 min/page on CPU — too slow for routine use. The bottleneck is
the default **server** detection model (`PP-OCRv5_server_det`), which is heavy
on CPU.

**Direction:** Pin the lightweight mobile detector (e.g.
`PP-OCRv5_mobile_det`) in the `PaddleOCR(...)` constructor in
`ocr-api/app/ocr_backends/paddleocr.py`. Usually much faster on CPU with only a
small accuracy hit on clean documents. Requires baking the mobile model into
the Docker warmup layer (rebuild) so first-run download doesn't block scans.
Consider making the model name configurable via a Settings/env var.

**Value:** High — directly addresses the ~2 min/page wall-clock that currently
makes PaddleOCR impractical as the default engine.

**Status:** Done (1a0692d). Still too slow on this host and can't read
handwriting; superseded by `OCR_ENGINE=openrouter` (2026-09-16).

## LLM transcript in Paperless full-text search

**Problem:** With `OCR_ENGINE=openrouter`, handwriting is transcribed by the LLM,
but Paperless indexes the PDF text layer (built from Tesseract lines) and
`deliver_paperless` uploads only the PDF, so handwritten text isn't findable
in Paperless search.

**Direction:** After upload, poll the Paperless consume task
(`GET /api/tasks/?task_id=<id>`) for the created document id, then
`PATCH /api/documents/<id>/` with `content=<joined OcrPage.text>`. Needs the
transcript passed from `process_scan()` to `deliver_paperless()`.

**Value:** Medium. Deferred in the 2026-09-16 LLM OCR design.

## Remove PaddleOCR from the image

**Problem:** Once `OCR_ENGINE=openrouter` is the production default, `paddleocr`,
`paddlepaddle`, and the model warmup layer (~600 MB+) are dead weight and slow
down every `docker compose build`.

**Direction:** Drop `paddleocr`/`paddlepaddle` from `requirements.txt` and the
warmup `RUN` from the Dockerfile; remove `paddleocr.py` and its tests, or keep
the backend with its import guard as an optional install.

**Value:** Medium: image size and build time.
