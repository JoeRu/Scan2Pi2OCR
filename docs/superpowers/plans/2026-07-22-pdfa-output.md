# PDF/A-2b Output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert every delivered PDF to PDF/A-2b (archival) via a Ghostscript post-pass after the searchable text layer is built, best-effort so a scan is never lost.

**Architecture:** New `pdfa.convert_to_pdfa(pdf_path)` runs `gs -dPDFA=2` with a generated `PDFA_def.ps` that embeds an sRGB ICC profile (for the required `/OutputIntent`) plus `--permit-file-read` (gs 10 SAFER). It converts to a temp file then atomically replaces `pdf_path`; any failure keeps the original. `process_scan()` calls it right after `build_searchable_pdf()`. The Dockerfile gains `ghostscript`.

**Tech Stack:** Python 3, Ghostscript 10.x, fpdf2, pypdf (dev), pytest.

## Global Constraints

- Work in `ocr-api/`; run tests `python3 -m pytest tests/ -q` from `ocr-api/` (no env vars; hermetic).
- PDF/A conformance level: **PDF/A-2b** (`-dPDFA=2`).
- Always on — no enable/disable setting. Conversion is **best-effort**: missing ICC, gs non-zero exit, or empty output → keep the original searchable PDF, log a warning, do NOT raise.
- gs cannot read and write the same file → convert to a temp file, then `os.replace` over the original.
- gs argv must include `-dPDFA=2` and `--permit-file-read=<icc>`.
- ICC profile discovered from the system (ships with the `ghostscript` apt package); do not hardcode a single path without a fallback glob.
- No new Python runtime deps (pypdf is dev-only, already added).
- Commit trailers:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5
  ```

---

## File Structure

- Create `ocr-api/app/ocr_backends/pdfa.py` — `convert_to_pdfa` + helpers (Task 1).
- Create `ocr-api/tests/test_pdfa.py` — hermetic + integration tests (Task 1).
- Modify `ocr-api/app/ocr.py` — call `convert_to_pdfa` in `process_scan` (Task 2).
- Modify `ocr-api/Dockerfile` — add `ghostscript` (Task 2).
- Modify `ocr-api/tests/test_ocr.py` — patch/assert the conversion call (Task 2).
- Modify `docs/backlog.md` — remove the completed PDF/A item (Task 2).

---

### Task 1: pdfa.convert_to_pdfa module

**Files:**
- Create: `ocr-api/app/ocr_backends/pdfa.py`
- Test: `ocr-api/tests/test_pdfa.py`

**Interfaces:**
- Produces: `convert_to_pdfa(pdf_path: Path) -> None` (in-place, best-effort);
  module-level `_find_icc_profile() -> str | None` (patched in tests).

- [ ] **Step 1: Write the failing hermetic tests**

Create `ocr-api/tests/test_pdfa.py`:

```python
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.ocr_backends.pdfa import convert_to_pdfa


def _write_pdf(p: Path, content: bytes = b"%PDF-1.7\noriginal\n") -> None:
    p.write_bytes(content)


def _output_file_arg(cmd) -> str:
    return next(a.split("=", 1)[1] for a in cmd if a.startswith("-sOutputFile="))


def test_convert_success_replaces_file(tmp_path):
    pdf = tmp_path / "out.pdf"
    _write_pdf(pdf)

    def fake_run(cmd, capture_output, text):
        Path(_output_file_arg(cmd)).write_bytes(b"%PDF-1.7\nconverted-pdfa\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.pdfa._find_icc_profile", return_value="/x/srgb.icc"), \
         patch("app.ocr_backends.pdfa.subprocess.run", side_effect=fake_run):
        convert_to_pdfa(pdf)

    assert pdf.read_bytes() == b"%PDF-1.7\nconverted-pdfa\n"


def test_convert_gs_failure_keeps_original(tmp_path):
    pdf = tmp_path / "out.pdf"
    _write_pdf(pdf)
    with patch("app.ocr_backends.pdfa._find_icc_profile", return_value="/x/srgb.icc"), \
         patch("app.ocr_backends.pdfa.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="", stderr="boom")):
        convert_to_pdfa(pdf)  # must not raise
    assert pdf.read_bytes() == b"%PDF-1.7\noriginal\n"


def test_convert_missing_icc_skips(tmp_path):
    pdf = tmp_path / "out.pdf"
    _write_pdf(pdf)
    with patch("app.ocr_backends.pdfa._find_icc_profile", return_value=None), \
         patch("app.ocr_backends.pdfa.subprocess.run") as mrun:
        convert_to_pdfa(pdf)
    mrun.assert_not_called()
    assert pdf.read_bytes() == b"%PDF-1.7\noriginal\n"


def test_convert_gs_command_has_pdfa_flags(tmp_path):
    pdf = tmp_path / "out.pdf"
    _write_pdf(pdf)
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        Path(_output_file_arg(cmd)).write_bytes(b"%PDF-1.7\nx\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.pdfa._find_icc_profile", return_value="/x/srgb.icc"), \
         patch("app.ocr_backends.pdfa.subprocess.run", side_effect=fake_run):
        convert_to_pdfa(pdf)

    assert "-dPDFA=2" in captured["cmd"]
    assert "--permit-file-read=/x/srgb.icc" in captured["cmd"]


@pytest.mark.skipif(shutil.which("gs") is None, reason="ghostscript not installed")
def test_convert_real_produces_pdfa(tmp_path):
    from PIL import Image

    from app.ocr_backends.build_pdf import build_searchable_pdf
    from app.ocr_backends.types import OcrLine, OcrPage

    tif = tmp_path / "scan_0001.pnm.tif"
    Image.new("RGB", (2480, 3508), (255, 255, 255)).save(str(tif), dpi=(300, 300))
    pdf = tmp_path / "out.pdf"
    build_searchable_pdf(
        [tif], [OcrPage([OcrLine("• Gesamt 1.201,90 € –", 200, 300, 1400, 360)])], pdf)

    convert_to_pdfa(pdf)

    raw = pdf.read_bytes()
    assert b"OutputIntent" in raw and b"pdfaid" in raw
    from pypdf import PdfReader
    text = PdfReader(str(pdf)).pages[0].extract_text()
    assert "€" in text and "•" in text
```

- [ ] **Step 2: Run to verify failure**

Run (from `ocr-api/`): `python3 -m pytest tests/test_pdfa.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.ocr_backends.pdfa'`.

- [ ] **Step 3: Create the module**

Create `ocr-api/app/ocr_backends/pdfa.py`:

```python
import logging
import os
import subprocess
from glob import glob
from pathlib import Path

logger = logging.getLogger("app.ocr_backends.pdfa")

_ICC_CANDIDATES = (
    "/usr/share/color/icc/ghostscript/srgb.icc",
    "/usr/share/color/icc/ghostscript/sRGB.icc",
)

_PDFA_DEF_TEMPLATE = """%!
[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark
[{{icc_PDFA}} << /N 3 >> /PUT pdfmark
[{{icc_PDFA}} ({icc}) (r) file /PUT pdfmark
[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFA}} <<
  /Type /OutputIntent /S /GTS_PDFA1
  /DestOutputProfile {{icc_PDFA}}
  /OutputConditionIdentifier (sRGB)
>> /PUT pdfmark
[{{Catalog}} <</OutputIntents [ {{OutputIntent_PDFA}} ]>> /PUT pdfmark
"""


def _find_icc_profile() -> str | None:
    for path in _ICC_CANDIDATES:
        if Path(path).is_file():
            return path
    for path in sorted(glob("/usr/share/ghostscript/*/iccprofiles/srgb.icc")):
        return path
    return None


def convert_to_pdfa(pdf_path: Path) -> None:
    """Convert pdf_path in place to PDF/A-2b via Ghostscript.

    Best-effort: on any failure (no ICC profile, gs error, empty output) the
    original searchable PDF is left untouched and a warning is logged.
    """
    icc = _find_icc_profile()
    if icc is None:
        logger.warning("No sRGB ICC profile found; skipping PDF/A conversion of %s", pdf_path.name)
        return

    tmp_dir = pdf_path.parent
    def_ps = tmp_dir / f"{pdf_path.stem}_pdfa_def.ps"
    out_pdf = tmp_dir / f"{pdf_path.stem}_pdfa.pdf"
    def_ps.write_text(_PDFA_DEF_TEMPLATE.format(icc=icc))

    cmd = [
        "gs", "-dPDFA=2", "-dBATCH", "-dNOPAUSE", "-dNOOUTERSAVE",
        "-dPDFACompatibilityPolicy=1", "-sColorConversionStrategy=RGB",
        "-sDEVICE=pdfwrite", f"--permit-file-read={icc}",
        f"-sOutputFile={out_pdf}", str(def_ps), str(pdf_path),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and out_pdf.exists() and out_pdf.stat().st_size > 0:
            os.replace(str(out_pdf), str(pdf_path))
            logger.info("PDF/A-2b conversion done: %s", pdf_path.name)
        else:
            logger.warning(
                "PDF/A conversion failed (rc=%s); keeping original %s: %s",
                result.returncode, pdf_path.name, result.stderr.strip()[:200])
    except Exception as exc:  # never fail the job over archival conversion
        logger.warning("PDF/A conversion error; keeping original %s: %s", pdf_path.name, exc)
    finally:
        def_ps.unlink(missing_ok=True)
        out_pdf.unlink(missing_ok=True)
```

- [ ] **Step 4: Run to verify pass**

Run (from `ocr-api/`): `python3 -m pytest tests/test_pdfa.py -v`
Expected: 4 hermetic tests PASS; `test_convert_real_produces_pdfa` PASSES if `gs` is installed (it is on this host) or SKIPS otherwise.

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr_backends/pdfa.py ocr-api/tests/test_pdfa.py
git commit -m "feat(pdfa): convert_to_pdfa via Ghostscript (PDF/A-2b, best-effort)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5"
```

---

### Task 2: Wire into pipeline, add dependency, retire backlog item

**Files:**
- Modify: `ocr-api/app/ocr.py`
- Modify: `ocr-api/Dockerfile`
- Modify: `ocr-api/tests/test_ocr.py`
- Modify: `docs/backlog.md`

**Interfaces:**
- Consumes: `convert_to_pdfa(pdf_path)` from Task 1.

- [ ] **Step 1: Update the failing test**

In `ocr-api/tests/test_ocr.py`, update `test_process_scan_uses_backend` to also
assert the PDF/A conversion runs. Add `convert_to_pdfa` to the patched context
and assert it was called with the pdf path. Replace the `with patch(...)` block
and add the assertion so the test reads:

```python
    captured = {}

    def fake_pdf(pages, pages_ocr, path):
        captured["pages_ocr"] = pages_ocr
        path.write_bytes(b"%PDF-1.4")

    with patch("app.ocr.get_settings", return_value=_settings_with_engine("tesseract")), \
         patch("app.ocr.get_backend", return_value=mock_backend), \
         patch("app.ocr.build_searchable_pdf", side_effect=fake_pdf), \
         patch("app.ocr.convert_to_pdfa") as mock_pdfa, \
         patch("app.ocr.remove_blank_pages"), \
         patch("app.ocr.clean_page"):

        result = asyncio.run(_process_scan(str(tmp_path), "output"))

    assert result["pdf"].endswith("output.pdf")
    assert Path(result["txt"]).read_text() == "extracted text"
    assert captured["pages_ocr"] == mock_backend.run.return_value
    mock_pdfa.assert_called_once()
    assert str(mock_pdfa.call_args.args[0]).endswith("output.pdf")
    mock_backend.run.assert_called_once()
```

(The rest of the test — building `tif`, `mock_backend.run.return_value` as a
`list[OcrPage]` — is unchanged.)

- [ ] **Step 2: Run to verify failure**

Run (from `ocr-api/`): `python3 -m pytest tests/test_ocr.py::test_process_scan_uses_backend -v`
Expected: FAIL — `AttributeError`/patch target `app.ocr.convert_to_pdfa` does not exist yet (not imported).

- [ ] **Step 3: Wire convert_to_pdfa into process_scan**

In `ocr-api/app/ocr.py`, add the import after the `build_searchable_pdf` import (line 11):

```python
from app.ocr_backends.pdfa import convert_to_pdfa
```

Then, in `process_scan()`, immediately after the `build_searchable_pdf` executor
block (the one that ends `)` before `txt_path.write_text(flat_text)`), insert:

```python
    await loop.run_in_executor(None, convert_to_pdfa, pdf_path)
```

So the sequence becomes: build searchable PDF → convert to PDF/A → write `.txt`.

- [ ] **Step 4: Run the pipeline test**

Run (from `ocr-api/`): `python3 -m pytest tests/test_ocr.py -v`
Expected: PASS.

- [ ] **Step 5: Add ghostscript to the Docker image**

In `ocr-api/Dockerfile`, add `ghostscript` to the apt install list (next to the
other CLI tools, e.g. after `fonts-dejavu-core \`):

```
    fonts-dejavu-core \
    ghostscript \
```

- [ ] **Step 6: Remove the completed PDF/A backlog item**

In `docs/backlog.md`, delete the entire `## PDF/A output` section (heading through
its `**Value:**` line). Leave the other backlog items intact.

- [ ] **Step 7: Run the full suite**

Run (from `ocr-api/`): `python3 -m pytest tests/ -q`
Expected: PASS (the real PDF/A integration test passes with gs installed; the two
pre-existing `test_paperless.py` failures noted in CLAUDE.md may or may not
appear and are unrelated).

- [ ] **Step 8: Commit**

```bash
git add ocr-api/app/ocr.py ocr-api/Dockerfile ocr-api/tests/test_ocr.py docs/backlog.md
git commit -m "feat(pdfa): convert output to PDF/A-2b in the pipeline

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5"
```

---

## Manual verification (maintainer, post-merge)

1. `docker compose up -d --build ocr-api` (adds ghostscript; app source changed).
2. Run a real scan; confirm the delivered PDF validates as PDF/A-2b (external
   validator or Paperless archival acceptance) and remains searchable.
   Note: a live run delivers to Paperless/OneDrive/email.

---

## Self-Review

- **Spec coverage:** module + helpers + fallback (§1,§2,§Robustness)→T1; pipeline
  integration (§2)→T2 Steps 1-4; ghostscript dependency (§3)→T2 Step 5; tests
  (§Testing) hermetic + skipif integration→T1 Step 1; backlog cleanup→T2 Step 6.
  Non-goals respected (no veraPDF, no toggle, no PDF/A-3). ✓
- **Placeholders:** none — full code in every step. ✓
- **Type consistency:** `convert_to_pdfa(pdf_path: Path) -> None` and
  `_find_icc_profile() -> str | None` used identically in module, tests, and the
  `process_scan` call site; patch target `app.ocr.convert_to_pdfa` matches the
  import added in T2 Step 3. ✓
