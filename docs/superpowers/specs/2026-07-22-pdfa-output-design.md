# Design: PDF/A-2b output (Ghostscript post-pass)

**Date:** 2026-07-22
**Status:** Approved

## Goal

Every delivered PDF is converted to **PDF/A-2b** (archival) after the searchable
text layer is built, so scans are self-contained and archive-compliant, while
staying searchable/copyable.

## Decisions (from brainstorming)

- **Conformance:** PDF/A-2b (`gs -dPDFA=2`).
- **Approach:** Ghostscript direct post-pass on our already-built searchable PDF
  (NOT ocrmypdf — we already produce OCR/text/images ourselves; gs adds only the
  `ghostscript` apt package, no Python deps).
- **Always on:** no enable/disable setting; conversion always attempted, with a
  graceful fallback (below) so it can never lose a scan.

## Spike findings (grounding)

Verified on the host with gs 10.02.1:
- A bare `gs -dPDFA=2` sets the XMP `pdfaid` conformance flag but produces **no
  `/OutputIntent`** → not valid PDF/A. A `PDFA_def.ps` that embeds an ICC profile
  is required to get the OutputIntent.
- gs 10.x runs SAFER by default and blocks the PostScript `file` operator from
  reading the ICC path → `--permit-file-read=<icc>` is required.
- With `PDFA_def.ps` (embedding `/usr/share/color/icc/ghostscript/srgb.icc`) +
  `--permit-file-read`, the output has `/OutputIntent` + `/DestOutputProfile` +
  `pdfaid`, and the invisible text (incl. `€`, `•`) is preserved (pypdf extract).
- The `srgb.icc` profile ships with the `ghostscript` apt package.

## Architecture

### 1. New module `ocr-api/app/ocr_backends/pdfa.py`

```python
def convert_to_pdfa(pdf_path: Path) -> None:
    """Convert pdf_path in place to PDF/A-2b via Ghostscript.

    Best-effort: on any failure (no ICC profile, gs error, empty output) the
    original searchable PDF is left untouched and a warning is logged — the
    caller's job still succeeds.
    """
```

Behavior:
1. `_find_icc_profile()` returns the first existing path from candidates:
   `/usr/share/color/icc/ghostscript/srgb.icc`, then a glob of
   `/usr/share/ghostscript/*/iccprofiles/srgb.icc`. If none → log warning, return
   (keep original).
2. Write a `PDFA_def.ps` to a temp file next to `pdf_path`, embedding the ICC path
   (pdfmark snippet: ICC stream + OutputIntent dict + Catalog /OutputIntents).
3. Run gs to a temp output file:
   ```
   gs -dPDFA=2 -dBATCH -dNOPAUSE -dNOOUTERSAVE -dPDFACompatibilityPolicy=1 \
      -sColorConversionStrategy=RGB -sDEVICE=pdfwrite --permit-file-read=<icc> \
      -sOutputFile=<tmp_out> <PDFA_def.ps> <pdf_path>
   ```
   (gs cannot read and write the same file, hence temp → replace.)
4. If gs returns 0 and the temp output is non-empty → `os.replace(tmp_out, pdf_path)`
   (atomic). Otherwise log a warning and keep the original.
5. Clean up the temp `PDFA_def.ps` and any temp output.

### 2. Pipeline integration — `ocr-api/app/ocr.py` `process_scan()`

Immediately after `build_searchable_pdf(...)` writes `pdf_path`:

```python
await loop.run_in_executor(None, convert_to_pdfa, pdf_path)
```

`convert_to_pdfa` rewrites `pdf_path` in place, so every downstream destination
(filesystem / Paperless / rclone / mail) delivers the PDF/A version with no other
changes. The `.txt` sidecar and AI-metadata path are unaffected.

### 3. Dependency — `ocr-api/Dockerfile`

Add `ghostscript` to the apt install list. This provides both the `gs` binary and
the `srgb.icc` profile under `/usr/share/color/icc/ghostscript/`. No new Python
dependencies. Rebuild required.

## Robustness

The "always on" choice means there is no toggle, so the conversion must be
best-effort: missing ICC, gs non-zero exit, or empty output all fall back to the
original searchable (non-A) PDF and log a warning. The job still succeeds and the
document is still delivered and searchable. This mirrors the Unicode-font fallback
in `build_pdf.py`.

## Testing

- **Hermetic** (`tests/test_pdfa.py`, mock `subprocess.run` and the ICC finder):
  - success: temp output written → `pdf_path` replaced with converted bytes.
  - gs failure (returncode != 0): original `pdf_path` unchanged, no exception.
  - missing ICC profile (`_find_icc_profile` → None): gs not invoked, original
    unchanged, no exception.
  - the constructed gs argv contains `-dPDFA=2` and `--permit-file-read=<icc>`.
- **Real integration** (`@pytest.mark.skipif(shutil.which("gs") is None)`):
  build a real searchable PDF (with a non-latin-1 line), run `convert_to_pdfa`,
  then assert the file now contains `/OutputIntent` and `pdfaid`, and that pypdf
  still extracts the `€`/`•` text.

## Non-goals

- Strict veraPDF validation in CI (we assert structural markers + text
  preservation; full ISO validation is out of scope — noted as a known
  limitation).
- PDF/A-3 embedded attachments.
- Per-job enable/disable toggle (chosen: always on).

## Verification (maintainer, post-merge)

Rebuild (`docker compose up -d --build ocr-api`, adds ghostscript), run a real
scan, and confirm the delivered PDF validates as PDF/A-2b (external validator or
Paperless archival acceptance). A live run delivers to Paperless/OneDrive/email.
