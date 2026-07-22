import pytest
from pathlib import Path
from typing import Protocol, runtime_checkable
from PIL import Image as PILImage
from app.ocr_backends.base import OcrBackend
from app.ocr_backends import get_backend
from app.ocr_backends.tesseract import TesseractBackend


def _make_tif(path: Path) -> None:
    PILImage.new("RGB", (10, 10), color=(255, 255, 255)).save(str(path), format="TIFF")


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
from app.config import Settings


_TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _tsv_row(level, page, block, par, line, word, left, top, width, height, conf, text):
    return "\t".join(str(v) for v in
                     [level, page, block, par, line, word, left, top, width, height, conf, text])


def test_tesseract_run_returns_pages_with_lines(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    tsv = "\n".join([
        _TSV_HEADER,
        _tsv_row(5, 1, 1, 1, 1, 1, 10, 20, 40, 15, 96, "Hallo"),
        _tsv_row(5, 1, 1, 1, 1, 2, 55, 20, 30, 15, 95, "Welt"),
        _tsv_row(5, 1, 1, 1, 2, 1, 10, 50, 60, 15, 90, "Zeile2"),
    ])

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.tsv").write_text(tsv)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        pages = TesseractBackend().run([page], "deu+eng")

    assert len(pages) == 1
    assert [l.text for l in pages[0].lines] == ["Hallo Welt", "Zeile2"]
    line0 = pages[0].lines[0]
    # union of the two words' boxes: x0=10, y0=20, x1=55+30=85, y1=20+15=35
    assert (line0.x0, line0.y0, line0.x1, line0.y1) == (10, 20, 85, 35)


def test_tesseract_skips_low_conf_and_empty(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    tsv = "\n".join([
        _TSV_HEADER,
        _tsv_row(5, 1, 1, 1, 1, 1, 10, 20, 40, 15, -1, "ghost"),   # conf -1 dropped
        _tsv_row(5, 1, 1, 1, 1, 2, 60, 20, 40, 15, 90, "  "),       # empty dropped
        _tsv_row(4, 1, 1, 1, 1, 0, 0, 0, 0, 0, -1, ""),             # non-word level dropped
        _tsv_row(5, 1, 1, 1, 1, 3, 110, 20, 40, 15, 88, "keep"),
    ])

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.tsv").write_text(tsv)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        pages = TesseractBackend().run([page], "deu")

    assert [l.text for l in pages[0].lines] == ["keep"]


def test_tesseract_returns_one_page_per_input_even_if_blank(tmp_path):
    p1 = tmp_path / "scan_0001.pnm.tif"; p1.touch()
    p2 = tmp_path / "scan_0002.pnm.tif"; p2.touch()
    tsv = "\n".join([_TSV_HEADER, _tsv_row(5, 1, 1, 1, 1, 1, 10, 20, 40, 15, 96, "only")])

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.tsv").write_text(tsv)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        pages = TesseractBackend().run([p1, p2], "deu")

    assert len(pages) == 2
    assert [l.text for l in pages[0].lines] == ["only"]
    assert pages[1].lines == []


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


def test_paddleocr_run_returns_pages_with_boxes(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)
    fake_result = [{
        "rec_texts": ["Hello World", "Second line"],
        "rec_boxes": [[10, 20, 100, 40], [10, 50, 120, 70]],
        "rec_scores": [0.99, 0.95],
    }]
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR, \
         patch("app.ocr_backends.paddleocr.get_settings", return_value=Settings(api_key="test")):
        inst = MagicMock()
        inst.predict.return_value = fake_result
        MockOCR.return_value = inst
        pages = PaddleOcrBackend().run([page], "deu+eng")

    assert len(pages) == 1
    assert [l.text for l in pages[0].lines] == ["Hello World", "Second line"]
    first = pages[0].lines[0]
    assert (first.x0, first.y0, first.x1, first.y1) == (10, 20, 100, 40)


def test_paddleocr_run_handles_empty_result(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR, \
         patch("app.ocr_backends.paddleocr.get_settings", return_value=Settings(api_key="test")):
        inst = MagicMock()
        inst.predict.return_value = [{"rec_texts": [], "rec_boxes": None, "rec_scores": []}]
        MockOCR.return_value = inst
        pages = PaddleOcrBackend().run([page], "deu")

    assert len(pages) == 1
    assert pages[0].lines == []


def test_paddleocr_run_passes_det_limit_kwargs(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)
    fake_result = [{"rec_texts": ["x"], "rec_boxes": [[0, 0, 5, 5]], "rec_scores": [0.9]}]
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR, \
         patch("app.ocr_backends.paddleocr.get_settings", return_value=Settings(api_key="test")):
        inst = MagicMock()
        inst.predict.return_value = fake_result
        MockOCR.return_value = inst
        PaddleOcrBackend().run([page], "deu")

    _, kwargs = inst.predict.call_args
    assert kwargs["text_det_limit_type"] == "max"
    assert kwargs["text_det_limit_side_len"] == 1600


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


from app.ocr_backends.gcv import GoogleCloudVisionBackend


def test_gcv_stub_raises_not_implemented(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    with pytest.raises(NotImplementedError, match="not yet implemented"):
        GoogleCloudVisionBackend().run([page], "deu")


def test_gcv_stub_raises_on_empty_pages():
    with pytest.raises((ValueError, NotImplementedError)):
        GoogleCloudVisionBackend().run([], "deu")


from app.ocr_backends.types import OcrLine, OcrPage


def test_ocrpage_text_joins_lines_with_newline():
    page = OcrPage([OcrLine("first", 0, 0, 10, 5), OcrLine("second", 0, 6, 10, 11)])
    assert page.text == "first\nsecond"


def test_ocrpage_empty_text_is_empty_string():
    assert OcrPage([]).text == ""
