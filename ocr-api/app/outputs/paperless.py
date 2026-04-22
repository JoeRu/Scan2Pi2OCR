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
            params={"name": name, "page_size": 1000},
            headers=headers,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        exact = [r for r in results if r["name"].lower() == name.lower()]
        logger.debug(
            "Paperless %s search %r → %d result(s), %d exact match(es)",
            resource, name, len(results), len(exact),
        )
        if exact:
            entity_id = exact[0]["id"]
            logger.debug("Paperless %s %r → found id=%s", resource, name, entity_id)
            # If the existing entity is privately owned, clear the owner so it becomes
            # globally visible (Paperless treats owner=null as accessible to all users).
            if exact[0].get("owner") is not None:
                patch_resp = await client.patch(
                    f"{base_url}/api/{resource}/{entity_id}/",
                    json={"owner": None},
                    headers=headers,
                )
                if patch_resp.is_success:
                    logger.info("Paperless %s %r: cleared owner to make globally visible", resource, name)
                else:
                    logger.debug("Paperless %s %r: could not clear owner (status %d)", resource, name, patch_resp.status_code)
            return entity_id
        logger.debug("Paperless %s %r → no exact match, creating", resource, name)
        create_resp = await client.post(
            f"{base_url}/api/{resource}/",
            json={"name": name, "owner": None},
            headers=headers,
        )
        create_resp.raise_for_status()
        entity_id = create_resp.json()["id"]
        logger.info("Paperless %s created: %r → id=%s", resource, name, entity_id)
        return entity_id
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

    logger.info(
        "Paperless upload fields: correspondent_id=%s document_type_id=%s tag_ids=%s",
        correspondent_id, document_type_id, tag_ids,
    )

    # httpx 0.27 multipart streams are SyncByteStream — incompatible with AsyncClient.
    # Also, combining files= and data= (list of tuples) is broken in httpx 0.27/h11
    # (h11 receives raw tuples instead of encoded bytes).
    # Workaround: encode ALL form fields as multipart parts inside files= only.
    def _post_sync() -> httpx.Response:
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()
        # Build a single multipart payload: file first, then all form fields.
        # Form fields use (None, value_bytes) — no filename = plain form part.
        multipart: list = [("document", (f"{file_name}.pdf", pdf_bytes, "application/pdf"))]
        for key, value in data_tuples:
            multipart.append((key, (None, value.encode())))
        with httpx.Client(timeout=120) as client:
            return client.post(url, headers=auth_headers, files=multipart)

    response = await asyncio.to_thread(_post_sync)
    if response.is_error:
        logger.error("Paperless-ngx returned HTTP %d: %s", response.status_code, response.text[:500])
    response.raise_for_status()
    task_id = response.text.strip()
    logger.info("Paperless-ngx accepted document: task_id=%s", task_id)
    return {"paperless": {"status": "ok", "task_id": task_id}}
