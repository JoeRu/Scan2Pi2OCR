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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from app.config import Settings, get_settings
from app.ocr_backends.tesseract import TesseractBackend
from app.ocr_backends.types import OcrPage

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
    "- Mark crossed-out text as [crossed out: <text>] instead of merging it into the sentence.\n"
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


def _as_bool_flag(value) -> bool:
    """True only for the JSON boolean `true` or a case-insensitive "true" string.

    Guards against `bool("false")` (a non-empty string) being truthy: an LLM
    that replies with the string "false" instead of the JSON literal must not
    be read as handwriting=True/uncertain=True (M1).
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _load_json_content(content: str) -> object | None:
    """Parse `content` (optionally fenced in ```json ... ```) as JSON, or None."""
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def parse_reply(content: str) -> tuple[str, bool, bool]:
    """Return (text, handwriting, uncertain); non-JSON content is used as raw text."""
    raw = (content or "").strip()
    data = _load_json_content(raw)
    if data is None:
        return raw, False, False
    if isinstance(data, str):
        # A bare JSON string (e.g. `"Hallo"`): use its value, not the raw
        # content (which would otherwise include the surrounding quotes; M2).
        return data.strip(), False, False
    if not isinstance(data, dict):
        return raw, False, False
    return (
        str(data.get("text") or "").strip(),
        _as_bool_flag(data.get("handwriting")),
        _as_bool_flag(data.get("uncertain")),
    )


_TRUNCATED_TEXT_FIELD_RE = re.compile(r'"text"\s*:\s*"')


def recover_truncated_text(content: str) -> str | None:
    """Best-effort recovery of the "text" value from JSON cut off mid-string by
    `finish_reason == "length"` (e.g. `{"text": "Sehr geehrte\\nDamen...`).

    Locates the start of the "text" value and decodes everything after it as a
    JSON string, trimming back over an incomplete trailing escape sequence (a
    lone backslash, or a cut-off `\\uXXXX`) until it decodes. Returns None if
    nothing usable can be recovered.
    """
    match = _TRUNCATED_TEXT_FIELD_RE.search(content or "")
    if not match:
        return None
    fragment = content[match.end():]
    while True:
        try:
            text = json.loads('"' + fragment + '"')
        except json.JSONDecodeError:
            if not fragment:
                return None
            fragment = fragment[:-1]
            continue
        text = text.strip()
        return text or None


def build_request(image: bytes, model: str, max_tokens: int, fallback_models: list[str],
                  reasoning_effort: str = "") -> dict:
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
    if reasoning_effort:
        request["reasoning"] = {"effort": reasoning_effort}
    return request


def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # list of content parts
        return "".join(getattr(part, "text", None) or "" for part in content)
    return ""


def call_llm(client, image: bytes, model: str, max_tokens: int, settings: Settings,
             reasoning_effort: str = "") -> LlmReply:
    """One transcription request, retried once on error. Raises on failure or empty text."""
    request = build_request(image, model, max_tokens, settings.ocr_llm_fallback_models,
                            reasoning_effort)
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
    content = _content_text(choice.message.content)
    raw = (content or "").strip()
    if choice.finish_reason == "length" and _load_json_content(raw) is None:
        # json_object mode cut off mid-string: the raw content is unusable as a
        # transcript verbatim (it's the JSON wrapper plus escape sequences, not
        # prose). Recover just the "text" value instead of storing that (I1).
        recovered = recover_truncated_text(raw)
        if not recovered:
            raise ValueError(f"truncated, unrecoverable JSON from {model}")
        text, handwriting, uncertain = recovered, False, False
    else:
        text, handwriting, uncertain = parse_reply(content)
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
        # The SDK's own default retry policy backs off on 5XX for up to an hour
        # (openrouter/chat.py). call_llm()'s single 2s-retry-once is the only
        # retry policy we want; without this, a scan can hang for hours.
        retry_config=None,
    )


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
        # `with client:` (not `with _client(settings) as client:`) so `client`
        # keeps referring to the object _client() returned: a MagicMock's
        # __enter__() auto-generates a different MagicMock unless configured,
        # which would otherwise silently detach `_transcribe`'s calls from the
        # object tests assert on.
        with client:
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
            reply = call_llm(client, image, settings.ocr_llm_model, settings.ocr_llm_max_tokens,
                             settings, settings.ocr_llm_reasoning_effort)
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
            strong_reply = call_llm(client, image, strong, 2 * settings.ocr_llm_max_tokens,
                                    settings, settings.ocr_llm_strong_reasoning_effort)
        except Exception as exc:
            logger.warning("Page %d: escalation to %s failed, keeping %s result: %s: %s",
                           page_no, strong, reply.model, type(exc).__name__, exc)
            return _PageOutcome(reply, False, reply.cost)
        _log_reply(page_no, strong_reply, None)
        return _PageOutcome(strong_reply, True, reply.cost + strong_reply.cost)


def _log_reply(page_no: int, reply: LlmReply, escalate_reason: str | None) -> None:
    # A truncated reply means the token budget ran out (often on reasoning): warn.
    level = logging.WARNING if reply.finish_reason == "length" else logging.INFO
    logger.log(
        level,
        "Page %d: model=%s fin=%s tokens=%d/%d cost=$%.4f latency=%.1fs%s",
        page_no, reply.model, reply.finish_reason, reply.prompt_tokens, reply.completion_tokens,
        reply.cost, reply.latency_s,
        f" escalate={escalate_reason}" if escalate_reason else "",
    )
