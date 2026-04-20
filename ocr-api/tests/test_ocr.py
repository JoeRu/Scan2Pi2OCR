import asyncio
import os
from pathlib import Path
from unittest.mock import patch, MagicMock
from PIL import Image
from app.config import Settings

import pytest
from app.ocr import is_blank_page


def test_is_blank_page_true():
    # black=3, white=1234 → ratio 0.0024 < 0.01 → blank
    histogram = "     1234: (255,255,255) #FFFFFF white\n      3: (0,0,0) #000000 black\n"
    assert is_blank_page(histogram) is True


def test_is_blank_page_false():
    # black=500, white=1000 → ratio 0.5 → not blank
    histogram = "     1000: (255,255,255) #FFFFFF white\n    500: (0,0,0) #000000 black\n"
    assert is_blank_page(histogram) is False


def test_is_blank_page_no_black():
    # no black pixels → blank
    histogram = "     9999: (255,255,255) #FFFFFF white\n"
    assert is_blank_page(histogram) is True


def _make_tif(path: Path) -> None:
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    img.save(str(path), dpi=(300, 300))


def _settings_with_engine(engine: str) -> Settings:
    return Settings(api_key="test", ocr_engine=engine, ocr_language="deu")


def test_process_scan_uses_backend(tmp_path):
    tif = tmp_path / "scan_0001.pnm.tif"
    _make_tif(tif)

    mock_backend = MagicMock()
    mock_backend.run.return_value = "extracted text"

    with patch("app.ocr.get_settings", return_value=_settings_with_engine("tesseract")), \
         patch("app.ocr.get_backend", return_value=mock_backend), \
         patch("app.ocr.build_searchable_pdf") as mock_pdf, \
         patch("app.ocr.remove_blank_pages"), \
         patch("app.ocr.clean_page"):

        mock_pdf.side_effect = lambda pages, text, path: path.write_bytes(b"%PDF-1.4")

        result = asyncio.run(_process_scan(str(tmp_path), "output"))

    assert result["pdf"].endswith("output.pdf")
    assert result["txt"].endswith("output.txt")
    assert Path(result["txt"]).read_text() == "extracted text"
    mock_backend.run.assert_called_once()


def test_process_scan_raises_when_all_pages_blank(tmp_path):
    # No .tif files in tmp_path after blank removal — glob returns empty list
    with patch("app.ocr.get_settings", return_value=_settings_with_engine("tesseract")), \
         patch("app.ocr.remove_blank_pages"), \
         patch("app.ocr.get_backend"):
        with pytest.raises(RuntimeError, match="No pages remaining"):
            asyncio.run(_process_scan(str(tmp_path), "output"))


def _process_scan(tmp_dir, file_name):
    from app.ocr import process_scan
    return process_scan(tmp_dir, file_name)
