import httpx

from app.config import get_settings


async def deliver_paperless(pdf_path: str, file_name: str) -> dict:
    """Upload PDF to Paperless-ngx via REST API."""
    settings = get_settings()
    url = f"{settings.paperless_url.rstrip('/')}/api/documents/post_document/"
    headers = {"Authorization": f"Token {settings.paperless_token}"}
    async with httpx.AsyncClient(timeout=120) as client:
        with open(pdf_path, "rb") as f:
            response = await client.post(
                url,
                headers=headers,
                files={"document": (f"{file_name}.pdf", f, "application/pdf")},
                data={"title": file_name},
            )
    response.raise_for_status()
    return {"paperless": {"status": "ok", "task_id": response.text.strip()}}
