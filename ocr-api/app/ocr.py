import asyncio
import os
import re
import subprocess
from pathlib import Path

from app.config import get_settings


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
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout


def remove_blank_pages(tmp_dir: str) -> list[str]:
    """Move blank pages to blanks/ subdirectory. Return list of remaining page paths."""
    blanks_dir = os.path.join(tmp_dir, "blanks")
    os.makedirs(blanks_dir, exist_ok=True)
    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))
    kept = []
    for page in pages:
        histogram = _run([
            "magick", str(page),
            "-threshold", "50%",
            "-format", "%c",
            "histogram:info:-",
        ])
        if is_blank_page(histogram):
            os.rename(page, os.path.join(blanks_dir, page.name))
        else:
            kept.append(str(page))
    return kept


def clean_page(page_path: str) -> None:
    """Apply brightness-contrast correction in-place."""
    _run(["magick", page_path, "-brightness-contrast", "1x40%", page_path])


def run_tesseract(tmp_dir: str, file_name: str) -> None:
    """Write scan_list.txt and run Tesseract. Outputs file_name.pdf/.txt/.hocr."""
    settings = get_settings()
    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))
    if not pages:
        raise RuntimeError("No pages remaining after blank page removal – nothing to OCR.")
    list_file = os.path.join(tmp_dir, "scan_list.txt")
    with open(list_file, "w") as f:
        for p in pages:
            f.write(p.name + "\n")
    _run([
        "tesseract", "scan_list.txt", file_name,
        "--dpi", "300",
        "--oem", "1",
        "-l", settings.ocr_language,
        "--psm", "1",
        "txt", "pdf", "hocr",
    ], cwd=tmp_dir)


async def process_scan(tmp_dir: str, file_name: str) -> dict:
    """Full OCR pipeline. Returns dict with paths to output files."""
    loop = asyncio.get_running_loop()

    # Blank page removal
    await loop.run_in_executor(None, remove_blank_pages, tmp_dir)

    # Contrast cleanup on remaining pages
    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))
    for page in pages:
        await loop.run_in_executor(None, clean_page, str(page))

    # OCR
    await loop.run_in_executor(None, run_tesseract, tmp_dir, file_name)

    return {
        "pdf": os.path.join(tmp_dir, f"{file_name}.pdf"),
        "txt": os.path.join(tmp_dir, f"{file_name}.txt"),
        "file_name": file_name,
    }
