# Scan2Pi2OCR – Copilot Instructions

## Project Overview

A distributed scan-to-OCR pipeline with two physical nodes:

- **Raspberry Pi** – scanner frontend. Runs insaned (button daemon) → triggers scan via SANE → transfers scanned images to the OCR machine over SSH/rsync.
- **OCR machine** – more powerful server. Receives images, runs Tesseract 4 inside a Docker container, uploads the resulting PDF to OneDrive via rclone, and emails the result.

## Architecture & Data Flow

```
Scanner button pressed
  → insaned daemon
  → insaned/events/scan          (lock file guard, calls scan.sh)
  → raspi/scan.sh                (scanimage: 300dpi, Gray, ADF Duplex, TIFF → /tmp/mktemp dir)
  → raspi/ocrit.sh &             (async; rsync to OCR machine, then SSH-triggers scan2ocr.sh)
  → ocr-machine/scan2ocr.sh     (on OCR machine)
      1. Blank page removal      (ImageMagick histogram: black/white ratio < 1%)
      2. Image cleanup           (ImageMagick brightness-contrast 1x40%)
      3. Tesseract OCR           (LSTM oem 1, deu+eng+frk, --psm 1, 300dpi)
      4. rclone → OneDrive
      5. mutt email with OneDrive link + OCR text preview
```

## Component Map

| Directory/File | Runs On | Purpose |
|---|---|---|
| `insaned/events/scan` | Raspberry Pi | insaned event hook; lock-guards and calls scan.sh |
| `raspi/scan.sh` | Raspberry Pi | Invokes `scanimage`, hands off to `ocrit.sh` |
| `raspi/ocrit.sh` | Raspberry Pi | rsync + SSH to OCR machine |
| `raspi/scanner/` | Raspberry Pi | Fujitsu ScanSnap S1300 firmware (`.nal`) and SANE config |
| `ocr-machine/scan2ocr.sh` | OCR machine | Full OCR pipeline |
| `ocr-machine/scan2ocr.config.sh` | OCR machine | Configuration (paths, credentials) – must be customised |
| `docker/Dockerfile` | OCR machine | Ubuntu 18.04 + Tesseract 4 (via alex-p PPA) |
| `docker/dockerfile.build.sh` | OCR machine | Builds the Docker image (`tesseractshadow/tesseract4ocrit`) |
| `docker/run.sh` | OCR machine | Starts the container (`t4re`) with the watch_scans volume mounted |

## Key Configuration Points

Every deployment requires adapting these hardcoded values:

- **`insaned/events/scan`** – hardcoded path `/home/joe/scan.sh`; change to actual scan.sh location.
- **`raspi/ocrit.sh`** – hardcoded SSH/rsync target `joe@joesnuc`; change to actual OCR machine user/host.
- **`ocr-machine/scan2ocr.config.sh`** – template with `<placeholder>` values:
  - `OUT_DIR`, `WATCH_SCANS` – output and watch directories
  - `TRASH_TMP_FILES` – `1` to delete temp files after processing, `0` to keep for debugging
  - `ABBYY_APPID` / `ABBYY_PWD` – only needed if using ABBY Cloud OCR (disabled by default)
- **`docker/run.sh`** – volume path `/MYZFS/Personal/joe/watch_scans` must match `WATCH_SCANS`.

## Docker Commands

```bash
# Build the Tesseract 4 image
./docker/dockerfile.build.sh

# Run the container (mounts watch_scans as /home/work)
./docker/run.sh

# Start / stop without removing
./docker/start.sh
./docker/stop.sh
```

The container is named `t4re`. Tesseract runs as root inside the container; the working directory is `/home/work`.

## Tesseract Invocation

```bash
# Called by scan2ocr.sh; scan_list.txt contains one filename per line
tesseract scan_list.txt $FILE_NAME --dpi 300 --oem 1 -l deu+eng+frk --psm 1 txt pdf hocr
```

- `--oem 1` = LSTM only
- `-l deu+eng+frk` = German + English + Fraktur
- Outputs: `.txt`, `.pdf`, `.hocr`

## Blank Page Detection

Pages are discarded when the ratio of black pixels to white pixels is below 1% after thresholding at 50%:

```bash
blank=$(echo "scale=4; ${black}/${white} < 0.01" | bc)
```

Blank pages are moved to a `blanks/` subdirectory (not deleted outright) for debugging.

## Raspberry Pi Scanner Setup

For Fujitsu ScanSnap S1300:

```bash
# Install firmware and SANE config
raspi/scanner/prepare_scanner.sh

# Verify scanner is detected
scanimage -L
```

Scans run as root (required by the hardware); output files are then `chown`-ed to `pi:pi`.

## Dependencies

**Raspberry Pi:** `sane`, `insaned`

**OCR machine:** `imagemagick`, `bc`, `exactimage`, `pdftk`, `tesseract-ocr`, `tesseract-ocr-deu`, `tesseract-ocr-eng`, `rclone`, `mutt`, Docker
