import asyncio
import subprocess

from app.config import get_settings


async def deliver_mail(pdf_path: str, file_name: str, txt_path: str) -> dict:
    """Send HTML email with OCR text preview via mutt + msmtp."""
    settings = get_settings()
    subject = f"{file_name} - ScanPi Mail"

    preview = ""
    try:
        with open(txt_path, "r", errors="replace") as f:
            preview = f.read(3000)
    except Exception:
        pass

    body = (
        "<html><body>"
        f"<p>OCR completed: <b>{file_name}</b></p>"
        f"<p>PDF path: <code>{pdf_path}</code></p>"
        "<h2>Content preview</h2>"
        f"<pre>{preview}</pre>"
        "</body></html>"
    )

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _send_mail, settings.mail_to, subject, body)
    return {"mail": {"status": "ok", "to": settings.mail_to}}


def _send_mail(to: str, subject: str, body: str) -> None:
    result = subprocess.run(
        ["mutt", "-e", "set content_type=text/html", "-s", subject, to],
        input=body,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"mutt failed: {result.stderr}")
