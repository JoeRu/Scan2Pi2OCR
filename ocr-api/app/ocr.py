import asyncio
import functools
import logging
import os
import re
import subprocess
from pathlib import Path

from app.config import get_settings
from app.ocr_backends import get_backend
from app.ocr_backends.build_pdf import build_searchable_pdf

logger = logging.getLogger("app.ocr")


def is_blank_page(histogram: str) -> bool:
    """Return True if black/white pixel ratio < 1% (page is blank)."""
    white_match = re.search(r"(\d+):\s*\(255,255,255\)", histogram)
    black_match = re.search(r"(\d+):\s*\(0,0,0\)", histogram)
    white = int(white_match.group(1)) if white_match else 0
    black = int(black_match.group(1)) if black_match else 0
    if white == 0:
        return True
    return (black / white) < 0.01


def _run(cmd: list[str], cwd: str | None = None) -> str:
    logger.debug("Running: %s (cwd=%s)", " ".join(cmd), cwd)
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        logger.error("Command failed (rc=%d): %s\nstdout: %s\nstderr: %s",
                     result.returncode, " ".join(cmd), result.stdout.strip(), result.stderr.strip())
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    if result.stderr.strip():
        logger.debug("Command stderr: %s", result.stderr.strip())
    return result.stdout


def remove_blank_pages(tmp_dir: str) -> list[str]:
    """Move blank pages to blanks/ subdirectory. Return list of remaining page paths."""
    blanks_dir = os.path.join(tmp_dir, "blanks")
    os.makedirs(blanks_dir, exist_ok=True)
    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))
    logger.info("Blank page check: found %d page(s)", len(pages))
    kept = []
    for page in pages:
        histogram = _run([
            "convert", str(page),
            "-threshold", "50%",
            "-format", "%c",
            "histogram:info:-",
        ])
        if is_blank_page(histogram):
            logger.info("  Blank page detected, skipping: %s", page.name)
            os.rename(page, os.path.join(blanks_dir, page.name))
        else:
            logger.debug("  Page kept: %s", page.name)
            kept.append(str(page))
    logger.info("Blank page removal done: %d kept, %d removed", len(kept), len(pages) - len(kept))
    return kept


def clean_page(page_path: str) -> None:
    """Apply brightness-contrast correction in-place."""
    logger.debug("Cleaning page: %s", os.path.basename(page_path))
    _run(["convert", page_path, "-brightness-contrast", "1x40%", page_path])


async def process_scan(tmp_dir: str, file_name: str) -> dict:
    """Full OCR pipeline. Returns dict with paths to output files."""
    loop = asyncio.get_running_loop()
    settings = get_settings()

    logger.info("OCR pipeline start: tmp_dir=%s name=%r engine=%s",
                tmp_dir, file_name, settings.ocr_engine)

    logger.info("Step 1/3: Blank page removal")
    await loop.run_in_executor(None, remove_blank_pages, tmp_dir)

    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))
    if not pages:
        raise RuntimeError("No pages remaining after blank page removal – nothing to OCR.")

    logger.info("Step 2/3: Contrast cleanup on %d page(s)", len(pages))
    for page in pages:
        await loop.run_in_executor(None, clean_page, str(page))

    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))

    logger.info("Step 3/3: OCR (%s)", settings.ocr_engine)
    backend = get_backend(settings.ocr_engine)
    text = await loop.run_in_executor(
        None,
        functools.partial(backend.run, pages, settings.ocr_language),
    )

    pdf_path = Path(tmp_dir) / f"{file_name}.pdf"
    txt_path = Path(tmp_dir) / f"{file_name}.txt"

    await loop.run_in_executor(
        None,
        functools.partial(build_searchable_pdf, pages, text, pdf_path),
    )
    txt_path.write_text(text)

    logger.info("OCR pipeline done: %s.{pdf,txt}", file_name)
    return {
        "pdf": str(pdf_path),
        "txt": str(txt_path),
        "file_name": file_name,
    }
