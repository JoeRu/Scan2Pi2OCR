import logging
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import TextMode
from PIL import Image

from app.ocr_backends.types import OcrPage

logger = logging.getLogger("app.ocr_backends.build_pdf")

# The invisible text layer needs a Unicode font: fpdf2's core fonts (Helvetica)
# are latin-1 only and raise FPDFUnicodeEncodingException on characters OCR
# routinely produces (•, €, –, smart quotes, …). Embed a system DejaVu TTF so
# the real characters land in the PDF (searchable/copyable), falling back to a
# lossy latin-1 layer only if no Unicode font is found.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/local/lib/python3.12/dist-packages/cv2/qt/fonts/DejaVuSans.ttf",
)
_UNICODE_FONT = "DejaVu"


def _find_unicode_font() -> str | None:
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


def build_searchable_pdf(pages: list[Path], pages_ocr: list[OcrPage], output_path: Path) -> None:
    """Create a searchable PDF: each page image plus an invisible, positioned text layer."""
    if not pages:
        raise ValueError("pages must not be empty")

    pdf = FPDF()
    pdf.set_auto_page_break(False)
    pdf.set_compression(False)

    font_path = _find_unicode_font()
    if font_path:
        pdf.add_font(_UNICODE_FONT, fname=font_path)
        text_font = _UNICODE_FONT
    else:
        text_font = "Helvetica"
        logger.warning(
            "No Unicode font found (%s); text layer falls back to Helvetica, "
            "non-latin-1 characters will be replaced", ", ".join(_FONT_CANDIDATES))

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

        pdf.set_font(text_font)
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
                text = line.text
                if text_font == "Helvetica":
                    text = text.encode("latin-1", "replace").decode("latin-1")
                pdf.text(x_mm, baseline_mm, text)

    pdf.output(str(output_path))
    logger.info("Searchable PDF written: %s (%d page(s))", output_path.name, len(pages))
