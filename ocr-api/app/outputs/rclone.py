import asyncio
import logging
import subprocess
from datetime import datetime, timedelta

from app.config import get_settings

logger = logging.getLogger("app.outputs.rclone")


async def deliver_rclone(pdf_path: str, file_name: str) -> dict:
    """Upload PDF to rclone remote, optionally with an expiring share link."""
    settings = get_settings()
    dest = f"{settings.rclone_target.rstrip('/')}/{file_name}.pdf"
    logger.info("Uploading via rclone: %s -> %s", pdf_path, dest)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _rclone_copy, pdf_path, dest)
    logger.info("rclone upload complete: %s", dest)
    result = {"status": "ok", "dest": dest}

    if settings.rclone_mail_link:
        days = settings.rclone_link_expire_days
        link = await loop.run_in_executor(None, _rclone_link, dest, days)
        if link:
            result["link"] = link
            result["link_expires"] = (datetime.now().astimezone() + timedelta(days=days)).isoformat(timespec="seconds")
            logger.info("rclone link created (expires in %d days): %s", days, link)
    return {"rclone": result}


def _rclone_copy(src: str, dest: str) -> None:
    result = subprocess.run(
        ["rclone", "copyto", src, dest],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error("rclone failed (rc=%d)\nstdout: %s\nstderr: %s",
                     result.returncode, result.stdout.strip(), result.stderr.strip())
        raise RuntimeError(f"rclone failed: {result.stderr}")


def _rclone_link(dest: str, expire_days: int) -> str | None:
    """Create a share link that expires after expire_days. None if rclone fails."""
    result = subprocess.run(
        ["rclone", "link", "--expire", f"{expire_days}d", dest],
        capture_output=True, text=True,
    )
    urls = [line for line in result.stdout.split() if line.startswith("https://")]
    if result.returncode != 0 or not urls:
        logger.warning("rclone link failed (rc=%d), mail goes out without link: %s",
                       result.returncode, result.stderr.strip())
        return None
    return urls[-1]
