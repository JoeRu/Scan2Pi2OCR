import asyncio
import logging
import subprocess

from app.config import get_settings

logger = logging.getLogger("app.outputs.mail")


async def deliver_mail(pdf_path: str, file_name: str, txt_path: str,
                      onedrive_link: str | None = None, link_expires: str | None = None) -> dict:
    """Send HTML email with OCR text preview via mutt + msmtp."""
    settings = get_settings()
    subject = f"{file_name} - ScanPi Mail"

    preview = ""
    try:
        with open(txt_path, "r", errors="replace") as f:
            preview = f.read(3000)
    except Exception as exc:
        logger.warning("Could not read OCR text for mail preview: %s", exc)

    link_html = ""
    if onedrive_link:
        valid_until = f" (valid until {link_expires[:10]})" if link_expires else ""
        link_html = f'<p>OneDrive link{valid_until}: <a href="{onedrive_link}">{onedrive_link}</a></p>'

    body = (
        "<html><body>"
        f"<p>OCR completed: <b>{file_name}</b></p>"
        f"<p>PDF path: <code>{pdf_path}</code></p>"
        f"{link_html}"
        "<h2>Content preview</h2>"
        f"<pre>{preview}</pre>"
        "</body></html>"
    )

    logger.info("Sending mail: to=%s subject=%r", settings.mail_to, subject)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _send_mail, settings.mail_to, subject, body)
    logger.info("Mail sent to %s", settings.mail_to)
    return {"mail": {"status": "ok", "to": settings.mail_to}}


def _send_mail(to: str, subject: str, body: str) -> None:
    result = subprocess.run(
        ["mutt", "-e", "set content_type=text/html", "-s", subject, to],
        input=body,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        logger.error("mutt failed (rc=%d)\nstdout: %s\nstderr: %s",
                     result.returncode, result.stdout.strip(), result.stderr.strip())
        raise RuntimeError(f"mutt failed: {result.stderr}")
