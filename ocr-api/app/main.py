import asyncio
import logging
import logging.config
import os
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, List

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile

from app.config import Settings, get_settings
from app.worker import enqueue_job, get_job_status, worker_loop

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "default",
        }
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "app": {"level": "DEBUG", "propagate": True},
    },
})

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Scan2OCR API — worker loop starting")
    task = asyncio.create_task(worker_loop())
    yield
    logger.info("Shutting down — cancelling worker loop")
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Worker loop stopped")


app = FastAPI(title="Scan2OCR API", lifespan=lifespan)


async def require_api_key(
    x_api_key: Annotated[str, Header()],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    if x_api_key != settings.api_key:
        logger.warning("Rejected request: invalid API key")
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
    file_name = os.path.basename(files[0].filename or "scan").split(".")[0]
    logger.info("Upload received: job_id=%s files=%d name=%r tmp_dir=%s",
                job_id, len(files), file_name, tmp_dir)

    saved = []
    for f in files:
        safe_name = os.path.basename(f.filename or f"file_{uuid.uuid4()}")
        dest = os.path.join(tmp_dir, safe_name)
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        size = os.path.getsize(dest)
        logger.debug("  Saved file: %s (%d bytes)", safe_name, size)
        saved.append(safe_name)
        await f.close()

    logger.info("All files saved for job %s: %s — enqueueing", job_id, saved)
    await enqueue_job(job_id, tmp_dir, file_name)
    return {"job_id": job_id, "status": "queued"}


@app.get("/scan/status/{job_id}")
async def scan_status(
    job_id: str,
    _: Annotated[None, Depends(require_api_key)],
):
    status = get_job_status(job_id)
    if status is None:
        logger.debug("Status requested for unknown job_id=%s", job_id)
        raise HTTPException(status_code=404, detail="Job not found")
    logger.debug("Status for job %s: %s", job_id, status.get("status"))
    return status
