import asyncio
import logging
import subprocess

from app.config import get_settings

logger = logging.getLogger("app.outputs.rclone")


async def deliver_rclone(pdf_path: str, file_name: str) -> dict:
    """Upload PDF to rclone remote."""
    settings = get_settings()
    dest = f"{settings.rclone_target}/{file_name}.pdf"
    logger.info("Uploading via rclone: %s -> %s", pdf_path, dest)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _rclone_copy, pdf_path, dest)
    logger.info("rclone upload complete: %s", dest)
    return {"rclone": {"status": "ok", "dest": dest}}


def _rclone_copy(src: str, dest: str) -> None:
    result = subprocess.run(
        ["rclone", "copy", src, dest],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        logger.error("rclone failed (rc=%d)\nstdout: %s\nstderr: %s",
                     result.returncode, result.stdout.strip(), result.stderr.strip())
        raise RuntimeError(f"rclone failed: {result.stderr}")
