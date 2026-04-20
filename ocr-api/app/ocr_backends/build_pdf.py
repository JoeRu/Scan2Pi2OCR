import logging
from pathlib import Path

from fpdf import FPDF
from PIL import Image

logger = logging.getLogger("app.ocr_backends.build_pdf")


def build_searchable_pdf(pages: list[Path], text: str, output_path: Path) -> None:
    """Create a searchable PDF from TIF page images with an invisible text layer."""
    if not pages:
        raise ValueError("pages must not be empty")

    pdf = FPDF()
    pdf.set_auto_page_break(False)
    pdf.set_compression(False)

    for i, page_path in enumerate(pages):
        with Image.open(page_path) as img:
            dpi = img.info.get("dpi", (300, 300))
            w_px, h_px = img.size
        dpi_x = dpi[0] if dpi[0] else 300
        dpi_y = dpi[1] if dpi[1] else 300
        w_mm = w_px / dpi_x * 25.4
        h_mm = h_px / dpi_y * 25.4

        pdf.add_page(format=(w_mm, h_mm))
        pdf.image(str(page_path), x=0, y=0, w=w_mm, h=h_mm)

        if i == 0 and text:
            # Invisible white text layer — searchable but not visible
            pdf.set_text_color(255, 255, 255)
            pdf.set_font("Helvetica", size=1)
            # Use absolute coordinates outside the tiny image bounds if needed
            pdf.set_xy(0, 0)
            try:
                pdf.multi_cell(w=w_mm, h=1, text=text)
            except Exception:
                # Page may be too small for multi_cell wrapping;
                # write text directly into the page content bytearray
                raw_op = (
                    f"BT /F1 1 Tf 0 0 Td ({text}) Tj ET\n"
                ).encode("latin-1", errors="replace")
                pdf.pages[pdf.page].contents.extend(raw_op)
            pdf.set_text_color(0, 0, 0)

    pdf.output(str(output_path))
    logger.info("Searchable PDF written: %s (%d page(s))", output_path.name, len(pages))
