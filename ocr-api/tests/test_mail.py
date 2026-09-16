from unittest.mock import MagicMock, patch

import pytest

from app.config import Settings
from app.outputs.mail import deliver_mail


async def _sent_body(tmp_path, **kwargs):
    txt = tmp_path / "out.txt"
    txt.write_text("Rechnung 42")
    send = MagicMock()
    with patch("app.outputs.mail.get_settings", return_value=Settings(api_key="k", mail_to="me@example.com")), \
         patch("app.outputs.mail._send_mail", send):
        await deliver_mail("/tmp/out.pdf", "doc", str(txt), **kwargs)
    return send.call_args.args[2]


@pytest.mark.asyncio
async def test_mail_contains_onedrive_link_and_expiry(tmp_path):
    body = await _sent_body(tmp_path, onedrive_link="https://1drv.ms/b/x",
                            link_expires="2026-10-16T20:00:00+02:00")
    assert '<a href="https://1drv.ms/b/x">https://1drv.ms/b/x</a>' in body
    assert "2026-10-16" in body


@pytest.mark.asyncio
async def test_mail_without_link_has_no_onedrive_section(tmp_path):
    body = await _sent_body(tmp_path)
    assert "OneDrive" not in body
