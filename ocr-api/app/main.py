import asyncio
import os
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, List

from fastapi import Depends, FastAPI, Header, HTTPException, UploadFile

from app.config import Settings, get_settings
from app.worker import enqueue_job, get_job_status, worker_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(worker_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="Scan2OCR API", lifespan=lifespan)


async def require_api_key(
    x_api_key: Annotated[str, Header()],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
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
    file_name = os.path.splitext(os.path.basename(files[0].filename or "scan"))[0]
    for f in files:
        safe_name = os.path.basename(f.filename or f"file_{uuid.uuid4()}")
        dest = os.path.join(tmp_dir, safe_name)
        with open(dest, "wb") as out:
            shutil.copyfileobj(f.file, out)
        await f.close()
    await enqueue_job(job_id, tmp_dir, file_name)
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
