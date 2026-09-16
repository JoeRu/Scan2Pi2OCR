import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from app.config import Settings
from scripts.compare_ocr_models import cer, compare, format_table, levenshtein, main


def _result(model, text):
    return SimpleNamespace(
        model=model,
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(
            content=json.dumps({"text": text, "handwriting": False, "uncertain": False})))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=3, cost=0.0001),
    )


def test_levenshtein():
    assert levenshtein("kitten", "sitting") == 3
    assert levenshtein("", "abc") == 3
    assert levenshtein("abc", "abc") == 0


def test_cer_normalizes_whitespace():
    assert cer("Hallo  Welt\n", "Hallo Welt") == 0.0
    assert cer("Hallo Welt", "Hallo Welk") == pytest.approx(0.1)


def test_cer_empty_truth():
    assert cer("", "") == 0.0
    assert cer("x", "") == 1.0


def test_compare_writes_transcripts_and_rows(tmp_path):
    img = tmp_path / "page1.png"
    Image.new("RGB", (50, 50), (255, 255, 255)).save(img)
    truth = tmp_path / "page1.txt"
    truth.write_text("Hallo Welt", encoding="utf-8")

    def send(**kwargs):
        if kwargs["model"] == "bad/model":
            raise RuntimeError("no route")
        return _result(kwargs["model"], "Hallo Welt")

    client = MagicMock()
    client.chat.send.side_effect = send
    settings = Settings(api_key="test", openrouter_api_key="sk", ocr_llm_fallback_models=["x/y"])

    with patch("app.ocr_backends.openrouter.time.sleep"):
        rows = compare([img], ["good/model", "bad/model"], [truth], tmp_path / "out", settings, client)

    assert rows[0]["model"] == "good/model"
    assert rows[0]["cer"] == 0.0
    assert rows[0]["cost"] == 0.0001
    assert rows[0]["finish_reason"] == "stop"  # M3: provenance for picking a model
    assert "error" in rows[1]
    assert (tmp_path / "out" / "page1__good_model.txt").read_text(encoding="utf-8") == "Hallo Welt"
    # each model is measured on its own: no OpenRouter fallback list
    assert all("models" not in c.kwargs for c in client.chat.send.call_args_list)
    table = format_table(rows)
    assert "good/model" in table
    assert "ERROR" in table
    assert "stop" in table


def test_compare_runs_each_model_per_reasoning_effort(tmp_path):
    img = tmp_path / "page1.png"
    Image.new("RGB", (50, 50), (255, 255, 255)).save(img)
    client = MagicMock()
    client.chat.send.side_effect = lambda **kw: _result(kw["model"], f"effort={kw.get('reasoning')}")
    settings = Settings(api_key="test", openrouter_api_key="sk")

    rows = compare([img], ["good/model"], [], tmp_path / "out", settings, client,
                   efforts=["", "low"])

    assert [r["effort"] for r in rows] == ["", "low"]
    sent = [c.kwargs.get("reasoning") for c in client.chat.send.call_args_list]
    assert sent == [None, {"effort": "low"}]
    assert (tmp_path / "out" / "page1__good_model.txt").exists()
    assert (tmp_path / "out" / "page1__good_model__low.txt").read_text(encoding="utf-8") \
        == "effort={'effort': 'low'}"
    assert "low" in format_table(rows)


def test_main_passes_efforts(tmp_path):
    img = tmp_path / "page1.png"
    Image.new("RGB", (50, 50), (255, 255, 255)).save(img)
    settings = Settings(api_key="test", openrouter_api_key="sk")
    with patch("scripts.compare_ocr_models.get_settings", return_value=settings), \
         patch("scripts.compare_ocr_models._client", return_value=MagicMock()), \
         patch("scripts.compare_ocr_models.compare", return_value=[]) as mock_compare:
        main([str(img), "--models", "m", "--efforts", "default,low,medium",
              "--out", str(tmp_path / "out")])
    assert mock_compare.call_args.kwargs["efforts"] == ["", "low", "medium"]


def test_main_requires_api_key(tmp_path):
    no_key = Settings(api_key="test", openrouter_api_key="")  # explicit: ignore any host env var
    with patch("scripts.compare_ocr_models.get_settings", return_value=no_key):
        assert main([str(tmp_path / "a.png"), "--models", "m"]) == 2


def test_main_truth_count_mismatch_exits():
    with pytest.raises(SystemExit):
        main(["a.png", "b.png", "--models", "m", "--truth", "a.txt"])


def test_main_closes_client_after_use(tmp_path):
    """M4: main() uses the client as a context manager, and hands compare() the
    same client object `_client()` returned (not __enter__'s return value)."""
    img = tmp_path / "page1.png"
    Image.new("RGB", (50, 50), (255, 255, 255)).save(img)
    settings = Settings(api_key="test", openrouter_api_key="sk")
    client = MagicMock()
    with patch("scripts.compare_ocr_models.get_settings", return_value=settings), \
         patch("scripts.compare_ocr_models._client", return_value=client), \
         patch("scripts.compare_ocr_models.compare", return_value=[]) as mock_compare:
        assert main([str(img), "--models", "m", "--out", str(tmp_path / "out")]) == 0
    client.__enter__.assert_called_once()
    client.__exit__.assert_called_once()
    mock_compare.assert_called_once()
    assert mock_compare.call_args.args[-1] is client
