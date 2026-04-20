from pathlib import Path
from typing import Protocol, runtime_checkable
from app.ocr_backends.base import OcrBackend


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
