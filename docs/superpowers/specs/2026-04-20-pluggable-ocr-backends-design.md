# Pluggable OCR Backends Design

**Date:** 2026-04-20
**Status:** Approved
**Branch:** feature/paddleocr

## Goal

Replace the hard-coded Tesseract OCR integration with a pluggable backend system that makes it easy to swap OCR engines. Initial backends: Tesseract (existing), PaddleOCR (new), Google Cloud Vision (stub). Searchable PDF output is the primary requirement.

## Architecture

### File Structure

```
ocr-api/app/
├── ocr.py                  # pipeline orchestrator (unchanged API)
├── ocr_backends/
│   ├── __init__.py         # exports OcrBackend protocol + get_backend() factory
│   ├── base.py             # OcrBackend Protocol definition
│   ├── tesseract.py        # existing tesseract logic, adapted
│   ├── paddleocr.py        # new PaddleOCR backend
│   └── gcv.py              # Google Cloud Vision backend (stub)
└── config.py               # adds ocr_engine setting
```

### OcrBackend Protocol

```python
class OcrBackend(Protocol):
    def run(self, pages: list[Path], language: str) -> str:
        ...  # returns extracted plain text
```

Each backend receives the list of preprocessed TIF page paths and the configured language string, and returns plain text extracted from all pages.

### Pipeline Flow

`process_scan()` in `ocr.py` remains the single entry point. The OCR step changes from:

```
run_tesseract() → pdf + txt + hocr
```

to:

```
backend.run(pages, language) → text
build_searchable_pdf(pages, text, output_path) → pdf   [via ocrmypdf]
write text to .txt file
```

Blank page removal and ImageMagick contrast cleanup are unchanged.

## PDF Generation

`build_searchable_pdf()` uses `ocrmypdf` to combine the original TIF images with the extracted text sidecar into a searchable PDF. This replaces Tesseract's native PDF output.

- `.hocr` output is dropped — it was never used downstream
- `.pdf` and `.txt` are the outputs, same as before
- `ocrmypdf` is added to `requirements.txt` as a pip dependency

## Configuration

New field in `config.py`:

```python
ocr_engine: str = "tesseract"  # "tesseract" | "paddleocr" | "gcv"
```

Optional fields for Google Cloud Vision:

```python
gcv_credentials_file: str = ""   # path to service account JSON
gcv_project_id: str = ""
```

## Backend Specifics

### Tesseract

- Uses existing subprocess logic, adapted to return plain text
- Language string passed as-is (`deu+eng+frk`)
- Tesseract apt packages remain in Dockerfile

### PaddleOCR

- Uses PaddleOCR Python API (no subprocess)
- Language mapping: `deu` → `german`, `eng` → `en`; `frk` (Fraktur) has no PaddleOCR equivalent — falls back to `german`
- `paddleocr` and `paddlepaddle` added to `requirements.txt`
- Lazy import so heavy deps only load when engine is active

### Google Cloud Vision

- Calls GCV REST API with image bytes
- Auto-detects language (ignores `ocr_language`)
- Raises descriptive error at runtime if `gcv_credentials_file` is missing
- Implemented as a stub for now — full implementation deferred

## Testing

- Each backend gets its own unit tests with mocked external calls
- `test_ocr.py` existing blank-page tests are unaffected
- Integration test for `process_scan()` mocks the active backend
- `build_searchable_pdf()` tested with a real minimal TIF + text

## Docker

- Tesseract apt packages stay (Tesseract backend still uses them)
- `paddleocr`, `paddlepaddle` added as pip packages
- `ocrmypdf` added as pip package
- Default `ocr_engine=tesseract` means no behavior change on existing deployments
