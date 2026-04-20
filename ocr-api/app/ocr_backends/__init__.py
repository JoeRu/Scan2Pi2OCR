from app.ocr_backends.base import OcrBackend

__all__ = ["OcrBackend", "get_backend"]


def get_backend(engine: str) -> OcrBackend:
    if engine == "tesseract":
        from app.ocr_backends.tesseract import TesseractBackend
        return TesseractBackend()
    if engine == "paddleocr":
        from app.ocr_backends.paddleocr import PaddleOcrBackend
        return PaddleOcrBackend()
    if engine == "gcv":
        from app.ocr_backends.gcv import GoogleCloudVisionBackend
        return GoogleCloudVisionBackend()
    raise ValueError(
        f"Unknown OCR engine: {engine!r}. Valid values: 'tesseract', 'paddleocr', 'gcv'."
    )
