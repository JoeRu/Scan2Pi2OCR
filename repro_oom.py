"""
Reproduction script for PaddleOCR #17955:
~43 GB RSS during CPU inference with PaddleOCR 3.x / PaddlePaddle 3.x.

Key finding: the OOM is triggered by image size.
  - A4 @ 150 dpi (1240×1754):  completes fine, ~0.8 GiB peak RSS
  - A4 @ 300 dpi (2480×3508):  OOM kill (43 GB RSS) — matches real scanner output

Usage:
    python3 repro_oom.py [--dpi 150|300]

Prints system info then runs a minimal inference call.
Memory is sampled every 0.5 s in a background thread so peak RSS is reported
even if the process is killed by the OOM killer mid-run.
"""
from __future__ import annotations

import argparse
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path

# ── system info ──────────────────────────────────────────────────────────────

def _run(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return "n/a"


def print_sysinfo() -> None:
    print("=" * 60)
    print("SYSTEM INFO")
    print("=" * 60)
    print(f"Python           : {sys.version}")
    print(f"OS               : {_run('lsb_release -ds')} / {platform.release()}")
    print(f"glibc            : {_run('ldd --version | head -1')}")

    cpu_model = _run("grep 'model name' /proc/cpuinfo | head -1 | cut -d: -f2")
    cpu_cores = _run("nproc")
    cpu_flags = _run("grep '^flags' /proc/cpuinfo | head -1 | cut -d: -f2 | xargs")
    mem_kb    = _run("grep MemTotal /proc/meminfo | awk '{print $2}'")
    mem_gib   = round(int(mem_kb) / 1024 / 1024, 1) if mem_kb.isdigit() else "n/a"

    print(f"CPU model        :{cpu_model}")
    print(f"CPU logical cores: {cpu_cores}")
    print(f"RAM total        : {mem_gib} GiB")
    print(f"CPU flags        : {cpu_flags[:80]}...")  # truncated for readability

    try:
        import paddle
        print(f"PaddlePaddle     : {paddle.__version__}")
    except ImportError:
        print("PaddlePaddle     : not installed")

    try:
        import paddleocr
        print(f"PaddleOCR        : {paddleocr.__version__}")
    except ImportError:
        print("PaddleOCR        : not installed")

    print("=" * 60)
    print()


# ── memory monitor ───────────────────────────────────────────────────────────

class _MemMonitor(threading.Thread):
    """Samples /proc/self/status RSS every 0.5 s, tracks peak."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.peak_kb: int = 0
        self._quit = threading.Event()  # not _stop — that name is reserved by Thread internals

    def run(self) -> None:
        while not self._quit.wait(0.5):
            try:
                text = Path("/proc/self/status").read_text()
                for line in text.splitlines():
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        if kb > self.peak_kb:
                            self.peak_kb = kb
                        break
            except Exception:
                pass

    def stop(self) -> int:
        self._quit.set()
        self.join(timeout=2)
        return self.peak_kb


# ── reproduction ─────────────────────────────────────────────────────────────

# A4 dimensions at common scanner DPI settings
_DPI_SIZES = {
    150: (1240, 1754),   # safe — completes, ~0.8 GiB peak
    300: (2480, 3508),   # triggers OOM kill (~43 GB RSS)
}


def reproduce(dpi: int) -> None:
    import tempfile
    from PIL import Image, ImageDraw

    w, h = _DPI_SIZES[dpi]
    print(f"Creating test image: A4 @ {dpi} dpi = {w}×{h} px (with text lines)...")

    img = Image.new("RGB", (w, h), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    # Repeat a text block across the page so text detection has real work to do
    block = (
        "Rechnung Nr. 2024-0042   Datum: 15. Januar 2024\n"
        "Absender: Musterfirma GmbH, Musterstraße 1, 12345 Musterstadt\n"
        "Empfänger: Max Mustermann, Beispielweg 7, 54321 Beispielstadt\n"
        "Pos  Beschreibung              Menge   Preis\n"
        "  1  Beratungsleistung           8 h   120,00 EUR\n"
        "  2  Reisekosten pauschal        1      50,00 EUR\n"
        "Nettobetrag: 1.010,00 EUR   MwSt. 19%: 191,90 EUR   Gesamt: 1.201,90 EUR\n"
        "IBAN: DE12 3456 7890 1234 5678 90   BIC: MUSTDEBBXXX\n"
    )
    line_height = max(16, h // 60)
    y = 60
    while y < h - 100:
        draw.multiline_text((60, y), block, fill=(0, 0, 0), spacing=4)
        y += line_height * 10

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img_path = Path(f.name)
    img.save(str(img_path))
    del img, draw  # free PIL image before OCR runs
    print(f"  → saved to {img_path}  ({img_path.stat().st_size // 1024} kB on disk)")

    monitor = _MemMonitor()
    monitor.start()

    try:
        from paddleocr import PaddleOCR
        print("Instantiating PaddleOCR(lang='german', enable_mkldnn=False) ...")
        t0 = time.time()
        ocr = PaddleOCR(lang="german", enable_mkldnn=False)
        print(f"  → instantiated in {time.time() - t0:.1f}s")

        print("Calling ocr.predict() ...")
        t1 = time.time()
        result = ocr.predict(str(img_path), use_textline_orientation=True)
        elapsed = time.time() - t1
        print(f"  → predict() returned in {elapsed:.1f}s")
        lines = result[0].get("rec_texts", []) if result else []
        print(f"  → text lines recognised: {len(lines)}")
    except MemoryError as exc:
        print(f"  ✗ MemoryError: {exc}")
    except Exception as exc:
        print(f"  ✗ {type(exc).__name__}: {exc}")
    finally:
        peak_kb = monitor.stop()
        print(f"\nPeak RSS observed by monitor: {peak_kb:,} kB  ({peak_kb / 1024 / 1024:.2f} GiB)")
        img_path.unlink(missing_ok=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repro for PaddleOCR #17955 OOM")
    parser.add_argument("--dpi", type=int, choices=[150, 300], default=300,
                        help="Scanner DPI to simulate (150=safe, 300=triggers OOM; default: 300)")
    args = parser.parse_args()

    print_sysinfo()
    print(f"NOTE: --dpi {args.dpi} → image size {_DPI_SIZES[args.dpi][0]}×{_DPI_SIZES[args.dpi][1]}")
    if args.dpi == 300:
        print("WARNING: this is expected to trigger an OOM kill on systems with < 48 GiB RAM.")
    print()
    reproduce(args.dpi)
