import pytest
from pathlib import Path
from typing import Protocol, runtime_checkable
from app.ocr_backends.base import OcrBackend
from app.ocr_backends import get_backend
from app.ocr_backends.tesseract import TesseractBackend


def test_ocr_backend_protocol_is_checkable():
    @runtime_checkable
    class _P(OcrBackend, Protocol): ...  # noqa: E701 — satisfies Protocol

    class Good:
        def run(self, pages: list[Path], language: str) -> str:
            return ""

    class Bad:
        pass

    assert isinstance(Good(), _P)
    assert not isinstance(Bad(), _P)


from pathlib import Path as _Path
from app.ocr_backends.build_pdf import build_searchable_pdf


def _make_minimal_tif(path: _Path) -> None:
    """Write a 10×10 white TIFF at 300 DPI so Pillow can open it."""
    from PIL import Image
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    img.save(str(path), dpi=(300, 300))


def test_build_searchable_pdf_creates_file(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_minimal_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], "hello world", out)
    assert out.exists()
    assert out.stat().st_size > 0


def test_build_searchable_pdf_text_in_content(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_minimal_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], "searchable text here", out)
    raw = out.read_bytes()
    # PDF content streams may be compressed; check the uncompressed raw bytes
    # for our text OR check that the PDF contains the text in a readable form
    assert b"searchable" in raw


def _make_realistic_tif(path: _Path) -> None:
    """248×350 px at 300 DPI ≈ 21×29.7 mm (A4 proxy)."""
    from PIL import Image
    img = Image.new("RGB", (248, 350), (255, 255, 255))
    img.save(str(path), dpi=(300, 300))


def test_build_searchable_pdf_realistic_page(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_realistic_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], "realistic page text", out)
    raw = out.read_bytes()
    assert out.stat().st_size > 0
    assert b"realistic" in raw


def test_build_searchable_pdf_multi_page(tmp_path):
    pages = []
    for i in range(3):
        p = tmp_path / f"scan_{i:04d}.pnm.tif"
        _make_minimal_tif(p)
        pages.append(p)
    out = tmp_path / "out.pdf"
    build_searchable_pdf(pages, "multi page text", out)
    content = out.read_bytes().decode("latin-1")
    # PDF should declare 3 pages
    assert content.count("/Page\n") >= 3 or "/Count 3" in content


from unittest.mock import patch, MagicMock
from app.ocr_backends.tesseract import TesseractBackend


def test_tesseract_run_returns_text(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    expected_text = "Hallo Welt\n"

    def fake_run(cmd, capture_output, text, cwd):
        # tesseract writes the output txt file as a side effect
        (Path(cwd) / "_ocr_out.txt").write_text(expected_text)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        backend = TesseractBackend()
        result = backend.run([page], "deu+eng")

    assert result == expected_text


def test_tesseract_run_writes_scan_list(tmp_path):
    page1 = tmp_path / "scan_0001.pnm.tif"
    page2 = tmp_path / "scan_0002.pnm.tif"
    page1.touch()
    page2.touch()

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.txt").write_text("text")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        TesseractBackend().run([page1, page2], "deu")

    scan_list = (tmp_path / "scan_list.txt").read_text()
    assert "scan_0001.pnm.tif" in scan_list
    assert "scan_0002.pnm.tif" in scan_list


def test_tesseract_run_raises_on_empty_pages():
    with pytest.raises(ValueError, match="No pages"):
        TesseractBackend().run([], "deu")


def test_tesseract_run_raises_on_subprocess_failure(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    with patch("app.ocr_backends.tesseract.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="", stderr="tesseract failed")):
        with pytest.raises(RuntimeError, match="Tesseract failed"):
            TesseractBackend().run([page], "deu")


def test_get_backend_tesseract():
    backend = get_backend("tesseract")
    assert isinstance(backend, TesseractBackend)


def test_get_backend_unknown_raises():
    with pytest.raises(ValueError, match="Unknown OCR engine"):
        get_backend("nonexistent")


def test_get_backend_paddleocr_returns_backend():
    from app.ocr_backends.paddleocr import PaddleOcrBackend
    backend = get_backend("paddleocr")
    assert isinstance(backend, PaddleOcrBackend)


def test_get_backend_gcv_returns_backend():
    from app.ocr_backends.gcv import GoogleCloudVisionBackend
    backend = get_backend("gcv")
    assert isinstance(backend, GoogleCloudVisionBackend)


from app.ocr_backends.paddleocr import PaddleOcrBackend


def test_paddleocr_run_returns_text(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()

    fake_result = [[
        [[[0, 0], [10, 0], [10, 10], [0, 10]], ("Hello World", 0.99)],
        [[[0, 12], [10, 12], [10, 22], [0, 22]], ("Second line", 0.95)],
    ]]

    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR:
        mock_ocr_instance = MagicMock()
        mock_ocr_instance.ocr.return_value = fake_result
        MockOCR.return_value = mock_ocr_instance

        backend = PaddleOcrBackend()
        result = backend.run([page], "deu+eng")

    assert "Hello World" in result
    assert "Second line" in result


def test_paddleocr_run_handles_empty_result(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()

    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR:
        mock_ocr_instance = MagicMock()
        mock_ocr_instance.ocr.return_value = [[]]
        MockOCR.return_value = mock_ocr_instance

        result = PaddleOcrBackend().run([page], "deu")

    assert result == ""


def test_paddleocr_language_mapping():
    backend = PaddleOcrBackend()
    assert backend._map_language("deu+eng+frk") == "german"
    assert backend._map_language("eng") == "en"
    assert backend._map_language("deu") == "german"
    assert backend._map_language("frk") == "german"
    assert backend._map_language("unknown") == "en"


def test_paddleocr_run_raises_on_empty_pages():
    with pytest.raises(ValueError, match="No pages"):
        PaddleOcrBackend().run([], "deu")
