from pathlib import Path


class PaddleOcrBackend:
    def run(self, pages: list[Path], language: str) -> str:
        raise NotImplementedError("PaddleOCR backend not yet implemented")
