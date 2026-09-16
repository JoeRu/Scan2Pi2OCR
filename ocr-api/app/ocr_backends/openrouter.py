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
