# OCR REST API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the SSH/rsync-based OCR pipeline with a containerised FastAPI service that accepts scan uploads, runs Tesseract 4 OCR asynchronously, and delivers results to Paperless-ngx, rclone/OneDrive, and/or the local filesystem.

**Architecture:** A single Docker container runs FastAPI (HTTP API) and an `asyncio`-based background worker in the same process. The Raspberry Pi's `ocrit.sh` is adapted to POST scan files via `curl` instead of using `rsync`+SSH. Output targets (Paperless, rclone, filesystem) are each enabled independently via env-var flags and executed in parallel after OCR completes.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, pydantic-settings, httpx, Tesseract 4, ImageMagick, rclone, docker-compose v2

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `ocr-api/Dockerfile` | Create | Ubuntu 22.04 + all system deps (Tesseract, ImageMagick, rclone, mutt) + Python |
| `ocr-api/requirements.txt` | Create | Python dependencies |
| `ocr-api/app/__init__.py` | Create | Package marker |
| `ocr-api/app/config.py` | Create | Pydantic `BaseSettings` – all env vars in one place |
| `ocr-api/app/main.py` | Create | FastAPI app, 3 endpoints, API-key dependency, lifespan |
| `ocr-api/app/worker.py` | Create | `asyncio.Queue` + worker loop, job-status registry |
| `ocr-api/app/ocr.py` | Create | Blank-page detection, ImageMagick cleanup, Tesseract invocation |
| `ocr-api/app/outputs/__init__.py` | Create | Package marker |
| `ocr-api/app/outputs/filesystem.py` | Create | Copy PDF to `OUTPUT_DIR` |
| `ocr-api/app/outputs/paperless.py` | Create | `httpx` POST to Paperless-ngx `/api/documents/post_document/` |
| `ocr-api/app/outputs/rclone.py` | Create | `subprocess` wrapper for `rclone copy` + `rclone link` |
| `ocr-api/tests/__init__.py` | Create | Package marker |
| `ocr-api/tests/test_api.py` | Create | Endpoint tests (auth, upload, status) |
| `ocr-api/tests/test_ocr.py` | Create | Blank-page detection unit tests |
| `docker-compose.yml` | Create | Service definition, volume mounts, env_file |
| `.env.example` | Create | Template for all env vars with comments |
| `.env` | **gitignored** | Actual secrets (never committed) |
| `raspi/ocrit.env.example` | Create | Template for Pi-side API_KEY + OCR_API_HOST |
| `raspi/ocrit.env` | **gitignored** | Actual Pi-side secrets |
| `raspi/ocrit.sh` | Modify | Replace rsync+SSH with `curl` multipart POST |
| `.gitignore` | Create | `.env`, `raspi/ocrit.env`, `__pycache__`, `*.pyc` |

---

## Task 1: Scaffold – Dockerfile, requirements, config, .gitignore

**Files:**
- Create: `ocr-api/Dockerfile`
- Create: `ocr-api/requirements.txt`
- Create: `ocr-api/app/__init__.py`
- Create: `ocr-api/app/outputs/__init__.py`
- Create: `ocr-api/app/config.py`
- Create: `.gitignore`

- [ ] **Step 1: Create `ocr-api/Dockerfile`**

```dockerfile
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-deu \
    tesseract-ocr-eng \
    tesseract-ocr-frk \
    imagemagick \
    rclone \
    mutt \
    bc \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /ocr-api
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY app/ /ocr-api/app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Create `ocr-api/requirements.txt`**

```
fastapi==0.111.0
uvicorn[standard]==0.29.0
python-multipart==0.0.9
httpx==0.27.0
pydantic-settings==2.2.1
pytest==8.2.0
pytest-asyncio==0.23.7
```

- [ ] **Step 3: Create `ocr-api/app/__init__.py` and `ocr-api/app/outputs/__init__.py`**

Both files are empty – they just mark the directories as Python packages.

- [ ] **Step 4: Create `ocr-api/app/config.py`**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    api_key: str

    enable_paperless: bool = False
    paperless_url: str = "https://paperless.jru.me"
    paperless_token: str = ""

    enable_rclone: bool = False
    rclone_target: str = "OneDrive_Joe:scanner/"

    enable_filesystem: bool = False
    output_dir: str = "/output"

    ocr_language: str = "deu+eng+frk"
    trash_tmp_files: bool = True
    mail_to: str = ""

    class Config:
        env_file = ".env"


settings = Settings()
```

- [ ] **Step 5: Create `.gitignore`**

```gitignore
# Secrets
.env
raspi/ocrit.env

# Python
__pycache__/
*.pyc
*.pyo
.pytest_cache/

# Temp scan output
output/
```

- [ ] **Step 6: Commit**

```bash
git add ocr-api/Dockerfile ocr-api/requirements.txt ocr-api/app/__init__.py \
        ocr-api/app/outputs/__init__.py ocr-api/app/config.py .gitignore
git commit -m "feat: scaffold OCR API – Dockerfile, deps, config, .gitignore"
```

---

## Task 2: Example config files

**Files:**
- Create: `.env.example`
- Create: `raspi/ocrit.env.example`

- [ ] **Step 1: Create `.env.example`**

```bash
# OCR API – copy to .env and fill in real values

# Authentication
API_KEY=change-me-to-a-long-random-string

# Output: Paperless-ngx
ENABLE_PAPERLESS=true
PAPERLESS_URL=https://paperless.jru.me
PAPERLESS_TOKEN=your-paperless-api-token-here

# Output: rclone / OneDrive
ENABLE_RCLONE=false
RCLONE_TARGET=OneDrive_Joe:scanner/

# Output: local filesystem
ENABLE_FILESYSTEM=true
OUTPUT_DIR=/output

# OCR settings
OCR_LANGUAGE=deu+eng+frk
TRASH_TMP_FILES=true

# Email notification (mutt required)
MAIL_TO=your@email.com
```

- [ ] **Step 2: Create `raspi/ocrit.env.example`**

```bash
# Raspberry Pi – OCR API client config
# Copy to raspi/ocrit.env and fill in real values

OCR_API_HOST=http://192.168.1.100:8000
API_KEY=change-me-to-a-long-random-string
```

- [ ] **Step 3: Commit**

```bash
git add .env.example raspi/ocrit.env.example
git commit -m "feat: add example config files for API keys and tokens"
```

---

## Task 3: FastAPI app – endpoints and API-key auth

**Files:**
- Create: `ocr-api/app/main.py`
- Create: `ocr-api/tests/__init__.py`
- Create: `ocr-api/tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Create `ocr-api/tests/__init__.py` (empty), then `ocr-api/tests/test_api.py`:

```python
import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
from app.main import app

VALID_KEY = "test-key"


@pytest.fixture(autouse=True)
def override_settings(monkeypatch):
    monkeypatch.setenv("API_KEY", VALID_KEY)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_upload_requires_api_key(client):
    resp = client.post("/scan/upload")
    assert resp.status_code == 422  # missing header


def test_upload_rejects_wrong_key(client):
    resp = client.post("/scan/upload", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401


def test_upload_returns_job_id(client, tmp_path):
    tif = tmp_path / "scan_001.pnm.tif"
    tif.write_bytes(b"FAKE")
    with patch("app.main.enqueue_job", new_callable=AsyncMock):
        resp = client.post(
            "/scan/upload",
            headers={"x-api-key": VALID_KEY},
            files=[("files", ("scan_001.pnm.tif", tif.open("rb"), "image/tiff"))],
        )
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert body["status"] == "queued"


def test_status_not_found(client):
    resp = client.get("/scan/status/nonexistent", headers={"x-api-key": VALID_KEY})
    assert resp.status_code == 404


def test_status_wrong_key(client):
    resp = client.get("/scan/status/anything", headers={"x-api-key": "wrong"})
    assert resp.status_code == 401
```

- [ ] **Step 2: Run tests – confirm they fail**

```bash
cd /container/compose/Scan2Pi2OCR/ocr-api
pip install -r requirements.txt -q
python -m pytest tests/test_api.py -v 2>&1 | head -30
```

Expected: `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 3: Create `ocr-api/app/main.py`**

```python
import asyncio
import os
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, List

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile

from app.config import settings
from app.worker import enqueue_job, get_job_status, worker_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(worker_loop())
    yield
    task.cancel()


app = FastAPI(title="Scan2OCR API", lifespan=lifespan)


async def require_api_key(x_api_key: Annotated[str, Header()]) -> None:
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/scan/upload")
async def upload_scan(
    files: List[UploadFile],
    _: Annotated[None, Depends(require_api_key)],
):
    job_id = str(uuid.uuid4())
    tmp_dir = tempfile.mkdtemp(prefix=f"scan_{job_id}_")
    for f in files:
        dest = os.path.join(tmp_dir, f.filename)
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
    await enqueue_job(job_id, tmp_dir)
    return {"job_id": job_id, "status": "queued"}


@app.get("/scan/status/{job_id}")
async def scan_status(
    job_id: str,
    _: Annotated[None, Depends(require_api_key)],
):
    status = get_job_status(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return status
```

- [ ] **Step 4: Create stub `ocr-api/app/worker.py`** (enough for tests to pass)

```python
import asyncio

_queue: asyncio.Queue = asyncio.Queue()
_status: dict = {}


async def enqueue_job(job_id: str, tmp_dir: str) -> None:
    _status[job_id] = {"status": "queued"}
    await _queue.put((job_id, tmp_dir))


def get_job_status(job_id: str) -> dict | None:
    return _status.get(job_id)


async def worker_loop() -> None:
    while True:
        job_id, tmp_dir = await _queue.get()
        _status[job_id] = {"status": "processing"}
        # Full implementation in Task 7
        _queue.task_done()
```

- [ ] **Step 5: Run tests – confirm they pass**

```bash
cd /container/compose/Scan2Pi2OCR/ocr-api
python -m pytest tests/test_api.py -v
```

Expected: all 6 tests PASS

- [ ] **Step 6: Commit**

```bash
git add ocr-api/app/main.py ocr-api/app/worker.py ocr-api/tests/__init__.py ocr-api/tests/test_api.py
git commit -m "feat: FastAPI endpoints with API-key auth and async job queue stub"
```

---

## Task 4: OCR processing

**Files:**
- Create: `ocr-api/app/ocr.py`
- Create: `ocr-api/tests/test_ocr.py`

- [ ] **Step 1: Write failing tests**

Create `ocr-api/tests/test_ocr.py`:

```python
import os
import shutil
import tempfile
import pytest
from unittest.mock import patch, MagicMock
from app.ocr import is_blank_page, run_tesseract, clean_page


def test_is_blank_page_true():
    # histogram output: only white pixels → blank
    histogram = "     1234: (255,255,255) #FFFFFF white\n      3: (0,0,0) #000000 black\n"
    # black=3, white=1234 → 3/1234 = 0.0024 < 0.01 → blank
    assert is_blank_page(histogram) is True


def test_is_blank_page_false():
    # enough black pixels → not blank
    histogram = "     1000: (255,255,255) #FFFFFF white\n    500: (0,0,0) #000000 black\n"
    # black=500, white=1000 → 0.5 → not blank
    assert is_blank_page(histogram) is False


def test_is_blank_page_no_black():
    # no black pixels at all → blank
    histogram = "     9999: (255,255,255) #FFFFFF white\n"
    assert is_blank_page(histogram) is True
```

- [ ] **Step 2: Run tests – confirm they fail**

```bash
cd /container/compose/Scan2Pi2OCR/ocr-api
python -m pytest tests/test_ocr.py -v 2>&1 | head -20
```

Expected: `ImportError: cannot import name 'is_blank_page'`

- [ ] **Step 3: Create `ocr-api/app/ocr.py`**

```python
import asyncio
import os
import re
import subprocess
from pathlib import Path

from app.config import settings


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
    """Move blank pages to blanks/ subdirectory. Return list of remaining pages."""
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
    """Write scan_list.txt and run Tesseract. Produces file_name.pdf/.txt/.hocr."""
    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))
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
    loop = asyncio.get_event_loop()

    # Blank page removal
    await loop.run_in_executor(None, remove_blank_pages, tmp_dir)

    # Contrast cleanup
    pages = sorted(Path(tmp_dir).glob("scan_*.pnm.tif"))
    for page in pages:
        await loop.run_in_executor(None, clean_page, str(page))

    # OCR
    await loop.run_in_executor(None, run_tesseract, tmp_dir, file_name)

    pdf_path = os.path.join(tmp_dir, f"{file_name}.pdf")
    txt_path = os.path.join(tmp_dir, f"{file_name}.txt")
    return {"pdf": pdf_path, "txt": txt_path, "file_name": file_name}
```

- [ ] **Step 4: Run tests – confirm they pass**

```bash
cd /container/compose/Scan2Pi2OCR/ocr-api
python -m pytest tests/test_ocr.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add ocr-api/app/ocr.py ocr-api/tests/test_ocr.py
git commit -m "feat: OCR pipeline – blank detection, image cleanup, Tesseract invocation"
```

---

## Task 5: Output – Filesystem

**Files:**
- Create: `ocr-api/app/outputs/filesystem.py`

- [ ] **Step 1: Create `ocr-api/app/outputs/filesystem.py`**

```python
import os
import shutil

from app.config import settings


async def deliver(pdf_path: str, file_name: str) -> dict:
    """Copy PDF to OUTPUT_DIR. Returns delivery metadata."""
    os.makedirs(settings.output_dir, exist_ok=True)
    dest = os.path.join(settings.output_dir, f"{file_name}.pdf")
    shutil.copy2(pdf_path, dest)
    return {"target": "filesystem", "path": dest}
```

- [ ] **Step 2: Commit**

```bash
git add ocr-api/app/outputs/filesystem.py
git commit -m "feat: filesystem output target"
```

---

## Task 6: Output – Paperless-ngx

**Files:**
- Create: `ocr-api/app/outputs/paperless.py`

- [ ] **Step 1: Create `ocr-api/app/outputs/paperless.py`**

```python
import httpx

from app.config import settings


async def deliver(pdf_path: str, file_name: str) -> dict:
    """Upload PDF to Paperless-ngx via REST API."""
    url = f"{settings.paperless_url.rstrip('/')}/api/documents/post_document/"
    headers = {"Authorization": f"Token {settings.paperless_token}"}

    async with httpx.AsyncClient(timeout=60) as client:
        with open(pdf_path, "rb") as f:
            resp = await client.post(
                url,
                headers=headers,
                files={"document": (f"{file_name}.pdf", f, "application/pdf")},
                data={"title": file_name},
            )
        resp.raise_for_status()

    return {"target": "paperless", "status": resp.status_code, "response": resp.text}
```

- [ ] **Step 2: Commit**

```bash
git add ocr-api/app/outputs/paperless.py
git commit -m "feat: Paperless-ngx output target via REST API"
```

---

## Task 7: Output – rclone

**Files:**
- Create: `ocr-api/app/outputs/rclone.py`

- [ ] **Step 1: Create `ocr-api/app/outputs/rclone.py`**

```python
import asyncio
import subprocess

from app.config import settings


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"rclone failed: {result.stderr}")
    return result.stdout.strip()


async def deliver(pdf_path: str, file_name: str) -> dict:
    """Upload PDF via rclone and return a shareable link."""
    loop = asyncio.get_event_loop()
    target = f"{settings.rclone_target.rstrip('/')}/{file_name}.pdf"

    await loop.run_in_executor(None, _run, ["rclone", "copy", pdf_path, settings.rclone_target])
    link = await loop.run_in_executor(None, _run, ["rclone", "link", target])

    return {"target": "rclone", "link": link}
```

- [ ] **Step 2: Commit**

```bash
git add ocr-api/app/outputs/rclone.py
git commit -m "feat: rclone output target with shareable link"
```

---

## Task 8: Wire worker loop – connect OCR + all outputs

**Files:**
- Modify: `ocr-api/app/worker.py`

- [ ] **Step 1: Replace stub worker with full implementation**

```python
import asyncio
import os
import shutil
from datetime import datetime

from app.config import settings
from app.ocr import process_scan

_queue: asyncio.Queue = asyncio.Queue()
_status: dict = {}


async def enqueue_job(job_id: str, tmp_dir: str) -> None:
    _status[job_id] = {"status": "queued"}
    await _queue.put((job_id, tmp_dir))


def get_job_status(job_id: str) -> dict | None:
    return _status.get(job_id)


async def _run_outputs(result: dict) -> list[dict]:
    """Run all enabled output targets in parallel."""
    tasks = []
    pdf_path = result["pdf"]
    file_name = result["file_name"]

    if settings.enable_filesystem:
        from app.outputs.filesystem import deliver as fs_deliver
        tasks.append(fs_deliver(pdf_path, file_name))

    if settings.enable_paperless:
        from app.outputs.paperless import deliver as pl_deliver
        tasks.append(pl_deliver(pdf_path, file_name))

    if settings.enable_rclone:
        from app.outputs.rclone import deliver as rc_deliver
        tasks.append(rc_deliver(pdf_path, file_name))

    return await asyncio.gather(*tasks, return_exceptions=True)


async def worker_loop() -> None:
    while True:
        job_id, tmp_dir = await _queue.get()
        _status[job_id] = {"status": "processing"}
        try:
            file_name = f"scan_{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            result = await process_scan(tmp_dir, file_name)
            deliveries = await _run_outputs(result)
            _status[job_id] = {
                "status": "done",
                "file_name": file_name,
                "outputs": [
                    d if not isinstance(d, Exception) else {"error": str(d)}
                    for d in deliveries
                ],
            }
        except Exception as e:
            _status[job_id] = {"status": "error", "error": str(e)}
        finally:
            if settings.trash_tmp_files:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            _queue.task_done()
```

- [ ] **Step 2: Run full test suite**

```bash
cd /container/compose/Scan2Pi2OCR/ocr-api
python -m pytest tests/ -v
```

Expected: all tests PASS

- [ ] **Step 3: Commit**

```bash
git add ocr-api/app/worker.py
git commit -m "feat: full worker loop – OCR + parallel output delivery"
```

---

## Task 9: docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create `docker-compose.yml`**

```yaml
services:
  ocr-api:
    build: ./ocr-api
    container_name: ocr-api
    ports:
      - "8000:8000"
    env_file: .env
    volumes:
      - ./output:/output
      - ${HOME}/.config/rclone/rclone.conf:/root/.config/rclone/rclone.conf:ro
    restart: unless-stopped
```

- [ ] **Step 2: Build and smoke-test**

```bash
cd /container/compose/Scan2Pi2OCR
docker compose build
docker compose up -d
sleep 3
curl -s http://localhost:8000/health
```

Expected: `{"status":"ok"}`

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat: docker-compose stack for OCR API service"
```

---

## Task 10: Adapt `raspi/ocrit.sh`

**Files:**
- Modify: `raspi/ocrit.sh`

- [ ] **Step 1: Replace SSH/rsync block with HTTP upload**

Replace the entire content of `raspi/ocrit.sh` with:

```bash
#!/bin/bash
exec 1> >(logger -s -t $(basename $0)) 2>&1

# Load config (OCR_API_HOST, API_KEY)
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [ -f "$DIR/ocrit.env" ]; then
    source "$DIR/ocrit.env"
fi

OUT_DIR=$1
TMP_DIR=$2
BASE=$(basename $TMP_DIR)

echo "$(id) – uploading $BASE to $OCR_API_HOST"

# Build -F arguments for each scan file
FILES_ARGS=()
for f in "$TMP_DIR"/scan_*.pnm.tif; do
    FILES_ARGS+=(-F "files=@${f};type=image/tiff")
done

if [ ${#FILES_ARGS[@]} -eq 0 ]; then
    echo "No scan files found in $TMP_DIR – aborting"
    exit 1
fi

RESPONSE=$(curl -s -X POST "${OCR_API_HOST}/scan/upload" \
    -H "x-api-key: ${API_KEY}" \
    "${FILES_ARGS[@]}")

echo "API response: $RESPONSE"

JOB_ID=$(echo "$RESPONSE" | grep -o '"job_id":"[^"]*"' | cut -d'"' -f4)
if [ -z "$JOB_ID" ]; then
    echo "ERROR: no job_id in response"
    exit 1
fi

echo "Job submitted: $JOB_ID – OCR running asynchronously on server"

# Clean up local temp dir (server has the files now)
rm -rf "$TMP_DIR"
echo "OCR handoff done"
```

- [ ] **Step 2: Make executable and test syntax**

```bash
chmod +x /container/compose/Scan2Pi2OCR/raspi/ocrit.sh
bash -n /container/compose/Scan2Pi2OCR/raspi/ocrit.sh
```

Expected: no output (syntax OK)

- [ ] **Step 3: Commit**

```bash
git add raspi/ocrit.sh raspi/ocrit.env.example
git commit -m "feat: raspi/ocrit.sh – replace SSH/rsync with HTTP POST to OCR API"
```

---

## Task 11: Update documentation

**Files:**
- Modify: `.github/copilot-instructions.md`
- Modify: `README.md`

- [ ] **Step 1: Update `README.md`** – replace the "Installation on Raspi" SSH section with the new flow:

```markdown
## OCR API Setup (OCR Machine)

```bash
cd /container/compose/Scan2Pi2OCR
cp .env.example .env
# Edit .env – set API_KEY, PAPERLESS_TOKEN, enable desired output targets
docker compose up -d
```

## Raspberry Pi Setup

```bash
cp raspi/ocrit.env.example raspi/ocrit.env
# Edit raspi/ocrit.env – set OCR_API_HOST and API_KEY
```
```

- [ ] **Step 2: Update `.github/copilot-instructions.md`** – add the new API section and docker-compose command.

- [ ] **Step 3: Final commit**

```bash
git add README.md .github/copilot-instructions.md
git commit -m "docs: update architecture docs for new REST API approach"
```
