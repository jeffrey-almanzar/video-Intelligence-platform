# Evaluations

Evaluation harness and committed results (ARCHITECTURE §5.8). Media datasets are downloaded to `evals/fixtures/` (gitignored) and never committed.

| Area                   | Data                                                                                                                                        | Run                                                | Results                                |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- | -------------------------------------- |
| Transcription WER      | LibriSpeech test-clean (EN), FLEURS es_419 test (ES); both CC BY 4.0                                                                        | `make eval-data`, then `make eval-transcription`   | `results/transcription/*.json`         |
| Long-form speed/memory | 10/60/120-min concatenations of LibriSpeech (`make eval-data` builds them)                                                                  | `make bench-transcription`                         | `results/transcription/longform.jsonl` |
| Report quality         | 4 CC BY 3.0 talks from Wikimedia Commons (`golden/manifest.json`), 164 min, with reference chapters and key points (`golden/*.labels.json`) | `make eval-golden-data`, then `make eval-analysis` | `results/analysis/*.json`              |

Report metrics: chapter boundary F1 within ±30 s (deterministic), citation validity (deterministic), key-point coverage and groundedness (judged by local Qwen3 8B, same family as the generator, so biased in its favour), schema-failure and repair rates. **The golden labels were written by Claude from the transcripts before any report existed and are not human-reviewed**; reviewing and correcting them is the most valuable next step for this harness. Only Creative Commons or self-recorded media.

| Results file                                       | What it is                                                        |
| -------------------------------------------------- | ----------------------------------------------------------------- |
| `results/analysis/2026-11-22-golden-v1.json`       | First full run (one chapter per 4 min)                            |
| `results/analysis/2026-11-22-golden-v1-final.json` | Shipped configuration (one chapter per 3 min)                     |
| `results/analysis/2026-11-22-blender-map8b.json`   | Qwen3 8B instead of 4B for the map step, Blender keynote only     |
| `results/analysis/2026-11-22-blender-fullctx.json` | One full-context call instead of map-reduce, Blender keynote only |

Attribution: LibriSpeech, V. Panayotov, G. Chen, D. Povey, S. Khudanpur (openslr.org/12). FLEURS, A. Conneau et al. (huggingface.co/datasets/google/fleurs). Both are licensed CC BY 4.0. Golden talks (CC BY 3.0): Blender Foundation / Ton Roosendaal; BerkmanCenter / William Fisher; YaleCourses / Craig Wright; Punjabi Wikimedians / Katherine Maher (sources in `golden/manifest.json`).
