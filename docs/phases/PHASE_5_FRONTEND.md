# Phase 5: Frontend

Status: **Done** (2026-11-23), on branch `phase-5-frontend`.

The platform now has a product people can use in a browser. Someone can create an account, drop in a video, watch it process live, explore its report on a single time axis, jump to any moment, and ask questions whose answers cite playable timestamps. Real authentication replaces the Phase 2 dev-user seam. A Playwright test runs that whole journey in CI against the real stack, with only the models stubbed.

```
Browser ──/api/*──▶ Next.js (rewrites, proxy.ts) ──▶ NestJS ──▶ Postgres / Redis / S3
   │                                                   └──(service token, NDJSON)──▶ Python /internal/v1/qa
   └──PUT parts (presigned)──────────────────────────────────────────▶ S3 (SeaweedFS)
```

Design decisions: [ADR-025](../TECHNICAL_DECISIONS.md#adr-025-frontend-auth-and-baseline-qa-phase-5) (and ADR-017, now built).

---

## 1. Authentication (NestJS)

**What.**

- **Routes:** `POST /v1/auth/register | login | refresh | logout`, and `GET /v1/me`.
- **Passwords:** Argon2id.
- **Tokens:** a 15-minute HS256 access JWT and a rotating opaque refresh token. Both are `httpOnly; SameSite=Lax` cookies, `Secure` in production. Scripts can send the access token as a Bearer header.
- **Protections:** the guard protects every route that isn't `@Public()`; an Origin check refuses cross-origin writes; register, login and refresh are rate-limited per client IP.
- **Seed:** the seeded dev account gets a local password (`DEV_USER_PASSWORD`, never in production) so earlier videos stay reachable.

**Why this shape.** Refresh tokens are single-use and grouped in families: a stolen token that's used after its owner has rotated it revokes every token in the family. A 10-second grace window avoids signing out a user whose two tabs refresh at the same moment.

**Failure scenarios.**

| Scenario                         | Result                                                                |
| -------------------------------- | --------------------------------------------------------------------- |
| Wrong password                   | One generic 401; unknown emails take the same Argon2 time             |
| Expired, forged or reused tokens | 401, and the client refreshes once                                    |
| A refresh that fails             | Sends the user to sign-in, returning them to the same page afterwards |
| Too many attempts                | 429                                                                   |
| A blank optional name            | Stored as no name (a bug the E2E test found)                          |

**Tests.**

- Pure rotation rules, 5 unit tests.
- 9 integration tests:
  - registration and cookies
  - duplicate and invalid input
  - login and timing parity
  - cookie and Bearer access, and expired JWTs
  - rotation, concurrency, reuse and family revocation
  - logout
  - the Origin guard and the rate limit
- Every earlier API suite now signs up its own user.

## 2. APIs the UI needed

| Endpoint                                                                    | Purpose                                                                                             |
| --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `GET /v1/videos/:id/playback`                                               | Presigned 6-hour GET URL: the 720p proxy when one exists, otherwise the original                    |
| `GET /v1/videos/:id/captions`                                               | WebVTT from the published transcript, for `<track kind="captions">`                                 |
| `GET /v1/videos/:id/search?q=`                                              | Full-text search (every word, prefix match) over the published transcript                           |
| `POST/GET /v1/videos/:id/conversations`, `GET/DELETE /v1/conversations/:id` | Ask AI conversations                                                                                |
| `POST /v1/conversations/:id/messages`                                       | Asks a question; SSE stream of `user`, `status`, `delta`, `done`, `error`, plus keep-alive comments |

## 3. Baseline Q&A (Python, the benchmark for Phase 6)

**What.** `POST /internal/v1/qa` is protected by the service token and streams NDJSON:

1. Postgres full-text retrieval: content words OR-ed with prefix matching, top 8 hits plus their neighbouring segments, within a 2,500-token budget.
2. The report's summary and chapter titles are added as context.
3. Qwen3 4B streams an answer that cites `[S12]`.
4. Code keeps only citations to segments it provided (normalising `[S12 | 03:04]` and `[S12, S14]`), and detects `[inferred]` and the "not covered" refusal.
5. Each answer is logged to `llm_calls`.

NestJS stores conversations and messages, sends recent turns as history for follow-ups, and saves the cleaned answer with resolved citations.

**Two things testing changed.**

- **Thinking mode.** With Ollama 0.40, Qwen3 4B ignores `think: false` for free text and narrates its reasoning as the answer. Q&A therefore runs with thinking on: the reasoning goes to a separate field, which is dropped.
- **Keep-alives.** While the model reasons, nothing streams, and the Next rewrite proxy closed one such stream. The Python service now re-sends a `thinking` status every 5 s, and NestJS sends SSE keep-alive comments every 10 s.

**Failure scenarios.**

| Scenario                      | Result                                                                                           |
| ----------------------------- | ------------------------------------------------------------------------------------------------ |
| The AI service is down        | An `error` event with a plain message; the question is kept, and the draft is restored in the UI |
| The browser disconnects       | Generation is aborted and nothing half-written is stored                                         |
| The video isn't processed yet | 409                                                                                              |
| Another user's conversation   | 404                                                                                              |
| An answer without citations   | Flagged in the UI ("cites no moment in the video")                                               |

**Tests.**

- 7 Python unit tests: query building, context expansion, prompt assembly with history, citation parsing and normalisation, refusal detection, the streamed event sequence.
- 2 API integration tests against a fake AI service: the SSE event order, persistence, history on follow-ups, title, 409 before processing, ownership, deletion.

## 4. The web app (Next.js 16)

**Routes.**

- `/login` and `/register`.
- `/`, the library:
  - drag-and-drop upload with pause, resume and cancel
  - status chips, reprocess and delete (with a confirmation dialog)
  - "Show more" paging, and polling while anything processes
- `/videos/:id`, the workspace:
  - **The Index Strip:** one time axis with chapter bands, highlight ticks (colour plus glyph by kind), whatever was just selected lit in amber, and the playhead. It's the product's signature: every claim maps to a moment.
  - The player, with WebVTT captions.
  - A live processing view: per-stage progress over SSE, a polling fallback, and failures explained in plain language with a Reprocess button.
  - Tabs:
    - **Overview:** summary, cited key points and takeaways. A solid edge marks stated content, a dashed edge marks AI interpretation.
    - **Chapters:** timecodes first, with the current chapter highlighted.
    - **Highlights,** filterable by kind.
    - **Topics,** whose mentions light up the strip.
    - **Transcript:** search with highlighted matches, follow-playback, click to seek.
    - **Ask AI:** conversations, streamed answers, citation chips that play the moment.

**Upload.** The browser uploads parts straight to storage: four at a time, each signed right before use and retried with backoff. ETags are checkpointed in `localStorage`, so selecting the same file again resumes with only the missing parts.

**Accessibility.** A skip link, WAI-ARIA tabs (arrow keys, Home/End), real buttons for every timecode and strip element, visible focus, a native `<dialog>`, labelled `<progress>`, captions, `aria-live` for progress and answers, and `prefers-reduced-motion`. The E2E test runs axe-core and finds no serious or critical WCAG 2 A/AA violations.

**Tests.** 10 Vitest unit tests for the logic layer:

- timecodes and durations
- error explanations
- the SSE parser
- the answer renderer, which produces structure, never markup
- the API client's refresh-and-retry and its shared refresh
- the uploader: retries, resume after a failure, cancel

## 5. End-to-end test (Playwright)

`apps/web/e2e/happy-path.spec.ts` runs the exit criterion as a user would:

1. Sign up.
2. Upload a 6-minute WebM from the browser.
3. Watch it process.
4. Read the report.
5. Seek from a takeaway.
6. Move between tabs with the keyboard.
7. Search the transcript.
8. Ask a specific question and play its citation.
9. Sign out.

It also runs four accessibility scans along the way.

**Stubbed AI backend.** Only the models are replaced:

- `TRANSCRIPTION_PROVIDER=fixture` writes a recorded transcript (the CC BY Blender keynote excerpt).
- `LLM_BACKEND=replay` serves 9 recorded Ollama responses.

Upload, BullMQ, FFmpeg, scene detection, Postgres, S3, NestJS, SSE and the browser are all real. The test uses its own ports (web 4100, API 3100, AI 8100) and queue prefix, so it never touches a dev stack. A new `e2e` CI job runs it.

**Results:** 12.1 s in replay mode, verified with Ollama stopped; 2.8 min when re-recording (`make e2e-record`).

## Exit criteria

| Criterion                                                                                                          | Result                                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| A non-technical user can upload, watch progress, explore the report, jump to timestamps and ask grounded questions | ✅ Done in the browser on the 68-min keynote: seeking from a key point to 13:23 lands on the matching slide with captions; answers cite the right moments (for example "no one from the region is on the Board of Trustees" → 24:41) |
| The Playwright E2E test passes in CI with a stubbed AI backend                                                     | ✅ Passes locally in replay mode with Ollama stopped (12 s); the `e2e` CI job runs the same command                                                                                                                                  |

## Caveats and follow-ups

- **Broad questions get weak answers.** Keyword retrieval finds little for "What is this video about?", so these usually come back without citations, flagged in the UI. Phase 6's intent-aware hybrid retrieval is meant to fix this, and this baseline is what it has to beat.
- **Answers take 11–22 s on the M1 Pro, and a broad question took 35 s.** Thinking mode costs reasoning tokens before the first visible word. The UI shows "Thinking…", but the wait is real.
- **Rate-limit counters are in memory,** which is fine for one API instance. Multiple instances need Redis-backed storage (Phase 7).
- **Deleting a video is a soft delete;** storage cleanup is still a background concern (Phase 7).
- **The UI is English-only,** and there's no password reset or email verification yet (no email service in the stack).
- **Earlier phases' test runs left rows owned by the dev account** ("talk", "a", "b" in its library). They're harmless; delete them from the UI if they're in the way.
