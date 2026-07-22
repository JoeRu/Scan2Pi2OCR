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

## PDF/A output

**Problem:** Output is plain PDF-1.3, not PDF/A (archival). Verified missing:
`/OutputIntent` (ICC profile), XMP `/Metadata`, and embedded fonts
(`/BaseFont /Helvetica` is referenced by name, which PDF/A forbids).
Note: this is independent of searchability — the current PDF *is* searchable;
it just isn't archival-compliant.

**Direction:** `fpdf2` can't emit PDF/A directly (no font embedding /
OutputIntent / XMP support). Cleanest path is a post-process conversion step:
- `ocrmypdf` — would also produce proper per-page text for free (could
  subsume the per-page item above), or
- Ghostscript `-dPDFA` convert as a final step in the pipeline.

Adds a system dependency to the Docker image either way. Decide whether to
adopt ocrmypdf wholesale (replaces the fpdf2 text-layer approach) or bolt on
a Ghostscript PDF/A pass after `build_searchable_pdf()`.

**Value:** Medium — nice for archival/compliance; no functional loss today.

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
