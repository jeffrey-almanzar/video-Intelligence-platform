# Analysis test fixtures

- `blender-excerpt.transcript.json`: the first 6 minutes of the Whisper transcript of
  "Blender Conference 2022 Keynote" by Ton Roosendaal (Blender Foundation), licensed
  CC BY 3.0, from [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Keynote_-_Blender_Conference_2022.webm).
- `blender-excerpt.llm.json`: raw Ollama responses (chat + embeddings) recorded for that
  transcript, replayed by `ReplayBackend` so the analysis graph runs in CI without a model server.
- `blender-excerpt.report.json`: the expected report (golden-output snapshot).

Re-record after changing prompts, schemas or models (needs Ollama running):

```bash
VIP_RECORD_LLM=1 uv run pytest tests/test_analysis_snapshot.py
```
