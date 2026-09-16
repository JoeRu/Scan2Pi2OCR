import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.outputs.rclone import _rclone_copy, deliver_rclone


def _ok():
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


def test_rclone_copy_uses_copyto_so_dest_is_a_file():
    # `rclone copy` treats dest as a directory and creates <name>.pdf/scan_001.pdf
    with patch("app.outputs.rclone.subprocess.run", return_value=_ok()) as run:
        _rclone_copy("/tmp/x/scan_001.pdf", "remote:scanner/doc.pdf")
    assert run.call_args.args[0] == ["rclone", "copyto", "/tmp/x/scan_001.pdf", "remote:scanner/doc.pdf"]


def test_rclone_copy_raises_on_failure():
    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    with patch("app.outputs.rclone.subprocess.run", return_value=failed):
        with pytest.raises(RuntimeError, match="boom"):
            _rclone_copy("/tmp/a.pdf", "remote:b.pdf")


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["OneDrive_Joe:scanner/", "OneDrive_Joe:scanner"])
async def test_deliver_rclone_dest_joins_target_and_name(target):
    settings = Settings(api_key="k", enable_rclone=True, rclone_target=target)
    copy = MagicMock()
    with patch("app.outputs.rclone.get_settings", return_value=settings), \
         patch("app.outputs.rclone._rclone_copy", copy):
        result = await deliver_rclone("/tmp/x/scan_001.pdf", "20260916_doc")
    copy.assert_called_once_with("/tmp/x/scan_001.pdf", "OneDrive_Joe:scanner/20260916_doc.pdf")
    assert result == {"rclone": {"status": "ok", "dest": "OneDrive_Joe:scanner/20260916_doc.pdf"}}
