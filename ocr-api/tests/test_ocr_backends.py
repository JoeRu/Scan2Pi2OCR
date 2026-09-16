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
    assert s.ocr_llm_strong_model == "google/gemini-3.1-pro-preview"
    assert s.ocr_llm_fallback_models == []
    assert s.ocr_llm_concurrency == 3
    assert s.ocr_llm_timeout == 90
    assert s.ocr_llm_max_tokens == 8000
    assert s.ocr_llm_image_max_side == 2000
    assert s.ocr_llm_escalate_unclear_max == 2
    assert s.ocr_llm_reasoning_effort == ""
    assert s.ocr_llm_strong_reasoning_effort == ""


def test_config_rejects_unknown_reasoning_effort():
    with pytest.raises(ValueError):
        Settings(api_key="test", ocr_llm_strong_reasoning_effort="turbo")


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


# M1: only JSON `true` / case-insensitive string "true" count as True.
def test_parse_reply_string_false_is_not_truthy():
    content = '{"text": "Hallo", "handwriting": "false", "uncertain": "false"}'
    assert parse_reply(content) == ("Hallo", False, False)


def test_parse_reply_string_true_is_truthy():
    content = '{"text": "Hallo", "handwriting": "True", "uncertain": "TRUE"}'
    assert parse_reply(content) == ("Hallo", True, True)


# M2: a JSON value that parses but isn't an object (e.g. a bare string) should
# use that string as the text, not the raw content (incl. quotes).
def test_parse_reply_json_string_uses_value_as_text():
    assert parse_reply('"Hallo"') == ("Hallo", False, False)


def test_parse_reply_json_number_keeps_raw_behavior():
    assert parse_reply("42") == ("42", False, False)


def test_parse_reply_json_list_keeps_raw_behavior():
    assert parse_reply('["a", "b"]') == ('["a", "b"]', False, False)


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


def test_build_request_omits_reasoning_by_default():
    assert "reasoning" not in build_request(b"x", "m", 10, [])


def test_build_request_sets_reasoning_effort():
    req = build_request(b"x", "m", 10, [], reasoning_effort="low")
    assert req["reasoning"] == {"effort": "low"}


def test_prompt_asks_to_mark_crossed_out_text():
    assert "[crossed out:" in orb.PROMPT


def test_call_llm_passes_reasoning_effort():
    client = MagicMock()
    client.chat.send.return_value = _llm_result()
    call_llm(client, b"img", "m", 100, _llm_settings(), reasoning_effort="medium")
    assert client.chat.send.call_args.kwargs["reasoning"] == {"effort": "medium"}


def test_log_reply_includes_finish_reason(caplog):
    with caplog.at_level("INFO", logger="app.ocr_backends.openrouter"):
        orb._log_reply(2, _reply(model="google/gemini-3.1-pro-preview", finish_reason="stop"), None)
    assert "fin=stop" in caplog.text


def test_log_reply_warns_on_truncation(caplog):
    with caplog.at_level("INFO", logger="app.ocr_backends.openrouter"):
        orb._log_reply(3, _reply(finish_reason="length"), None)
    assert any(r.levelname == "WARNING" and "fin=length" in r.getMessage() for r in caplog.records)


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


# ---------------------------------------------------------------------------
# I1: recover the "text" value out of JSON truncated by finish_reason=="length"
# ---------------------------------------------------------------------------
from app.ocr_backends.openrouter import recover_truncated_text


def test_recover_truncated_text_simple():
    content = '{"text": "Sehr geehrte Damen und Herren'
    assert recover_truncated_text(content) == "Sehr geehrte Damen und Herren"


def test_recover_truncated_text_decodes_escape_sequences():
    content = '{"text": "Sehr geehrte\\nDamen, Gr\\u00fc\\u00dfe'
    assert recover_truncated_text(content) == "Sehr geehrte\nDamen, Grüße"


def test_recover_truncated_text_handles_dangling_trailing_backslash():
    content = '{"text": "Sehr geehrte\\'
    assert recover_truncated_text(content) == "Sehr geehrte"


def test_recover_truncated_text_handles_incomplete_unicode_escape():
    content = '{"text": "Gr\\u00'
    assert recover_truncated_text(content) == "Gr"


def test_recover_truncated_text_unrecoverable_returns_none():
    assert recover_truncated_text("not json at all, no text field") is None


def test_recover_truncated_text_empty_recovered_text_returns_none():
    assert recover_truncated_text('{"text": "') is None


def test_call_llm_truncated_recoverable_json_uses_recovered_text():
    client = MagicMock()
    content = '{"text": "Sehr geehrte Damen und Herren'  # cut off mid-string
    client.chat.send.return_value = _llm_result(content=content, finish_reason="length")
    reply = call_llm(client, b"img", "m", 100, _llm_settings())
    assert reply.text == "Sehr geehrte Damen und Herren"
    assert reply.handwriting is False
    assert reply.uncertain is False
    assert reply.finish_reason == "length"  # escalation still triggers on this


def test_call_llm_truncated_unrecoverable_json_raises():
    client = MagicMock()
    client.chat.send.return_value = _llm_result(content="garbled, no text field",
                                                 finish_reason="length")
    with pytest.raises(ValueError, match="truncated"):
        call_llm(client, b"img", "m", 100, _llm_settings())


def test_call_llm_non_length_non_json_still_uses_raw_text():
    """Complete (non-truncated) non-JSON replies keep the existing raw-text behavior."""
    client = MagicMock()
    client.chat.send.return_value = _llm_result(content="Sehr geehrte Damen und Herren",
                                                 finish_reason="stop")
    reply = call_llm(client, b"img", "m", 100, _llm_settings())
    assert reply.text == "Sehr geehrte Damen und Herren"


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


def test_client_passes_expected_kwargs():
    with patch("app.ocr_backends.openrouter.OpenRouter") as MockOpenRouter:
        MockOpenRouter.return_value = "the-client"
        result = orb._client(_llm_settings(openrouter_api_key="sk-abc"))
    assert result == "the-client"
    MockOpenRouter.assert_called_once_with(
        api_key="sk-abc",
        http_referer="https://github.com/Scan2Pi2OCR",
        retry_config=None,
    )


def test_client_raises_when_openrouter_package_missing():
    with patch("app.ocr_backends.openrouter.OpenRouter", None):
        with pytest.raises(RuntimeError, match="not installed"):
            orb._client(_llm_settings())


def test_client_disables_sdk_retry_so_call_llm_retries_only_once(monkeypatch):
    """C1 contract test: without retry_config=None, the real SDK's default backoff
    retries 5XX responses for up to an hour on its own, on top of call_llm's own
    2s-retry-once policy -> a scan can hang for hours during an outage.

    Builds the client the same way `_client()` does (same kwargs), but injects a
    MockTransport so no real network call happens. The handler is call-count
    limited so that even if the fix is missing, the test fails fast instead of
    actually waiting out the SDK's real backoff timers (a few real sub-second
    sleeps happen from the SDK's own un-patched backoff, capped deliberately low).
    """
    openrouter_sdk = pytest.importorskip("openrouter")
    import httpx

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] > 2:
            raise AssertionError("handler called more than twice; SDK retry not disabled")
        return httpx.Response(503, json={"error": {"message": "unavailable"}})

    class _ForwardingOpenRouter:
        """Stands in for `app.ocr_backends.openrouter.OpenRouter`, forwarding every
        kwarg `_client()` passes (so this test exercises `_client`'s real kwargs)
        plus a mocked transport so no real network call happens."""

        def __init__(self, **kwargs):
            kwargs["client"] = httpx.Client(transport=httpx.MockTransport(handler))
            self._inner = openrouter_sdk.OpenRouter(**kwargs)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    monkeypatch.setattr(orb, "OpenRouter", _ForwardingOpenRouter)
    client = orb._client(_llm_settings())

    with patch("app.ocr_backends.openrouter.time.sleep") as sleep:
        with pytest.raises(Exception):
            call_llm(client, b"img", "google/gemini-3.1-flash-lite", 4000, _llm_settings())

    assert calls["n"] == 2
    sleep.assert_called_once_with(2.0)


# ---------------------------------------------------------------------------
# OpenRouterBackend
# ---------------------------------------------------------------------------
from time import sleep as _real_sleep

from app.ocr_backends.openrouter import OpenRouterBackend


def _page_name(send_kwargs):
    """prepare_image is patched to return the page file name as bytes; decode it back."""
    url = send_kwargs["messages"][0]["content"][1]["image_url"]["url"]
    return base64.b64decode(url.split(",", 1)[1]).decode()


def _run_backend(tmp_path, settings, send, n_pages=1, client_error=None):
    pages = [tmp_path / f"scan_{i:04d}.pnm.tif" for i in range(1, n_pages + 1)]
    tesseract_pages = [OcrPage([OcrLine(f"tess {i}", 0, 0, 10, 5)]) for i in range(1, n_pages + 1)]
    client = MagicMock()
    client.chat.send.side_effect = send
    client_patch = (patch("app.ocr_backends.openrouter._client", side_effect=client_error)
                    if client_error else
                    patch("app.ocr_backends.openrouter._client", return_value=client))
    with patch("app.ocr_backends.openrouter.get_settings", return_value=settings), \
         patch("app.ocr_backends.openrouter.TesseractBackend") as MockTess, \
         patch("app.ocr_backends.openrouter.prepare_image",
               side_effect=lambda path, max_side: path.name.encode()), \
         patch("app.ocr_backends.openrouter._image_size", return_value=(2000, 3000)), \
         client_patch as mock_client, \
         patch("app.ocr_backends.openrouter.time.sleep"):
        MockTess.return_value.run.return_value = tesseract_pages
        result = OpenRouterBackend().run(pages, "deu+eng")
        MockTess.return_value.run.assert_called_once_with(pages, "deu+eng")
    return result, client, mock_client


def test_openrouter_run_sets_transcript_and_keeps_lines(tmp_path):
    result, client, _ = _run_backend(tmp_path, _llm_settings(), [_llm_result(text="LLM Text")])
    assert len(result) == 1
    page = result[0]
    assert [l.text for l in page.lines] == ["tess 1"]
    assert page.transcript == "LLM Text"
    assert page.text == "LLM Text"
    assert page.transcript_model == "google/gemini-3.1-flash-lite"
    assert page.escalated is False
    assert client.chat.send.call_count == 1
    assert client.chat.send.call_args.kwargs["model"] == "google/gemini-3.1-flash-lite"


def test_openrouter_run_escalates_handwriting_to_strong_model(tmp_path):
    send = [
        _llm_result(text="cheap", handwriting=True),
        _llm_result(text="strong", model="google/gemini-3.1-pro-preview"),
    ]
    result, client, _ = _run_backend(tmp_path, _llm_settings(), send)
    page = result[0]
    assert page.transcript == "strong"
    assert page.transcript_model == "google/gemini-3.1-pro-preview"
    assert page.escalated is True
    second = client.chat.send.call_args_list[1].kwargs
    assert second["model"] == "google/gemini-3.1-pro-preview"
    assert second["max_tokens"] == 16000


def test_openrouter_run_uses_per_tier_reasoning_effort(tmp_path):
    settings = _llm_settings(ocr_llm_reasoning_effort="minimal", ocr_llm_strong_reasoning_effort="high")
    send = [
        _llm_result(text="cheap", handwriting=True),
        _llm_result(text="strong", model="google/gemini-3.1-pro-preview"),
    ]
    _, client, _ = _run_backend(tmp_path, settings, send)
    first, second = (c.kwargs for c in client.chat.send.call_args_list)
    assert first["reasoning"] == {"effort": "minimal"}
    assert second["reasoning"] == {"effort": "high"}


def test_openrouter_run_default_cheap_call_sends_no_reasoning(tmp_path):
    _, client, _ = _run_backend(tmp_path, _llm_settings(), [_llm_result()])
    assert "reasoning" not in client.chat.send.call_args.kwargs


@pytest.mark.parametrize("first", [
    {"uncertain": True},
    {"text": "a [?] b [?] c [?]"},
    {"finish_reason": "length"},
])
def test_openrouter_run_escalates_on_other_reasons(tmp_path, first):
    send = [_llm_result(**first), _llm_result(text="strong", model="google/gemini-3.1-pro-preview")]
    result, client, _ = _run_backend(tmp_path, _llm_settings(), send)
    assert client.chat.send.call_count == 2
    assert result[0].escalated is True


@pytest.mark.parametrize("strong", ["", "google/gemini-3.1-flash-lite"])
def test_openrouter_run_no_escalation_without_distinct_strong_model(tmp_path, strong):
    settings = _llm_settings(ocr_llm_strong_model=strong)
    result, client, _ = _run_backend(tmp_path, settings, [_llm_result(text="cheap", handwriting=True)])
    assert client.chat.send.call_count == 1
    assert result[0].transcript == "cheap"
    assert result[0].escalated is False


def test_openrouter_run_strong_failure_keeps_cheap_result(tmp_path):
    send = [_llm_result(text="cheap", handwriting=True), RuntimeError("no route"), RuntimeError("no route")]
    result, client, _ = _run_backend(tmp_path, _llm_settings(), send)
    assert client.chat.send.call_count == 3
    assert result[0].transcript == "cheap"
    assert result[0].transcript_model == "google/gemini-3.1-flash-lite"
    assert result[0].escalated is False


def test_openrouter_run_llm_failure_keeps_tesseract_text(tmp_path):
    result, _, _ = _run_backend(tmp_path, _llm_settings(), ConnectionError("down"))
    assert result[0].transcript is None
    assert result[0].transcript_model is None
    assert result[0].text == "tess 1"


def test_openrouter_run_empty_reply_keeps_tesseract_text(tmp_path):
    result, client, _ = _run_backend(tmp_path, _llm_settings(), [_llm_result(text="")])
    assert client.chat.send.call_count == 1
    assert result[0].transcript is None
    assert result[0].text == "tess 1"


def test_openrouter_run_invalid_json_uses_raw_text_without_escalation(tmp_path):
    result, client, _ = _run_backend(tmp_path, _llm_settings(), [_llm_result(content="Rohtext ohne JSON")])
    assert client.chat.send.call_count == 1
    assert result[0].transcript == "Rohtext ohne JSON"
    assert result[0].escalated is False


def test_openrouter_run_closes_client_after_use(tmp_path):
    """M4: the client is used as a context manager (closed after the pool
    finishes). `_transcribe`'s calls must land on the same object `_client()`
    returned, not on `__enter__`'s return value -- a MagicMock's `__enter__`
    auto-generates a *different* MagicMock unless configured, which would
    silently stop `client.chat.send` from being observed."""
    result, client, _ = _run_backend(tmp_path, _llm_settings(), [_llm_result(text="LLM Text")])
    client.__enter__.assert_called_once()
    client.__exit__.assert_called_once()
    assert client.chat.send.call_count == 1
    assert result[0].transcript == "LLM Text"


def test_openrouter_run_truncated_recoverable_cheap_escalates(tmp_path):
    """I1: cheap reply cut off by finish_reason=="length" but its "text" value is
    still recoverable -> that recovered text is used and escalation still fires."""
    send = [
        _llm_result(content='{"text": "cheap text cut off', finish_reason="length"),
        _llm_result(text="strong", model="google/gemini-3.1-pro-preview"),
    ]
    result, client, _ = _run_backend(tmp_path, _llm_settings(), send)
    assert client.chat.send.call_count == 2
    assert result[0].transcript == "strong"
    assert result[0].transcript_model == "google/gemini-3.1-pro-preview"
    assert result[0].escalated is True


def test_openrouter_run_truncated_unrecoverable_strong_keeps_cheap_result(tmp_path):
    """I1: if the strong model's reply is also truncated and unrecoverable, the
    cheap result is kept rather than storing garbage."""
    send = [
        _llm_result(text="cheap", handwriting=True),
        _llm_result(content="garbled, no text field", finish_reason="length"),
    ]
    result, client, _ = _run_backend(tmp_path, _llm_settings(), send)
    assert client.chat.send.call_count == 2
    assert result[0].transcript == "cheap"
    assert result[0].transcript_model == "google/gemini-3.1-flash-lite"
    assert result[0].escalated is False


def test_openrouter_run_missing_api_key_skips_llm(tmp_path):
    settings = _llm_settings(openrouter_api_key="")
    result, client, mock_client = _run_backend(tmp_path, settings, [_llm_result()])
    mock_client.assert_not_called()
    client.chat.send.assert_not_called()
    assert result[0].transcript is None


def test_openrouter_run_client_creation_failure_keeps_tesseract_text(tmp_path):
    result, _, _ = _run_backend(tmp_path, _llm_settings(), [_llm_result()],
                                client_error=RuntimeError("openrouter package is not installed"))
    assert result[0].transcript is None
    assert result[0].text == "tess 1"


def test_openrouter_run_preserves_page_order_with_concurrency(tmp_path):
    delays = {"scan_0001.pnm.tif": 0.2, "scan_0002.pnm.tif": 0.1, "scan_0003.pnm.tif": 0.0}

    def send(**kwargs):
        name = _page_name(kwargs)
        _real_sleep(delays[name])  # page 1 finishes last
        return _llm_result(text=f"llm {name}")

    settings = _llm_settings(ocr_llm_concurrency=3)
    result, _, _ = _run_backend(tmp_path, settings, send, n_pages=3)
    assert [p.transcript for p in result] == [
        "llm scan_0001.pnm.tif", "llm scan_0002.pnm.tif", "llm scan_0003.pnm.tif",
    ]


def test_get_backend_openrouter_returns_backend():
    assert isinstance(get_backend("openrouter"), OpenRouterBackend)


# ---------------------------------------------------------------------------
# LLM text in the PDF layer: OCR_LLM_PDF_TEXT = off | block | positioned
# ---------------------------------------------------------------------------
from app.ocr_backends.openrouter import LINES_PROMPT, call_llm_lines, parse_box_lines


def _lines_content(*entries):
    return _json.dumps({"lines": [
        {"text": t, "box_2d": b, "handwritten": hw} for (t, b, hw) in entries
    ]})


def _lines_result(*entries, model="google/gemini-3.1-pro-preview", content=None):
    return _llm_result(model=model, content=content if content is not None else _lines_content(*entries))


def test_config_pdf_text_default_off_and_validated():
    assert Settings(api_key="test").ocr_llm_pdf_text == "off"
    assert Settings(api_key="test", ocr_llm_pdf_text="positioned").ocr_llm_pdf_text == "positioned"
    with pytest.raises(ValueError):
        Settings(api_key="test", ocr_llm_pdf_text="everywhere")


def test_prompt_asks_for_markdown_tables():
    assert "Markdown" in orb.PROMPT


def test_parse_box_lines_converts_normalized_boxes_to_pixels():
    content = _lines_content(("Hallo", [100, 200, 150, 600], True))
    lines = parse_box_lines(content, width_px=2000, height_px=3000)
    assert lines == [OcrLine("Hallo", x0=400, y0=300, x1=1200, y1=450)]


def test_parse_box_lines_drops_invalid_boxes_when_80_percent_valid():
    content = _lines_content(
        ("a", [0, 0, 10, 10], False), ("b", [10, 0, 20, 10], False),
        ("c", [20, 0, 30, 10], False), ("d", [30, 0, 40, 10], False),
        ("bad", [50, 50, 40, 60], False),  # ymax < ymin
    )
    lines = parse_box_lines(content, width_px=1000, height_px=1000)
    assert [l.text for l in lines] == ["a", "b", "c", "d"]


@pytest.mark.parametrize("bad_box", [[0, 0, 10], [0, 0, 1001, 10], [0, 10, 5, 10], ["0", 0, 10, 10]])
def test_parse_box_lines_rejects_page_below_80_percent_valid(bad_box):
    content = _lines_content(
        ("a", [0, 0, 10, 10], False), ("b", [10, 0, 20, 10], False),
        ("c", [20, 0, 30, 10], False), ("bad", bad_box, False),
    )
    assert parse_box_lines(content, width_px=1000, height_px=1000) is None


@pytest.mark.parametrize("content", ["", "not json", '{"lines": []}', '{"text": "x"}',
                                     '{"lines": [{"text": "", "box_2d": [0, 0, 10, 10], "handwritten": false}]}'])
def test_parse_box_lines_returns_none_for_unusable_content(content):
    assert parse_box_lines(content, width_px=1000, height_px=1000) is None


def test_build_request_custom_prompt_and_response_format():
    fmt = {"type": "json_schema", "json_schema": {"name": "x"}}
    req = build_request(b"x", "m", 10, [], prompt=LINES_PROMPT, response_format=fmt)
    assert req["messages"][0]["content"][0]["text"] == LINES_PROMPT
    assert req["response_format"] == fmt


def test_call_llm_lines_returns_reply_and_pixel_lines():
    client = MagicMock()
    client.chat.send.return_value = _lines_result(
        ("Er hat 70 €", [100, 100, 120, 500], True), ("Viel Erfolg!", [500, 400, 520, 600], False))
    reply, lines = call_llm_lines(client, b"img", "google/gemini-3.1-pro-preview", 16000,
                                  _llm_settings(), "", width_px=1000, height_px=2000)
    sent = client.chat.send.call_args.kwargs
    assert sent["messages"][0]["content"][0]["text"] == LINES_PROMPT
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert reply.text == "Er hat 70 €\nViel Erfolg!"
    assert reply.handwriting is True
    assert lines[0] == OcrLine("Er hat 70 €", x0=100, y0=200, x1=500, y1=240)


def test_call_llm_lines_raises_on_unusable_boxes():
    client = MagicMock()
    client.chat.send.return_value = _lines_result(content="not json")
    with pytest.raises(ValueError, match=r"box.*fin=stop.*out_tokens=40"):
        call_llm_lines(client, b"img", "m", 100, _llm_settings(), "", width_px=10, height_px=10)
    assert client.chat.send.call_count == 1


def test_call_llm_lines_real_sdk_serializes_json_schema():
    openrouter = pytest.importorskip("openrouter")
    import httpx
    captured = {}

    def handler(request):
        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={
            "id": "gen-1", "created": 1, "object": "chat.completion", "system_fingerprint": None,
            "model": "google/gemini-3.1-pro-preview",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": _lines_content(("Hallo", [0, 0, 100, 100], True))}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2, "cost": 0.01},
        })

    client = openrouter.OpenRouter(api_key="sk-test", retry_config=None,
                                   client=httpx.Client(transport=httpx.MockTransport(handler)))
    reply, lines = call_llm_lines(client, b"img", "google/gemini-3.1-pro-preview", 16000,
                                  _llm_settings(), "", width_px=100, height_px=100)
    fmt = captured["body"]["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["schema"]["required"] == ["lines"]
    assert lines == [OcrLine("Hallo", 0, 0, 10, 10)]


def test_openrouter_run_positioned_replaces_tesseract_lines_on_escalated_page(tmp_path):
    settings = _llm_settings(ocr_llm_pdf_text="positioned")
    send = [
        _llm_result(text="cheap", handwriting=True),
        _lines_result(("S01", [10, 800, 30, 900], True), ("Aufgabe 1", [100, 100, 120, 400], False)),
    ]
    result, client, _ = _run_backend(tmp_path, settings, send)
    page = result[0]
    assert client.chat.send.call_count == 2
    assert client.chat.send.call_args_list[1].kwargs["response_format"]["type"] == "json_schema"
    assert page.pdf_text == "positioned"
    assert page.escalated is True
    assert page.transcript == "S01\nAufgabe 1"
    assert page.transcript_model == "google/gemini-3.1-pro-preview"
    # image patched to 2000x3000 px: [ymin, xmin, ymax, xmax] -> pixels
    assert page.lines == [OcrLine("S01", 1600, 30, 1800, 90), OcrLine("Aufgabe 1", 200, 300, 800, 360)]


def test_openrouter_run_positioned_falls_back_to_block_when_boxes_unusable(tmp_path):
    settings = _llm_settings(ocr_llm_pdf_text="positioned")
    send = [
        _llm_result(text="cheap", handwriting=True),
        _lines_result(content="garbage"),
        _llm_result(text="strong plain", model="google/gemini-3.1-pro-preview"),
    ]
    result, client, _ = _run_backend(tmp_path, settings, send)
    page = result[0]
    assert client.chat.send.call_count == 3
    assert page.pdf_text == "block"
    assert page.transcript == "strong plain"
    assert [l.text for l in page.lines] == ["tess 1"]


def test_openrouter_run_positioned_keeps_tesseract_on_non_escalated_page(tmp_path):
    settings = _llm_settings(ocr_llm_pdf_text="positioned")
    result, client, _ = _run_backend(tmp_path, settings, [_llm_result(text="printed")])
    assert client.chat.send.call_count == 1
    assert result[0].pdf_text == "tesseract"
    assert [l.text for l in result[0].lines] == ["tess 1"]


def test_openrouter_run_block_mode_marks_every_transcribed_page(tmp_path):
    settings = _llm_settings(ocr_llm_pdf_text="block")
    result, _, _ = _run_backend(tmp_path, settings, [_llm_result(text="printed")])
    assert result[0].pdf_text == "block"


def test_openrouter_run_off_mode_uses_plain_escalation(tmp_path):
    send = [_llm_result(text="cheap", handwriting=True),
            _llm_result(text="strong", model="google/gemini-3.1-pro-preview")]
    result, client, _ = _run_backend(tmp_path, _llm_settings(), send)
    assert client.chat.send.call_args_list[1].kwargs["response_format"] == {"type": "json_object"}
    assert result[0].pdf_text == "tesseract"


def test_openrouter_run_llm_failure_page_stays_tesseract_in_positioned_mode(tmp_path):
    settings = _llm_settings(ocr_llm_pdf_text="positioned")
    result, _, _ = _run_backend(tmp_path, settings, ConnectionError("down"))
    assert result[0].pdf_text == "tesseract"
    assert result[0].transcript is None


def test_log_reply_includes_pdf_text(caplog):
    with caplog.at_level("INFO", logger="app.ocr_backends.openrouter"):
        orb._log_reply(1, _reply(), None, pdf_text="positioned")
    assert "pdf=positioned" in caplog.text


def test_build_searchable_pdf_block_adds_transcript_text(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_realistic_tif(page)
    out = tmp_path / "out.pdf"
    ocr = OcrPage([OcrLine("printed", 10, 10, 120, 30)],
                  transcript="handschrift zeile\nzweite zeile", pdf_text="block")
    build_searchable_pdf([page], [ocr], out)
    text = _extract_pdf_text(out)
    assert "printed" in text
    assert "handschrift zeile" in text and "zweite zeile" in text


def test_build_searchable_pdf_skips_glyphs_missing_from_font(tmp_path):
    # Real scan 2026-09-16: an LLM line held a character DejaVuSans has no glyph
    # for; fpdf raised TypeError and the whole job failed. U+1D465 (math italic x)
    # is such a character. The PDF must still be built, with the rest of the text.
    page = tmp_path / "scan_0001.pnm.tif"
    _make_realistic_tif(page)
    out = tmp_path / "out.pdf"
    ocr = OcrPage([OcrLine("Wert \U0001D465 = 5", 10, 10, 200, 30)],
                  transcript="Block \U0001D465 zeile", pdf_text="block")
    build_searchable_pdf([page], [ocr], out)
    text = _extract_pdf_text(out)
    assert "Wert" in text and "= 5" in text
    assert "Block" in text


def test_build_searchable_pdf_tesseract_mode_ignores_transcript(tmp_path):
    page = tmp_path / "scan_0001.pnm.tif"
    _make_realistic_tif(page)
    out = tmp_path / "out.pdf"
    ocr = OcrPage([OcrLine("printed", 10, 10, 120, 30)], transcript="handschrift zeile")
    build_searchable_pdf([page], [ocr], out)
    assert "handschrift" not in _extract_pdf_text(out)
