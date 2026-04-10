# Copilot Instructions for Scan2Pi2OCR

## Project Overview

Scan2Pi2OCR connects a Raspberry Pi scanner to an OCR pipeline. A button press on the scanner triggers `insaned`, which calls `raspi/scan.sh`, which calls `raspi/ocrit.sh` to POST scanned pages to the OCR REST API.

## Component Map

| Directory | Purpose |
|-----------|---------|
| `raspi/` | Scripts that run on the scanner Pi (`scan.sh`, `ocrit.sh`, `ocrit.env`) |
| `ocr-api/` | Containerized FastAPI REST API service for OCR processing |
| `insaned/` | insaned event configuration for scanner button handling |
| `docker/` | Legacy Docker/tesseract image files |
| `ocr-machine/` | Legacy OCR scripts for direct-execution approach |

## Data Flow

```
Scanner Pi
  insaned ──▶ scan.sh ──▶ ocrit.sh
                               │
                          curl POST (multipart)
                               │
                               ▼
                      OCR API (:8000)  [ocr-api/]
                               │
               ┌───────────────┼───────────────┐
               ▼               ▼               ▼
         Filesystem       Paperless-ngx      rclone
          (output/)        (REST API)       (OneDrive)
```

`raspi/ocrit.sh` POSTs scanned image pages to `/scan/upload` on the OCR API using `curl`. This replaces the previous SSH/rsync approach where files were transferred to a remote machine for processing.

## OCR API (`ocr-api/`)

- Built with **FastAPI**, containerized via Docker Compose
- Authentication via `X-Api-Key` header (all routes except `/health`)
- Jobs are processed asynchronously; poll `/scan/status/{job_id}` for results
- Output destinations controlled by env flags: `ENABLE_FILESYSTEM`, `ENABLE_PAPERLESS`, `ENABLE_RCLONE`

## Raspberry Pi Setup

- Copy `raspi/ocrit.env.example` to `raspi/ocrit.env` and fill in `API_HOST` and `API_KEY`
- `insaned` events live in `insaned/events/`

## Coding Conventions

- Python code in `ocr-api/` follows standard FastAPI patterns
- Tests live in `ocr-api/tests/` and run with `python3 -m pytest tests/ -q`
- Environment configuration uses `.env` files (never commit secrets)
