from pathlib import Path
from typing import Protocol, runtime_checkable

from app.ocr_backends.types import OcrPage


@runtime_checkable
class OcrBackend(Protocol):
    def run(self, pages: list[Path], language: str) -> list[OcrPage]:
        ...
