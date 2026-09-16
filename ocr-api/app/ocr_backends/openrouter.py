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
