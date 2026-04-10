import os
import shutil

from app.config import get_settings


async def deliver_filesystem(pdf_path: str, file_name: str) -> dict:
    """Copy PDF to the configured output directory."""
    settings = get_settings()
    os.makedirs(settings.output_dir, exist_ok=True)
    dest = os.path.join(settings.output_dir, f"{file_name}.pdf")
    shutil.copy2(pdf_path, dest)
    return {"filesystem": {"status": "ok", "path": dest}}
