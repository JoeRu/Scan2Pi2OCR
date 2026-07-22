# Design: Positioned searchable-text layer (both engines, line-level)

**Date:** 2026-07-22
**Status:** Approved

## Goal

Every page of an output PDF — on both OCR engines — gets an invisible text
layer positioned at each recognized line's location, so the PDF is searchable
and copy-pasteable on all pages (not just page 1) with text roughly where it
visually appears.

## Motivation

Today `build_searchable_pdf()` writes all OCR text as one blob onto **page 1
only** (`build_pdf.py:38`, `if i == 0`) using a white-color trick. Multi-page
scans are therefore searchable on page 1 and image-only afterwards. Both
engines already know where each line sits — PaddleOCR returns `rec_boxes`,
Tesseract can emit TSV — but the pipeline discards everything except the flat
text. This feature carries the box coordinates through to the PDF.

Decisions locked during brainstorming:
- **Both engines** get positioned text (Tesseract reworked to TSV).
- **Line/region-level** granularity (not word-level).
- **Extend the custom `fpdf2` builder**, not adopt ocrmypdf (ocrmypdf runs its
  own Tesseract and cannot consume PaddleOCR results; PDF/A remains a separate
  backlog item).

## Architecture

### 1. Data model — new `ocr-api/app/ocr_backends/types.py`

```python
from dataclasses import dataclass, field


@dataclass
class OcrLine:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int   # axis-aligned bounding box, ORIGINAL-image pixel coordinates


@dataclass
class OcrPage:
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Flat text for the .txt sidecar and AI metadata."""
        return "\n".join(line.text for line in self.lines)
```

Coordinates are in the pixel space of the image the backend received, which
equals the TIF page's pixel dimensions (PNG conversion and contrast cleanup
preserve dimensions). `build_pdf` maps px→mm using each page's dpi.

### 2. Contract change — `ocr-api/app/ocr_backends/base.py`

```python
def run(self, pages: list[Path], language: str) -> list[OcrPage]: ...
```

Returns one `OcrPage` per input page, index-aligned with `pages`. Replaces the
current `-> str`. This is the breaking change that ripples through every
backend and `process_scan`.

### 3. PaddleOCR backend — `ocr-api/app/ocr_backends/paddleocr.py`

Per page, after `ocr.predict(..., text_det_limit_type=..., text_det_limit_side_len=...)`
(cap kwargs unchanged), build an `OcrPage`:

- `rec_texts: list[str]`, `rec_boxes: ndarray (N,4) int16` as `[x0,y0,x1,y1]`.
- Zip them into `OcrLine`s, casting numpy ints to `int`.
- Empty / missing results → `OcrPage([])`.

### 4. Tesseract backend — `ocr-api/app/ocr_backends/tesseract.py`

- Change the CLI config word `txt` → `tsv`; read `_OUTPUT_STEM + ".tsv"`.
- TSV columns: `level page_num block_num par_num line_num word_num left top width height conf text`.
- Parse: keep word rows (`level == 5`) with non-empty text and `conf != -1`.
- Group by `page_num`; within a page group words by `(block_num, par_num, line_num)`:
  - line text = words joined by a space in `word_num` order,
  - line box = `min(left)`, `min(top)`, `max(left+width)`, `max(top+height)`.
- Emit one `OcrPage` per input page in `page_num` order (1..N). A page with no
  words yields `OcrPage([])`; the result list length always equals
  `len(pages)`.

### 5. GCV backend — `ocr-api/app/ocr_backends/gcv.py`

Signature updated to `-> list[OcrPage]`; body still raises `NotImplementedError`.

### 6. Pipeline — `ocr-api/app/ocr.py` `process_scan()`

```python
pages_ocr = await loop.run_in_executor(None, functools.partial(backend.run, pages, settings.ocr_language))
flat = "\n\n".join(p.text for p in pages_ocr)     # per-page blocks
...
txt_path.write_text(flat)                          # unchanged .txt behavior
# AI metadata (elsewhere in the flow) receives `flat`, same as before
build_searchable_pdf(pages, pages_ocr, pdf_path)   # new structured signature
```

`build_searchable_pdf` runs in the executor as today. AI-metadata extraction
keeps receiving a flat string, so that path is unchanged.

### 7. PDF builder — `ocr-api/app/ocr_backends/build_pdf.py`

New signature: `build_searchable_pdf(pages: list[Path], pages_ocr: list[OcrPage], output_path: Path)`.

Per `(page_path, ocr_page)` (zipped; a missing `OcrPage` is treated as empty):
- Open image → `w_px, h_px, dpi`; `add_page(format=(w_mm, h_mm))`; draw image
  full-bleed (unchanged math).
- `pdf.set_font("Helvetica")`.
- `with pdf.local_context(text_mode=TextMode.INVISIBLE):` for each line:
  - skip if `text` empty or box height `<= 0`,
  - `h_px = y1 - y0`; font size (pt) = `h_px / dpi_y * 72`; `pdf.set_font_size(max(size, 1))`,
  - `x_mm = x0 / dpi_x * 25.4`,
  - baseline: `baseline_px = y0 + 0.8 * h_px`; `y_mm = baseline_px / dpi_y * 25.4`,
  - `pdf.text(x_mm, y_mm, line.text)`.
- Remove the `if i == 0` page-1-only blob, the white-color trick, and the
  raw-bytearray `FPDFException` fallback entirely.

`TextMode` import: `from fpdf.enums import TextMode`. Keep
`pdf.set_compression(False)` for now (larger but greppable files; enabling
compression is a trivial later change and does not affect searchability).

Text render mode 3 (invisible) is essential: positioned text sits *over* the
scanned image, so the old white-fill approach would be visible over dark
content.

## Non-goals (this iteration)

- Word-level boxes (`return_word_box` / Tesseract word rows).
- Following rotated-text polygons (we use axis-aligned boxes only).
- Exact horizontal width-fitting of the invisible text (search/copy work
  without it; pixel-perfect selection highlighting is a future refinement).
- PDF/A output (separate backlog item, may reuse ocrmypdf/Ghostscript).

## Testing

- `tests/test_ocr_backends.py`:
  - PaddleOCR: mock `predict` to return `rec_texts` + `rec_boxes`; assert
    `run()` → `list[OcrPage]` with correct `OcrLine` text and boxes; empty
    result → `OcrPage([])`. Existing det-limit-kwargs test stays valid.
  - Tesseract: mock `subprocess.run` to drop a fake `_ocr_out.tsv`; assert word
    rows aggregate into lines with union boxes, grouped per `page_num`.
  - `build_searchable_pdf`: pass `list[OcrPage]`; assert extracted/greppable
    text present on multiple pages and rendered in invisible text mode.
- `tests/test_ocr.py`: mock `backend.run` → `list[OcrPage]`; assert flat-text
  derivation for `.txt`, that AI metadata receives the flat string, and that
  `build_searchable_pdf` is called with `pages_ocr`.
- `tests/test_ai_metadata.py`: unchanged (still string-based).
- All backend tests remain hermetic (patch `get_settings` where `run()` reads
  it — see the API_KEY note in project memory).

## Verification

- Unit suite green via `python3 -m pytest tests/ -q` (no env vars).
- Manual: a real multi-page scan through the live stack, then confirm the
  output PDF has positioned text on **every** page (extract per-page text) and
  that the text is not visually rendered over the image. Note: a live run
  delivers to Paperless/OneDrive/email — treat as an explicit, confirmed step.
