import logging
from pathlib import Path

logger = logging.getLogger("app.ocr_backends.paddleocr")

try:
    from paddleocr import PaddleOCR
except ImportError:
    PaddleOCR = None  # type: ignore[assignment,misc]

_LANGUAGE_MAP = {
    "deu": "german",
    "eng": "en",
    "frk": "german",  # Fraktur: no PaddleOCR equivalent, fall back to german
}


class PaddleOcrBackend:
    def run(self, pages: list[Path], language: str) -> str:
        if not pages:
            raise ValueError("No pages provided to PaddleOcrBackend")

        lang = self._map_language(language)
        logger.info("Running PaddleOCR on %d page(s), mapped language=%s", len(pages), lang)
        ocr = PaddleOCR(use_angle_cls=True, lang=lang, show_log=False)

        page_texts: list[str] = []
        for page in pages:
            result = ocr.ocr(str(page), cls=True)
            if not result or not result[0]:
                page_texts.append("")
                continue
            lines = [line[1][0] for line in result[0] if line and line[1]]
            page_texts.append("\n".join(lines))

        text = "\n\n".join(t for t in page_texts if t)
        logger.info("PaddleOCR finished, %d chars extracted", len(text))
        return text

    def _map_language(self, language: str) -> str:
        for code in language.split("+"):
            if code in _LANGUAGE_MAP:
                return _LANGUAGE_MAP[code]
        return "en"
