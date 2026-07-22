# Positioned Searchable-Text Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every page of the output PDF (both OCR engines) an invisible text layer positioned at each recognized line's location, so all pages are searchable/copyable with text roughly where it appears.

**Architecture:** Introduce a structured OCR result (`OcrLine`/`OcrPage`) carrying per-line bounding boxes in original-image pixels. Change the `OcrBackend.run()` contract from `-> str` to `-> list[OcrPage]`. PaddleOCR maps `rec_texts`+`rec_boxes`; Tesseract switches to TSV output and aggregates words into lines. `build_searchable_pdf()` places each line's text at its scaled box using PDF invisible text mode. `process_scan()` derives flat text (for `.txt` + AI metadata) from the structured result.

**Tech Stack:** Python 3, FastAPI, PaddleOCR 3.4.1, Tesseract CLI, fpdf2 2.7.9, Pillow, pytest.

## Global Constraints

- Work in `ocr-api/`; run tests with `python3 -m pytest tests/ -q` from `ocr-api/` (no env vars — backend tests must stay hermetic; patch `get_settings` where `run()` reads it).
- Box coordinates are axis-aligned `[x0,y0,x1,y1]` in **original-image pixels**.
- `OcrBackend.run(pages, language) -> list[OcrPage]`, one `OcrPage` per input page, index-aligned; result length always equals `len(pages)`.
- `build_searchable_pdf(pages: list[Path], pages_ocr: list[OcrPage], output_path: Path) -> None`.
- Invisible text via `from fpdf.enums import TextMode` + `pdf.local_context(text_mode=TextMode.INVISIBLE)` — NOT the old white-color trick.
- Keep `pdf.set_compression(False)` (greppable output; unchanged).
- Line-level granularity only. Non-goals: word-level, rotated polygons, width-fitting, PDF/A.
- The `.txt` sidecar keeps flat text; AI metadata reads that file (`worker.py:73`) — do not change worker.py or ai_metadata.py.
- Commit trailers used in this repo:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5
  ```

---

## File Structure

- Create `ocr-api/app/ocr_backends/types.py` — `OcrLine`, `OcrPage` (Task 1).
- Modify `ocr-api/app/ocr_backends/base.py` — Protocol return type (Task 2).
- Modify `ocr-api/app/ocr_backends/paddleocr.py` — return `list[OcrPage]` (Task 2).
- Modify `ocr-api/app/ocr_backends/gcv.py` — signature only (Task 2).
- Modify `ocr-api/app/ocr_backends/tesseract.py` — TSV output + parsing (Task 3).
- Modify `ocr-api/app/ocr_backends/build_pdf.py` — positioned invisible text (Task 4).
- Modify `ocr-api/app/ocr.py` — `process_scan` wiring (Task 5).
- Tests: `tests/test_ocr_backends.py`, `tests/test_ocr.py`.

---

### Task 1: OcrLine / OcrPage data model

**Files:**
- Create: `ocr-api/app/ocr_backends/types.py`
- Test: `ocr-api/tests/test_ocr_backends.py`

**Interfaces:**
- Produces: `OcrLine(text: str, x0: int, y0: int, x1: int, y1: int)`;
  `OcrPage(lines: list[OcrLine])` with `.text` property joining line texts by `\n`.

- [ ] **Step 1: Write the failing test**

Append to `ocr-api/tests/test_ocr_backends.py`:

```python
from app.ocr_backends.types import OcrLine, OcrPage


def test_ocrpage_text_joins_lines_with_newline():
    page = OcrPage([OcrLine("first", 0, 0, 10, 5), OcrLine("second", 0, 6, 10, 11)])
    assert page.text == "first\nsecond"


def test_ocrpage_empty_text_is_empty_string():
    assert OcrPage([]).text == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m pytest tests/test_ocr_backends.py::test_ocrpage_text_joins_lines_with_newline -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ocr_backends.types'`.

- [ ] **Step 3: Create the data model**

Create `ocr-api/app/ocr_backends/types.py`:

```python
from dataclasses import dataclass, field


@dataclass
class OcrLine:
    text: str
    x0: int
    y0: int
    x1: int
    y1: int  # axis-aligned bounding box, original-image pixel coordinates


@dataclass
class OcrPage:
    lines: list[OcrLine] = field(default_factory=list)

    @property
    def text(self) -> str:
        """Flat text for the .txt sidecar and AI metadata."""
        return "\n".join(line.text for line in self.lines)
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 -m pytest tests/test_ocr_backends.py -k ocrpage -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr_backends/types.py ocr-api/tests/test_ocr_backends.py
git commit -m "feat(ocr): add OcrLine/OcrPage structured result model

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5"
```

---

### Task 2: PaddleOCR backend returns list[OcrPage]

**Files:**
- Modify: `ocr-api/app/ocr_backends/base.py`
- Modify: `ocr-api/app/ocr_backends/paddleocr.py`
- Modify: `ocr-api/app/ocr_backends/gcv.py`
- Test: `ocr-api/tests/test_ocr_backends.py`

**Interfaces:**
- Consumes: `OcrLine`, `OcrPage` from Task 1.
- Produces: `PaddleOcrBackend.run(pages, language) -> list[OcrPage]`.

- [ ] **Step 1: Update the failing tests**

In `ocr-api/tests/test_ocr_backends.py`, replace the three PaddleOCR tests
(`test_paddleocr_run_returns_text`, `test_paddleocr_run_handles_empty_result`,
`test_paddleocr_run_passes_det_limit_kwargs`) with:

```python
def test_paddleocr_run_returns_pages_with_boxes(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)
    fake_result = [{
        "rec_texts": ["Hello World", "Second line"],
        "rec_boxes": [[10, 20, 100, 40], [10, 50, 120, 70]],
        "rec_scores": [0.99, 0.95],
    }]
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR, \
         patch("app.ocr_backends.paddleocr.get_settings", return_value=Settings(api_key="test")):
        inst = MagicMock()
        inst.predict.return_value = fake_result
        MockOCR.return_value = inst
        pages = PaddleOcrBackend().run([page], "deu+eng")

    assert len(pages) == 1
    assert [l.text for l in pages[0].lines] == ["Hello World", "Second line"]
    first = pages[0].lines[0]
    assert (first.x0, first.y0, first.x1, first.y1) == (10, 20, 100, 40)


def test_paddleocr_run_handles_empty_result(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR, \
         patch("app.ocr_backends.paddleocr.get_settings", return_value=Settings(api_key="test")):
        inst = MagicMock()
        inst.predict.return_value = [{"rec_texts": [], "rec_boxes": None, "rec_scores": []}]
        MockOCR.return_value = inst
        pages = PaddleOcrBackend().run([page], "deu")

    assert len(pages) == 1
    assert pages[0].lines == []


def test_paddleocr_run_passes_det_limit_kwargs(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)
    fake_result = [{"rec_texts": ["x"], "rec_boxes": [[0, 0, 5, 5]], "rec_scores": [0.9]}]
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR, \
         patch("app.ocr_backends.paddleocr.get_settings", return_value=Settings(api_key="test")):
        inst = MagicMock()
        inst.predict.return_value = fake_result
        MockOCR.return_value = inst
        PaddleOcrBackend().run([page], "deu")

    _, kwargs = inst.predict.call_args
    assert kwargs["text_det_limit_type"] == "max"
    assert kwargs["text_det_limit_side_len"] == 1600
```

Note: `Settings(api_key="test")` yields defaults `paddle_det_limit_side_len=1600`, `type="max"`.

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_ocr_backends.py -k paddleocr_run -v`
Expected: FAIL (current `run()` returns a `str`, has no `.lines`).

- [ ] **Step 3: Update the base Protocol**

In `ocr-api/app/ocr_backends/base.py`, add the import and change the return type:

```python
from app.ocr_backends.types import OcrPage
```
and change the `run` signature in the Protocol to:
```python
    def run(self, pages: list[Path], language: str) -> list[OcrPage]: ...
```

- [ ] **Step 4: Update the PaddleOCR backend**

In `ocr-api/app/ocr_backends/paddleocr.py`, add import:
```python
from app.ocr_backends.types import OcrLine, OcrPage
```
Replace the body of `run()` (the page loop through the `return text`) with:

```python
        lang = self._map_language(language)
        logger.info("Running PaddleOCR on %d page(s), mapped language=%s", len(pages), lang)
        settings = get_settings()
        # enable_mkldnn=False: oneDNN triggers a NotImplementedError on some CPUs with PaddlePaddle 3.x
        ocr = PaddleOCR(lang=lang, enable_mkldnn=False)

        result_pages: list[OcrPage] = []
        for page in pages:
            png_path, converted = self._to_png_if_needed(page)
            try:
                results = ocr.predict(
                    str(png_path),
                    use_textline_orientation=True,
                    text_det_limit_type=settings.paddle_det_limit_type,
                    text_det_limit_side_len=settings.paddle_det_limit_side_len,
                )
                result_pages.append(self._to_ocr_page(results))
            finally:
                if converted:
                    png_path.unlink(missing_ok=True)

        total = sum(len(p.lines) for p in result_pages)
        logger.info("PaddleOCR finished, %d line(s) across %d page(s)", total, len(result_pages))
        return result_pages

    @staticmethod
    def _to_ocr_page(results) -> OcrPage:
        if not results:
            return OcrPage([])
        data = results[0]
        rec_texts = data.get("rec_texts") or []
        rec_boxes = data.get("rec_boxes")
        if rec_boxes is None:
            return OcrPage([])
        lines = [
            OcrLine(text=text, x0=int(box[0]), y0=int(box[1]), x1=int(box[2]), y1=int(box[3]))
            for text, box in zip(rec_texts, rec_boxes)
        ]
        return OcrPage(lines)
```

Keep the existing `if not pages: raise ValueError(...)` guard at the top of `run()`, and keep `_to_png_if_needed` / `_map_language` unchanged.

- [ ] **Step 5: Update the GCV stub signature**

In `ocr-api/app/ocr_backends/gcv.py`, import `OcrPage` and change the `run` return annotation to `-> list[OcrPage]` (body still raises `NotImplementedError`).

- [ ] **Step 6: Run PaddleOCR + protocol tests**

Run: `python3 -m pytest tests/test_ocr_backends.py -k "paddleocr or protocol or get_backend" -v`
Expected: PASS. (`test_ocr_backend_protocol_is_checkable` still passes — runtime_checkable Protocols verify method presence, not signatures.)

- [ ] **Step 7: Commit**

```bash
git add ocr-api/app/ocr_backends/base.py ocr-api/app/ocr_backends/paddleocr.py ocr-api/app/ocr_backends/gcv.py ocr-api/tests/test_ocr_backends.py
git commit -m "feat(paddleocr): return per-line boxes as list[OcrPage]

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5"
```

---

### Task 3: Tesseract backend TSV → list[OcrPage]

**Files:**
- Modify: `ocr-api/app/ocr_backends/tesseract.py`
- Test: `ocr-api/tests/test_ocr_backends.py`

**Interfaces:**
- Consumes: `OcrLine`, `OcrPage`.
- Produces: `TesseractBackend.run(pages, language) -> list[OcrPage]`; internal
  `_parse_tsv(tsv: str, page_count: int) -> list[OcrPage]`.

- [ ] **Step 1: Replace the failing tests**

In `ocr-api/tests/test_ocr_backends.py`, replace `test_tesseract_run_returns_text`
and `test_tesseract_run_writes_scan_list` with (keep the two raising tests
unchanged, but note the output file is now `.tsv`):

```python
_TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _tsv_row(level, page, block, par, line, word, left, top, width, height, conf, text):
    return "\t".join(str(v) for v in
                     [level, page, block, par, line, word, left, top, width, height, conf, text])


def test_tesseract_run_returns_pages_with_lines(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    tsv = "\n".join([
        _TSV_HEADER,
        _tsv_row(5, 1, 1, 1, 1, 1, 10, 20, 40, 15, 96, "Hallo"),
        _tsv_row(5, 1, 1, 1, 1, 2, 55, 20, 30, 15, 95, "Welt"),
        _tsv_row(5, 1, 1, 1, 2, 1, 10, 50, 60, 15, 90, "Zeile2"),
    ])

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.tsv").write_text(tsv)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        pages = TesseractBackend().run([page], "deu+eng")

    assert len(pages) == 1
    assert [l.text for l in pages[0].lines] == ["Hallo Welt", "Zeile2"]
    line0 = pages[0].lines[0]
    # union of the two words' boxes: x0=10, y0=20, x1=55+30=85, y1=20+15=35
    assert (line0.x0, line0.y0, line0.x1, line0.y1) == (10, 20, 85, 35)


def test_tesseract_skips_low_conf_and_empty(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    tsv = "\n".join([
        _TSV_HEADER,
        _tsv_row(5, 1, 1, 1, 1, 1, 10, 20, 40, 15, -1, "ghost"),   # conf -1 dropped
        _tsv_row(5, 1, 1, 1, 1, 2, 60, 20, 40, 15, 90, "  "),       # empty dropped
        _tsv_row(4, 1, 1, 1, 1, 0, 0, 0, 0, 0, -1, ""),             # non-word level dropped
        _tsv_row(5, 1, 1, 1, 1, 3, 110, 20, 40, 15, 88, "keep"),
    ])

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.tsv").write_text(tsv)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        pages = TesseractBackend().run([page], "deu")

    assert [l.text for l in pages[0].lines] == ["keep"]


def test_tesseract_returns_one_page_per_input_even_if_blank(tmp_path):
    p1 = tmp_path / "scan_0001.pnm.tif"; p1.touch()
    p2 = tmp_path / "scan_0002.pnm.tif"; p2.touch()
    tsv = "\n".join([_TSV_HEADER, _tsv_row(5, 1, 1, 1, 1, 1, 10, 20, 40, 15, 96, "only")])

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.tsv").write_text(tsv)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        pages = TesseractBackend().run([p1, p2], "deu")

    assert len(pages) == 2
    assert [l.text for l in pages[0].lines] == ["only"]
    assert pages[1].lines == []
```

Also update `test_tesseract_run_raises_on_subprocess_failure` — it stays valid
(returncode=1 path). No `.txt` assertions remain.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_ocr_backends.py -k tesseract -v`
Expected: FAIL (current `run()` returns a `str` from `.txt`).

- [ ] **Step 3: Rewrite the Tesseract backend**

Replace `ocr-api/app/ocr_backends/tesseract.py` with:

```python
import logging
import subprocess
from collections import OrderedDict
from pathlib import Path

from app.ocr_backends.types import OcrLine, OcrPage

logger = logging.getLogger("app.ocr_backends.tesseract")

_OUTPUT_STEM = "_ocr_out"


class TesseractBackend:
    def run(self, pages: list[Path], language: str) -> list[OcrPage]:
        if not pages:
            raise ValueError("No pages provided to TesseractBackend")
        tmp_dir = pages[0].parent
        list_file = tmp_dir / "scan_list.txt"
        list_file.write_text("\n".join(p.name for p in pages))

        cmd = [
            "tesseract", list_file.name, _OUTPUT_STEM,
            "--dpi", "300", "--oem", "1",
            "-l", language, "--psm", "1", "tsv",
        ]
        logger.info("Running Tesseract on %d page(s), language=%s", len(pages), language)
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp_dir))
        if result.returncode != 0:
            raise RuntimeError(f"Tesseract failed: {result.stderr.strip()}")

        tsv = (tmp_dir / f"{_OUTPUT_STEM}.tsv").read_text()
        result_pages = _parse_tsv(tsv, len(pages))
        total = sum(len(p.lines) for p in result_pages)
        logger.info("Tesseract finished, %d line(s) across %d page(s)", total, len(result_pages))
        return result_pages


def _parse_tsv(tsv: str, page_count: int) -> list[OcrPage]:
    # page -> ordered {(block,par,line): [(word_num, text, left, top, width, height)]}
    by_page: dict[int, "OrderedDict[tuple, list]"] = {}
    for row in tsv.splitlines()[1:]:  # skip header
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":  # word-level rows only
            continue
        text = cols[11].strip()
        if not text:
            continue
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if conf < 0:
            continue
        page, block, par, line, word = (int(cols[i]) for i in range(1, 6))
        left, top, width, height = (int(cols[i]) for i in range(6, 10))
        key = (block, par, line)
        by_page.setdefault(page, OrderedDict()).setdefault(key, []).append(
            (word, text, left, top, width, height))

    result: list[OcrPage] = []
    for page in range(1, page_count + 1):
        lines: list[OcrLine] = []
        for words in by_page.get(page, {}).values():
            words.sort(key=lambda w: w[0])
            lines.append(OcrLine(
                text=" ".join(w[1] for w in words),
                x0=min(w[2] for w in words),
                y0=min(w[3] for w in words),
                x1=max(w[2] + w[4] for w in words),
                y1=max(w[3] + w[5] for w in words),
            ))
        result.append(OcrPage(lines))
    return result
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_ocr_backends.py -k tesseract -v`
Expected: PASS (all tesseract tests).

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr_backends/tesseract.py ocr-api/tests/test_ocr_backends.py
git commit -m "feat(tesseract): emit per-line boxes via TSV as list[OcrPage]

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5"
```

---

### Task 4: Positioned invisible text in build_pdf

**Files:**
- Modify: `ocr-api/app/ocr_backends/build_pdf.py`
- Test: `ocr-api/tests/test_ocr_backends.py`

**Interfaces:**
- Consumes: `OcrPage`, `OcrLine`.
- Produces: `build_searchable_pdf(pages: list[Path], pages_ocr: list[OcrPage], output_path: Path) -> None`.

- [ ] **Step 1: Replace the failing build_pdf tests**

In `ocr-api/tests/test_ocr_backends.py`, update the existing build_pdf tests
(`_creates_file`, `_text_in_content`, `_realistic_page`, `_multi_page`) to pass
`list[OcrPage]` and add a positioned-page test. Replace those four with:

```python
def _page(*lines):
    return OcrPage([OcrLine(t, x0, y0, x1, y1) for (t, x0, y0, x1, y1) in lines])


def test_build_searchable_pdf_creates_file(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_minimal_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], [_page(("hello world", 1, 1, 8, 4))], out)
    assert out.exists() and out.stat().st_size > 0


def test_build_searchable_pdf_text_in_content(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_minimal_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], [_page(("searchable", 1, 1, 8, 4))], out)
    assert b"searchable" in out.read_bytes()


def test_build_searchable_pdf_text_on_every_page(tmp_path):
    pages = []
    for i in range(3):
        p = tmp_path / f"scan_{i:04d}.pnm.tif"
        _make_realistic_tif(p)
        pages.append(p)
    ocr = [_page((f"pagetext{i}", 10, 10, 120, 30)) for i in range(3)]
    out = tmp_path / "out.pdf"
    build_searchable_pdf(pages, ocr, out)
    raw = out.read_bytes()
    for i in range(3):
        assert f"pagetext{i}".encode() in raw       # text present on each page
    content = raw.decode("latin-1")
    assert content.count("/Page\n") >= 3 or "/Count 3" in content


def test_build_searchable_pdf_empty_page_ok(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_minimal_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], [OcrPage([])], out)   # no lines -> image-only page
    assert out.exists() and out.stat().st_size > 0
```

(`_make_minimal_tif` and `_make_realistic_tif` already exist in the file.)

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_ocr_backends.py -k build_searchable_pdf -v`
Expected: FAIL (current signature takes a `str`, not `list[OcrPage]`).

- [ ] **Step 3: Rewrite build_pdf**

Replace `ocr-api/app/ocr_backends/build_pdf.py` with:

```python
import logging
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import TextMode
from PIL import Image

from app.ocr_backends.types import OcrPage

logger = logging.getLogger("app.ocr_backends.build_pdf")


def build_searchable_pdf(pages: list[Path], pages_ocr: list[OcrPage], output_path: Path) -> None:
    """Create a searchable PDF: each page image plus an invisible, positioned text layer."""
    if not pages:
        raise ValueError("pages must not be empty")

    pdf = FPDF()
    pdf.set_auto_page_break(False)
    pdf.set_compression(False)

    for i, page_path in enumerate(pages):
        ocr_page = pages_ocr[i] if i < len(pages_ocr) else OcrPage([])

        with Image.open(page_path) as img:
            dpi = img.info.get("dpi", (300, 300))
            w_px, h_px = img.size
        dpi_x = dpi[0] if dpi[0] else 300
        dpi_y = dpi[1] if dpi[1] else 300
        w_mm = w_px / dpi_x * 25.4
        h_mm = h_px / dpi_y * 25.4

        pdf.add_page(format=(w_mm, h_mm))
        pdf.image(str(page_path), x=0, y=0, w=w_mm, h=h_mm)

        pdf.set_font("Helvetica")
        with pdf.local_context(text_mode=TextMode.INVISIBLE):
            for line in ocr_page.lines:
                if not line.text:
                    continue
                line_h_px = line.y1 - line.y0
                if line_h_px <= 0:
                    continue
                pdf.set_font_size(max(line_h_px / dpi_y * 72, 1))
                x_mm = line.x0 / dpi_x * 25.4
                baseline_mm = (line.y0 + 0.8 * line_h_px) / dpi_y * 25.4
                pdf.text(x_mm, baseline_mm, line.text)

    pdf.output(str(output_path))
    logger.info("Searchable PDF written: %s (%d page(s))", output_path.name, len(pages))
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/test_ocr_backends.py -k build_searchable_pdf -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr_backends/build_pdf.py ocr-api/tests/test_ocr_backends.py
git commit -m "feat(build_pdf): positioned invisible text layer on every page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5"
```

---

### Task 5: Wire process_scan to structured results

**Files:**
- Modify: `ocr-api/app/ocr.py`
- Test: `ocr-api/tests/test_ocr.py`

**Interfaces:**
- Consumes: `backend.run() -> list[OcrPage]`; `build_searchable_pdf(pages, pages_ocr, path)`.
- Produces: `.txt` sidecar with flat text; `build_searchable_pdf` called with `pages_ocr`.

- [ ] **Step 1: Update the failing test**

In `ocr-api/tests/test_ocr.py`, add the import near the top:
```python
from app.ocr_backends.types import OcrLine, OcrPage
```
Replace `test_process_scan_uses_backend` with:

```python
def test_process_scan_uses_backend(tmp_path):
    tif = tmp_path / "scan_0001.pnm.tif"
    _make_tif(tif)

    mock_backend = MagicMock()
    mock_backend.run.return_value = [OcrPage([OcrLine("extracted text", 0, 0, 10, 5)])]

    captured = {}

    def fake_pdf(pages, pages_ocr, path):
        captured["pages_ocr"] = pages_ocr
        path.write_bytes(b"%PDF-1.4")

    with patch("app.ocr.get_settings", return_value=_settings_with_engine("tesseract")), \
         patch("app.ocr.get_backend", return_value=mock_backend), \
         patch("app.ocr.build_searchable_pdf", side_effect=fake_pdf), \
         patch("app.ocr.remove_blank_pages"), \
         patch("app.ocr.clean_page"):

        result = asyncio.run(_process_scan(str(tmp_path), "output"))

    assert result["pdf"].endswith("output.pdf")
    assert Path(result["txt"]).read_text() == "extracted text"
    assert captured["pages_ocr"] == mock_backend.run.return_value
    mock_backend.run.assert_called_once()
```

(`test_process_scan_raises_when_all_pages_blank` is unchanged.)

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/test_ocr.py::test_process_scan_uses_backend -v`
Expected: FAIL (current code passes a `str` to `build_searchable_pdf` and writes it directly).

- [ ] **Step 3: Update process_scan**

In `ocr-api/app/ocr.py` `process_scan()`, change the OCR + output section
(currently the `text = await ... backend.run ...` through `txt_path.write_text(text)`)
to:

```python
    logger.info("Step 3/3: OCR (%s)", settings.ocr_engine)
    backend = get_backend(settings.ocr_engine)
    pages_ocr = await loop.run_in_executor(
        None,
        functools.partial(backend.run, pages, settings.ocr_language),
    )
    flat_text = "\n\n".join(page.text for page in pages_ocr)

    pdf_path = Path(tmp_dir) / f"{file_name}.pdf"
    txt_path = Path(tmp_dir) / f"{file_name}.txt"

    await loop.run_in_executor(
        None,
        functools.partial(build_searchable_pdf, pages, pages_ocr, pdf_path),
    )
    txt_path.write_text(flat_text)
```

(Leave the surrounding lines — the `pages` glob, the return dict — unchanged.)

- [ ] **Step 4: Run the file's tests**

Run: `python3 -m pytest tests/test_ocr.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS (the two pre-existing `test_paperless.py` failures noted in CLAUDE.md may remain; no other failures).

- [ ] **Step 6: Commit**

```bash
git add ocr-api/app/ocr.py ocr-api/tests/test_ocr.py
git commit -m "feat(ocr): wire process_scan to structured OcrPage results

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5"
```

---

## Manual verification (maintainer, post-merge)

Not automatable here — needs a rebuild + real scan, and delivers to
Paperless/OneDrive/email (confirmed step):

1. `docker compose up -d --build ocr-api` (source changed; no requirements
   change, so warmup layer is cached).
2. Run a multi-page scan through the pipeline.
3. Confirm the output PDF has extractable text on **every** page and the text
   is not visibly rendered over the image.

---

## Self-Review

- **Spec coverage:** data model (§1)→T1; contract+PaddleOCR (§2,§3)→T2; Tesseract TSV (§4)→T3; GCV (§5)→T2; pipeline (§6)→T5; build_pdf (§7)→T4; testing (§Testing)→per-task; non-goals respected (line-level, axis-aligned, no PDF/A). ✓
- **Placeholders:** none — every code/edit step shows full content. ✓
- **Type consistency:** `OcrLine(text,x0,y0,x1,y1)` and `OcrPage(lines)` used identically across types, backends, build_pdf, and tests; `build_searchable_pdf(pages, pages_ocr, path)` signature matches its call in `process_scan` and every test; `run() -> list[OcrPage]` consistent across base/paddle/tesseract/gcv. ✓
