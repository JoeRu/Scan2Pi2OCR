import logging

import httpx

from app.config import get_settings

logger = logging.getLogger("app.outputs.paperless")


async def deliver_paperless(pdf_path: str, file_name: str) -> dict:
    """Upload PDF to Paperless-ngx via REST API."""
    settings = get_settings()
    url = f"{settings.paperless_url.rstrip('/')}/api/documents/post_document/"
    logger.info("Uploading to Paperless-ngx: url=%s name=%r", url, file_name)
    headers = {"Authorization": f"Token {settings.paperless_token}"}
    async with httpx.AsyncClient(timeout=120) as client:
        with open(pdf_path, "rb") as f:
            response = await client.post(
                url,
                headers=headers,
                files={"document": (f"{file_name}.pdf", f, "application/pdf")},
                data={"title": file_name},
            )
    if response.is_error:
        logger.error("Paperless-ngx returned HTTP %d: %s", response.status_code, response.text[:500])
    response.raise_for_status()
    task_id = response.text.strip()
    logger.info("Paperless-ngx accepted document: task_id=%s", task_id)
    return {"paperless": {"status": "ok", "task_id": task_id}}
