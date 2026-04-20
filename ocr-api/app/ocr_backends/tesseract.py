import logging
import subprocess
from pathlib import Path

logger = logging.getLogger("app.ocr_backends.tesseract")

_OUTPUT_STEM = "_ocr_out"


class TesseractBackend:
    def run(self, pages: list[Path], language: str) -> str:
        if not pages:
            raise ValueError("No pages provided to TesseractBackend")
        tmp_dir = pages[0].parent
        list_file = tmp_dir / "scan_list.txt"
        list_file.write_text("\n".join(p.name for p in pages))

        cmd = [
            "tesseract", list_file.name, _OUTPUT_STEM,
            "--dpi", "300", "--oem", "1",
            "-l", language, "--psm", "1", "txt",
        ]
        logger.info("Running Tesseract on %d page(s), language=%s", len(pages), language)
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp_dir))
        if result.returncode != 0:
            raise RuntimeError(f"Tesseract failed: {result.stderr.strip()}")

        out_file = tmp_dir / f"{_OUTPUT_STEM}.txt"
        text = out_file.read_text()
        logger.info("Tesseract finished, %d chars extracted", len(text))
        return text
