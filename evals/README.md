# Evaluations

Evaluation harness and committed results (ARCHITECTURE §5.8). Media datasets are downloaded to `evals/fixtures/` (gitignored) and never committed.

| Area                   | Data                                                                       | Run                                              | Results                                |
| ---------------------- | -------------------------------------------------------------------------- | ------------------------------------------------ | -------------------------------------- |
| Transcription WER      | LibriSpeech test-clean (EN), FLEURS es_419 test (ES); both CC BY 4.0       | `make eval-data`, then `make eval-transcription` | `results/transcription/*.json`         |
| Long-form speed/memory | 10/60/120-min concatenations of LibriSpeech (`make eval-data` builds them) | `make bench-transcription`                       | `results/transcription/longform.jsonl` |

Report quality evaluations (chapters, takeaways, groundedness) are added in Phase 4. Only Creative Commons or self-recorded media.

Attribution: LibriSpeech, V. Panayotov, G. Chen, D. Povey, S. Khudanpur (openslr.org/12). FLEURS, A. Conneau et al. (huggingface.co/datasets/google/fleurs). Both are licensed CC BY 4.0.
