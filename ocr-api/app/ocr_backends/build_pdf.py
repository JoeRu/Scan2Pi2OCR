import logging
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import TextMode
from PIL import Image

from app.ocr_backends.types import OcrPage

logger = logging.getLogger("app.ocr_backends.build_pdf")


def build_searchable_pdf(pages: list[Path], pages_ocr: list[OcrPage], output_path: Path) -> None:
    """Create a searchable PDF: each page image plus an invisible, positioned text layer."""
    if not pages:
        raise ValueError("pages must not be empty")

    pdf = FPDF()
    pdf.set_auto_page_break(False)
    pdf.set_compression(False)

    for i, page_path in enumerate(pages):
        ocr_page = pages_ocr[i] if i < len(pages_ocr) else OcrPage([])

        with Image.open(page_path) as img:
            dpi = img.info.get("dpi", (300, 300))
            w_px, h_px = img.size
        dpi_x = dpi[0] if dpi[0] else 300
        dpi_y = dpi[1] if dpi[1] else 300
        w_mm = w_px / dpi_x * 25.4
        h_mm = h_px / dpi_y * 25.4

        pdf.add_page(format=(w_mm, h_mm))
        pdf.image(str(page_path), x=0, y=0, w=w_mm, h=h_mm)

        pdf.set_font("Helvetica")
        with pdf.local_context(text_mode=TextMode.INVISIBLE):
            for line in ocr_page.lines:
                if not line.text:
                    continue
                line_h_px = line.y1 - line.y0
                if line_h_px <= 0:
                    continue
                pdf.set_font_size(max(line_h_px / dpi_y * 72, 1))
                x_mm = line.x0 / dpi_x * 25.4
                baseline_mm = (line.y0 + 0.8 * line_h_px) / dpi_y * 25.4
                pdf.text(x_mm, baseline_mm, line.text)

    pdf.output(str(output_path))
    logger.info("Searchable PDF written: %s (%d page(s))", output_path.name, len(pages))
