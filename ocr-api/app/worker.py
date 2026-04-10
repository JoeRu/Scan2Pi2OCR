import asyncio
import logging
import os
import shutil
from datetime import datetime

import httpx

from app.ai_metadata import AiMetadata, extract_ai_metadata
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


async def enqueue_job(job_id: str, tmp_dir: str, file_name: str, scan_timestamp: datetime) -> None:
    _status[job_id] = {"status": "queued"}
    await _get_queue().put((job_id, tmp_dir, file_name, scan_timestamp))
    logger.info("Job queued: job_id=%s name=%r", job_id, file_name)


def get_job_status(job_id: str) -> dict | None:
    return _status.get(job_id)


async def _fetch_paperless_document_types(settings) -> list[str] | None:
    """Fetch document type names from Paperless-ngx. Returns None on failure."""
    try:
        url = f"{settings.paperless_url.rstrip('/')}/api/document_types/?page_size=100"
        headers = {"Authorization": f"Token {settings.paperless_token}"}
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
        data = response.json()
        return [item["name"] for item in data.get("results", [])]
    except Exception as exc:
        logger.warning("Could not fetch Paperless document types: %s", exc)
        return None


async def _process_job(job_id: str, tmp_dir: str, file_name: str, scan_timestamp: datetime) -> None:
    settings = get_settings()
    logger.info("Job started: job_id=%s name=%r", job_id, file_name)
    try:
        _status[job_id] = {"status": "processing"}

        logger.info("[%s] Running OCR pipeline", job_id)
        ocr_result = await process_scan(tmp_dir, file_name)
        pdf_path = ocr_result["pdf"]
        txt_path = ocr_result["txt"]
        logger.info("[%s] OCR complete — pdf=%s", job_id, pdf_path)

        ai_meta: AiMetadata | None = None
        if settings.enable_ai_metadata:
            logger.info("[%s] Running AI metadata extraction", job_id)
            document_types: list[str] | None = None
            if settings.enable_paperless:
                document_types = await _fetch_paperless_document_types(settings)
            ai_meta = await extract_ai_metadata(txt_path, scan_timestamp, settings, document_types)
            if ai_meta:
                logger.info("[%s] AI metadata: dokumenttyp=%r korrespondent=%r filename_stem=%r",
                            job_id, ai_meta.dokumenttyp, ai_meta.korrespondent, ai_meta.filename_stem)
                file_name = ai_meta.filename_stem
            else:
                logger.warning("[%s] AI metadata unavailable, using original filename", job_id)

        tasks = []
        task_names = []
        if settings.enable_filesystem:
            tasks.append(deliver_filesystem(pdf_path, file_name))
            task_names.append("filesystem")
        if settings.enable_paperless:
            tasks.append(deliver_paperless(pdf_path, file_name, ai_meta if settings.enable_ai_metadata else None))
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
            job_id, tmp_dir, file_name, scan_timestamp = await _queue.get()
            logger.debug("Dequeued job_id=%s", job_id)
            asyncio.create_task(_process_job(job_id, tmp_dir, file_name, scan_timestamp))
            _queue.task_done()
    except asyncio.CancelledError:
        logger.info("Worker loop cancelled")
