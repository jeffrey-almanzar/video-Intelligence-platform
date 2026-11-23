# End-to-end fixtures (the "stubbed AI backend")

- `transcript.json`: first 6 minutes of the Whisper transcript of the Blender Conference 2022
  keynote by Ton Roosendaal (CC BY 3.0, Wikimedia Commons). The worker runs with
  `TRANSCRIPTION_PROVIDER=fixture`, so this replaces Whisper.
- `llm.json`: raw Ollama responses (analysis + Q&A) recorded for exactly what the E2E test does,
  replayed with `LLM_BACKEND=replay`. Everything else is real: upload, queue, FFmpeg, Postgres,
  S3, API, SSE and the browser.

Re-record after changing prompts, models or the test's questions (needs Ollama):

```bash
make e2e-record
```
