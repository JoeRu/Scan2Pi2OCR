import logging
import re
from contextlib import contextmanager
from pathlib import Path

import fpdf.output
from fpdf import FPDF
from fpdf.syntax import PDFContentStream
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


# fpdf2 (2.7.9 and 2.8.8) writes the ToUnicode CMap as a single `N beginbfchar` block.
# CMaps allow at most 100 entries per block; with > 100 distinct characters in a
# document, Ghostscript's PDF/A conversion silently drops /ToUnicode and the whole
# text layer becomes unsearchable garbage. Split the block while fpdf2 serializes.
_MAX_BFCHAR_ENTRIES = 100
_BFCHAR_BLOCK = re.compile(r"\d+ beginbfchar\n(.*?)endbfchar\n", re.S)


def _chunk_bfchar_blocks(cmap: str) -> str:
    def split(match: re.Match) -> str:
        entries = match.group(1).splitlines(keepends=True)
        return "".join(
            f"{len(chunk)} beginbfchar\n{''.join(chunk)}endbfchar\n"
            for chunk in (entries[i:i + _MAX_BFCHAR_ENTRIES]
                          for i in range(0, len(entries), _MAX_BFCHAR_ENTRIES))
        )
    return _BFCHAR_BLOCK.sub(split, cmap)


class _ChunkedCMapContentStream(PDFContentStream):
    def __init__(self, contents, compress=False):
        if isinstance(contents, str) and "beginbfchar" in contents:
            contents = _chunk_bfchar_blocks(contents)
        super().__init__(contents, compress=compress)


@contextmanager
def _chunked_tounicode_cmaps():
    # fpdf.output builds the ToUnicode stream via its module-level PDFContentStream name.
    # PDFs are built one at a time (single worker), so swapping the name is safe here.
    original = fpdf.output.PDFContentStream
    fpdf.output.PDFContentStream = _ChunkedCMapContentStream
    try:
        yield
    finally:
        fpdf.output.PDFContentStream = original


# Font size of the invisible transcript block (OcrPage.pdf_text == "block").
_BLOCK_FONT_PT = 4


def _pdf_safe(text: str, text_font: str, font_cmap: dict | None = None) -> str:
    """Make text encodable by the text-layer font.

    fpdf2 raises TypeError (instead of skipping) for characters the embedded TTF
    has no glyph for, e.g. math-alphanumeric symbols an LLM transcription can
    contain; one such character used to fail the whole scan. Replace them with "?".
    """
    if text_font == "Helvetica":
        return text.encode("latin-1", "replace").decode("latin-1")
    if font_cmap is None:
        return text
    return "".join(ch if ord(ch) in font_cmap else "?" for ch in text)


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
        font_cmap = getattr(pdf.current_font, "cmap", None)
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
                pdf.text(x_mm, baseline_mm, _pdf_safe(line.text, text_font, font_cmap))

            if ocr_page.pdf_text == "block" and ocr_page.transcript:
                # Unpositioned: searchable (PDF viewers, Paperless) but highlights
                # land in the top-left corner, not on the handwriting.
                pdf.set_font_size(_BLOCK_FONT_PT)
                step_mm = _BLOCK_FONT_PT / 72 * 25.4
                for n, text in enumerate(t for t in ocr_page.transcript.splitlines() if t.strip()):
                    pdf.text(2, 2 + (n + 1) * step_mm, _pdf_safe(text, text_font, font_cmap))

    with _chunked_tounicode_cmaps():
        pdf.output(str(output_path))
    logger.info("Searchable PDF written: %s (%d page(s))", output_path.name, len(pages))
