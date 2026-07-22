import logging
import os
import subprocess
from glob import glob
from pathlib import Path

logger = logging.getLogger("app.ocr_backends.pdfa")

_ICC_CANDIDATES = (
    "/usr/share/color/icc/ghostscript/srgb.icc",
    "/usr/share/color/icc/ghostscript/sRGB.icc",
)

_PDFA_DEF_TEMPLATE = """%!
[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark
[{{icc_PDFA}} << /N 3 >> /PUT pdfmark
[{{icc_PDFA}} ({icc}) (r) file /PUT pdfmark
[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFA}} <<
  /Type /OutputIntent /S /GTS_PDFA1
  /DestOutputProfile {{icc_PDFA}}
  /OutputConditionIdentifier (sRGB)
>> /PUT pdfmark
[{{Catalog}} <</OutputIntents [ {{OutputIntent_PDFA}} ]>> /PUT pdfmark
"""


def _find_icc_profile() -> str | None:
    for path in _ICC_CANDIDATES:
        if Path(path).is_file():
            return path
    for path in sorted(glob("/usr/share/ghostscript/*/iccprofiles/srgb.icc")):
        return path
    return None


def convert_to_pdfa(pdf_path: Path) -> None:
    """Convert pdf_path in place to PDF/A-2b via Ghostscript.

    Best-effort: on any failure (no ICC profile, gs error, empty output) the
    original searchable PDF is left untouched and a warning is logged.
    """
    icc = _find_icc_profile()
    if icc is None:
        logger.warning("No sRGB ICC profile found; skipping PDF/A conversion of %s", pdf_path.name)
        return

    tmp_dir = pdf_path.parent
    def_ps = tmp_dir / f"{pdf_path.stem}_pdfa_def.ps"
    out_pdf = tmp_dir / f"{pdf_path.stem}_pdfa.pdf"

    cmd = [
        "gs", "-dPDFA=2", "-dBATCH", "-dNOPAUSE", "-dNOOUTERSAVE",
        "-dPDFACompatibilityPolicy=1", "-sColorConversionStrategy=RGB",
        "-sDEVICE=pdfwrite", f"--permit-file-read={icc}",
        f"-sOutputFile={out_pdf}", str(def_ps), str(pdf_path),
    ]
    try:
        def_ps.write_text(_PDFA_DEF_TEMPLATE.format(icc=icc))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0 and out_pdf.exists() and out_pdf.stat().st_size > 0:
            os.replace(str(out_pdf), str(pdf_path))
            logger.info("PDF/A-2b conversion done: %s", pdf_path.name)
        else:
            logger.warning(
                "PDF/A conversion failed (rc=%s); keeping original %s: %s",
                result.returncode, pdf_path.name, result.stderr.strip()[:200])
    except Exception as exc:  # never fail the job over archival conversion
        logger.warning("PDF/A conversion error; keeping original %s: %s", pdf_path.name, exc)
    finally:
        def_ps.unlink(missing_ok=True)
        out_pdf.unlink(missing_ok=True)
