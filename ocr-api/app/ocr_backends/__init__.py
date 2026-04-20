from app.ocr_backends.base import OcrBackend

__all__ = ["OcrBackend", "get_backend"]


def get_backend(engine: str) -> OcrBackend:
    raise NotImplementedError(f"No backend registered for {engine!r}")
