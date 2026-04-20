# Pluggable OCR Backends Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hard-coded Tesseract integration with a pluggable `OcrBackend` protocol, add PaddleOCR and a GCV stub, and produce searchable PDFs for all backends via `fpdf2`.

**Architecture:** An `OcrBackend` Protocol defines `run(pages: list[Path], language: str) -> str`. A `get_backend()` factory in `ocr_backends/__init__.py` lazily imports and returns the backend named in `config.ocr_engine`. `build_searchable_pdf()` in `ocr_backends/build_pdf.py` creates a PDF from TIF images with an invisible white text overlay containing the OCR result — making the PDF searchable with any reader. `process_scan()` in `ocr.py` is refactored to use these two pieces; blank-page removal and ImageMagick contrast cleanup are untouched.

**Tech Stack:** Python 3.12, fpdf2, Pillow, paddleocr, paddlepaddle (CPU), pydantic-settings, pytest, unittest.mock

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `ocr-api/app/ocr_backends/__init__.py` | `get_backend()` factory |
| Create | `ocr-api/app/ocr_backends/base.py` | `OcrBackend` Protocol |
| Create | `ocr-api/app/ocr_backends/tesseract.py` | Tesseract text extraction |
| Create | `ocr-api/app/ocr_backends/paddleocr.py` | PaddleOCR text extraction |
| Create | `ocr-api/app/ocr_backends/gcv.py` | GCV stub |
| Create | `ocr-api/app/ocr_backends/build_pdf.py` | `build_searchable_pdf()` |
| Modify | `ocr-api/app/ocr.py` | Use backend + build_pdf; remove `run_tesseract()` |
| Modify | `ocr-api/app/config.py` | Add `ocr_engine`, `gcv_credentials_file`, `gcv_project_id` |
| Modify | `ocr-api/requirements.txt` | Add fpdf2, Pillow, paddleocr, paddlepaddle |
| Modify | `ocr-api/Dockerfile` | Pre-download PaddleOCR models |
| Create | `ocr-api/tests/test_ocr_backends.py` | Tests for all backends + build_pdf |
| Modify | `ocr-api/tests/test_ocr.py` | Update process_scan mock to use new interface |

---

## Task 0: Create the feature branch

**Files:** none

- [ ] **Step 1: Create branch**

```bash
git checkout -b feature/paddleocr
```

Expected: switched to new branch `feature/paddleocr`

---

## Task 1: OcrBackend Protocol

**Files:**
- Create: `ocr-api/app/ocr_backends/__init__.py`
- Create: `ocr-api/app/ocr_backends/base.py`
- Create: `ocr-api/tests/test_ocr_backends.py`

- [ ] **Step 1: Write the failing test**

```python
# ocr-api/tests/test_ocr_backends.py
from pathlib import Path
from typing import runtime_checkable
from app.ocr_backends.base import OcrBackend


def test_ocr_backend_protocol_is_checkable():
    @runtime_checkable
    class _P(OcrBackend): ...  # noqa: E701 — satisfies Protocol

    class Good:
        def run(self, pages: list[Path], language: str) -> str:
            return ""

    class Bad:
        pass

    assert isinstance(Good(), _P)
    assert not isinstance(Bad(), _P)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py::test_ocr_backend_protocol_is_checkable -v
```

Expected: `ModuleNotFoundError: No module named 'app.ocr_backends'`

- [ ] **Step 3: Create the package and Protocol**

```python
# ocr-api/app/ocr_backends/__init__.py
from app.ocr_backends.base import OcrBackend

__all__ = ["OcrBackend", "get_backend"]


def get_backend(engine: str) -> OcrBackend:
    raise NotImplementedError(f"No backend registered for {engine!r}")
```

```python
# ocr-api/app/ocr_backends/base.py
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class OcrBackend(Protocol):
    def run(self, pages: list[Path], language: str) -> str:
        ...
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py::test_ocr_backend_protocol_is_checkable -v
```

Expected: `PASSED`

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr_backends/ ocr-api/tests/test_ocr_backends.py
git commit -m "feat: add OcrBackend protocol and package skeleton"
```

---

## Task 2: build_searchable_pdf()

**Files:**
- Create: `ocr-api/app/ocr_backends/build_pdf.py`
- Modify: `ocr-api/requirements.txt`
- Modify: `ocr-api/tests/test_ocr_backends.py`

- [ ] **Step 1: Add dependencies to requirements.txt**

Replace the contents of `ocr-api/requirements.txt` with:

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
python-multipart==0.0.9
httpx==0.27.0
pydantic-settings==2.2.1
fpdf2==2.7.9
Pillow==10.3.0
```

Install locally for tests:

```bash
cd ocr-api && pip install fpdf2==2.7.9 Pillow==10.3.0 --break-system-packages -q
```

- [ ] **Step 2: Write the failing test**

Add to `ocr-api/tests/test_ocr_backends.py`:

```python
import struct, zlib
from pathlib import Path
from app.ocr_backends.build_pdf import build_searchable_pdf


def _make_minimal_tif(path: Path) -> None:
    """Write a 10×10 white TIFF at 300 DPI so Pillow can open it."""
    from PIL import Image
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    img.save(str(path), dpi=(300, 300))


def test_build_searchable_pdf_creates_file(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_minimal_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], "hello world", out)
    assert out.exists()
    assert out.stat().st_size > 0


def test_build_searchable_pdf_text_in_content(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_minimal_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], "searchable text here", out)
    raw = out.read_bytes()
    # PDF content streams may be compressed; check the uncompressed raw bytes
    # for our text OR check that the PDF contains the text in a readable form
    assert b"searchable" in raw or b"searchable text here" in raw.decode("latin-1", errors="replace").encode()


def test_build_searchable_pdf_multi_page(tmp_path):
    pages = []
    for i in range(3):
        p = tmp_path / f"scan_{i:04d}.pnm.tif"
        _make_minimal_tif(p)
        pages.append(p)
    out = tmp_path / "out.pdf"
    build_searchable_pdf(pages, "multi page text", out)
    content = out.read_bytes().decode("latin-1")
    # PDF should declare 3 pages
    assert content.count("/Page\n") >= 3 or "/Count 3" in content
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py::test_build_searchable_pdf_creates_file tests/test_ocr_backends.py::test_build_searchable_pdf_text_in_content tests/test_ocr_backends.py::test_build_searchable_pdf_multi_page -v
```

Expected: `ModuleNotFoundError: No module named 'app.ocr_backends.build_pdf'`

- [ ] **Step 4: Implement build_searchable_pdf()**

```python
# ocr-api/app/ocr_backends/build_pdf.py
import logging
from pathlib import Path

from fpdf import FPDF
from PIL import Image

logger = logging.getLogger("app.ocr_backends.build_pdf")


def build_searchable_pdf(pages: list[Path], text: str, output_path: Path) -> None:
    """Create a searchable PDF from TIF page images with an invisible text layer."""
    if not pages:
        raise ValueError("pages must not be empty")

    pdf = FPDF()
    pdf.set_auto_page_break(False)

    for i, page_path in enumerate(pages):
        with Image.open(page_path) as img:
            dpi = img.info.get("dpi", (300, 300))
            w_px, h_px = img.size
        dpi_x = dpi[0] if dpi[0] else 300
        dpi_y = dpi[1] if dpi[1] else 300
        w_mm = w_px / dpi_x * 25.4
        h_mm = h_px / dpi_y * 25.4

        pdf.add_page(format=(w_mm, h_mm))
        pdf.image(str(page_path), x=0, y=0, w=w_mm, h=h_mm)

        if i == 0 and text:
            # Invisible white text layer — searchable but not visible
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", size=1)
            pdf.set_xy(0, 0)
            pdf.multi_cell(w=w_mm, h=1, txt=text)
            pdf.set_text_color(0, 0, 0)

    pdf.output(str(output_path))
    logger.info("Searchable PDF written: %s (%d page(s))", output_path.name, len(pages))
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py::test_build_searchable_pdf_creates_file tests/test_ocr_backends.py::test_build_searchable_pdf_text_in_content tests/test_ocr_backends.py::test_build_searchable_pdf_multi_page -v
```

Expected: all 3 `PASSED`

- [ ] **Step 6: Commit**

```bash
git add ocr-api/app/ocr_backends/build_pdf.py ocr-api/requirements.txt
git commit -m "feat: add build_searchable_pdf() using fpdf2"
```

---

## Task 3: TesseractBackend

**Files:**
- Create: `ocr-api/app/ocr_backends/tesseract.py`
- Modify: `ocr-api/tests/test_ocr_backends.py`

- [ ] **Step 1: Write the failing test**

Add to `ocr-api/tests/test_ocr_backends.py`:

```python
import os
from unittest.mock import patch, MagicMock
from app.ocr_backends.tesseract import TesseractBackend


def test_tesseract_run_returns_text(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    expected_text = "Hallo Welt\n"
    txt_file = tmp_path / "_ocr_out.txt"

    def fake_run(cmd, capture_output, text, cwd):
        # tesseract writes the output txt file as a side effect
        (Path(cwd) / "_ocr_out.txt").write_text(expected_text)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        backend = TesseractBackend()
        result = backend.run([page], "deu+eng")

    assert result == expected_text


def test_tesseract_run_writes_scan_list(tmp_path):
    page1 = tmp_path / "scan_0001.pnm.tif"
    page2 = tmp_path / "scan_0002.pnm.tif"
    page1.touch()
    page2.touch()

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.txt").write_text("text")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        TesseractBackend().run([page1, page2], "deu")

    scan_list = (tmp_path / "scan_list.txt").read_text()
    assert "scan_0001.pnm.tif" in scan_list
    assert "scan_0002.pnm.tif" in scan_list


def test_tesseract_run_raises_on_empty_pages():
    with pytest.raises(ValueError, match="No pages"):
        TesseractBackend().run([], "deu")


def test_tesseract_run_raises_on_subprocess_failure(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    with patch("app.ocr_backends.tesseract.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="", stderr="tesseract failed")):
        with pytest.raises(RuntimeError, match="Tesseract failed"):
            TesseractBackend().run([page], "deu")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py -k "tesseract" -v
```

Expected: `ModuleNotFoundError: No module named 'app.ocr_backends.tesseract'`

- [ ] **Step 3: Implement TesseractBackend**

```python
# ocr-api/app/ocr_backends/tesseract.py
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("app.ocr_backends.tesseract")

_OUTPUT_STEM = "_ocr_out"


class TesseractBackend:
    def run(self, pages: list[Path], language: str) -> str:
        if not pages:
            raise ValueError("No pages provided to TesseractBackend")
        tmp_dir = pages[0].parent
        list_file = tmp_dir / "scan_list.txt"
        list_file.write_text("\n".join(p.name for p in pages))

        cmd = [
            "tesseract", list_file.name, _OUTPUT_STEM,
            "--dpi", "300", "--oem", "1",
            "-l", language, "--psm", "1", "txt",
        ]
        logger.info("Running Tesseract on %d page(s), language=%s", len(pages), language)
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp_dir))
        if result.returncode != 0:
            raise RuntimeError(f"Tesseract failed: {result.stderr.strip()}")

        out_file = tmp_dir / f"{_OUTPUT_STEM}.txt"
        text = out_file.read_text()
        logger.info("Tesseract finished, %d chars extracted", len(text))
        return text
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py -k "tesseract" -v
```

Expected: all 4 `PASSED`

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr_backends/tesseract.py
git commit -m "feat: add TesseractBackend"
```

---

## Task 4: Config update + get_backend() factory

**Files:**
- Modify: `ocr-api/app/config.py`
- Modify: `ocr-api/app/ocr_backends/__init__.py`
- Modify: `ocr-api/tests/test_ocr_backends.py`

- [ ] **Step 1: Write the failing tests**

Add to `ocr-api/tests/test_ocr_backends.py`:

```python
from app.ocr_backends import get_backend
from app.ocr_backends.tesseract import TesseractBackend


def test_get_backend_tesseract():
    backend = get_backend("tesseract")
    assert isinstance(backend, TesseractBackend)


def test_get_backend_unknown_raises():
    with pytest.raises(ValueError, match="Unknown OCR engine"):
        get_backend("nonexistent")


def test_get_backend_paddleocr_returns_backend():
    # PaddleOCR is lazily imported; just check we get the right type back
    from app.ocr_backends.paddleocr import PaddleOcrBackend
    backend = get_backend("paddleocr")
    assert isinstance(backend, PaddleOcrBackend)


def test_get_backend_gcv_returns_backend():
    from app.ocr_backends.gcv import GoogleCloudVisionBackend
    backend = get_backend("gcv")
    assert isinstance(backend, GoogleCloudVisionBackend)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py -k "get_backend" -v
```

Expected: `FAILED` — `get_backend` raises `NotImplementedError`

- [ ] **Step 3: Add ocr_engine to config.py**

Replace the contents of `ocr-api/app/config.py`:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    api_key: str

    enable_paperless: bool = False
    paperless_url: str = "https://paperless.jru.me"
    paperless_token: str = ""

    enable_rclone: bool = False
    rclone_target: str = "OneDrive_Joe:scanner/"

    enable_filesystem: bool = False
    output_dir: str = "/ocr-api/output"

    ocr_language: str = "deu+eng+frk"
    ocr_engine: str = "tesseract"
    trash_tmp_files: bool = True

    enable_ai_metadata: bool = False
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-haiku-4.5"
    ai_document_language: str = "de"

    enable_mail: bool = False
    mail_to: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""

    gcv_credentials_file: str = ""
    gcv_project_id: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Implement get_backend() factory**

Replace the contents of `ocr-api/app/ocr_backends/__init__.py`:

```python
from app.ocr_backends.base import OcrBackend

__all__ = ["OcrBackend", "get_backend"]


def get_backend(engine: str) -> OcrBackend:
    if engine == "tesseract":
        from app.ocr_backends.tesseract import TesseractBackend
        return TesseractBackend()
    if engine == "paddleocr":
        from app.ocr_backends.paddleocr import PaddleOcrBackend
        return PaddleOcrBackend()
    if engine == "gcv":
        from app.ocr_backends.gcv import GoogleCloudVisionBackend
        return GoogleCloudVisionBackend()
    raise ValueError(
        f"Unknown OCR engine: {engine!r}. Valid values: 'tesseract', 'paddleocr', 'gcv'."
    )
```

- [ ] **Step 5: Create placeholder stubs so imports resolve**

These will be replaced in later tasks. Create them now so the factory tests pass:

```python
# ocr-api/app/ocr_backends/paddleocr.py  (temporary stub)
from pathlib import Path


class PaddleOcrBackend:
    def run(self, pages: list[Path], language: str) -> str:
        raise NotImplementedError("PaddleOCR backend not yet implemented")
```

```python
# ocr-api/app/ocr_backends/gcv.py  (temporary stub)
from pathlib import Path


class GoogleCloudVisionBackend:
    def run(self, pages: list[Path], language: str) -> str:
        raise NotImplementedError("Google Cloud Vision backend not yet implemented")
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py -k "get_backend" -v
```

Expected: all 4 `PASSED`

- [ ] **Step 7: Commit**

```bash
git add ocr-api/app/config.py ocr-api/app/ocr_backends/__init__.py \
        ocr-api/app/ocr_backends/paddleocr.py ocr-api/app/ocr_backends/gcv.py
git commit -m "feat: add get_backend() factory and ocr_engine config setting"
```

---

## Task 5: Refactor process_scan() in ocr.py

**Files:**
- Modify: `ocr-api/app/ocr.py`
- Modify: `ocr-api/tests/test_ocr.py`

- [ ] **Step 1: Write the failing test for refactored process_scan**

Add to `ocr-api/tests/test_ocr.py`:

```python
import asyncio
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image
from app.config import Settings


def _make_tif(path: Path) -> None:
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    img.save(str(path), dpi=(300, 300))


def _settings_with_engine(engine: str) -> Settings:
    return Settings(api_key="test", ocr_engine=engine, ocr_language="deu")


def test_process_scan_uses_backend(tmp_path):
    tif = tmp_path / "scan_0001.pnm.tif"
    _make_tif(tif)

    mock_backend = MagicMock()
    mock_backend.run.return_value = "extracted text"

    with patch("app.ocr.get_settings", return_value=_settings_with_engine("tesseract")), \
         patch("app.ocr.get_backend", return_value=mock_backend), \
         patch("app.ocr.build_searchable_pdf") as mock_pdf, \
         patch("app.ocr.remove_blank_pages", return_value=[tif]), \
         patch("app.ocr.clean_page"):

        mock_pdf.side_effect = lambda pages, text, path: path.write_bytes(b"%PDF-1.4")

        result = asyncio.run(app_process_scan(str(tmp_path), "output"))

    assert result["pdf"].endswith("output.pdf")
    assert result["txt"].endswith("output.txt")
    assert Path(result["txt"]).read_text() == "extracted text"
    mock_backend.run.assert_called_once()


def app_process_scan(tmp_dir, file_name):
    from app.ocr import process_scan
    return process_scan(tmp_dir, file_name)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ocr-api && python -m pytest tests/test_ocr.py::test_process_scan_uses_backend -v
```

Expected: `FAILED` — `process_scan` currently calls `run_tesseract`, not `get_backend`

- [ ] **Step 3: Refactor ocr.py**

Replace the contents of `ocr-api/app/ocr.py`:

```python
import asyncio
import functools
import logging
import os
import re
import subprocess
from pathlib import Path

from app.config import get_settings
from app.ocr_backends import get_backend
from app.ocr_backends.build_pdf import build_searchable_pdf

logger = logging.getLogger("app.ocr")


def is_blank_page(histogram: str) -> bool:
    """Return True if black/white pixel ratio < 1% (page is blank)."""
    white_match = re.search(r"(\d+):\s*\(255,255,255\)", histogram)
    black_match = re.search(r"(\d+):\s*\(0,0,0\)", histogram)
    white = int(white_match.group(1)) if white_match else 0
    black = int(black_match.group(1)) if black_match else 0
    if white == 0:
        return True
    return (black / white) < 0.01


def _run(cmd: list[str], cwd: str | None = None) -> str:
    logger.debug("Running: %s (cwd=%s)", " ".join(cmd), cwd)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        logger.error("Command failed (rc=%d): %s\nstdout: %s\nstderr: %s",
                     result.returncode, " ".join(cmd), result.stdout.strip(), result.stderr.strip())
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    if result.stderr.strip():
        logger.debug("Command stderr: %s", result.stderr.strip())
    return result.stdout


def remove_blank_pages(tmp_dir: str) -> list[str]:
    """Move blank pages to blanks/ subdirectory. Return list of remaining page paths."""
    blanks_dir = os.path.join(tmp_dir, "blanks")
    os.makedirs(blanks_dir, exist_ok=True)
    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))
    logger.info("Blank page check: found %d page(s)", len(pages))
    kept = []
    for page in pages:
        histogram = _run([
            "convert", str(page),
            "-threshold", "50%",
            "-format", "%c",
            "histogram:info:-",
        ])
        if is_blank_page(histogram):
            logger.info("  Blank page detected, skipping: %s", page.name)
            os.rename(page, os.path.join(blanks_dir, page.name))
        else:
            logger.debug("  Page kept: %s", page.name)
            kept.append(str(page))
    logger.info("Blank page removal done: %d kept, %d removed", len(kept), len(pages) - len(kept))
    return kept


def clean_page(page_path: str) -> None:
    """Apply brightness-contrast correction in-place."""
    logger.debug("Cleaning page: %s", os.path.basename(page_path))
    _run(["convert", page_path, "-brightness-contrast", "1x40%", page_path])


async def process_scan(tmp_dir: str, file_name: str) -> dict:
    """Full OCR pipeline. Returns dict with paths to output files."""
    loop = asyncio.get_running_loop()
    settings = get_settings()

    logger.info("OCR pipeline start: tmp_dir=%s name=%r engine=%s",
                tmp_dir, file_name, settings.ocr_engine)

    logger.info("Step 1/3: Blank page removal")
    await loop.run_in_executor(None, remove_blank_pages, tmp_dir)

    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))
    if not pages:
        raise RuntimeError("No pages remaining after blank page removal – nothing to OCR.")

    logger.info("Step 2/3: Contrast cleanup on %d page(s)", len(pages))
    for page in pages:
        await loop.run_in_executor(None, clean_page, str(page))

    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))

    logger.info("Step 3/3: OCR (%s)", settings.ocr_engine)
    backend = get_backend(settings.ocr_engine)
    text = await loop.run_in_executor(
        None,
        functools.partial(backend.run, pages, settings.ocr_language),
    )

    pdf_path = Path(tmp_dir) / f"{file_name}.pdf"
    txt_path = Path(tmp_dir) / f"{file_name}.txt"

    await loop.run_in_executor(
        None,
        functools.partial(build_searchable_pdf, pages, text, pdf_path),
    )
    txt_path.write_text(text)

    logger.info("OCR pipeline done: %s.{{pdf,txt}}", file_name)
    return {
        "pdf": str(pdf_path),
        "txt": str(txt_path),
        "file_name": file_name,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd ocr-api && python -m pytest tests/test_ocr.py -v
```

Expected: all tests `PASSED` (existing blank-page tests still pass, new process_scan test passes)

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr.py ocr-api/tests/test_ocr.py
git commit -m "refactor: use pluggable backend + build_searchable_pdf in process_scan"
```

---

## Task 6: PaddleOCR backend (real implementation)

**Files:**
- Modify: `ocr-api/app/ocr_backends/paddleocr.py`
- Modify: `ocr-api/requirements.txt`
- Modify: `ocr-api/tests/test_ocr_backends.py`

- [ ] **Step 1: Add PaddleOCR dependencies**

Append to `ocr-api/requirements.txt`:

```
paddleocr==2.8.1
paddlepaddle==2.6.1
```

Install locally for tests:

```bash
cd ocr-api && pip install paddleocr==2.8.1 paddlepaddle==2.6.1 --break-system-packages -q
```

> Note: PaddleOCR is large (~500 MB with models). If the install is too slow for the build environment, use `paddleocr` without pinning paddlepaddle and let it resolve. GPU environments should use `paddlepaddle-gpu` instead.

- [ ] **Step 2: Write the failing tests**

Add to `ocr-api/tests/test_ocr_backends.py`:

```python
from unittest.mock import patch, MagicMock
from app.ocr_backends.paddleocr import PaddleOcrBackend


def test_paddleocr_run_returns_text(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()

    fake_result = [[
        [[[0, 0], [10, 0], [10, 10], [0, 10]], ("Hello World", 0.99)],
        [[[0, 12], [10, 12], [10, 22], [0, 22]], ("Second line", 0.95)],
    ]]

    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR:
        mock_ocr_instance = MagicMock()
        mock_ocr_instance.ocr.return_value = fake_result
        MockOCR.return_value = mock_ocr_instance

        backend = PaddleOcrBackend()
        result = backend.run([page], "deu+eng")

    assert "Hello World" in result
    assert "Second line" in result


def test_paddleocr_run_handles_empty_result(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()

    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR:
        mock_ocr_instance = MagicMock()
        mock_ocr_instance.ocr.return_value = [[]]
        MockOCR.return_value = mock_ocr_instance

        result = PaddleOcrBackend().run([page], "deu")

    assert result == ""


def test_paddleocr_language_mapping():
    backend = PaddleOcrBackend()
    assert backend._map_language("deu+eng+frk") == "german"
    assert backend._map_language("eng") == "en"
    assert backend._map_language("deu") == "german"
    assert backend._map_language("frk") == "german"
    assert backend._map_language("unknown") == "en"


def test_paddleocr_run_raises_on_empty_pages():
    with pytest.raises(ValueError, match="No pages"):
        PaddleOcrBackend().run([], "deu")
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py -k "paddleocr" -v
```

Expected: `FAILED` — the stub raises `NotImplementedError`

- [ ] **Step 4: Implement PaddleOcrBackend**

Replace the contents of `ocr-api/app/ocr_backends/paddleocr.py`:

```python
import logging
from pathlib import Path

logger = logging.getLogger("app.ocr_backends.paddleocr")

_LANGUAGE_MAP = {
    "deu": "german",
    "eng": "en",
    "frk": "german",  # Fraktur: no PaddleOCR equivalent, fall back to german
}


class PaddleOcrBackend:
    def run(self, pages: list[Path], language: str) -> str:
        if not pages:
            raise ValueError("No pages provided to PaddleOcrBackend")

        from paddleocr import PaddleOCR  # lazy import — heavy dependency

        lang = self._map_language(language)
        logger.info("Running PaddleOCR on %d page(s), mapped language=%s", len(pages), lang)
        ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

        page_texts: list[str] = []
        for page in pages:
            result = ocr.ocr(str(page), cls=True)
            if not result or not result[0]:
                page_texts.append("")
                continue
            lines = [line[1][0] for line in result[0] if line and line[1]]
            page_texts.append("\n".join(lines))

        text = "\n\n".join(t for t in page_texts if t)
        logger.info("PaddleOCR finished, %d chars extracted", len(text))
        return text

    def _map_language(self, language: str) -> str:
        for code in language.split("+"):
            if code in _LANGUAGE_MAP:
                return _LANGUAGE_MAP[code]
        return "en"
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py -k "paddleocr" -v
```

Expected: all 4 `PASSED`

- [ ] **Step 6: Commit**

```bash
git add ocr-api/app/ocr_backends/paddleocr.py ocr-api/requirements.txt
git commit -m "feat: implement PaddleOcrBackend with lazy import and language mapping"
```

---

## Task 7: GoogleCloudVisionBackend stub

**Files:**
- Modify: `ocr-api/app/ocr_backends/gcv.py`
- Modify: `ocr-api/tests/test_ocr_backends.py`

- [ ] **Step 1: Write the failing test**

Add to `ocr-api/tests/test_ocr_backends.py`:

```python
from app.ocr_backends.gcv import GoogleCloudVisionBackend


def test_gcv_stub_raises_not_implemented(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    with pytest.raises(NotImplementedError, match="not yet implemented"):
        GoogleCloudVisionBackend().run([page], "deu")


def test_gcv_stub_raises_on_empty_pages():
    with pytest.raises((ValueError, NotImplementedError)):
        GoogleCloudVisionBackend().run([], "deu")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py -k "gcv" -v
```

Expected: `FAILED` — current stub raises `NotImplementedError` with wrong message

- [ ] **Step 3: Implement the stub**

Replace the contents of `ocr-api/app/ocr_backends/gcv.py`:

```python
import logging
from pathlib import Path

logger = logging.getLogger("app.ocr_backends.gcv")


class GoogleCloudVisionBackend:
    def run(self, pages: list[Path], language: str) -> str:
        raise NotImplementedError(
            "Google Cloud Vision backend is not yet implemented. "
            "Set OCR_ENGINE=tesseract or OCR_ENGINE=paddleocr in your environment."
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd ocr-api && python -m pytest tests/test_ocr_backends.py -k "gcv" -v
```

Expected: both `PASSED`

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr_backends/gcv.py
git commit -m "feat: add GoogleCloudVisionBackend stub with helpful error message"
```

---

## Task 8: Update Dockerfile

**Files:**
- Modify: `ocr-api/Dockerfile`

- [ ] **Step 1: Update the Dockerfile**

Replace the contents of `ocr-api/Dockerfile`:

```dockerfile
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-deu \
    tesseract-ocr-eng \
    tesseract-ocr-frk \
    imagemagick \
    rclone \
    mutt \
    msmtp \
    msmtp-mta \
    ca-certificates \
    curl \
    bc \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# ImageMagick: allow read/write of TIF files in /tmp
RUN sed -i 's|<policy domain="coder" rights="none" pattern="TIFF"|<policy domain="coder" rights="read|write" pattern="TIFF"|g' \
        /etc/ImageMagick-6/policy.xml 2>/dev/null || true

WORKDIR /ocr-api
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# Pre-download PaddleOCR models so first-run latency is avoided
# This adds ~600 MB to the image; remove this block if image size matters more than cold-start speed
RUN python3 -c "\
from paddleocr import PaddleOCR; \
PaddleOCR(lang='german', show_log=False); \
PaddleOCR(lang='en', show_log=False)" || true

COPY app/ /ocr-api/app/

RUN addgroup --system ocr && adduser --system --ingroup ocr --home /ocr-api ocr \
    && mkdir -p /ocr-api/.config/msmtp /ocr-api/.config/rclone \
    && chown -R ocr:ocr /ocr-api
USER ocr

COPY --chown=ocr:ocr entrypoint.sh /ocr-api/entrypoint.sh
RUN chmod +x /ocr-api/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/ocr-api/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Commit**

```bash
git add ocr-api/Dockerfile
git commit -m "feat: add PaddleOCR and fpdf2 deps to Dockerfile, pre-download models"
```

---

## Task 9: Full test suite verification

- [ ] **Step 1: Run the complete test suite**

```bash
cd ocr-api && python -m pytest tests/ -v
```

Expected: all tests pass. If any fail, fix the issue before proceeding — do not skip tests.

- [ ] **Step 2: Confirm no old tesseract references remain in ocr.py**

```bash
grep -n "run_tesseract\|tesseract" ocr-api/app/ocr.py
```

Expected: zero matches (Tesseract logic now lives in `ocr_backends/tesseract.py`).

- [ ] **Step 3: Final commit if any fixups were needed**

```bash
git add -p  # stage only relevant changes
git commit -m "fix: address test suite issues after backend refactor"
```

---

## Switching engines at runtime

To test PaddleOCR, set the environment variable before starting the container:

```bash
OCR_ENGINE=paddleocr docker compose up ocr-api
```

Or add to `.env`:

```
OCR_ENGINE=paddleocr
```

To revert to Tesseract:

```bash
OCR_ENGINE=tesseract  # or remove the variable — tesseract is the default
```
