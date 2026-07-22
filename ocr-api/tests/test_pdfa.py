import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.ocr_backends.pdfa import convert_to_pdfa


def _write_pdf(p: Path, content: bytes = b"%PDF-1.7\noriginal\n") -> None:
    p.write_bytes(content)


def _output_file_arg(cmd) -> str:
    return next(a.split("=", 1)[1] for a in cmd if a.startswith("-sOutputFile="))


def test_convert_success_replaces_file(tmp_path):
    pdf = tmp_path / "out.pdf"
    _write_pdf(pdf)

    def fake_run(cmd, capture_output, text):
        Path(_output_file_arg(cmd)).write_bytes(b"%PDF-1.7\nconverted-pdfa\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.pdfa._find_icc_profile", return_value="/x/srgb.icc"), \
         patch("app.ocr_backends.pdfa.subprocess.run", side_effect=fake_run):
        convert_to_pdfa(pdf)

    assert pdf.read_bytes() == b"%PDF-1.7\nconverted-pdfa\n"


def test_convert_gs_failure_keeps_original(tmp_path):
    pdf = tmp_path / "out.pdf"
    _write_pdf(pdf)
    with patch("app.ocr_backends.pdfa._find_icc_profile", return_value="/x/srgb.icc"), \
         patch("app.ocr_backends.pdfa.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="", stderr="boom")):
        convert_to_pdfa(pdf)  # must not raise
    assert pdf.read_bytes() == b"%PDF-1.7\noriginal\n"


def test_convert_missing_icc_skips(tmp_path):
    pdf = tmp_path / "out.pdf"
    _write_pdf(pdf)
    with patch("app.ocr_backends.pdfa._find_icc_profile", return_value=None), \
         patch("app.ocr_backends.pdfa.subprocess.run") as mrun:
        convert_to_pdfa(pdf)
    mrun.assert_not_called()
    assert pdf.read_bytes() == b"%PDF-1.7\noriginal\n"


def test_convert_gs_command_has_pdfa_flags(tmp_path):
    pdf = tmp_path / "out.pdf"
    _write_pdf(pdf)
    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        Path(_output_file_arg(cmd)).write_bytes(b"%PDF-1.7\nx\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.pdfa._find_icc_profile", return_value="/x/srgb.icc"), \
         patch("app.ocr_backends.pdfa.subprocess.run", side_effect=fake_run):
        convert_to_pdfa(pdf)

    assert "-dPDFA=2" in captured["cmd"]
    assert "--permit-file-read=/x/srgb.icc" in captured["cmd"]


@pytest.mark.skipif(shutil.which("gs") is None, reason="ghostscript not installed")
def test_convert_real_produces_pdfa(tmp_path):
    from PIL import Image

    from app.ocr_backends.build_pdf import build_searchable_pdf
    from app.ocr_backends.types import OcrLine, OcrPage

    tif = tmp_path / "scan_0001.pnm.tif"
    Image.new("RGB", (2480, 3508), (255, 255, 255)).save(str(tif), dpi=(300, 300))
    pdf = tmp_path / "out.pdf"
    build_searchable_pdf(
        [tif], [OcrPage([OcrLine("• Gesamt 1.201,90 € –", 200, 300, 1400, 360)])], pdf)

    convert_to_pdfa(pdf)

    raw = pdf.read_bytes()
    assert b"OutputIntent" in raw and b"pdfaid" in raw
    from pypdf import PdfReader
    text = PdfReader(str(pdf)).pages[0].extract_text()
    assert "€" in text and "•" in text
