# Design: OpenRouter LLM OCR engine (hybrid with Tesseract)

**Date:** 2026-09-16
**Status:** Approved

## Goal

Add an `OCR_ENGINE=openrouter` backend that transcribes scanned pages with a
vision LLM via OpenRouter, so that:

- scans are fast on this CPU-only host (PaddleOCR takes minutes per page), and
- handwritten text is recognized (Tesseract and PaddleOCR fail on it).

## Decisions (from brainstorming)

- **Text layer: hybrid.** Tesseract still produces the positioned lines
  (`OcrLine` boxes) for the invisible PDF text layer. The LLM transcription is
  used for the `.txt` sidecar, AI metadata (and so the filename and Paperless
  tags/type/correspondent), and the mail body. Accepted trade-off: the PDF text
  layer, and therefore Ctrl+F in the PDF **and Paperless full-text search**
  (Paperless reads the PDF layer, and `deliver_paperless` uploads only the
  PDF), covers only what Tesseract read. Handwriting is not searchable there.
  Deferred; see *Out of scope*.
- **Model choice: escalation tier.** Every page goes to a cheap model first.
  Pages the model reports as handwritten or uncertain are re-run on a stronger
  model.
- **Privacy: no-training providers.** Every OCR request sets
  `provider={"data_collection": "deny"}`. No ZDR restriction.
- **Never fail a scan because of the LLM.** Every error falls back to Tesseract
  text, which is today's quality.
- **Client: the official `openrouter` Python SDK** (PyPI `openrouter`, verified
  1.1.148, Python >= 3.10). `ai_metadata.py` keeps using httpx (out of scope).
- **Approach A:** a new backend that wraps `TesseractBackend`. Rejected
  alternatives: B, a separate `ENABLE_LLM_TRANSCRIPTION` pipeline step (a
  setting that overlaps with `OCR_ENGINE`, more combinations); C, merging OCR
  into the AI metadata call (couples two jobs, and a single failure loses both).

## Grounding (research, 2026-09-16)

- OpenRouter lists no dedicated OCR model (GLM-OCR is not available there).
  Candidates are general vision models.
- codesota handwriting ranking (IAM, April 2026): the frontier VLMs (GPT-5,
  Claude Opus 4.7, Gemini 3) lead at about 1.2–1.4% CER, and GPT-5-mini is
  about 1.5%. Only Gemini returns (line-level, approximate) boxes, which is why
  the design uses the hybrid text layer.
- GLM-OCR is strong on printed documents but weaker on handwriting (86.1 vs
  Gemini 3 Pro 94.5 on Handwritten-KIE).
- Prices taken from the live OpenRouter `/models` list (USD per 1M tokens,
  in/out): `google/gemini-3.1-flash-lite` 0.25/1.50,
  `google/gemini-3.5-flash` 1.50/9.00, `anthropic/claude-sonnet-5` 2/10,
  `openai/gpt-5.6-sol` 2/10.

## Architecture

### 1. `app/ocr_backends/types.py`

```python
@dataclass
class OcrPage:
    lines: list[OcrLine] = field(default_factory=list)
    transcript: str | None = None        # LLM text; overrides joined lines
    transcript_model: str | None = None  # model that produced transcript
    escalated: bool = False

    @property
    def text(self) -> str:
        if self.transcript is not None:
            return self.transcript
        return "\n".join(line.text for line in self.lines)
```

`build_pdf.py` keeps reading only `lines` and does not change.

### 2. `app/ocr_backends/openrouter.py`: `OpenRouterBackend`

`run(pages, language) -> list[OcrPage]`:

1. `TesseractBackend().run(pages, language)` produces the boxed lines. This
   step always runs, and its exceptions propagate as they do today.
2. If `openrouter_api_key` is empty, log ERROR once and return the Tesseract
   pages unchanged.
3. Transcribe the pages concurrently with
   `ThreadPoolExecutor(ocr_llm_concurrency)` using the SDK's **sync** client,
   keeping page order. `run()` is already called in an executor from
   `process_scan()`.
4. Per page:
   - **Image prep** (`prepare_image(path, max_side) -> bytes`): convert to
     grayscale, downscale so the long side is at most `ocr_llm_image_max_side`
     (never upscale), encode as JPEG quality 85, then base64 into a
     `data:image/jpeg;base64,...` URL.
   - **Request:** a user message with the prompt text plus `image_url`;
     `model=ocr_llm_model`; `models=ocr_llm_fallback_models` (if non-empty);
     `provider={"data_collection": "deny"}`; `temperature=0`;
     `max_tokens=ocr_llm_max_tokens`; `response_format={"type": "json_object"}`;
     timeout `ocr_llm_timeout`.
   - **Prompt** (English instructions): transcribe all text on the page
     exactly, in reading order, in its original language (German, English,
     Fraktur possible); keep line breaks; mark unreadable words as `[?]`; do not
     summarize, translate, or correct. Reply only with JSON
     `{"text": str, "handwriting": bool, "uncertain": bool}`, where
     `handwriting` is true if any handwritten text is present and `uncertain`
     is true if parts could not be read confidently.
   - **Parse** (`parse_reply(content) -> (text, handwriting, uncertain)`):
     strip code fences, then `json.loads`. If that fails and the content is not
     empty, use the raw content as the text with `handwriting=False` and
     `uncertain=False`.
   - **Escalate** when `handwriting` is true, `uncertain` is true,
     `text.count("[?]") > ocr_llm_escalate_unclear_max`, or
     `finish_reason == "length"`. The escalation repeats the request with
     `model=ocr_llm_strong_model` and `max_tokens=2 * ocr_llm_max_tokens`.
     Escalation is skipped when the strong model is empty or equal to the cheap
     model.
   - Set `transcript`, `transcript_model` (the model reported in the response,
     otherwise the requested one), and `escalated`.
5. The SDK client is created through a module-level `_client(settings)` factory
   so tests can patch it.

### 3. Failure handling

| Situation | Result |
|---|---|
| API key missing | ERROR log; all pages keep Tesseract text |
| Network error / 429 / 5xx | OpenRouter `models` fallback; one retry with backoff (2 s); then the page keeps Tesseract text |
| Timeout | Same as a network error |
| Invalid JSON, non-empty content | Raw content is the transcript; no escalation |
| Empty text/content on a page | Counts as a failure; the page keeps Tesseract text |
| `finish_reason == "length"` | Escalate |
| Strong model fails or returns empty | Keep the cheap model's result (`escalated=False`) |

A page that keeps its Tesseract text has `transcript=None` and
`transcript_model=None`. Every fallback logs a WARNING with the page index and
the exception type and message.

### 4. `app/ocr.py`: provenance

`process_scan()` adds this to its result dict:

```python
"ocr_pages": [
    {"page": i + 1, "model": p.transcript_model, "escalated": p.escalated}
    for i, p in enumerate(pages_ocr)
]
```

`model` is `None` for pages that fell back to Tesseract, and also for other
engines. The worker does not currently pass `process_scan()` keys through
(`worker.py` builds `outputs` only from delivery results), so `worker.py`
sets `merged["ocr_pages"] = ocr_result["ocr_pages"]` for both the `done` and
`done_with_errors` statuses. `tests/test_worker.py` covers this.

### 5. `app/config.py`

```python
ocr_engine: Literal["tesseract", "paddleocr", "gcv", "openrouter"] = "tesseract"
ocr_llm_model: str = "google/gemini-3.1-flash-lite"
ocr_llm_strong_model: str = "google/gemini-3.5-flash"
ocr_llm_fallback_models: list[str] = []   # env: JSON list
ocr_llm_concurrency: int = 3
ocr_llm_timeout: int = 90
ocr_llm_max_tokens: int = 4000
ocr_llm_image_max_side: int = 2000
ocr_llm_escalate_unclear_max: int = 2
```

The API key reuses the existing `openrouter_api_key`. `get_backend("openrouter")`
lazily imports `OpenRouterBackend`, and the `ValueError` message lists
`'openrouter'`.

### 6. Logging and cost

- One INFO line per page: page index, model, prompt/completion tokens,
  `usage.cost` (if present), latency, and the escalation reason (if any).
- One INFO summary per job:
  `OCR LLM: <n> pages, <k> escalated, <f> fallback, $<cost>, <seconds>s`.
- No budget cap (YAGNI). The cost log makes it possible to add one later.
- Expected cost: about $0.001 per printed page (Flash-Lite) and about
  $0.005–0.01 per escalated page.

### 7. Dependencies and docs

- `requirements.txt`: add `openrouter==1.1.148`.
- `.env.example`: an `OCR_ENGINE=openrouter` block documenting all `OCR_LLM_*`
  variables and the privacy setting.
- `CLAUDE.md`: add `openrouter.py` to the backend list.
- `docs/backlog.md`: add "Remove PaddleOCR from the image once openrouter is the
  default" (image size); mark the handwriting/speed need as addressed.

## Model comparison script

`ocr-api/scripts/compare_ocr_models.py` is a manual tool and is not part of CI.

```
python -m scripts.compare_ocr_models <image>... \
  --models m1,m2,... [--truth page.txt ...] --out compare_out/
```

- It reuses `prepare_image`, the prompt, and `parse_reply` from
  `openrouter.py`, so the comparison matches production requests. It makes no
  escalation calls (each model is measured on its own).
- It prints a table per page and model: latency, tokens, cost, the
  `handwriting` and `uncertain` flags, and CER when `--truth` is given (plain
  Levenshtein, no new dependency).
- It writes `<out>/<page-stem>__<model-slug>.txt` per result.
- It can run in the live container with `docker compose exec ocr-api ...`
  (no restart).

## Testing

Unit tests in `tests/test_ocr_backends.py` (plus `test_ocr.py`) never make
network calls. They patch `_client`, `TesseractBackend.run`, and
`get_settings()`, since tests have no `.env`.

- `run()` returns one page per input, keeps the Tesseract `lines`, and sets
  `transcript`.
- The request contains `provider.data_collection="deny"`, `temperature=0`, the
  configured model, and the fallback `models`.
- Escalation fires on `handwriting`, on `uncertain`, when the `[?]` count
  exceeds the threshold, and on `finish_reason="length"`. It does not fire on a
  clean printed page, or when the strong model is empty or equal to the cheap
  model.
- Fallbacks: exception or timeout, empty content, invalid JSON (raw text used),
  strong model failure (cheap result kept), and missing API key (no client
  calls).
- Page order is preserved with concurrency above 1.
- `prepare_image`: grayscale, long side ≤ max, no upscaling, JPEG bytes.
- `OcrPage.text` prefers the transcript.
- `process_scan()` result contains `ocr_pages`; the worker status `outputs` include it.
- `get_backend("openrouter")` returns the backend, and the config accepts
  `"openrouter"`.
- `parse_reply`: plain JSON, fenced JSON, and non-JSON input.

## Rollout

The stack is live production, so any build, restart, or `.env` change needs
explicit approval first.

1. Implement on `feat/openrouter-ocr` with TDD, and run the full suite green
   (except the two known `test_paperless.py` failures).
2. Docker smoke test from CLAUDE.md (only after approval).
3. Run the comparison script on 3–5 real pages (printed, mixed, handwritten),
   then set `OCR_LLM_MODEL` and `OCR_LLM_STRONG_MODEL` in `.env`.
4. Set `OCR_ENGINE=openrouter` and do one real button scan with a handwritten
   page. Check `ocr_pages` in the status and the cost log line.
5. Merge to master.

## Out of scope

- LLM transcript in Paperless full-text search (decided 2026-09-16: deferred).
  Follow-up: poll the Paperless consume task for the document id, then `PATCH
  /api/documents/<id>/` with `content=<LLM transcript>`. Added to
  `docs/backlog.md` during implementation.

- Positioned (highlightable) text for handwriting. This would need LLM/box
  alignment ("Hybrid + align").
- Migrating `ai_metadata.py` to the SDK.
- Merging transcription with metadata classification.
- Removing PaddleOCR (backlog).
- Per-scan model selection from the Pi.
