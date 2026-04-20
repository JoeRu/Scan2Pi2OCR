from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class OcrBackend(Protocol):
    def run(self, pages: list[Path], language: str) -> str:
        ...
