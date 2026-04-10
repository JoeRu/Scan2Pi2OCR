import asyncio
import subprocess

from app.config import get_settings


async def deliver_rclone(pdf_path: str, file_name: str) -> dict:
    """Upload PDF to rclone remote."""
    settings = get_settings()
    dest = f"{settings.rclone_target}/{file_name}.pdf"
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _rclone_copy, pdf_path, dest)
    return {"rclone": {"status": "ok", "dest": dest}}


def _rclone_copy(src: str, dest: str) -> None:
    result = subprocess.run(
        ["rclone", "copy", src, dest],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"rclone failed: {result.stderr}")
