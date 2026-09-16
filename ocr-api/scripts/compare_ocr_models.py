"""Compare OpenRouter OCR models on real scan pages (manual tool, not used by the API).

Usage (from ocr-api/, or inside the container from /ocr-api):
    python3 -m scripts.compare_ocr_models page1.png page2.tif \\
        --models google/gemini-3.1-flash-lite,google/gemini-3.5-flash \\
        [--efforts default,low,medium] [--truth page1.txt page2.txt] --out output/compare_out

Turn a delivered PDF into page images first, e.g.:
    gs -sDEVICE=pnggray -r300 -o page_%02d.png scan.pdf
"""
import argparse
import itertools
import sys
from pathlib import Path

from app.config import Settings, get_settings
from app.ocr_backends.openrouter import _client, call_llm, prepare_image


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(hypothesis: str, truth: str) -> float:
    """Character error rate with whitespace normalized."""
    hyp = " ".join(hypothesis.split())
    ref = " ".join(truth.split())
    if not ref:
        return 0.0 if not hyp else 1.0
    return levenshtein(hyp, ref) / len(ref)


def _slug(model: str) -> str:
    return model.replace("/", "_").replace(":", "_")


def compare(images: list[Path], models: list[str], truths: list[Path], out_dir: Path,
            settings: Settings, client, efforts: list[str] | None = None) -> list[dict]:
    """Run every model at every reasoning effort ("" = provider default) on every page."""
    # Measure each model on its own: no OpenRouter fallback to a different model.
    settings = settings.model_copy(update={"ocr_llm_fallback_models": []})
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for index, image_path in enumerate(images):
        image = prepare_image(image_path, settings.ocr_llm_image_max_side)
        truth = truths[index].read_text(encoding="utf-8") if truths else None
        for model, effort in itertools.product(models, efforts or [""]):
            row = {"page": image_path.name, "model": model, "effort": effort}
            try:
                # strong-model budget, so reasoning can't truncate the comparison
                reply = call_llm(client, image, model, settings.ocr_llm_strong_max_tokens, settings, effort)
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                rows.append(row)
                continue
            suffix = f"__{effort}" if effort else ""
            (out_dir / f"{image_path.stem}__{_slug(model)}{suffix}.txt").write_text(reply.text, encoding="utf-8")
            row.update(
                latency_s=reply.latency_s,
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
                cost=reply.cost,
                handwriting=reply.handwriting,
                uncertain=reply.uncertain,
                finish_reason=reply.finish_reason,
                cer=cer(reply.text, truth) if truth is not None else None,
            )
            rows.append(row)
    return rows


def format_table(rows: list[dict]) -> str:
    header = (f"{'page':<24} {'model':<40} {'effort':<8} {'time':>7} {'tokens in/out':>13} "
              f"{'cost':>9} {'hw':>3} {'unc':>3} {'fin':>8} {'CER':>6}")
    lines = [header, "-" * len(header)]
    for r in rows:
        effort = r.get("effort") or "default"
        if "error" in r:
            lines.append(f"{r['page']:<24} {r['model']:<40} {effort:<8} ERROR {r['error']}")
            continue
        cer_s = f"{r['cer']:.1%}" if r["cer"] is not None else "-"
        tokens = f"{r['prompt_tokens']}/{r['completion_tokens']}"
        lines.append(
            f"{r['page']:<24} {r['model']:<40} {effort:<8} {r['latency_s']:>6.1f}s {tokens:>13} "
            f"${r['cost']:>8.4f} {'y' if r['handwriting'] else 'n':>3} "
            f"{'y' if r['uncertain'] else 'n':>3} {str(r['finish_reason']):>8} {cer_s:>6}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare OpenRouter OCR models on scan pages.")
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--models", required=True, help="comma-separated OpenRouter model IDs")
    parser.add_argument("--truth", nargs="*", type=Path, default=[],
                        help="ground-truth .txt per image, same order as images")
    parser.add_argument("--efforts", default="default",
                        help="comma-separated reasoning efforts to try per model "
                             "(default, none, minimal, low, medium, high)")
    parser.add_argument("--out", type=Path, default=Path("compare_out"))
    args = parser.parse_args(argv)
    if args.truth and len(args.truth) != len(args.images):
        parser.error("--truth needs exactly one file per image")

    settings = get_settings()
    if not settings.openrouter_api_key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 2
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    efforts = ["" if e.strip() == "default" else e.strip() for e in args.efforts.split(",") if e.strip()]
    client = _client(settings)
    with client:  # not `with _client(settings) as client:` -- see openrouter.py
        rows = compare(args.images, models, args.truth, args.out, settings, client, efforts=efforts)
    print(format_table(rows))
    print(f"\nTranscripts written to {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
