import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger("app.ocr_backends.paddleocr")

_PADDLE_SUPPORTED = {".jpg", ".jpeg", ".png", ".bmp", ".pdf"}

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
        ocr = PaddleOCR(lang=lang)

        page_texts: list[str] = []
        for page in pages:
            png_path, converted = self._to_png_if_needed(page)
            try:
                results = ocr.predict(str(png_path), use_textline_orientation=True)
                if not results:
                    page_texts.append("")
                    continue
                rec_texts: list[str] = results[0].get("rec_texts") or []
                page_texts.append("\n".join(rec_texts))
            finally:
                if converted:
                    png_path.unlink(missing_ok=True)

        text = "\n\n".join(t for t in page_texts if t)
        logger.info("PaddleOCR finished, %d chars extracted", len(text))
        return text

    def _to_png_if_needed(self, page: Path) -> tuple[Path, bool]:
        """Convert to PNG if the format isn't natively supported by PaddleOCR."""
        if page.suffix.lower() in _PADDLE_SUPPORTED:
            return page, False
        png_path = page.with_suffix(".png")
        with Image.open(page) as img:
            img.save(str(png_path), format="PNG")
        logger.debug("Converted %s → %s for PaddleOCR", page.name, png_path.name)
        return png_path, True

    def _map_language(self, language: str) -> str:
        for code in language.split("+"):
            if code in _LANGUAGE_MAP:
                return _LANGUAGE_MAP[code]
        return "en"
