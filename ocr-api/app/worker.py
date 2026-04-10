import asyncio
import os
import shutil

from app.config import get_settings
from app.ocr import process_scan
from app.outputs.filesystem import deliver_filesystem
from app.outputs.mail import deliver_mail
from app.outputs.paperless import deliver_paperless
from app.outputs.rclone import deliver_rclone

_queue: asyncio.Queue | None = None
_status: dict = {}


def _get_queue() -> asyncio.Queue:
    global _queue
    if _queue is None:
        _queue = asyncio.Queue()
    return _queue


async def enqueue_job(job_id: str, tmp_dir: str, file_name: str) -> None:
    _status[job_id] = {"status": "queued"}
    await _get_queue().put((job_id, tmp_dir, file_name))


def get_job_status(job_id: str) -> dict | None:
    return _status.get(job_id)


async def _process_job(job_id: str, tmp_dir: str, file_name: str) -> None:
    settings = get_settings()
    try:
        _status[job_id] = {"status": "processing"}
        ocr_result = await process_scan(tmp_dir, file_name)
        pdf_path = ocr_result["pdf"]
        txt_path = ocr_result["txt"]

        tasks = []
        if settings.enable_filesystem:
            tasks.append(deliver_filesystem(pdf_path, file_name))
        if settings.enable_paperless:
            tasks.append(deliver_paperless(pdf_path, file_name))
        if settings.enable_rclone:
            tasks.append(deliver_rclone(pdf_path, file_name))
        if settings.enable_mail and settings.mail_to:
            tasks.append(deliver_mail(pdf_path, file_name, txt_path))

        results = await asyncio.gather(*tasks)
        merged = {}
        for r in results:
            merged.update(r)

        _status[job_id] = {"status": "done", "outputs": merged}
    except Exception as exc:
        _status[job_id] = {"status": "failed", "error": str(exc)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


async def worker_loop() -> None:
    global _queue
    _queue = asyncio.Queue()
    try:
        while True:
            job_id, tmp_dir, file_name = await _queue.get()
            asyncio.create_task(_process_job(job_id, tmp_dir, file_name))
            _queue.task_done()
    except asyncio.CancelledError:
        pass
