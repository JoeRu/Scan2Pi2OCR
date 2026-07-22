import logging
import subprocess
from collections import OrderedDict
from pathlib import Path

from app.ocr_backends.types import OcrLine, OcrPage

logger = logging.getLogger("app.ocr_backends.tesseract")

_OUTPUT_STEM = "_ocr_out"


class TesseractBackend:
    def run(self, pages: list[Path], language: str) -> list[OcrPage]:
        if not pages:
            raise ValueError("No pages provided to TesseractBackend")
        tmp_dir = pages[0].parent
        list_file = tmp_dir / "scan_list.txt"
        list_file.write_text("\n".join(p.name for p in pages))

        cmd = [
            "tesseract", list_file.name, _OUTPUT_STEM,
            "--dpi", "300", "--oem", "1",
            "-l", language, "--psm", "1", "tsv",
        ]
        logger.info("Running Tesseract on %d page(s), language=%s", len(pages), language)
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp_dir))
        if result.returncode != 0:
            raise RuntimeError(f"Tesseract failed: {result.stderr.strip()}")

        tsv = (tmp_dir / f"{_OUTPUT_STEM}.tsv").read_text()
        result_pages = _parse_tsv(tsv, len(pages))
        total = sum(len(p.lines) for p in result_pages)
        logger.info("Tesseract finished, %d line(s) across %d page(s)", total, len(result_pages))
        return result_pages


def _parse_tsv(tsv: str, page_count: int) -> list[OcrPage]:
    # page -> ordered {(block,par,line): [(word_num, text, left, top, width, height)]}
    by_page: dict[int, "OrderedDict[tuple, list]"] = {}
    for row in tsv.splitlines()[1:]:  # skip header
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":  # word-level rows only
            continue
        text = cols[11].strip()
        if not text:
            continue
        try:
            conf = float(cols[10])
        except ValueError:
            continue
        if conf < 0:
            continue
        page, block, par, line, word = (int(cols[i]) for i in range(1, 6))
        left, top, width, height = (int(cols[i]) for i in range(6, 10))
        key = (block, par, line)
        by_page.setdefault(page, OrderedDict()).setdefault(key, []).append(
            (word, text, left, top, width, height))

    result: list[OcrPage] = []
    for page in range(1, page_count + 1):
        lines: list[OcrLine] = []
        for words in by_page.get(page, {}).values():
            words.sort(key=lambda w: w[0])
            lines.append(OcrLine(
                text=" ".join(w[1] for w in words),
                x0=min(w[2] for w in words),
                y0=min(w[3] for w in words),
                x1=max(w[2] + w[4] for w in words),
                y1=max(w[3] + w[5] for w in words),
            ))
        result.append(OcrPage(lines))
    return result
