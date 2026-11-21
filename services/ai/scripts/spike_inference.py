"""Spike B (Phase 1): validate local inference on this machine and record real numbers.

Checks:
  1. MLX Whisper transcription of a synthetic speech clip (macOS `say`): time, RTF, WER, memory.
  2. Qwen3 structured output via Ollama JSON-schema `format` + Pydantic validation.
  3. `keep_alive: 0` actually unloads the model (sequential model loading, ADR-010).
  4. Qwen3-Embedding dimension (must match the planned vector(1024) column).
  5. Qwen3-VL reads text from a rendered "slide" frame.

Run:  cd services/ai && uv run python scripts/spike_inference.py [--whisper-model ...]
Writes JSON results to spike-output/inference.json (gitignored) and prints a summary.
"""

from __future__ import annotations

import argparse
import base64
import itertools
import json
import platform
import re
import resource
import subprocess
import sys
import time
from pathlib import Path

import httpx
from pydantic import BaseModel, Field, ValidationError

HERE = Path(__file__).parent
OUT = HERE.parent / "spike-output"
OLLAMA = "http://localhost:11434"


# ---------- helpers ---------------------------------------------------------------------------


def rss_mb() -> float:
    # ru_maxrss is bytes on macOS (kilobytes on Linux).
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = words(reference), words(hypothesis)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return round(prev[-1] / max(len(ref), 1), 4)


def ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(out.stdout.strip())


def loaded_models(client: httpx.Client) -> list[dict[str, object]]:
    return list(client.get(f"{OLLAMA}/api/ps").json().get("models", []))


# ---------- 1. transcription -------------------------------------------------------------------


def make_audio(work: Path) -> tuple[Path, str]:
    source = HERE / "spike_fixtures" / "talk.txt"
    wav = work / "talk.wav"
    if not wav.exists():
        # 16 kHz mono PCM: the format the pipeline's extract_media stage will produce.
        # (Writing AIFF and converting hung `say` on macOS 26; writing WAV directly works.)
        subprocess.run(
            [
                "say",
                "-r",
                "170",
                "--file-format=WAVE",
                "--data-format=LEI16@16000",
                "-o",
                str(wav),
                "-f",
                str(source),
            ],
            check=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
        )
    return wav, source.read_text()


def spike_whisper(wav: Path, reference: str, model: str) -> dict[str, object]:
    import mlx.core as mx
    import mlx_whisper

    duration = ffprobe_duration(wav)
    results: dict[str, object] = {"model": model, "audio_seconds": round(duration, 1)}

    # Cold run includes model download (first time) + load; warm run is steady-state speed.
    for label in ("cold", "warm"):
        t0 = time.perf_counter()
        out = mlx_whisper.transcribe(
            str(wav),
            path_or_hf_repo=model,
            word_timestamps=True,
            condition_on_previous_text=False,
            language="en",
        )
        elapsed = time.perf_counter() - t0
        results[f"{label}_seconds"] = round(elapsed, 2)
        results[f"{label}_rtf"] = round(elapsed / duration, 3)

    segments = out["segments"]
    starts = [s["start"] for s in segments]
    results.update(
        segments=len(segments),
        timestamps_monotonic=all(a <= b for a, b in itertools.pairwise(starts)),
        last_segment_end=round(segments[-1]["end"], 1) if segments else None,
        wer=wer(reference, out["text"]),
        mlx_peak_memory_mb=round(mx.get_peak_memory() / 1024**2, 1),
        process_peak_rss_mb=rss_mb(),
        sample=out["text"][:160].strip(),
    )
    # Release GPU memory before the next model (sequential loading).
    del out
    mx.clear_cache()
    return results


# ---------- 2/3. structured LLM output + unload -----------------------------------------------


class Recommendation(BaseModel):
    text: str = Field(min_length=5)
    segment_ids: list[int] = Field(min_length=1)


class ChunkNotes(BaseModel):
    summary: str = Field(min_length=20)
    recommendations: list[Recommendation] = Field(min_length=1, max_length=5)
    topics: list[str] = Field(min_length=1, max_length=8)


def segments_for_prompt(text: str) -> tuple[str, int]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    return "\n".join(f"[S{i}] {s}" for i, s in enumerate(sentences)), len(sentences)


def spike_llm(client: httpx.Client, model: str, transcript: str) -> dict[str, object]:
    context, n_segments = segments_for_prompt(transcript)
    prompt = (
        "You extract structured notes from a video transcript. Each line starts with a segment "
        "ID like [S12]. Cite segment IDs (integers only) that support each recommendation. "
        "Only use information stated in the transcript.\n\n"
        f"TRANSCRIPT:\n{context}"
    )
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "format": ChunkNotes.model_json_schema(),
        "think": False,
        "stream": False,
        "keep_alive": 0,  # unload right after this call
        "options": {"temperature": 0.1, "num_ctx": 8192},
    }
    t0 = time.perf_counter()
    res = client.post(f"{OLLAMA}/api/chat", json=body, timeout=600)
    elapsed = time.perf_counter() - t0
    res.raise_for_status()
    data = res.json()

    result: dict[str, object] = {
        "model": model,
        "wall_seconds": round(elapsed, 2),
        "load_seconds": round(data.get("load_duration", 0) / 1e9, 2),
        "prompt_tokens": data.get("prompt_eval_count"),
        "prompt_tokens_per_s": round(
            data["prompt_eval_count"] / (data["prompt_eval_duration"] / 1e9), 1
        )
        if data.get("prompt_eval_duration")
        else None,
        "output_tokens": data.get("eval_count"),
        "output_tokens_per_s": round(data["eval_count"] / (data["eval_duration"] / 1e9), 1)
        if data.get("eval_duration")
        else None,
    }
    try:
        notes = ChunkNotes.model_validate_json(data["message"]["content"])
        cited = [i for r in notes.recommendations for i in r.segment_ids]
        result.update(
            schema_valid=True,
            recommendations=[r.text for r in notes.recommendations],
            citations_valid=all(0 <= i < n_segments for i in cited),
            topics=notes.topics,
        )
    except ValidationError as exc:
        result.update(schema_valid=False, validation_error=str(exc)[:300])

    time.sleep(1.5)  # give Ollama a moment to unload
    result["models_loaded_after_keep_alive_0"] = [m["name"] for m in loaded_models(client)]
    return result


# ---------- 4. embeddings ----------------------------------------------------------------------


def spike_embedding(client: httpx.Client, model: str) -> dict[str, object]:
    texts = [
        "start with a modular monolith",
        "begin with a single deployable split into modules",
        "scale GPU workers to zero when the queue is empty",
    ]
    t0 = time.perf_counter()
    res = client.post(
        f"{OLLAMA}/api/embed", json={"model": model, "input": texts, "keep_alive": 0}, timeout=300
    )
    res.raise_for_status()
    vectors = res.json()["embeddings"]

    def cos(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(x * x for x in b) ** 0.5
        return round(dot / (na * nb), 3)

    return {
        "model": model,
        "dimension": len(vectors[0]),
        "seconds": round(time.perf_counter() - t0, 2),
        "cos_paraphrase": cos(vectors[0], vectors[1]),
        "cos_unrelated": cos(vectors[0], vectors[2]),
    }


# ---------- 5. vision --------------------------------------------------------------------------


def make_slide(work: Path) -> Path:
    # Rendered with Pillow: Homebrew's FFmpeg build has no `drawtext` filter (no freetype).
    from PIL import Image, ImageDraw, ImageFont

    png = work / "slide.png"
    font_path = "/System/Library/Fonts/Supplemental/Arial.ttf"
    img = Image.new("RGB", (1280, 720), "white")
    draw = ImageDraw.Draw(img)
    draw.text(
        (80, 120), "Monolith vs Microservices", fill="black", font=ImageFont.truetype(font_path, 64)
    )
    body = ImageFont.truetype(font_path, 40)
    draw.text((80, 280), "1. Start with a modular monolith", fill="black", font=body)
    draw.text((80, 350), "2. Split for scaling or team autonomy", fill="black", font=body)
    img.save(png)
    return png


class SlideAnalysis(BaseModel):
    visual_type: str
    title: str
    ocr_text: str


def spike_vision(client: httpx.Client, model: str, slide: Path) -> dict[str, object]:
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "Classify this video frame (slide, code, diagram, chart, demo, person, other), "
                "give its title, and transcribe all visible text.",
                "images": [base64.b64encode(slide.read_bytes()).decode()],
            }
        ],
        "format": SlideAnalysis.model_json_schema(),
        "think": False,
        "stream": False,
        "keep_alive": 0,
        "options": {"temperature": 0, "num_ctx": 4096},
    }
    t0 = time.perf_counter()
    res = client.post(f"{OLLAMA}/api/chat", json=body, timeout=600)
    res.raise_for_status()
    elapsed = time.perf_counter() - t0
    parsed = SlideAnalysis.model_validate_json(res.json()["message"]["content"])
    expected = ["monolith", "microservices", "modular", "scaling", "team"]
    found = [w for w in expected if w in parsed.ocr_text.lower() + parsed.title.lower()]
    return {
        "model": model,
        "wall_seconds": round(elapsed, 2),
        "visual_type": parsed.visual_type,
        "title": parsed.title,
        "keywords_recovered": f"{len(found)}/{len(expected)}",
    }


# ---------- main -------------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--whisper-model", default="mlx-community/whisper-large-v3-turbo")
    parser.add_argument("--llms", default="qwen3:4b,qwen3:8b")
    parser.add_argument("--vision-model", default="qwen3-vl:4b")
    parser.add_argument("--embed-model", default="qwen3-embedding:0.6b")
    parser.add_argument("--skip-whisper", action="store_true")
    args = parser.parse_args()

    OUT.mkdir(exist_ok=True)
    report: dict[str, object] = {
        "machine": {"platform": platform.platform(), "processor": platform.processor()},
        "ollama_version": httpx.get(f"{OLLAMA}/api/version").json()["version"],
    }

    wav, reference = make_audio(OUT)
    if not args.skip_whisper:
        print("→ whisper…", flush=True)
        report["whisper"] = spike_whisper(wav, reference, args.whisper_model)
        print(json.dumps(report["whisper"], indent=2), flush=True)

    with httpx.Client() as client:
        report["llm"] = []
        for model in args.llms.split(","):
            print(f"→ {model}…", flush=True)
            r = spike_llm(client, model, reference)
            report["llm"].append(r)  # type: ignore[attr-defined]
            print(json.dumps(r, indent=2), flush=True)

        print("→ embeddings…", flush=True)
        report["embedding"] = spike_embedding(client, args.embed_model)
        print(json.dumps(report["embedding"], indent=2), flush=True)

        print("→ vision…", flush=True)
        report["vision"] = spike_vision(client, args.vision_model, make_slide(OUT))
        print(json.dumps(report["vision"], indent=2), flush=True)

    (OUT / "inference.json").write_text(json.dumps(report, indent=2))
    print(f"\nSaved {OUT / 'inference.json'}")


if __name__ == "__main__":
    main()
