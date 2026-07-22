import logging
from pathlib import Path

from PIL import Image

from app.config import get_settings
from app.ocr_backends.types import OcrLine, OcrPage

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
    def run(self, pages: list[Path], language: str) -> list[OcrPage]:
        if not pages:
            raise ValueError("No pages provided to PaddleOcrBackend")

        lang = self._map_language(language)
        logger.info("Running PaddleOCR on %d page(s), mapped language=%s", len(pages), lang)
        settings = get_settings()
        # enable_mkldnn=False: oneDNN triggers a NotImplementedError on some CPUs with PaddlePaddle 3.x
        ocr = PaddleOCR(lang=lang, enable_mkldnn=False)

        result_pages: list[OcrPage] = []
        for page in pages:
            png_path, converted = self._to_png_if_needed(page)
            try:
                results = ocr.predict(
                    str(png_path),
                    use_textline_orientation=True,
                    text_det_limit_type=settings.paddle_det_limit_type,
                    text_det_limit_side_len=settings.paddle_det_limit_side_len,
                )
                result_pages.append(self._to_ocr_page(results))
            finally:
                if converted:
                    png_path.unlink(missing_ok=True)

        total = sum(len(p.lines) for p in result_pages)
        logger.info("PaddleOCR finished, %d line(s) across %d page(s)", total, len(result_pages))
        return result_pages

    @staticmethod
    def _to_ocr_page(results) -> OcrPage:
        if not results:
            return OcrPage([])
        data = results[0]
        rec_texts = data.get("rec_texts") or []
        rec_boxes = data.get("rec_boxes")
        if rec_boxes is None:
            return OcrPage([])
        lines = [
            OcrLine(text=text, x0=int(box[0]), y0=int(box[1]), x1=int(box[2]), y1=int(box[3]))
            for text, box in zip(rec_texts, rec_boxes)
        ]
        return OcrPage(lines)

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
