from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import httpx

from app.config import get_settings

if TYPE_CHECKING:
    from app.ai_metadata import AiMetadata

logger = logging.getLogger("app.outputs.paperless")


async def _lookup_or_create(
    client: httpx.AsyncClient,
    base_url: str,
    resource: str,
    name: str,
    headers: dict,
) -> int | None:
    """Look up entity by name; create if not found. Returns the entity id or None on error."""
    try:
        resp = await client.get(
            f"{base_url}/api/{resource}/",
            params={"name": name},
            headers=headers,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        if results:
            return results[0]["id"]
        create_resp = await client.post(
            f"{base_url}/api/{resource}/",
            json={"name": name},
            headers=headers,
        )
        create_resp.raise_for_status()
        return create_resp.json()["id"]
    except Exception as exc:
        logger.warning("Could not look up/create Paperless %s %r: %s", resource, name, exc)
        return None


async def deliver_paperless(
    pdf_path: str,
    file_name: str,
    ai_meta: "AiMetadata | None" = None,
) -> dict:
    """Upload PDF to Paperless-ngx via REST API."""
    settings = get_settings()
    url = f"{settings.paperless_url.rstrip('/')}/api/documents/post_document/"
    logger.info("Uploading to Paperless-ngx: url=%s name=%r", url, file_name)
    auth_headers = {"Authorization": f"Token {settings.paperless_token}"}

    correspondent_id: int | None = None
    document_type_id: int | None = None
    tag_ids: list[int] = []

    if ai_meta:
        logger.info(
            "Applying AI metadata for Paperless: title=%r correspondent=%r doc_type=%r tags=%s",
            file_name, ai_meta.korrespondent, ai_meta.dokumenttyp, ai_meta.tags,
        )
        base_url = settings.paperless_url.rstrip("/")
        async with httpx.AsyncClient(timeout=15) as client:
            if ai_meta.korrespondent:
                correspondent_id = await _lookup_or_create(
                    client, base_url, "correspondents", ai_meta.korrespondent, auth_headers
                )
            if ai_meta.dokumenttyp:
                document_type_id = await _lookup_or_create(
                    client, base_url, "document_types", ai_meta.dokumenttyp, auth_headers
                )
            for tag_name in (ai_meta.tags or []):
                tag_id = await _lookup_or_create(client, base_url, "tags", tag_name, auth_headers)
                if tag_id is not None:
                    tag_ids.append(tag_id)

    data_tuples = [("title", file_name)]
    if correspondent_id is not None:
        data_tuples.append(("correspondent", str(correspondent_id)))
    if document_type_id is not None:
        data_tuples.append(("document_type", str(document_type_id)))
    for tid in tag_ids:
        data_tuples.append(("tags", str(tid)))

    # httpx 0.27 multipart streams are SyncByteStream — incompatible with AsyncClient.
    # Run the multipart POST in a thread to avoid the event-loop conflict.
    def _post_sync() -> httpx.Response:
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        with httpx.Client(timeout=120) as client:
            return client.post(
                url,
                headers=auth_headers,
                files={"document": (f"{file_name}.pdf", pdf_bytes, "application/pdf")},
                data=data_tuples,
            )

    response = await asyncio.to_thread(_post_sync)
    if response.is_error:
        logger.error("Paperless-ngx returned HTTP %d: %s", response.status_code, response.text[:500])
    response.raise_for_status()
    task_id = response.text.strip()
    logger.info("Paperless-ngx accepted document: task_id=%s", task_id)
    return {"paperless": {"status": "ok", "task_id": task_id}}
