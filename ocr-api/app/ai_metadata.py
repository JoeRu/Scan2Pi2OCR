import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime

import httpx

from app.config import Settings

logger = logging.getLogger("app.ai_metadata")


@dataclass
class AiMetadata:
    topic: str
    korrespondent: str
    dokumenttyp: str
    tags: list[str]
    filename_stem: str


def sanitize_filename_part(s: str, max_len: int = 40) -> str:
    s = s.lower()
    s = s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = re.sub(r"[^a-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s)
    s = s.strip("_")
    return s[:max_len]


def _build_prompt(text: str, language: str, document_types: list[str] | None) -> str:
    doc_types_str = ", ".join(document_types) if document_types else None

    if language == "de":
        types_line = doc_types_str if doc_types_str else "keine vorgegeben"
        return (
            "Du bist ein Dokumentenklassifikator. Analysiere den folgenden OCR-Text eines gescannten Dokuments.\n"
            "Antworte ausschließlich mit einem JSON-Objekt (kein Markdown, kein Text darum herum):\n\n"
            "{\n"
            '  "topic": "<prägnantes Thema, max 5 Wörter, geeignet für einen Dateinamen>",\n'
            '  "korrespondent": "<Absender oder Organisation, max 3 Wörter>",\n'
            '  "dokumenttyp": "<wähle einen aus der Liste unten oder schlage einen neuen vor wenn keiner passt>",\n'
            '  "tags": ["<tag1>", "<tag2>"]\n'
            "}\n\n"
            "Erlaubte Dokumenttypen (bevorzuge diese):\n"
            f"{types_line}\n\n"
            "OCR-Text (erste Seite):\n"
            "---\n"
            f"{text}"
        )
    else:
        types_line = doc_types_str if doc_types_str else "none provided"
        return (
            "You are a document classifier. Analyze the following OCR text of a scanned document.\n"
            "Reply ONLY with a JSON object (no markdown, no surrounding text):\n\n"
            "{\n"
            '  "topic": "<concise topic, max 5 words, suitable for a filename>",\n'
            '  "korrespondent": "<sender or organisation, max 3 words>",\n'
            '  "dokumenttyp": "<choose one from the list below or propose a new one if none fits>",\n'
            '  "tags": ["<tag1>", "<tag2>"]\n'
            "}\n\n"
            "Allowed document types (prefer these):\n"
            f"{types_line}\n\n"
            "OCR text (first page):\n"
            "---\n"
            f"{text}"
        )


async def extract_ai_metadata(
    txt_path: str,
    scan_timestamp: datetime,
    settings: Settings,
    document_types: list[str] | None = None,
) -> AiMetadata | None:
    try:
        with open(txt_path, encoding="utf-8", errors="replace") as f:
            text = f.read(3000)

        prompt = _build_prompt(text, settings.ai_document_language, document_types)

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "HTTP-Referer": "https://github.com/Scan2Pi2OCR",
                },
                json={
                    "model": settings.openrouter_model,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()

        data = response.json()
        content = data["choices"][0]["message"]["content"]
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
        metadata = json.loads(content)

        ts = scan_timestamp.strftime("%Y%m%d_%H%M%S")
        topic_part = sanitize_filename_part(metadata["topic"])
        korr_part = sanitize_filename_part(metadata["korrespondent"])
        filename_stem = f"{ts}_{topic_part}_{korr_part}"

        return AiMetadata(
            topic=metadata["topic"],
            korrespondent=metadata["korrespondent"],
            dokumenttyp=metadata["dokumenttyp"],
            tags=metadata.get("tags", []),
            filename_stem=filename_stem,
        )

    except Exception as exc:
        logger.warning("AI metadata extraction failed: %s: %s", type(exc).__name__, exc)
        return None
