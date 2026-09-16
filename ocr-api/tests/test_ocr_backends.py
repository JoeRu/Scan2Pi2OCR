import pytest
from pathlib import Path
from typing import Protocol, runtime_checkable
from PIL import Image as PILImage
from app.ocr_backends.base import OcrBackend
from app.ocr_backends import get_backend
from app.ocr_backends.tesseract import TesseractBackend


def _make_tif(path: Path) -> None:
    PILImage.new("RGB", (10, 10), color=(255, 255, 255)).save(str(path), format="TIFF")


def test_ocr_backend_protocol_is_checkable():
    @runtime_checkable
    class _P(OcrBackend, Protocol): ...  # noqa: E701 — satisfies Protocol

    class Good:
        def run(self, pages: list[Path], language: str) -> str:
            return ""

    class Bad:
        pass

    assert isinstance(Good(), _P)
    assert not isinstance(Bad(), _P)


from pathlib import Path as _Path
from app.ocr_backends.build_pdf import build_searchable_pdf
from app.ocr_backends.types import OcrLine, OcrPage


def _make_minimal_tif(path: _Path) -> None:
    """Write a 10×10 white TIFF at 300 DPI so Pillow can open it."""
    from PIL import Image
    img = Image.new("RGB", (10, 10), (255, 255, 255))
    img.save(str(path), dpi=(300, 300))


def _make_realistic_tif(path: _Path) -> None:
    """248×350 px at 300 DPI ≈ 21×29.7 mm (A4 proxy)."""
    from PIL import Image
    img = Image.new("RGB", (248, 350), (255, 255, 255))
    img.save(str(path), dpi=(300, 300))


def _page(*lines):
    return OcrPage([OcrLine(t, x0, y0, x1, y1) for (t, x0, y0, x1, y1) in lines])


def test_build_searchable_pdf_creates_file(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_minimal_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], [_page(("hello world", 1, 1, 8, 4))], out)
    assert out.exists() and out.stat().st_size > 0


def test_build_searchable_pdf_text_in_content(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_minimal_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], [_page(("searchable", 1, 1, 8, 4))], out)
    assert "searchable" in _extract_pdf_text(out)


def test_build_searchable_pdf_text_on_every_page(tmp_path):
    pages = []
    for i in range(3):
        p = tmp_path / f"scan_{i:04d}.pnm.tif"
        _make_realistic_tif(p)
        pages.append(p)
    ocr = [_page((f"pagetext{i}", 10, 10, 120, 30)) for i in range(3)]
    out = tmp_path / "out.pdf"
    build_searchable_pdf(pages, ocr, out)
    text = _extract_pdf_text(out)
    for i in range(3):
        assert f"pagetext{i}" in text                # text present on each page
    content = out.read_bytes().decode("latin-1")
    assert content.count("/Page\n") >= 3 or "/Count 3" in content


def test_build_searchable_pdf_empty_page_ok(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_minimal_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], [OcrPage([])], out)   # no lines -> image-only page
    assert out.exists() and out.stat().st_size > 0


def _extract_pdf_text(path) -> str:
    from pypdf import PdfReader
    return "\n".join(p.extract_text() for p in PdfReader(str(path)).pages)


def test_build_searchable_pdf_handles_non_latin1_text(tmp_path):
    # Bullet, euro, en-dash: all outside latin-1. Core fonts raise on these;
    # the text layer must embed a Unicode font so real scans don't crash.
    page = tmp_path / "scan_0001.pnm.tif"
    _make_realistic_tif(page)
    out = tmp_path / "out.pdf"
    build_searchable_pdf([page], [_page(("• Gesamt: 1.201,90 € – ok", 10, 10, 200, 30))], out)
    text = _extract_pdf_text(out)
    assert "€" in text and "•" in text


def test_build_searchable_pdf_falls_back_without_unicode_font(tmp_path):
    # If no Unicode font is available, degrade to latin-1 (lossy) instead of crashing.
    page = tmp_path / "scan_0001.pnm.tif"
    _make_realistic_tif(page)
    out = tmp_path / "out.pdf"
    with patch("app.ocr_backends.build_pdf._find_unicode_font", return_value=None):
        build_searchable_pdf([page], [_page(("Gesamt 1201 EUR • x", 10, 10, 200, 30))], out)
    assert out.exists() and out.stat().st_size > 0
    assert "Gesamt 1201 EUR" in _extract_pdf_text(out)


from unittest.mock import patch, MagicMock
from app.ocr_backends.tesseract import TesseractBackend
from app.config import Settings


_TSV_HEADER = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"


def _tsv_row(level, page, block, par, line, word, left, top, width, height, conf, text):
    return "\t".join(str(v) for v in
                     [level, page, block, par, line, word, left, top, width, height, conf, text])


def test_tesseract_run_returns_pages_with_lines(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    tsv = "\n".join([
        _TSV_HEADER,
        _tsv_row(5, 1, 1, 1, 1, 1, 10, 20, 40, 15, 96, "Hallo"),
        _tsv_row(5, 1, 1, 1, 1, 2, 55, 20, 30, 15, 95, "Welt"),
        _tsv_row(5, 1, 1, 1, 2, 1, 10, 50, 60, 15, 90, "Zeile2"),
    ])

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.tsv").write_text(tsv)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        pages = TesseractBackend().run([page], "deu+eng")

    assert len(pages) == 1
    assert [l.text for l in pages[0].lines] == ["Hallo Welt", "Zeile2"]
    line0 = pages[0].lines[0]
    # union of the two words' boxes: x0=10, y0=20, x1=55+30=85, y1=20+15=35
    assert (line0.x0, line0.y0, line0.x1, line0.y1) == (10, 20, 85, 35)


def test_tesseract_skips_low_conf_and_empty(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    tsv = "\n".join([
        _TSV_HEADER,
        _tsv_row(5, 1, 1, 1, 1, 1, 10, 20, 40, 15, -1, "ghost"),   # conf -1 dropped
        _tsv_row(5, 1, 1, 1, 1, 2, 60, 20, 40, 15, 90, "  "),       # empty dropped
        _tsv_row(4, 1, 1, 1, 1, 0, 0, 0, 0, 0, -1, ""),             # non-word level dropped
        _tsv_row(5, 1, 1, 1, 1, 3, 110, 20, 40, 15, 88, "keep"),
    ])

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.tsv").write_text(tsv)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        pages = TesseractBackend().run([page], "deu")

    assert [l.text for l in pages[0].lines] == ["keep"]


def test_tesseract_returns_one_page_per_input_even_if_blank(tmp_path):
    p1 = tmp_path / "scan_0001.pnm.tif"; p1.touch()
    p2 = tmp_path / "scan_0002.pnm.tif"; p2.touch()
    tsv = "\n".join([_TSV_HEADER, _tsv_row(5, 1, 1, 1, 1, 1, 10, 20, 40, 15, 96, "only")])

    def fake_run(cmd, capture_output, text, cwd):
        (Path(cwd) / "_ocr_out.tsv").write_text(tsv)
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("app.ocr_backends.tesseract.subprocess.run", side_effect=fake_run):
        pages = TesseractBackend().run([p1, p2], "deu")

    assert len(pages) == 2
    assert [l.text for l in pages[0].lines] == ["only"]
    assert pages[1].lines == []


def test_tesseract_run_raises_on_empty_pages():
    with pytest.raises(ValueError, match="No pages"):
        TesseractBackend().run([], "deu")


def test_tesseract_run_raises_on_subprocess_failure(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    with patch("app.ocr_backends.tesseract.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="", stderr="tesseract failed")):
        with pytest.raises(RuntimeError, match="Tesseract failed"):
            TesseractBackend().run([page], "deu")


def test_get_backend_tesseract():
    backend = get_backend("tesseract")
    assert isinstance(backend, TesseractBackend)


def test_get_backend_unknown_raises():
    with pytest.raises(ValueError, match="Unknown OCR engine"):
        get_backend("nonexistent")


def test_get_backend_paddleocr_returns_backend():
    from app.ocr_backends.paddleocr import PaddleOcrBackend
    backend = get_backend("paddleocr")
    assert isinstance(backend, PaddleOcrBackend)


def test_get_backend_gcv_returns_backend():
    from app.ocr_backends.gcv import GoogleCloudVisionBackend
    backend = get_backend("gcv")
    assert isinstance(backend, GoogleCloudVisionBackend)


from app.ocr_backends.paddleocr import PaddleOcrBackend


def test_paddleocr_run_returns_pages_with_boxes(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)
    fake_result = [{
        "rec_texts": ["Hello World", "Second line"],
        "rec_boxes": [[10, 20, 100, 40], [10, 50, 120, 70]],
        "rec_scores": [0.99, 0.95],
    }]
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR, \
         patch("app.ocr_backends.paddleocr.get_settings", return_value=Settings(api_key="test")):
        inst = MagicMock()
        inst.predict.return_value = fake_result
        MockOCR.return_value = inst
        pages = PaddleOcrBackend().run([page], "deu+eng")

    assert len(pages) == 1
    assert [l.text for l in pages[0].lines] == ["Hello World", "Second line"]
    first = pages[0].lines[0]
    assert (first.x0, first.y0, first.x1, first.y1) == (10, 20, 100, 40)


def test_paddleocr_run_handles_empty_result(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR, \
         patch("app.ocr_backends.paddleocr.get_settings", return_value=Settings(api_key="test")):
        inst = MagicMock()
        inst.predict.return_value = [{"rec_texts": [], "rec_boxes": None, "rec_scores": []}]
        MockOCR.return_value = inst
        pages = PaddleOcrBackend().run([page], "deu")

    assert len(pages) == 1
    assert pages[0].lines == []


def test_paddleocr_run_passes_det_model_name(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)
    fake_result = [{"rec_texts": ["x"], "rec_boxes": [[0, 0, 5, 5]], "rec_scores": [0.9]}]
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR, \
         patch("app.ocr_backends.paddleocr.get_settings", return_value=Settings(api_key="test")):
        inst = MagicMock()
        inst.predict.return_value = fake_result
        MockOCR.return_value = inst
        PaddleOcrBackend().run([page], "deu")

    _, kwargs = MockOCR.call_args
    # lightweight mobile detector baked into the image; server recognition untouched
    assert kwargs["text_detection_model_name"] == "PP-OCRv5_mobile_det"
    assert "text_recognition_model_name" not in kwargs


def test_config_default_paddle_det_model_is_mobile():
    assert Settings(api_key="test").paddle_text_det_model == "PP-OCRv5_mobile_det"


def test_paddleocr_run_passes_det_limit_kwargs(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_tif(page)
    fake_result = [{"rec_texts": ["x"], "rec_boxes": [[0, 0, 5, 5]], "rec_scores": [0.9]}]
    with patch("app.ocr_backends.paddleocr.PaddleOCR") as MockOCR, \
         patch("app.ocr_backends.paddleocr.get_settings", return_value=Settings(api_key="test")):
        inst = MagicMock()
        inst.predict.return_value = fake_result
        MockOCR.return_value = inst
        PaddleOcrBackend().run([page], "deu")

    _, kwargs = inst.predict.call_args
    assert kwargs["text_det_limit_type"] == "max"
    assert kwargs["text_det_limit_side_len"] == 1600


def test_paddleocr_language_mapping():
    backend = PaddleOcrBackend()
    assert backend._map_language("deu+eng+frk") == "german"
    assert backend._map_language("eng") == "en"
    assert backend._map_language("deu") == "german"
    assert backend._map_language("frk") == "german"
    assert backend._map_language("unknown") == "en"


def test_paddleocr_run_raises_on_empty_pages():
    with pytest.raises(ValueError, match="No pages"):
        PaddleOcrBackend().run([], "deu")


from app.ocr_backends.gcv import GoogleCloudVisionBackend


def test_gcv_stub_raises_not_implemented(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    page.touch()
    with pytest.raises(NotImplementedError, match="not yet implemented"):
        GoogleCloudVisionBackend().run([page], "deu")


def test_gcv_stub_raises_on_empty_pages():
    with pytest.raises((ValueError, NotImplementedError)):
        GoogleCloudVisionBackend().run([], "deu")


from app.ocr_backends.types import OcrLine, OcrPage


def test_ocrpage_text_joins_lines_with_newline():
    page = OcrPage([OcrLine("first", 0, 0, 10, 5), OcrLine("second", 0, 6, 10, 11)])
    assert page.text == "first\nsecond"


def test_ocrpage_empty_text_is_empty_string():
    assert OcrPage([]).text == ""


# ---------------------------------------------------------------------------
# OcrPage transcript + LLM OCR settings
# ---------------------------------------------------------------------------

def test_ocr_page_text_prefers_transcript():
    page = OcrPage([OcrLine("tesseract", 0, 0, 1, 1)], transcript="llm text")
    assert page.text == "llm text"


def test_ocr_page_text_falls_back_to_lines():
    page = OcrPage([OcrLine("a", 0, 0, 1, 1), OcrLine("b", 0, 0, 1, 1)])
    assert page.text == "a\nb"
    assert page.transcript is None
    assert page.transcript_model is None
    assert page.escalated is False


def test_config_accepts_openrouter_engine():
    assert Settings(api_key="test", ocr_engine="openrouter").ocr_engine == "openrouter"


def test_config_ocr_llm_defaults():
    s = Settings(api_key="test")
    assert s.ocr_llm_model == "google/gemini-3.1-flash-lite"
    assert s.ocr_llm_strong_model == "google/gemini-3.5-flash"
    assert s.ocr_llm_fallback_models == []
    assert s.ocr_llm_concurrency == 3
    assert s.ocr_llm_timeout == 90
    assert s.ocr_llm_max_tokens == 4000
    assert s.ocr_llm_image_max_side == 2000
    assert s.ocr_llm_escalate_unclear_max == 2


def test_config_fallback_models_from_env_json(monkeypatch):
    monkeypatch.setenv("OCR_LLM_FALLBACK_MODELS", '["openai/gpt-5-mini", "anthropic/claude-haiku-4.5"]')
    assert Settings(api_key="test").ocr_llm_fallback_models == [
        "openai/gpt-5-mini", "anthropic/claude-haiku-4.5",
    ]


# ---------------------------------------------------------------------------
# OpenRouter LLM OCR helpers
# ---------------------------------------------------------------------------
import base64
import io
import json as _json
from types import SimpleNamespace

from app.ocr_backends import openrouter as orb
from app.ocr_backends.openrouter import (
    LlmReply,
    build_request,
    call_llm,
    escalation_reason,
    parse_reply,
    prepare_image,
)


def _llm_settings(**kwargs):
    base = dict(api_key="test", openrouter_api_key="sk-test")
    base.update(kwargs)
    return Settings(**base)


def _llm_result(text="Hallo Welt", handwriting=False, uncertain=False,
                model="google/gemini-3.1-flash-lite", finish_reason="stop",
                cost=0.001, content=None):
    """Fake openrouter ChatResult (only the attributes call_llm reads)."""
    if content is None:
        content = _json.dumps({"text": text, "handwriting": handwriting, "uncertain": uncertain})
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(finish_reason=finish_reason,
                                 message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=1500, completion_tokens=40, cost=cost),
    )


def _reply(**kwargs):
    base = dict(text="t", handwriting=False, uncertain=False, model="m", finish_reason="stop",
                prompt_tokens=0, completion_tokens=0, cost=0.0, latency_s=0.0)
    base.update(kwargs)
    return LlmReply(**base)


def test_prepare_image_grayscale_jpeg_downscaled(tmp_path):
    src = tmp_path / "scan_0001.pnm.tif"
    PILImage.new("RGB", (3000, 1500), (200, 10, 10)).save(str(src), format="TIFF")
    with PILImage.open(io.BytesIO(prepare_image(src, 2000))) as img:
        assert img.format == "JPEG"
        assert img.mode == "L"
        assert img.size == (2000, 1000)


def test_prepare_image_never_upscales(tmp_path):
    src = tmp_path / "small.tif"
    PILImage.new("RGB", (800, 600), (255, 255, 255)).save(str(src), format="TIFF")
    with PILImage.open(io.BytesIO(prepare_image(src, 2000))) as img:
        assert img.size == (800, 600)


def test_parse_reply_plain_json():
    assert parse_reply('{"text": "Hallo", "handwriting": true, "uncertain": false}') == ("Hallo", True, False)


def test_parse_reply_fenced_json():
    content = '```json\n{"text": "Hallo", "handwriting": false, "uncertain": true}\n```'
    assert parse_reply(content) == ("Hallo", False, True)


def test_parse_reply_non_json_returns_raw_text():
    assert parse_reply("Sehr geehrte Damen und Herren") == ("Sehr geehrte Damen und Herren", False, False)


def test_parse_reply_empty():
    assert parse_reply("") == ("", False, False)


def test_build_request_shape():
    image = b"\xff\xd8img"
    req = build_request(image, "google/gemini-3.1-flash-lite", 4000, ["openai/gpt-5-mini"])
    assert req["model"] == "google/gemini-3.1-flash-lite"
    assert req["models"] == ["google/gemini-3.1-flash-lite", "openai/gpt-5-mini"]
    assert req["provider"] == {"data_collection": "deny"}
    assert req["temperature"] == 0
    assert req["max_tokens"] == 4000
    assert req["response_format"] == {"type": "json_object"}
    parts = req["messages"][0]["content"]
    assert req["messages"][0]["role"] == "user"
    assert parts[0] == {"type": "text", "text": orb.PROMPT}
    assert parts[1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(image).decode()},
    }


def test_build_request_without_fallbacks_omits_models():
    assert "models" not in build_request(b"x", "m", 10, [])


def test_call_llm_parses_result():
    client = MagicMock()
    client.chat.send.return_value = _llm_result(text="Hallo", handwriting=True, cost=0.002)
    reply = call_llm(client, b"img", "google/gemini-3.1-flash-lite", 4000, _llm_settings())
    assert reply.text == "Hallo"
    assert reply.handwriting is True
    assert reply.uncertain is False
    assert reply.model == "google/gemini-3.1-flash-lite"
    assert reply.finish_reason == "stop"
    assert (reply.prompt_tokens, reply.completion_tokens, reply.cost) == (1500, 40, 0.002)
    assert client.chat.send.call_args.kwargs["timeout_ms"] == 90_000


def test_call_llm_uses_requested_model_when_result_has_none():
    client = MagicMock()
    client.chat.send.return_value = _llm_result(model=None)
    assert call_llm(client, b"img", "x/requested", 100, _llm_settings()).model == "x/requested"


def test_call_llm_retries_once_then_succeeds():
    client = MagicMock()
    client.chat.send.side_effect = [ConnectionError("boom"), _llm_result(text="ok")]
    with patch("app.ocr_backends.openrouter.time.sleep") as sleep:
        reply = call_llm(client, b"img", "m", 100, _llm_settings())
    assert reply.text == "ok"
    assert client.chat.send.call_count == 2
    sleep.assert_called_once_with(2.0)


def test_call_llm_raises_after_second_failure():
    client = MagicMock()
    client.chat.send.side_effect = ConnectionError("down")
    with patch("app.ocr_backends.openrouter.time.sleep"):
        with pytest.raises(ConnectionError):
            call_llm(client, b"img", "m", 100, _llm_settings())
    assert client.chat.send.call_count == 2


def test_call_llm_empty_text_raises_without_retry():
    client = MagicMock()
    client.chat.send.return_value = _llm_result(text="")
    with pytest.raises(ValueError, match="empty"):
        call_llm(client, b"img", "m", 100, _llm_settings())
    assert client.chat.send.call_count == 1


@pytest.mark.parametrize("kwargs,expected", [
    ({}, None),
    ({"handwriting": True}, "handwriting"),
    ({"uncertain": True}, "uncertain"),
    ({"text": "a [?] b [?] c [?]"}, "unclear"),
    ({"text": "a [?] b [?]"}, None),
    ({"finish_reason": "length"}, "truncated"),
])
def test_escalation_reason(kwargs, expected):
    assert escalation_reason(_reply(**kwargs), unclear_max=2) == expected


def test_call_llm_against_real_sdk_request_shape():
    """Contract test: the real openrouter SDK serializes our request and parses a reply."""
    openrouter = pytest.importorskip("openrouter")
    import httpx

    captured = {}

    def handler(request):
        captured["body"] = _json.loads(request.content)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={
            "id": "gen-1", "created": 1, "object": "chat.completion", "system_fingerprint": None,
            "model": "google/gemini-3.1-flash-lite",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant",
                "content": _json.dumps({"text": "Hallo", "handwriting": True, "uncertain": False}),
            }}],
            "usage": {"prompt_tokens": 1500, "completion_tokens": 12, "total_tokens": 1512, "cost": 0.0004},
        })

    client = openrouter.OpenRouter(api_key="sk-test",
                                   client=httpx.Client(transport=httpx.MockTransport(handler)))
    settings = _llm_settings(ocr_llm_fallback_models=["openai/gpt-5-mini"])
    reply = call_llm(client, b"img", "google/gemini-3.1-flash-lite", 4000, settings)

    body = captured["body"]
    assert captured["auth"] == "Bearer sk-test"
    assert body["provider"] == {"data_collection": "deny"}
    assert body["models"] == ["google/gemini-3.1-flash-lite", "openai/gpt-5-mini"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["content"][1]["type"] == "image_url"
    assert (reply.text, reply.handwriting, reply.cost) == ("Hallo", True, 0.0004)
