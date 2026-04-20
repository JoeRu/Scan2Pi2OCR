import logging
from pathlib import Path

logger = logging.getLogger("app.ocr_backends.gcv")


class GoogleCloudVisionBackend:
    def run(self, pages: list[Path], language: str) -> str:
        raise NotImplementedError(
            "Google Cloud Vision backend is not yet implemented. "
            "Set OCR_ENGINE=tesseract or OCR_ENGINE=paddleocr in your environment."
        )
