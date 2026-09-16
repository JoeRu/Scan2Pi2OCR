import subprocess
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.outputs.rclone import _rclone_copy, _rclone_link, deliver_rclone


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


def _link_settings(**kwargs):
    defaults = dict(api_key="k", enable_rclone=True, rclone_target="remote:scanner/", rclone_mail_link=True)
    defaults.update(kwargs)
    return Settings(**defaults)


def test_link_expiry_defaults_to_30_days():
    assert Settings(api_key="k").rclone_link_expire_days == 30


@pytest.mark.parametrize("days", [0, 31, 365])
def test_link_expiry_outside_1_to_30_days_is_rejected(days):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(api_key="k", rclone_link_expire_days=days)


def test_rclone_link_always_passes_expire_and_returns_url():
    out = subprocess.CompletedProcess(
        args=[], returncode=0, stderr="NOTICE: Don't know how to convert share link",
        stdout="https://1drv.ms/b/c/abc/IQBT\n")
    with patch("app.outputs.rclone.subprocess.run", return_value=out) as run:
        assert _rclone_link("remote:scanner/doc.pdf", 30) == "https://1drv.ms/b/c/abc/IQBT"
    assert run.call_args.args[0] == ["rclone", "link", "--expire", "30d", "remote:scanner/doc.pdf"]


def test_rclone_link_returns_none_on_failure():
    failed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="expire not supported")
    with patch("app.outputs.rclone.subprocess.run", return_value=failed):
        assert _rclone_link("remote:scanner/doc.pdf", 30) is None


@pytest.mark.asyncio
async def test_deliver_rclone_adds_expiring_link_when_enabled():
    link = MagicMock(return_value="https://1drv.ms/b/x")
    with patch("app.outputs.rclone.get_settings", return_value=_link_settings(rclone_link_expire_days=7)), \
         patch("app.outputs.rclone._rclone_copy"), \
         patch("app.outputs.rclone._rclone_link", link):
        result = await deliver_rclone("/tmp/a.pdf", "doc")
    link.assert_called_once_with("remote:scanner/doc.pdf", 7)
    assert result["rclone"]["link"] == "https://1drv.ms/b/x"
    expires = datetime.fromisoformat(result["rclone"]["link_expires"])
    assert timedelta(days=6, hours=23) < expires - datetime.now().astimezone() <= timedelta(days=7)


@pytest.mark.asyncio
async def test_deliver_rclone_without_link_when_link_creation_fails():
    with patch("app.outputs.rclone.get_settings", return_value=_link_settings()), \
         patch("app.outputs.rclone._rclone_copy"), \
         patch("app.outputs.rclone._rclone_link", return_value=None):
        result = await deliver_rclone("/tmp/a.pdf", "doc")
    assert result == {"rclone": {"status": "ok", "dest": "remote:scanner/doc.pdf"}}


@pytest.mark.asyncio
async def test_deliver_rclone_creates_no_link_when_disabled():
    link = MagicMock()
    with patch("app.outputs.rclone.get_settings", return_value=_link_settings(rclone_mail_link=False)), \
         patch("app.outputs.rclone._rclone_copy"), \
         patch("app.outputs.rclone._rclone_link", link):
        result = await deliver_rclone("/tmp/a.pdf", "doc")
    link.assert_not_called()
    assert "link" not in result["rclone"]
