# Design: Clear Dependabot alerts + fix PaddleOCR OOM

**Date:** 2026-07-20
**Status:** Approved

## Goal

Two independent changes, delivered as two separate commits on `master`:

1. Close all open Dependabot security alerts.
2. Make PaddleOCR usable on real full-resolution scans without triggering the Linux OOM killer, so `OCR_ENGINE=paddleocr` can be used for upcoming scans.

---

## Part 1 — Security bump (Commit 1)

### Findings

All 4 open Dependabot alerts are the same package, `python-multipart`, in `ocr-api/requirements.txt`, currently pinned `==0.0.27`:

| Alert | Severity | Fixed in |
|-------|----------|----------|
| #11 | high | 0.0.30 — quadratic-time querystring parsing (CPU DoS) |
| #10 | low | 0.0.31 — negative Content-Length buffers whole body |
| #9  | low | 0.0.30 — semicolon querystring separator (param smuggling) |
| #8  | low | 0.0.30 — Content-Disposition RFC 2231/5987 param smuggling |

### Change

`ocr-api/requirements.txt`: `python-multipart==0.0.27` → `python-multipart==0.0.31`.

`0.0.31` is the highest `first_patched_version` across all four alerts, so a single bump closes every alert. `python-multipart` is FastAPI's form parser; no application code imports it directly, so there is no code or test change.

### Verification

Docker smoke test (per `CLAUDE.md`): rebuild image, `curl -sf http://localhost:9000/health` returns `{"status":"ok"}`. After merge to `master`, Dependabot auto-closes all four alerts.

---

## Part 2 — PaddleOCR detection input cap (Commit 2)

### Root cause (from PaddleOCR issue #17955)

PP-OCRv5's text detector runs at near-full resolution. The default `limit_type="min", limit_side_len=64` only *upscales* small images and never *downscales* large ones, and `max_side_limit=4000` is too high to catch a normal scan. A 300 dpi A4 page (2480×3508 ≈ 8.7 MP) is fed to `PP-OCRv5_server_det` unscaled; the CPU detector needs ~5 GB per megapixel → ~44 GB RSS → OOM kill. PaddleOCR 2.x defaulted to `max`/`960`, which is why this never happened before.

Our current call in `app/ocr_backends/paddleocr.py` omits the cap:

```python
results = ocr.predict(str(png_path), use_textline_orientation=True)
```

Maintainer's recommended workaround:

```python
ocr.predict(img, text_det_limit_type="max", text_det_limit_side_len=960)
```

Larger `side_len` = better small-text recall at higher memory cost.

### Decision

Cap detection input, configurable via Settings, default `text_det_limit_side_len=1600` (`≈` 1131×1600 ≈ 1.8 MP ≈ ~9 GB peak on the ~47 GiB host) — balances small-text recall against memory headroom. `text_det_limit_type` defaults to `"max"`.

### Changes (all in `ocr-api/`)

**1. `app/config.py`** — add two settings alongside the existing OCR config:

```python
paddle_det_limit_type: Literal["max", "min"] = "max"
paddle_det_limit_side_len: int = 1600
```

Env-overridable as `PADDLE_DET_LIMIT_TYPE` / `PADDLE_DET_LIMIT_SIDE_LEN`, matching the existing pydantic-`Settings` pattern.

**2. `app/ocr_backends/paddleocr.py`** — read settings and pass them into `predict()`:

```python
from app.config import get_settings
...
def run(self, pages, language):
    settings = get_settings()
    ...
    results = ocr.predict(
        str(png_path),
        use_textline_orientation=True,
        text_det_limit_type=settings.paddle_det_limit_type,
        text_det_limit_side_len=settings.paddle_det_limit_side_len,
    )
```

`get_settings()` is `lru_cache`-wrapped and mock-overridable, consistent with the rest of the app.

**3. `tests/test_ocr_backends.py`** — the two existing `predict` tests keep passing (MagicMock accepts the new kwargs). Add one assertion that `predict` was called with `text_det_limit_type="max"` and `text_det_limit_side_len=1600` so the cap cannot silently regress.

**4. Docs** — update the PaddleOCR section of `CLAUDE.md` to document the det-cap and the two env vars; add the two vars (commented) to `.env.example` near the `OCR_ENGINE` block.

### Explicitly out of scope

- **Dockerfile warmup** — `predict()` on the 8×8 blank warmup image never OOMs; leaving it unchanged exercises the default path and keeps warmup honest.
- **Switching detection model** (server → mobile) — larger change; the config cap already resolves the OOM.

### Verification

- Unit tests (`python3 -m pytest tests/ -q`) run here, including the new cap assertion.
- Full runtime check requires `OCR_ENGINE=paddleocr` + a real 300 dpi A4 scan on the Pi/scanner — a **manual step for the maintainer**, since PaddleOCR is not the default engine and needs real hardware/scan input.

---

## Delivery

- **Commit 1:** `chore(deps): bump python-multipart from 0.0.27 to 0.0.31`
- **Commit 2:** `fix(paddleocr): cap detection input size to prevent OOM on full-resolution scans`
