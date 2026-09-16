# OpenRouter LLM OCR Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `OCR_ENGINE=openrouter`: Tesseract builds the positioned PDF text layer, and a vision LLM on OpenRouter transcribes each page (fast, reads handwriting). Handwritten or uncertain pages escalate to a stronger model, and every LLM failure falls back to the Tesseract text.

**Architecture:** A new `app/ocr_backends/openrouter.py` holds `OpenRouterBackend`, which runs `TesseractBackend` and then transcribes pages concurrently with the official `openrouter` SDK (sync client in a thread pool). `OcrPage` gains `transcript` / `transcript_model` / `escalated`; `OcrPage.text` prefers the transcript, while `build_pdf.py` keeps using `lines` and is unchanged. `process_scan()` reports per-page provenance (`ocr_pages`), and the worker copies it into the job status. A manual `scripts/compare_ocr_models.py` compares models on real pages.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, `openrouter==1.1.148` SDK, httpx 0.28.1, Pillow, Tesseract, pytest.

**Spec:** `docs/superpowers/specs/2026-09-16-openrouter-llm-ocr-design.md`

## Global Constraints

- Work in `ocr-api/`. Run tests with `python3 -m pytest tests/ -q` from `ocr-api/`, with no env vars (hermetic; there is no `ocr-api/.env`).
- Tests make **no network calls**. Patch `app.ocr_backends.openrouter._client`, `TesseractBackend`, `prepare_image`, `time.sleep`, and `get_settings` (at the import site, e.g. `app.ocr_backends.openrouter.get_settings`), because `Settings()` needs `api_key`.
- Every OCR request sets `provider={"data_collection": "deny"}`, `temperature=0`, and `response_format={"type": "json_object"}`.
- **A scan never fails because of the LLM.** A missing key, client error, request error or timeout, or empty text leaves the page with `transcript=None` (Tesseract text).
- Defaults: `ocr_llm_model="google/gemini-3.1-flash-lite"`, `ocr_llm_strong_model="google/gemini-3.5-flash"`, `ocr_llm_fallback_models=[]`, `ocr_llm_concurrency=3`, `ocr_llm_timeout=90`, `ocr_llm_max_tokens=4000`, `ocr_llm_image_max_side=2000`, `ocr_llm_escalate_unclear_max=2`.
- Dependencies: `openrouter==1.1.148` requires `httpx>=0.28.1` and `pydantic<2.13`, so pin `httpx==0.28.1` and `pydantic==2.12.5` (verified 2026-09-16: all 91 existing tests pass with these).
- `ai_metadata.py` and Paperless delivery are **not** changed (Paperless LLM content is deferred to the backlog).
- **Live production stack:** never run `docker compose build/up/restart` or edit the repo-root `.env` without explicit user approval (Task 7 only).
- Commit trailer on every commit:
  ```
  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
  ```

---

## File Structure

- Modify `ocr-api/requirements.txt`: pin `httpx==0.28.1` and `pydantic==2.12.5`, add `openrouter==1.1.148` (Task 1).
- Modify `ocr-api/app/config.py`: `"openrouter"` engine plus the `ocr_llm_*` settings (Task 1).
- Modify `ocr-api/app/ocr_backends/types.py`: `OcrPage` transcript fields (Task 1).
- Create `ocr-api/app/ocr_backends/openrouter.py`: helpers (`prepare_image`, `parse_reply`, `build_request`, `call_llm`, `escalation_reason`, `_client`) in Task 2, `OpenRouterBackend` in Task 3.
- Modify `ocr-api/app/ocr_backends/__init__.py`: factory branch (Task 3).
- Modify `ocr-api/app/ocr.py`: `ocr_pages` in the result (Task 4).
- Modify `ocr-api/app/worker.py`: `ocr_pages` in the status outputs (Task 4).
- Create `ocr-api/scripts/__init__.py` and `ocr-api/scripts/compare_ocr_models.py`; modify `ocr-api/Dockerfile` to COPY `scripts/` (Task 5).
- Tests: `ocr-api/tests/test_ocr_backends.py` (Tasks 1–3), `tests/test_ocr.py` and `tests/test_worker.py` (Task 4), and a new `tests/test_compare_ocr_models.py` (Task 5).
- Docs: `.env.example`, `CLAUDE.md`, `docs/backlog.md` (Task 6).

Setup (once, before Task 1): `git checkout -b feat/openrouter-ocr` from `master`.

---

### Task 1: Dependencies, config and `OcrPage` transcript fields

**Files:**
- Modify: `ocr-api/requirements.txt`
- Modify: `ocr-api/app/config.py:24-28` (OCR settings block)
- Modify: `ocr-api/app/ocr_backends/types.py`
- Test: `ocr-api/tests/test_ocr_backends.py` (append)

**Interfaces:**
- Produces: `OcrPage(lines, transcript: str | None = None, transcript_model: str | None = None, escalated: bool = False)`. `OcrPage.text` returns `transcript` if it is not None, otherwise the joined `lines`.
- Produces: `Settings.ocr_engine` accepts `"openrouter"`, plus the `Settings.ocr_llm_model: str`, `ocr_llm_strong_model: str`, `ocr_llm_fallback_models: list[str]`, `ocr_llm_concurrency: int`, `ocr_llm_timeout: int`, `ocr_llm_max_tokens: int`, `ocr_llm_image_max_side: int`, and `ocr_llm_escalate_unclear_max: int` settings.

- [ ] **Step 1: Update and install dependencies**

In `ocr-api/requirements.txt`, change `httpx==0.27.0` to `httpx==0.28.1`, and append two lines so the file reads:

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
python-multipart==0.0.31
httpx==0.28.1
pydantic-settings==2.2.1
fpdf2==2.7.9
Pillow==12.3.0
paddleocr==3.4.1
paddlepaddle==3.3.1
pydantic==2.12.5
openrouter==1.1.148
```

Run: `pip3 install --break-system-packages httpx==0.28.1 pydantic==2.12.5 openrouter==1.1.148`
Then run: `python3 -m pytest tests/ -q`
Expected: all existing tests PASS (91 passed).

- [ ] **Step 2: Write the failing tests**

Append to `ocr-api/tests/test_ocr_backends.py`:

```python
# ---------------------------------------------------------------------------
# OcrPage transcript + LLM OCR settings
# ---------------------------------------------------------------------------

def test_ocr_page_text_prefers_transcript():
    page = OcrPage([OcrLine("tesseract", 0, 0, 1, 1)], transcript="llm text")
    assert page.text == "llm text"


def test_ocr_page_text_falls_back_to_lines():
    page = OcrPage([OcrLine("a", 0, 0, 1, 1), OcrLine("b", 0, 0, 1, 1)])
    assert page.text == "a\nb"
    assert page.transcript is None
    assert page.transcript_model is None
    assert page.escalated is False


def test_config_accepts_openrouter_engine():
    assert Settings(api_key="test", ocr_engine="openrouter").ocr_engine == "openrouter"


def test_config_ocr_llm_defaults():
    s = Settings(api_key="test")
    assert s.ocr_llm_model == "google/gemini-3.1-flash-lite"
    assert s.ocr_llm_strong_model == "google/gemini-3.5-flash"
    assert s.ocr_llm_fallback_models == []
    assert s.ocr_llm_concurrency == 3
    assert s.ocr_llm_timeout == 90
    assert s.ocr_llm_max_tokens == 4000
    assert s.ocr_llm_image_max_side == 2000
    assert s.ocr_llm_escalate_unclear_max == 2


def test_config_fallback_models_from_env_json(monkeypatch):
    monkeypatch.setenv("OCR_LLM_FALLBACK_MODELS", '["openai/gpt-5-mini", "anthropic/claude-haiku-4.5"]')
    assert Settings(api_key="test").ocr_llm_fallback_models == [
        "openai/gpt-5-mini", "anthropic/claude-haiku-4.5",
    ]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_ocr_backends.py -k "transcript or openrouter_engine or ocr_llm or fallback_models" -v`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'transcript'`, `ValidationError` for `ocr_engine`, `AttributeError: ... 'ocr_llm_model'`).

- [ ] **Step 4: Implement**

Replace the `OcrPage` class in `ocr-api/app/ocr_backends/types.py` with:

```python
@dataclass
class OcrPage:
    lines: list[OcrLine] = field(default_factory=list)
    # LLM transcription (OCR_ENGINE=openrouter). lines still drive the PDF text layer.
    transcript: str | None = None
    transcript_model: str | None = None
    escalated: bool = False

    @property
    def text(self) -> str:
        """Flat text for the .txt sidecar and AI metadata."""
        if self.transcript is not None:
            return self.transcript
        return "\n".join(line.text for line in self.lines)
```

In `ocr-api/app/config.py`, change the `ocr_engine` line and add the LLM OCR settings directly after `paddle_text_det_model` (before `trash_tmp_files`):

```python
    ocr_engine: Literal["tesseract", "paddleocr", "gcv", "openrouter"] = "tesseract"
```

```python
    # OCR_ENGINE=openrouter: Tesseract builds the PDF text layer, a vision LLM
    # transcribes each page. Cheap model first; handwritten/uncertain pages are
    # re-run on the strong model (empty = no escalation). Uses openrouter_api_key.
    ocr_llm_model: str = "google/gemini-3.1-flash-lite"
    ocr_llm_strong_model: str = "google/gemini-3.5-flash"
    ocr_llm_fallback_models: list[str] = []  # env: JSON list
    ocr_llm_concurrency: int = 3
    ocr_llm_timeout: int = 90
    ocr_llm_max_tokens: int = 4000
    ocr_llm_image_max_side: int = 2000
    ocr_llm_escalate_unclear_max: int = 2
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/ -q`
Expected: all PASS (96 passed).

- [ ] **Step 6: Commit**

```bash
git add ocr-api/requirements.txt ocr-api/app/config.py ocr-api/app/ocr_backends/types.py ocr-api/tests/test_ocr_backends.py
git commit -m "feat(ocr): add LLM OCR settings, OcrPage transcript fields, openrouter SDK dep

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: OpenRouter request helpers

**Files:**
- Create: `ocr-api/app/ocr_backends/openrouter.py`
- Test: `ocr-api/tests/test_ocr_backends.py` (append)

**Interfaces:**
- Consumes: `Settings` fields from Task 1.
- Produces (all in `app.ocr_backends.openrouter`):
  - `PROMPT: str`
  - `@dataclass LlmReply(text: str, handwriting: bool, uncertain: bool, model: str, finish_reason: str | None, prompt_tokens: int, completion_tokens: int, cost: float, latency_s: float)`
  - `prepare_image(path: Path, max_side: int) -> bytes`: grayscale JPEG
  - `parse_reply(content: str) -> tuple[str, bool, bool]`: `(text, handwriting, uncertain)`
  - `build_request(image: bytes, model: str, max_tokens: int, fallback_models: list[str]) -> dict`: kwargs for `client.chat.send`
  - `call_llm(client, image: bytes, model: str, max_tokens: int, settings: Settings) -> LlmReply`: retries once (sleeping `_RETRY_DELAY_S = 2.0`), raises on a second failure or empty text
  - `escalation_reason(reply: LlmReply, unclear_max: int) -> str | None`: `"handwriting" | "uncertain" | "unclear" | "truncated" | None`
  - `_client(settings: Settings)`: an `openrouter.OpenRouter` instance

- [ ] **Step 1: Write the failing tests**

Append to `ocr-api/tests/test_ocr_backends.py`:

```python
# ---------------------------------------------------------------------------
# OpenRouter LLM OCR helpers
# ---------------------------------------------------------------------------
import base64
import io
import json as _json
from types import SimpleNamespace

from app.ocr_backends import openrouter as orb
from app.ocr_backends.openrouter import (
    LlmReply,
    build_request,
    call_llm,
    escalation_reason,
    parse_reply,
    prepare_image,
)


def _llm_settings(**kwargs):
    base = dict(api_key="test", openrouter_api_key="sk-test")
    base.update(kwargs)
    return Settings(**base)


def _llm_result(text="Hallo Welt", handwriting=False, uncertain=False,
                model="google/gemini-3.1-flash-lite", finish_reason="stop",
                cost=0.001, content=None):
    """Fake openrouter ChatResult (only the attributes call_llm reads)."""
    if content is None:
        content = _json.dumps({"text": text, "handwriting": handwriting, "uncertain": uncertain})
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(finish_reason=finish_reason,
                                 message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=1500, completion_tokens=40, cost=cost),
    )


def _reply(**kwargs):
    base = dict(text="t", handwriting=False, uncertain=False, model="m", finish_reason="stop",
                prompt_tokens=0, completion_tokens=0, cost=0.0, latency_s=0.0)
    base.update(kwargs)
    return LlmReply(**base)


def test_prepare_image_grayscale_jpeg_downscaled(tmp_path):
    src = tmp_path / "scan_0001.pnm.tif"
    PILImage.new("RGB", (3000, 1500), (200, 10, 10)).save(str(src), format="TIFF")
    with PILImage.open(io.BytesIO(prepare_image(src, 2000))) as img:
        assert img.format == "JPEG"
        assert img.mode == "L"
        assert img.size == (2000, 1000)


def test_prepare_image_never_upscales(tmp_path):
    src = tmp_path / "small.tif"
    PILImage.new("RGB", (800, 600), (255, 255, 255)).save(str(src), format="TIFF")
    with PILImage.open(io.BytesIO(prepare_image(src, 2000))) as img:
        assert img.size == (800, 600)


def test_parse_reply_plain_json():
    assert parse_reply('{"text": "Hallo", "handwriting": true, "uncertain": false}') == ("Hallo", True, False)


def test_parse_reply_fenced_json():
    content = '```json\n{"text": "Hallo", "handwriting": false, "uncertain": true}\n```'
    assert parse_reply(content) == ("Hallo", False, True)


def test_parse_reply_non_json_returns_raw_text():
    assert parse_reply("Sehr geehrte Damen und Herren") == ("Sehr geehrte Damen und Herren", False, False)


def test_parse_reply_empty():
    assert parse_reply("") == ("", False, False)


def test_build_request_shape():
    image = b"\xff\xd8img"
    req = build_request(image, "google/gemini-3.1-flash-lite", 4000, ["openai/gpt-5-mini"])
    assert req["model"] == "google/gemini-3.1-flash-lite"
    assert req["models"] == ["google/gemini-3.1-flash-lite", "openai/gpt-5-mini"]
    assert req["provider"] == {"data_collection": "deny"}
    assert req["temperature"] == 0
    assert req["max_tokens"] == 4000
    assert req["response_format"] == {"type": "json_object"}
    parts = req["messages"][0]["content"]
    assert req["messages"][0]["role"] == "user"
    assert parts[0] == {"type": "text", "text": orb.PROMPT}
    assert parts[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()},
    }


def test_build_request_without_fallbacks_omits_models():
    assert "models" not in build_request(b"x", "m", 10, [])


def test_call_llm_parses_result():
    client = MagicMock()
    client.chat.send.return_value = _llm_result(text="Hallo", handwriting=True, cost=0.002)
    reply = call_llm(client, b"img", "google/gemini-3.1-flash-lite", 4000, _llm_settings())
    assert reply.text == "Hallo"
    assert reply.handwriting is True
    assert reply.uncertain is False
    assert reply.model == "google/gemini-3.1-flash-lite"
    assert reply.finish_reason == "stop"
    assert (reply.prompt_tokens, reply.completion_tokens, reply.cost) == (1500, 40, 0.002)
    assert client.chat.send.call_args.kwargs["timeout_ms"] == 90_000


def test_call_llm_uses_requested_model_when_result_has_none():
    client = MagicMock()
    client.chat.send.return_value = _llm_result(model=None)
    assert call_llm(client, b"img", "x/requested", 100, _llm_settings()).model == "x/requested"


def test_call_llm_retries_once_then_succeeds():
    client = MagicMock()
    client.chat.send.side_effect = [ConnectionError("boom"), _llm_result(text="ok")]
    with patch("app.ocr_backends.openrouter.time.sleep") as sleep:
        reply = call_llm(client, b"img", "m", 100, _llm_settings())
    assert reply.text == "ok"
    assert client.chat.send.call_count == 2
    sleep.assert_called_once_with(2.0)


def test_call_llm_raises_after_second_failure():
    client = MagicMock()
    client.chat.send.side_effect = ConnectionError("down")
    with patch("app.ocr_backends.openrouter.time.sleep"):
        with pytest.raises(ConnectionError):
            call_llm(client, b"img", "m", 100, _llm_settings())
    assert client.chat.send.call_count == 2


def test_call_llm_empty_text_raises_without_retry():
    client = MagicMock()
    client.chat.send.return_value = _llm_result(text="")
    with pytest.raises(ValueError, match="empty"):
        call_llm(client, b"img", "m", 100, _llm_settings())
    assert client.chat.send.call_count == 1


@pytest.mark.parametrize("kwargs,expected", [
    ({}, None),
    ({"handwriting": True}, "handwriting"),
    ({"uncertain": True}, "uncertain"),
    ({"text": "a [?] b [?] c [?]"}, "unclear"),
    ({"text": "a [?] b [?]"}, None),
    ({"finish_reason": "length"}, "truncated"),
])
def test_escalation_reason(kwargs, expected):
    assert escalation_reason(_reply(**kwargs), unclear_max=2) == expected


def test_call_llm_against_real_sdk_request_shape():
    """Contract test: the real openrouter SDK serializes our request and parses a reply."""
    openrouter = pytest.importorskip("openrouter")
    import httpx

    captured = {}

    def handler(request):
        captured["body"] = _json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "id": "gen-1", "created": 1, "object": "chat.completion", "system_fingerprint": None,
            "model": "google/gemini-3.1-flash-lite",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant",
                "content": _json.dumps({"text": "Hallo", "handwriting": True, "uncertain": False}),
            }}],
            "usage": {"prompt_tokens": 1500, "completion_tokens": 12, "total_tokens": 1512, "cost": 0.0004},
        })

    client = openrouter.OpenRouter(api_key="sk-test",
                                   client=httpx.Client(transport=httpx.MockTransport(handler)))
    settings = _llm_settings(ocr_llm_fallback_models=["openai/gpt-5-mini"])
    reply = call_llm(client, b"img", "google/gemini-3.1-flash-lite", 4000, settings)

    body = captured["body"]
    assert captured["auth"] == "Bearer sk-test"
    assert body["provider"] == {"data_collection": "deny"}
    assert body["models"] == ["google/gemini-3.1-flash-lite", "openai/gpt-5-mini"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["content"][1]["type"] == "image_url"
    assert (reply.text, reply.handwriting, reply.cost) == ("Hallo", True, 0.0004)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_ocr_backends.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'app.ocr_backends.openrouter'`.

- [ ] **Step 3: Implement**

Create `ocr-api/app/ocr_backends/openrouter.py`:

```python
"""OCR via a vision LLM on OpenRouter, layered on Tesseract.

Tesseract supplies the positioned lines for the PDF text layer; the LLM
transcript (much better on handwriting) becomes OcrPage.transcript. Any LLM
failure leaves the page with its Tesseract text, so a scan never fails
because of the LLM.
"""
import base64
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.config import Settings

try:
    from openrouter import OpenRouter
except ImportError:
    OpenRouter = None  # type: ignore[assignment,misc]

logger = logging.getLogger("app.ocr_backends.openrouter")

_RETRY_DELAY_S = 2.0
_JPEG_QUALITY = 85

PROMPT = (
    "Transcribe all text on this scanned document page exactly as written.\n"
    "- Keep the reading order and the line breaks.\n"
    "- Keep the original language (mostly German or English; Fraktur is possible).\n"
    "- Include handwritten text. Mark words you cannot read as [?].\n"
    "- Do not summarize, translate, correct, or add anything.\n"
    "Reply only with a JSON object:\n"
    '{"text": "<transcription>", '
    '"handwriting": <true if any handwritten text is present>, '
    '"uncertain": <true if parts could not be read confidently>}'
)


@dataclass
class LlmReply:
    text: str
    handwriting: bool
    uncertain: bool
    model: str
    finish_reason: str | None
    prompt_tokens: int
    completion_tokens: int
    cost: float
    latency_s: float


def prepare_image(path: Path, max_side: int) -> bytes:
    """Grayscale JPEG with the long side capped at max_side (never upscaled)."""
    with Image.open(path) as img:
        gray = img.convert("L")
    gray.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    gray.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


def parse_reply(content: str) -> tuple[str, bool, bool]:
    """Return (text, handwriting, uncertain); non-JSON content is used as raw text."""
    raw = (content or "").strip()
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return raw, False, False
    if not isinstance(data, dict):
        return raw, False, False
    return (
        str(data.get("text") or "").strip(),
        bool(data.get("handwriting")),
        bool(data.get("uncertain")),
    )


def build_request(image: bytes, model: str, max_tokens: int, fallback_models: list[str]) -> dict:
    data_url = "data:image/jpeg;base64," + base64.b64encode(image).decode("ascii")
    request = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        # Scans hold personal data: only route to providers that don't store/train on prompts.
        "provider": {"data_collection": "deny"},
        "temperature": 0,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }
    if fallback_models:
        request["models"] = [model, *fallback_models]
    return request


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # list of content parts
        return "".join(getattr(part, "text", None) or "" for part in content)
    return ""


def call_llm(client, image: bytes, model: str, max_tokens: int, settings: Settings) -> LlmReply:
    """One transcription request, retried once on error. Raises on failure or empty text."""
    request = build_request(image, model, max_tokens, settings.ocr_llm_fallback_models)
    timeout_ms = settings.ocr_llm_timeout * 1000
    start = time.monotonic()
    try:
        result = client.chat.send(**request, timeout_ms=timeout_ms)
    except Exception as exc:
        logger.info("OpenRouter request to %s failed (%s: %s), retrying once",
                    model, type(exc).__name__, exc)
        time.sleep(_RETRY_DELAY_S)
        result = client.chat.send(**request, timeout_ms=timeout_ms)
    latency = time.monotonic() - start

    choice = result.choices[0]
    text, handwriting, uncertain = parse_reply(_content_text(choice.message.content))
    if not text:
        raise ValueError(f"empty transcription from {model}")
    usage = result.usage
    return LlmReply(
        text=text,
        handwriting=handwriting,
        uncertain=uncertain,
        model=result.model or model,
        finish_reason=choice.finish_reason,
        prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
        cost=getattr(usage, "cost", 0.0) or 0.0,
        latency_s=latency,
    )


def escalation_reason(reply: LlmReply, unclear_max: int) -> str | None:
    if reply.handwriting:
        return "handwriting"
    if reply.uncertain:
        return "uncertain"
    if reply.text.count("[?]") > unclear_max:
        return "unclear"
    if reply.finish_reason == "length":
        return "truncated"
    return None


def _client(settings: Settings):
    if OpenRouter is None:
        raise RuntimeError("openrouter package is not installed")
    return OpenRouter(
        api_key=settings.openrouter_api_key,
        http_referer="https://github.com/Scan2Pi2OCR",
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_ocr_backends.py -q`
Expected: all PASS, including `test_call_llm_against_real_sdk_request_shape` (not skipped, because the SDK was installed in Task 1).

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr_backends/openrouter.py ocr-api/tests/test_ocr_backends.py
git commit -m "feat(openrouter): LLM OCR request helpers (image prep, request, parse, retry, escalation)

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `OpenRouterBackend` and factory wiring

**Files:**
- Modify: `ocr-api/app/ocr_backends/openrouter.py` (append the backend, extend imports)
- Modify: `ocr-api/app/ocr_backends/__init__.py`
- Test: `ocr-api/tests/test_ocr_backends.py` (append)

**Interfaces:**
- Consumes: Task 2 helpers (`prepare_image`, `call_llm`, `escalation_reason`, `_client`, `LlmReply`), `TesseractBackend().run(pages, language) -> list[OcrPage]`, and `get_settings()`.
- Produces: `OpenRouterBackend().run(pages: list[Path], language: str) -> list[OcrPage]` (the Tesseract pages with `transcript` / `transcript_model` / `escalated` set where the LLM succeeded), and `get_backend("openrouter") -> OpenRouterBackend`.

- [ ] **Step 1: Write the failing tests**

Append to `ocr-api/tests/test_ocr_backends.py`:

```python
# ---------------------------------------------------------------------------
# OpenRouterBackend
# ---------------------------------------------------------------------------
from time import sleep as _real_sleep

from app.ocr_backends.openrouter import OpenRouterBackend


def _page_name(send_kwargs):
    """prepare_image is patched to return the page file name as bytes; decode it back."""
    url = send_kwargs["messages"][0]["content"][1]["image_url"]["url"]
    return base64.b64decode(url.split(",", 1)[1]).decode()


def _run_backend(tmp_path, settings, send, n_pages=1, client_error=None):
    pages = [tmp_path / f"scan_{i:04d}.pnm.tif" for i in range(1, n_pages + 1)]
    tesseract_pages = [OcrPage([OcrLine(f"tess {i}", 0, 0, 10, 5)]) for i in range(1, n_pages + 1)]
    client = MagicMock()
    client.chat.send.side_effect = send
    client_patch = (patch("app.ocr_backends.openrouter._client", side_effect=client_error)
                    if client_error else
                    patch("app.ocr_backends.openrouter._client", return_value=client))
    with patch("app.ocr_backends.openrouter.get_settings", return_value=settings), \
         patch("app.ocr_backends.openrouter.TesseractBackend") as MockTess, \
         patch("app.ocr_backends.openrouter.prepare_image",
               side_effect=lambda path, max_side: path.name.encode()), \
         client_patch as mock_client, \
         patch("app.ocr_backends.openrouter.time.sleep"):
        MockTess.return_value.run.return_value = tesseract_pages
        result = OpenRouterBackend().run(pages, "deu+eng")
        MockTess.return_value.run.assert_called_once_with(pages, "deu+eng")
    return result, client, mock_client


def test_openrouter_run_sets_transcript_and_keeps_lines(tmp_path):
    result, client, _ = _run_backend(tmp_path, _llm_settings(), [_llm_result(text="LLM Text")])
    assert len(result) == 1
    page = result[0]
    assert [l.text for l in page.lines] == ["tess 1"]
    assert page.transcript == "LLM Text"
    assert page.text == "LLM Text"
    assert page.transcript_model == "google/gemini-3.1-flash-lite"
    assert page.escalated is False
    assert client.chat.send.call_count == 1
    assert client.chat.send.call_args.kwargs["model"] == "google/gemini-3.1-flash-lite"


def test_openrouter_run_escalates_handwriting_to_strong_model(tmp_path):
    send = [
        _llm_result(text="cheap", handwriting=True),
        _llm_result(text="strong", model="google/gemini-3.5-flash"),
    ]
    result, client, _ = _run_backend(tmp_path, _llm_settings(), send)
    page = result[0]
    assert page.transcript == "strong"
    assert page.transcript_model == "google/gemini-3.5-flash"
    assert page.escalated is True
    second = client.chat.send.call_args_list[1].kwargs
    assert second["model"] == "google/gemini-3.5-flash"
    assert second["max_tokens"] == 8000


@pytest.mark.parametrize("first", [
    {"uncertain": True},
    {"text": "a [?] b [?] c [?]"},
    {"finish_reason": "length"},
])
def test_openrouter_run_escalates_on_other_reasons(tmp_path, first):
    send = [_llm_result(**first), _llm_result(text="strong", model="google/gemini-3.5-flash")]
    result, client, _ = _run_backend(tmp_path, _llm_settings(), send)
    assert client.chat.send.call_count == 2
    assert result[0].escalated is True


@pytest.mark.parametrize("strong", ["", "google/gemini-3.1-flash-lite"])
def test_openrouter_run_no_escalation_without_distinct_strong_model(tmp_path, strong):
    settings = _llm_settings(ocr_llm_strong_model=strong)
    result, client, _ = _run_backend(tmp_path, settings, [_llm_result(text="cheap", handwriting=True)])
    assert client.chat.send.call_count == 1
    assert result[0].transcript == "cheap"
    assert result[0].escalated is False


def test_openrouter_run_strong_failure_keeps_cheap_result(tmp_path):
    send = [_llm_result(text="cheap", handwriting=True), RuntimeError("no route"), RuntimeError("no route")]
    result, client, _ = _run_backend(tmp_path, _llm_settings(), send)
    assert client.chat.send.call_count == 3
    assert result[0].transcript == "cheap"
    assert result[0].transcript_model == "google/gemini-3.1-flash-lite"
    assert result[0].escalated is False


def test_openrouter_run_llm_failure_keeps_tesseract_text(tmp_path):
    result, _, _ = _run_backend(tmp_path, _llm_settings(), ConnectionError("down"))
    assert result[0].transcript is None
    assert result[0].transcript_model is None
    assert result[0].text == "tess 1"


def test_openrouter_run_empty_reply_keeps_tesseract_text(tmp_path):
    result, client, _ = _run_backend(tmp_path, _llm_settings(), [_llm_result(text="")])
    assert client.chat.send.call_count == 1
    assert result[0].transcript is None
    assert result[0].text == "tess 1"


def test_openrouter_run_invalid_json_uses_raw_text_without_escalation(tmp_path):
    result, client, _ = _run_backend(tmp_path, _llm_settings(), [_llm_result(content="Rohtext ohne JSON")])
    assert client.chat.send.call_count == 1
    assert result[0].transcript == "Rohtext ohne JSON"
    assert result[0].escalated is False


def test_openrouter_run_missing_api_key_skips_llm(tmp_path):
    settings = _llm_settings(openrouter_api_key="")
    result, client, mock_client = _run_backend(tmp_path, settings, [_llm_result()])
    mock_client.assert_not_called()
    client.chat.send.assert_not_called()
    assert result[0].transcript is None


def test_openrouter_run_client_creation_failure_keeps_tesseract_text(tmp_path):
    result, _, _ = _run_backend(tmp_path, _llm_settings(), [_llm_result()],
                                client_error=RuntimeError("openrouter package is not installed"))
    assert result[0].transcript is None
    assert result[0].text == "tess 1"


def test_openrouter_run_preserves_page_order_with_concurrency(tmp_path):
    delays = {"scan_0001.pnm.tif": 0.2, "scan_0002.pnm.tif": 0.1, "scan_0003.pnm.tif": 0.0}

    def send(**kwargs):
        name = _page_name(kwargs)
        _real_sleep(delays[name])  # page 1 finishes last
        return _llm_result(text=f"llm {name}")

    settings = _llm_settings(ocr_llm_concurrency=3)
    result, _, _ = _run_backend(tmp_path, settings, send, n_pages=3)
    assert [p.transcript for p in result] == [
        "llm scan_0001.pnm.tif", "llm scan_0002.pnm.tif", "llm scan_0003.pnm.tif",
    ]


def test_get_backend_openrouter_returns_backend():
    assert isinstance(get_backend("openrouter"), OpenRouterBackend)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_ocr_backends.py -q`
Expected: collection ERROR, `ImportError: cannot import name 'OpenRouterBackend'`.

- [ ] **Step 3: Implement the backend**

In `ocr-api/app/ocr_backends/openrouter.py`, extend the imports so the top of the file reads:

```python
import base64
import io
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.config import Settings, get_settings
from app.ocr_backends.tesseract import TesseractBackend
from app.ocr_backends.types import OcrPage
```

Append to the end of `ocr-api/app/ocr_backends/openrouter.py`:

```python
@dataclass
class _PageOutcome:
    reply: LlmReply
    escalated: bool
    cost: float  # includes the cheap call when escalated


class OpenRouterBackend:
    def run(self, pages: list[Path], language: str) -> list[OcrPage]:
        result = TesseractBackend().run(pages, language)
        settings = get_settings()
        if not settings.openrouter_api_key:
            logger.error("OCR_ENGINE=openrouter but OPENROUTER_API_KEY is empty; "
                         "using Tesseract text only")
            return result
        try:
            client = _client(settings)
        except Exception as exc:
            logger.error("Could not create OpenRouter client, using Tesseract text only: %s: %s",
                         type(exc).__name__, exc)
            return result

        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=max(1, settings.ocr_llm_concurrency)) as pool:
            outcomes = list(pool.map(
                lambda item: self._transcribe(client, settings, item[0], item[1]),
                enumerate(pages),
            ))

        for ocr_page, outcome in zip(result, outcomes):
            if outcome is None:
                continue
            ocr_page.transcript = outcome.reply.text
            ocr_page.transcript_model = outcome.reply.model
            ocr_page.escalated = outcome.escalated

        logger.info(
            "OCR LLM: %d pages, %d escalated, %d fallback, $%.4f, %.1fs",
            len(pages),
            sum(1 for o in outcomes if o is not None and o.escalated),
            sum(1 for o in outcomes if o is None),
            sum(o.cost for o in outcomes if o is not None),
            time.monotonic() - start,
        )
        return result

    def _transcribe(self, client, settings: Settings, index: int, path: Path) -> _PageOutcome | None:
        page_no = index + 1
        try:
            image = prepare_image(path, settings.ocr_llm_image_max_side)
            reply = call_llm(client, image, settings.ocr_llm_model, settings.ocr_llm_max_tokens, settings)
        except Exception as exc:
            logger.warning("Page %d: LLM OCR failed, keeping Tesseract text: %s: %s",
                           page_no, type(exc).__name__, exc)
            return None

        reason = escalation_reason(reply, settings.ocr_llm_escalate_unclear_max)
        _log_reply(page_no, reply, reason)
        strong = settings.ocr_llm_strong_model
        if reason is None or not strong or strong == settings.ocr_llm_model:
            return _PageOutcome(reply, False, reply.cost)

        try:
            strong_reply = call_llm(client, image, strong, 2 * settings.ocr_llm_max_tokens, settings)
        except Exception as exc:
            logger.warning("Page %d: escalation to %s failed, keeping %s result: %s: %s",
                           page_no, strong, reply.model, type(exc).__name__, exc)
            return _PageOutcome(reply, False, reply.cost)
        _log_reply(page_no, strong_reply, None)
        return _PageOutcome(strong_reply, True, reply.cost + strong_reply.cost)


def _log_reply(page_no: int, reply: LlmReply, escalate_reason: str | None) -> None:
    logger.info(
        "Page %d: model=%s tokens=%d/%d cost=$%.4f latency=%.1fs%s",
        page_no, reply.model, reply.prompt_tokens, reply.completion_tokens,
        reply.cost, reply.latency_s,
        f" escalate={escalate_reason}" if escalate_reason else "",
    )
```

- [ ] **Step 4: Wire the factory**

In `ocr-api/app/ocr_backends/__init__.py`, add a branch before the final `raise`, and update the error message:

```python
    if engine == "openrouter":
        from app.ocr_backends.openrouter import OpenRouterBackend
        return OpenRouterBackend()
    raise ValueError(
        f"Unknown OCR engine: {engine!r}. "
        "Valid values: 'tesseract', 'paddleocr', 'gcv', 'openrouter'."
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/ -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add ocr-api/app/ocr_backends/openrouter.py ocr-api/app/ocr_backends/__init__.py ocr-api/tests/test_ocr_backends.py
git commit -m "feat(openrouter): OpenRouterBackend with escalation and Tesseract fallback

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Per-page provenance in the scan result and job status

**Files:**
- Modify: `ocr-api/app/ocr.py` (return dict at the end of `process_scan`)
- Modify: `ocr-api/app/worker.py` (after the delivery results loop, before `if errors:`)
- Test: `ocr-api/tests/test_ocr.py`, `ocr-api/tests/test_worker.py` (append)

**Interfaces:**
- Consumes: `OcrPage.transcript_model`, `OcrPage.escalated` (Task 1).
- Produces: `process_scan()` result key `"ocr_pages": list[{"page": int, "model": str | None, "escalated": bool}]`, and `GET /scan/status/{job_id}` → `outputs["ocr_pages"]` (for both `done` and `done_with_errors`).

- [ ] **Step 1: Write the failing tests**

Append to `ocr-api/tests/test_ocr.py`:

```python
def test_process_scan_reports_ocr_pages_provenance(tmp_path):
    _make_tif(tmp_path / "scan_0001.pnm.tif")
    _make_tif(tmp_path / "scan_0002.pnm.tif")

    mock_backend = MagicMock()
    mock_backend.run.return_value = [
        OcrPage([OcrLine("tess a", 0, 0, 10, 5)], transcript="llm a",
                transcript_model="google/gemini-3.5-flash", escalated=True),
        OcrPage([OcrLine("tess b", 0, 0, 10, 5)]),
    ]

    def fake_pdf(pages, pages_ocr, path):
        path.write_bytes(b"%PDF-1.4")

    with patch("app.ocr.get_settings", return_value=_settings_with_engine("openrouter")), \
         patch("app.ocr.get_backend", return_value=mock_backend), \
         patch("app.ocr.build_searchable_pdf", side_effect=fake_pdf), \
         patch("app.ocr.convert_to_pdfa"), \
         patch("app.ocr.remove_blank_pages"), \
         patch("app.ocr.clean_page"):
        result = asyncio.run(_process_scan(str(tmp_path), "output"))

    assert result["ocr_pages"] == [
        {"page": 1, "model": "google/gemini-3.5-flash", "escalated": True},
        {"page": 2, "model": None, "escalated": False},
    ]
    assert Path(result["txt"]).read_text() == "llm a\n\ntess b"
```

Append to `ocr-api/tests/test_worker.py`:

```python
@pytest.mark.asyncio
async def test_process_job_status_includes_ocr_pages(tmp_path):
    settings = _make_settings()
    ocr_pages = [{"page": 1, "model": "google/gemini-3.1-flash-lite", "escalated": False}]
    ocr_result = {"pdf": str(tmp_path / "out.pdf"), "txt": str(tmp_path / "out.txt"),
                  "ocr_pages": ocr_pages}

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, return_value=ocr_result), \
         patch("shutil.rmtree"):
        await _process_job("jp1", str(tmp_path), "scan_001", _now())

    assert worker_mod._status["jp1"]["status"] == "done"
    assert worker_mod._status["jp1"]["outputs"]["ocr_pages"] == ocr_pages


@pytest.mark.asyncio
async def test_process_job_status_with_errors_includes_ocr_pages(tmp_path):
    settings = _make_settings(enable_filesystem=True)
    ocr_pages = [{"page": 1, "model": None, "escalated": False}]
    ocr_result = {"pdf": str(tmp_path / "out.pdf"), "txt": str(tmp_path / "out.txt"),
                  "ocr_pages": ocr_pages}

    with patch("app.worker.get_settings", return_value=settings), \
         patch("app.worker.process_scan", new_callable=AsyncMock, return_value=ocr_result), \
         patch("app.worker.deliver_filesystem", new_callable=AsyncMock, side_effect=OSError("disk full")), \
         patch("shutil.rmtree"):
        await _process_job("jp2", str(tmp_path), "scan_001", _now())

    status = worker_mod._status["jp2"]
    assert status["status"] == "done_with_errors"
    assert status["outputs"]["ocr_pages"] == ocr_pages
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_ocr.py tests/test_worker.py -k ocr_pages -v`
Expected: FAIL (`KeyError: 'ocr_pages'`).

- [ ] **Step 3: Implement**

In `ocr-api/app/ocr.py`, replace the final `return` of `process_scan()` with:

```python
    return {
        "pdf": str(pdf_path),
        "txt": str(txt_path),
        "file_name": file_name,
        # Which model read each page (None = engine text / Tesseract fallback).
        "ocr_pages": [
            {"page": i + 1, "model": page.transcript_model, "escalated": page.escalated}
            for i, page in enumerate(pages_ocr)
        ],
    }
```

In `ocr-api/app/worker.py`, directly after the `for name, result in zip(task_names, results):` loop and before `if errors:`, insert:

```python
        if "ocr_pages" in ocr_result:
            merged["ocr_pages"] = ocr_result["ocr_pages"]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/ -q`
Expected: all PASS (the existing `test_process_job_no_outputs_succeeds` still gets `outputs == {}` because its mocked result has no `ocr_pages`).

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr.py ocr-api/app/worker.py ocr-api/tests/test_ocr.py ocr-api/tests/test_worker.py
git commit -m "feat(ocr): report per-page OCR model provenance in job status

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Model comparison script

**Files:**
- Create: `ocr-api/scripts/__init__.py` (empty)
- Create: `ocr-api/scripts/compare_ocr_models.py`
- Modify: `ocr-api/Dockerfile` (COPY `scripts/` next to `app/`)
- Test: `ocr-api/tests/test_compare_ocr_models.py` (new)

**Interfaces:**
- Consumes: `prepare_image`, `call_llm`, `_client` from `app.ocr_backends.openrouter`, and `get_settings`.
- Produces: `levenshtein(a, b) -> int`, `cer(hypothesis, truth) -> float`, `compare(images: list[Path], models: list[str], truths: list[Path], out_dir: Path, settings: Settings, client) -> list[dict]`, `format_table(rows) -> str`, and `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `ocr-api/tests/test_compare_ocr_models.py`:

```python
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.config import Settings
from scripts.compare_ocr_models import cer, compare, format_table, levenshtein, main


def _result(model, text):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content=json.dumps({"text": text, "handwriting": False, "uncertain": False})))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3, cost=0.0001),
    )


def test_levenshtein():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "abc") == 0


def test_cer_normalizes_whitespace():
    assert cer("Hallo  Welt\n", "Hallo Welt") == 0.0
    assert cer("Hallo Welt", "Hallo Welk") == pytest.approx(0.1)


def test_cer_empty_truth():
    assert cer("", "") == 0.0
    assert cer("x", "") == 1.0


def test_compare_writes_transcripts_and_rows(tmp_path):
    img = tmp_path / "page1.png"
    Image.new("RGB", (50, 50), (255, 255, 255)).save(img)
    truth = tmp_path / "page1.txt"
    truth.write_text("Hallo Welt", encoding="utf-8")

    def send(**kwargs):
        if kwargs["model"] == "bad/model":
            raise RuntimeError("no route")
        return _result(kwargs["model"], "Hallo Welt")

    client = MagicMock()
    client.chat.send.side_effect = send
    settings = Settings(api_key="test", openrouter_api_key="sk", ocr_llm_fallback_models=["x/y"])

    with patch("app.ocr_backends.openrouter.time.sleep"):
        rows = compare([img], ["good/model", "bad/model"], [truth], tmp_path / "out", settings, client)

    assert rows[0]["model"] == "good/model"
    assert rows[0]["cer"] == 0.0
    assert rows[0]["cost"] == 0.0001
    assert "error" in rows[1]
    assert (tmp_path / "out" / "page1__good_model.txt").read_text(encoding="utf-8") == "Hallo Welt"
    # each model is measured on its own: no OpenRouter fallback list
    assert all("models" not in c.kwargs for c in client.chat.send.call_args_list)
    table = format_table(rows)
    assert "good/model" in table
    assert "ERROR" in table


def test_main_requires_api_key(tmp_path):
    no_key = Settings(api_key="test", openrouter_api_key="")  # explicit: ignore any host env var
    with patch("scripts.compare_ocr_models.get_settings", return_value=no_key):
        assert main([str(tmp_path / "a.png"), "--models", "m"]) == 2


def test_main_truth_count_mismatch_exits():
    with pytest.raises(SystemExit):
        main(["a.png", "b.png", "--models", "m", "--truth", "a.txt"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_compare_ocr_models.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'scripts'`.

- [ ] **Step 3: Implement**

Create an empty `ocr-api/scripts/__init__.py`.

Create `ocr-api/scripts/compare_ocr_models.py`:

```python
"""Compare OpenRouter OCR models on real scan pages (manual tool, not used by the API).

Usage (from ocr-api/, or inside the container from /ocr-api):
    python3 -m scripts.compare_ocr_models page1.png page2.tif \\
        --models google/gemini-3.1-flash-lite,google/gemini-3.5-flash \\
        [--truth page1.txt page2.txt] --out output/compare_out

Turn a delivered PDF into page images first, e.g.:
    gs -sDEVICE=pnggray -r300 -o page_%02d.png scan.pdf
"""
import argparse
import sys
from pathlib import Path

from app.config import Settings, get_settings
from app.ocr_backends.openrouter import _client, call_llm, prepare_image


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(hypothesis: str, truth: str) -> float:
    """Character error rate with whitespace normalized."""
    hyp = " ".join(hypothesis.split())
    ref = " ".join(truth.split())
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(hyp, ref) / len(ref)


def _slug(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")


def compare(images: list[Path], models: list[str], truths: list[Path], out_dir: Path,
            settings: Settings, client) -> list[dict]:
    # Measure each model on its own: no OpenRouter fallback to a different model.
    settings = settings.model_copy(update={"ocr_llm_fallback_models": []})
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, image_path in enumerate(images):
        image = prepare_image(image_path, settings.ocr_llm_image_max_side)
        truth = truths[index].read_text(encoding="utf-8") if truths else None
        for model in models:
            row = {"page": image_path.name, "model": model}
            try:
                reply = call_llm(client, image, model, settings.ocr_llm_max_tokens, settings)
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                rows.append(row)
                continue
            (out_dir / f"{image_path.stem}__{_slug(model)}.txt").write_text(reply.text, encoding="utf-8")
            row.update(
                latency_s=reply.latency_s,
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                cost=reply.cost,
                handwriting=reply.handwriting,
                uncertain=reply.uncertain,
                cer=cer(reply.text, truth) if truth is not None else None,
            )
            rows.append(row)
    return rows


def format_table(rows: list[dict]) -> str:
    header = (f"{'page':<24} {'model':<40} {'time':>7} {'tokens in/out':>13} "
              f"{'cost':>9} {'hw':>3} {'unc':>3} {'CER':>6}")
    lines = [header, "-" * len(header)]
    for r in rows:
        if "error" in r:
            lines.append(f"{r['page']:<24} {r['model']:<40} ERROR {r['error']}")
            continue
        cer_s = f"{r['cer']:.1%}" if r["cer"] is not None else "-"
        tokens = f"{r['prompt_tokens']}/{r['completion_tokens']}"
        lines.append(
            f"{r['page']:<24} {r['model']:<40} {r['latency_s']:>6.1f}s {tokens:>13} "
            f"${r['cost']:>8.4f} {'y' if r['handwriting'] else 'n':>3} "
            f"{'y' if r['uncertain'] else 'n':>3} {cer_s:>6}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare OpenRouter OCR models on scan pages.")
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--models", required=True, help="comma-separated OpenRouter model IDs")
    parser.add_argument("--truth", nargs="*", type=Path, default=[],
                        help="ground-truth .txt per image, same order as images")
    parser.add_argument("--out", type=Path, default=Path("compare_out"))
    args = parser.parse_args(argv)
    if args.truth and len(args.truth) != len(args.images):
        parser.error("--truth needs exactly one file per image")

    settings = get_settings()
    if not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 2
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    rows = compare(args.images, models, args.truth, args.out, settings, _client(settings))
    print(format_table(rows))
    print(f"\nTranscripts written to {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

In `ocr-api/Dockerfile`, directly after `COPY --chown=ocr:ocr app/ /ocr-api/app/`, add:

```dockerfile
COPY --chown=ocr:ocr scripts/ /ocr-api/scripts/
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/ -q`
Expected: all PASS.

Run: `python3 -m scripts.compare_ocr_models --help`
Expected: argparse usage text, exit 0.

- [ ] **Step 5: Commit**

```bash
git add ocr-api/scripts/ ocr-api/Dockerfile ocr-api/tests/test_compare_ocr_models.py
git commit -m "feat(scripts): compare_ocr_models tool for picking OpenRouter OCR models

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Documentation

**Files:**
- Modify: `.env.example` (OCR settings section)
- Modify: `CLAUDE.md` (pluggable backends section, config line, test layout)
- Modify: `docs/backlog.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Update `.env.example`**

Replace the line `# OCR engine: tesseract (default), paddleocr, or gcv (Google Cloud Vision stub)` with:

```
# OCR engine: tesseract (default), paddleocr, openrouter (Tesseract + vision LLM),
# or gcv (Google Cloud Vision stub)
```

Add this line to the language-code comment list, after the `paddleocr:` line:

```
#   openrouter: deu+eng+frk  (used for the Tesseract text layer; the LLM detects language itself)
```

Insert this block directly after `TRASH_TMP_FILES=true`:

```
# ── LLM OCR (OCR_ENGINE=openrouter) ───────────────────────────────────────────
# Tesseract still builds the positioned PDF text layer; a vision LLM on OpenRouter
# transcribes each page (much better on handwriting) for the .txt sidecar, AI
# metadata and mail body. Any LLM failure falls back to the Tesseract text.
# Uses OPENROUTER_API_KEY (below). Requests only route to providers that don't
# store or train on prompts (provider.data_collection=deny).
# Cheap model for every page; pages reported as handwritten/uncertain are re-run
# on the strong model (empty = no escalation). Compare models on your own pages:
#   docker compose exec ocr-api python3 -m scripts.compare_ocr_models --help
# OCR_LLM_MODEL=google/gemini-3.1-flash-lite
# OCR_LLM_STRONG_MODEL=google/gemini-3.5-flash
# Outage fallbacks tried by OpenRouter (JSON list):
# OCR_LLM_FALLBACK_MODELS=["openai/gpt-5-mini"]
# OCR_LLM_CONCURRENCY=3
# OCR_LLM_TIMEOUT=90
# OCR_LLM_MAX_TOKENS=4000
# OCR_LLM_IMAGE_MAX_SIDE=2000
# Escalate when the transcript contains more than this many [?] markers:
# OCR_LLM_ESCALATE_UNCLEAR_MAX=2
```

- [ ] **Step 2: Update `CLAUDE.md`**

In "Pluggable OCR backends", add after the `gcv.py` bullet:

```markdown
- `openrouter.py` — hybrid: runs `TesseractBackend` for the positioned lines, then transcribes each page with a vision LLM via the `openrouter` SDK into `OcrPage.transcript` (which `OcrPage.text` prefers; `build_pdf` still uses `lines`). Cheap `OCR_LLM_MODEL` first; handwritten/uncertain/truncated pages escalate to `OCR_LLM_STRONG_MODEL`. Requests set `provider.data_collection=deny`. Any LLM failure keeps the Tesseract text. Tests patch `_client`, `TesseractBackend`, `prepare_image`, and `time.sleep`. `scripts/compare_ocr_models.py` compares models on real pages.
```

Change `Literal["tesseract", "paddleocr", "gcv"]` to `Literal["tesseract", "paddleocr", "gcv", "openrouter"]`.

In "Request lifecycle", change `→ updates _status[job_id]` to `→ updates _status[job_id] (outputs include ocr_pages: model per page)`.

In "Test layout", add:

```markdown
- `tests/test_compare_ocr_models.py` — CER + comparison script (mocked client)
```

- [ ] **Step 3: Update `docs/backlog.md`**

At the end of the "Switch PaddleOCR to the lightweight (mobile) detection model" section, add:

```markdown
**Status:** Done (1a0692d). Still too slow on this host and can't read
handwriting; superseded by `OCR_ENGINE=openrouter` (2026-09-16).
```

Append these sections at the end of the file:

```markdown
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
```

- [ ] **Step 4: Verify and commit**

Run: `cd ocr-api && python3 -m pytest tests/ -q`
Expected: all PASS.

```bash
git add .env.example CLAUDE.md docs/backlog.md
git commit -m "docs: document OCR_ENGINE=openrouter, backlog Paperless content + Paddle removal

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Rollout (gated: every step needs explicit user approval)

The stack is the user's live pipeline. **Stop and ask before each step.**

- [ ] **Step 1: Final review of the branch**

Run: `cd ocr-api && python3 -m pytest tests/ -q` → all PASS. Then request a code review of `master..feat/openrouter-ocr`.

- [ ] **Step 2: Docker smoke test (ASK FIRST)**

From the repo root:

```bash
docker compose build --no-cache 2>&1 | grep -E "error|ERROR|failed|FAILED|ImportError" | grep -iv "warn"
docker compose up -d && sleep 5 && curl -sf http://localhost:9000/health
```

Expected: no build errors, and `{"status":"ok"}`. Check the port mapping in `docker-compose.yml` first, because an earlier session found the service on 8000. Also verify inside the container:

```bash
docker compose exec ocr-api python3 -c "import openrouter, httpx, pydantic; print(openrouter.VERSION, httpx.__version__, pydantic.VERSION)"
```

Expected: `1.1.148 0.28.1 2.12.5`.

- [ ] **Step 3: Compare models on real pages (ASK FIRST; costs a few cents)**

Ask the user for 3–5 page images (printed, mixed, handwritten), optionally with ground-truth `.txt` files. Place them under `ocr-api/output/compare_in/`, then run:

```bash
docker compose exec ocr-api python3 -m scripts.compare_ocr_models output/compare_in/*.png \
  --models google/gemini-3.1-flash-lite,google/gemini-3.5-flash,anthropic/claude-sonnet-5,openai/gpt-5.6-sol \
  --out output/compare_out
```

Show the user the table and point to the transcripts in `ocr-api/output/compare_out/`, and let them choose `OCR_LLM_MODEL` / `OCR_LLM_STRONG_MODEL`.

- [ ] **Step 4: Switch the engine (ASK FIRST)**

In the repo-root `.env`, set `OCR_ENGINE=openrouter` plus any chosen `OCR_LLM_*` values, then run `docker compose up -d`. Ask the user to do a real button scan that includes a handwritten page, and check:
- `docker compose logs ocr-api | grep -E "OCR LLM:|Page [0-9]+:"` shows the per-page model and cost, and the summary line.
- `GET /scan/status/<job_id>` → `outputs.ocr_pages` lists the models.
- The `.txt` / mail body contains the handwriting.

- [ ] **Step 5: Merge (ASK FIRST)**

Use superpowers:finishing-a-development-branch to merge `feat/openrouter-ocr` into `master` and push.
