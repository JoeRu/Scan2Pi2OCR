# Dependabot Bump + PaddleOCR OOM Cap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all open Dependabot alerts (python-multipart) and cap PaddleOCR's detection input size so full-resolution scans no longer OOM-kill the process.

**Architecture:** Two independent commits. Commit 1 is a one-line dependency pin bump. Commit 2 adds two pydantic Settings (`paddle_det_limit_type`, `paddle_det_limit_side_len`), wires them into the `PaddleOcrBackend.predict()` call, tests the wiring, and documents the env vars.

**Tech Stack:** Python 3, FastAPI, pydantic-settings, PaddleOCR 3.4.1 / PaddlePaddle 3.3.1, pytest, Docker Compose.

## Global Constraints

- Work happens in `ocr-api/`; run commands from there unless noted.
- `python-multipart` MUST be pinned to exactly `==0.0.31` (highest first_patched_version across all 4 alerts).
- New Settings default: `paddle_det_limit_type="max"`, `paddle_det_limit_side_len=1600`.
- Env var names: `PADDLE_DET_LIMIT_TYPE`, `PADDLE_DET_LIMIT_SIDE_LEN`.
- Follow existing pydantic-`Settings` pattern; `get_settings()` is `lru_cache`-wrapped and overridable in tests.
- Do NOT modify the Dockerfile PaddleOCR warmup.
- Tests run with `python3 -m pytest tests/ -q` from `ocr-api/`.
- Commit message trailers used in this repo:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5
  ```

---

## File Structure

- `ocr-api/requirements.txt` — bump the `python-multipart` pin (Task 1).
- `ocr-api/app/config.py` — add two PaddleOCR detection settings (Task 2).
- `ocr-api/app/ocr_backends/paddleocr.py` — pass the settings into `ocr.predict()` (Task 2).
- `ocr-api/tests/test_ocr_backends.py` — assert the cap kwargs are passed (Task 2).
- `CLAUDE.md`, `.env.example` — document the new env vars (Task 3).

---

### Task 1: Bump python-multipart

**Files:**
- Modify: `ocr-api/requirements.txt:3`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing consumed by later tasks (independent commit).

- [ ] **Step 1: Edit the pin**

In `ocr-api/requirements.txt`, change line 3 from:
```
python-multipart==0.0.27
```
to:
```
python-multipart==0.0.31
```

- [ ] **Step 2: Verify no other pin references the old version**

Run (from repo root):
```bash
grep -rn "python-multipart" ocr-api/
```
Expected: exactly one match, `ocr-api/requirements.txt:3` now reading `==0.0.31`.

- [ ] **Step 3: Docker smoke test**

Run (from repo root):
```bash
docker compose build --no-cache 2>&1 | grep -E "error|ERROR|failed|FAILED|ImportError" | grep -iv "warn"
docker compose up -d && sleep 5 && curl -sf http://localhost:9000/health
```
Expected: build prints no error lines; curl returns `{"status":"ok"}`.

- [ ] **Step 4: Commit**

```bash
git add ocr-api/requirements.txt
git commit -m "chore(deps): bump python-multipart from 0.0.27 to 0.0.31

Closes all 4 open Dependabot alerts (1 high, 3 low) for python-multipart.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5"
```

---

### Task 2: Cap PaddleOCR detection input size

**Files:**
- Modify: `ocr-api/app/config.py:22-24` (add settings near `ocr_engine`)
- Modify: `ocr-api/app/ocr_backends/paddleocr.py:1-48`
- Test: `ocr-api/tests/test_ocr_backends.py`

**Interfaces:**
- Consumes: `app.config.get_settings()` → `Settings` with new attributes
  `paddle_det_limit_type: str` and `paddle_det_limit_side_len: int`.
- Produces: `PaddleOcrBackend.run(pages, language)` now calls
  `ocr.predict(str(png_path), use_textline_orientation=True,
  text_det_limit_type=<settings>, text_det_limit_side_len=<settings>)`.

- [ ] **Step 1: Write the failing test**

Add to `ocr-api/tests/test_ocr_backends.py` (near the other PaddleOCR tests, after `test_paddleocr_run_returns_text`):

```python
def test_paddleocr_run_passes_det_limit_kwargs(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)

    fake_result = [{"rec_texts": ["x"], "rec_scores": [0.9]}]
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR:
        mock_ocr_instance = MagicMock()
        mock_ocr_instance.predict.return_value = fake_result
        MockOCR.return_value = mock_ocr_instance

        PaddleOcrBackend().run([page], "deu")

    _, kwargs = mock_ocr_instance.predict.call_args
    assert kwargs["text_det_limit_type"] == "max"
    assert kwargs["text_det_limit_side_len"] == 1600
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `ocr-api/`):
```bash
python3 -m pytest tests/test_ocr_backends.py::test_paddleocr_run_passes_det_limit_kwargs -v
```
Expected: FAIL with `KeyError: 'text_det_limit_type'` (kwargs not yet passed).

- [ ] **Step 3: Add the settings to config.py**

In `ocr-api/app/config.py`, immediately after the `ocr_engine` line (line 23), add:

```python
    paddle_det_limit_type: Literal["max", "min"] = "max"
    paddle_det_limit_side_len: int = 1600
```

`Literal` is already imported at the top of the file — no new import needed.

- [ ] **Step 4: Wire settings into the backend**

In `ocr-api/app/ocr_backends/paddleocr.py`, add the import after the existing imports (below line 4):

```python
from app.config import get_settings
```

Then in `run()`, read settings once before the page loop. Change the top of `run()` so it reads:

```python
    def run(self, pages: list[Path], language: str) -> str:
        if not pages:
            raise ValueError("No pages provided to PaddleOcrBackend")

        settings = get_settings()
        lang = self._map_language(language)
        logger.info("Running PaddleOCR on %d page(s), mapped language=%s", len(pages), lang)
        # enable_mkldnn=False: oneDNN triggers a NotImplementedError on some CPUs with PaddlePaddle 3.x
        ocr = PaddleOCR(lang=lang, enable_mkldnn=False)
```

And change the `predict` call (currently line 36) from:

```python
                results = ocr.predict(str(png_path), use_textline_orientation=True)
```
to:
```python
                results = ocr.predict(
                    str(png_path),
                    use_textline_orientation=True,
                    text_det_limit_type=settings.paddle_det_limit_type,
                    text_det_limit_side_len=settings.paddle_det_limit_side_len,
                )
```

- [ ] **Step 5: Run the new test to verify it passes**

Run (from `ocr-api/`):
```bash
python3 -m pytest tests/test_ocr_backends.py::test_paddleocr_run_passes_det_limit_kwargs -v
```
Expected: PASS.

- [ ] **Step 6: Run the full backend suite to confirm no regression**

Run (from `ocr-api/`):
```bash
python3 -m pytest tests/test_ocr_backends.py -q
```
Expected: all pass (the two pre-existing PaddleOCR `predict` tests still pass — MagicMock accepts the new kwargs).

- [ ] **Step 7: Commit**

```bash
git add ocr-api/app/config.py ocr-api/app/ocr_backends/paddleocr.py ocr-api/tests/test_ocr_backends.py
git commit -m "fix(paddleocr): cap detection input size to prevent OOM on full-resolution scans

PP-OCRv5's detector runs at near-full resolution; a 300 dpi A4 scan
(~8.7 MP) needs ~44 GB RSS and gets OOM-killed. Cap detection input via
new PADDLE_DET_LIMIT_TYPE (max) / PADDLE_DET_LIMIT_SIDE_LEN (1600) settings.

Refs PaddlePaddle/PaddleOCR#17955

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5"
```

---

### Task 3: Document the new env vars

**Files:**
- Modify: `CLAUDE.md` (PaddleOCR backend section)
- Modify: `.env.example` (near the `OCR_ENGINE` block, ~line 23)

**Interfaces:**
- Consumes: env var names/defaults defined in Task 2.
- Produces: nothing consumed by code.

- [ ] **Step 1: Add env vars to .env.example**

In `.env.example`, after the OCR language block (around line 26), add:

```bash
# PaddleOCR detection input cap (prevents OOM on full-resolution scans).
# Larger side_len = better small-text recall, higher memory (~5 GB per megapixel).
# PADDLE_DET_LIMIT_TYPE=max
# PADDLE_DET_LIMIT_SIDE_LEN=1600
```

- [ ] **Step 2: Document in CLAUDE.md**

In `CLAUDE.md`, in the "Pluggable OCR backends" section, under the `paddleocr.py` description, append a sentence:

```
PaddleOCR's detector is capped via `PADDLE_DET_LIMIT_TYPE` (default `max`) and
`PADDLE_DET_LIMIT_SIDE_LEN` (default `1600`); without the cap a 300 dpi A4 scan
OOM-kills the process (PaddleOCR#17955).
```

- [ ] **Step 3: Verify tests still green (docs-only, sanity)**

Run (from `ocr-api/`):
```bash
python3 -m pytest tests/ -q
```
Expected: same pass/fail baseline as before (the two pre-existing `test_paperless.py` failures noted in CLAUDE.md are unrelated and may remain).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .env.example
git commit -m "docs: document PaddleOCR detection cap env vars

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01KK8AeXKu4B7KDYEwYW4Xr5"
```

---

## Manual verification (maintainer, post-merge)

Not automatable here — requires real hardware and a scan:

1. Set `OCR_ENGINE=paddleocr` in `.env`.
2. Trigger a real 300 dpi A4 scan through the Pi pipeline.
3. Confirm the job reaches `done` (not OOM-killed) and peak RSS stays well under host RAM.
4. Optionally tune `PADDLE_DET_LIMIT_SIDE_LEN` up if small-text recall is insufficient.

---

## Self-Review

- **Spec coverage:** Part 1 pin bump → Task 1. Part 2 config + backend + test → Task 2. Docs → Task 3. Manual runtime check → documented as maintainer step. Out-of-scope items (Dockerfile warmup, model switch) explicitly excluded. ✓
- **Placeholders:** none — every code/edit step shows exact content. ✓
- **Type consistency:** `paddle_det_limit_type` / `paddle_det_limit_side_len` used identically in config, backend, and test; `predict` kwargs `text_det_limit_type` / `text_det_limit_side_len` match the PaddleOCR API and the test assertions. ✓
