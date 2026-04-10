import asyncio
import logging
import os
import shutil

from app.config import get_settings
from app.ocr import process_scan
from app.outputs.filesystem import deliver_filesystem
from app.outputs.mail import deliver_mail
from app.outputs.paperless import deliver_paperless
from app.outputs.rclone import deliver_rclone

logger = logging.getLogger("app.worker")

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
    logger.info("Job queued: job_id=%s name=%r", job_id, file_name)


def get_job_status(job_id: str) -> dict | None:
    return _status.get(job_id)


async def _process_job(job_id: str, tmp_dir: str, file_name: str) -> None:
    settings = get_settings()
    logger.info("Job started: job_id=%s name=%r", job_id, file_name)
    try:
        _status[job_id] = {"status": "processing"}

        logger.info("[%s] Running OCR pipeline", job_id)
        ocr_result = await process_scan(tmp_dir, file_name)
        pdf_path = ocr_result["pdf"]
        txt_path = ocr_result["txt"]
        logger.info("[%s] OCR complete — pdf=%s", job_id, pdf_path)

        tasks = []
        task_names = []
        if settings.enable_filesystem:
            tasks.append(deliver_filesystem(pdf_path, file_name))
            task_names.append("filesystem")
        if settings.enable_paperless:
            tasks.append(deliver_paperless(pdf_path, file_name))
            task_names.append("paperless")
        if settings.enable_rclone:
            tasks.append(deliver_rclone(pdf_path, file_name))
            task_names.append("rclone")
        if settings.enable_mail and settings.mail_to:
            tasks.append(deliver_mail(pdf_path, file_name, txt_path))
            task_names.append("mail")

        if tasks:
            logger.info("[%s] Delivering to: %s", job_id, task_names)
        else:
            logger.warning("[%s] No output destinations enabled", job_id)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        merged = {}
        errors = {}
        for name, result in zip(task_names, results):
            if isinstance(result, Exception):
                logger.error("[%s] Delivery failed [%s]: %s", job_id, name, result, exc_info=result)
                errors[name] = str(result)
            else:
                logger.info("[%s] Delivery succeeded [%s]: %s", job_id, name, result)
                merged.update(result)

        if errors:
            logger.warning("[%s] Finished with errors: %s", job_id, list(errors.keys()))
            _status[job_id] = {"status": "done_with_errors", "outputs": merged, "errors": errors}
        else:
            logger.info("[%s] Job completed successfully", job_id)
            _status[job_id] = {"status": "done", "outputs": merged}
    except Exception as exc:
        logger.error("[%s] Job failed: %s", job_id, exc, exc_info=True)
        _status[job_id] = {"status": "failed", "error": str(exc)}
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        logger.debug("[%s] Cleaned up tmp_dir=%s", job_id, tmp_dir)


async def worker_loop() -> None:
    global _queue
    _queue = asyncio.Queue()
    logger.info("Worker loop ready")
    try:
        while True:
            job_id, tmp_dir, file_name = await _queue.get()
            logger.debug("Dequeued job_id=%s", job_id)
            asyncio.create_task(_process_job(job_id, tmp_dir, file_name))
            _queue.task_done()
    except asyncio.CancelledError:
        logger.info("Worker loop cancelled")
