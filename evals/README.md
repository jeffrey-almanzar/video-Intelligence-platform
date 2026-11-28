# Evaluations

Evaluation harness and committed results (ARCHITECTURE §5.8). Media datasets are downloaded to `evals/fixtures/` (gitignored) and never committed.

| Area                   | Data                                                                                                                                                          | Run                                                                                                                           | Results                                |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| Transcription WER      | LibriSpeech test-clean (EN), FLEURS es_419 test (ES); both CC BY 4.0                                                                                          | `make eval-data`, then `make eval-transcription`                                                                              | `results/transcription/*.json`         |
| Long-form speed/memory | 10/60/120-min concatenations of LibriSpeech (`make eval-data` builds them)                                                                                    | `make bench-transcription`                                                                                                    | `results/transcription/longform.jsonl` |
| Q&A (Phase 6)          | 52 questions over the same 4 talks (`golden/qa.json`): factual, broad, visual and unanswerable, each answerable one with the window where it is said or shown | Process the golden talks, then `cd services/ai && uv run python scripts/eval_qa.py --strategy hybrid\|baseline --name <name>` | `results/qa/*.json`                    |
| Report quality         | 4 CC BY 3.0 talks from Wikimedia Commons (`golden/manifest.json`), 164 min, with reference chapters and key points (`golden/*.labels.json`)                   | `make eval-golden-data`, then `make eval-analysis`                                                                            | `results/analysis/*.json`              |

Report metrics: chapter boundary F1 within ±30 s (deterministic), citation validity (deterministic), key-point coverage and groundedness (judged by local Qwen3 8B, same family as the generator, so biased in its favour), schema-failure and repair rates. **The golden labels were written by Claude from the transcripts before any report existed and are not human-reviewed**; reviewing and correcting them is the most valuable next step for this harness. Only Creative Commons or self-recorded media.

| Results file                                       | What it is                                                        |
| -------------------------------------------------- | ----------------------------------------------------------------- |
| `results/analysis/2026-11-22-golden-v1.json`       | First full run (one chapter per 4 min)                            |
| `results/analysis/2026-11-22-golden-v1-final.json` | Shipped configuration (one chapter per 3 min)                     |
| `results/analysis/2026-11-22-blender-map8b.json`   | Qwen3 8B instead of 4B for the map step, Blender keynote only     |
| `results/analysis/2026-11-22-blender-fullctx.json` | One full-context call instead of map-reduce, Blender keynote only |

Q&A metrics: Recall@1/3/5/10 and MRR@10 (a retrieved item overlapping the reference window ±5 s is a hit), context recall, answer correctness and faithfulness (Qwen3 8B judge, `qa-judge-v1`), citation rate and citations inside the window, refusal accuracy on unanswerable questions and false refusals on answerable ones, latency. The run is two passes (answers, then judging) so only one model is loaded at a time on 16 GB. Like the report labels, **the QA set was written by Claude and is not human-reviewed**.

| Results file                          | What it is                                         |
| ------------------------------------- | -------------------------------------------------- |
| `results/qa/2026-11-23-baseline.json` | Phase 5 baseline: Postgres full-text over segments |
| `results/qa/2026-11-28-hybrid.json`   | Phase 6 hybrid (shipped default)                   |

Attribution: LibriSpeech, V. Panayotov, G. Chen, D. Povey, S. Khudanpur (openslr.org/12). FLEURS, A. Conneau et al. (huggingface.co/datasets/google/fleurs). Both are licensed CC BY 4.0. Golden talks (CC BY 3.0): Blender Foundation / Ton Roosendaal; BerkmanCenter / William Fisher; YaleCourses / Craig Wright; Punjabi Wikimedians / Katherine Maher (sources in `golden/manifest.json`).
