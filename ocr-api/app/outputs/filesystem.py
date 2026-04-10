import logging
import os
import shutil

from app.config import get_settings

logger = logging.getLogger("app.outputs.filesystem")


async def deliver_filesystem(pdf_path: str, file_name: str) -> dict:
    """Copy PDF to the configured output directory."""
    settings = get_settings()
    os.makedirs(settings.output_dir, exist_ok=True)
    dest = os.path.join(settings.output_dir, f"{file_name}.pdf")
    logger.info("Copying to filesystem: %s -> %s", pdf_path, dest)
    shutil.copy2(pdf_path, dest)
    size = os.path.getsize(dest)
    logger.info("Filesystem delivery complete: %s (%d bytes)", dest, size)
    return {"filesystem": {"status": "ok", "path": dest}}
