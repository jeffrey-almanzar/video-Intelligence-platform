'use client';

import { type FormEvent, type ReactNode, useEffect, useRef, useState } from 'react';
import { groundingLabel, type Inline, parseAnswer } from '@/lib/answer';
import { ApiError, api, apiFetch } from '@/lib/api';
import { readSse } from '@/lib/sse';
import type { Citation, Conversation, Message } from '@/lib/types';
import { TimeChip } from './TimeChip';
import styles from './workspace.module.css';

const SUGGESTIONS = [
  'What is this video about?',
  'What are the main recommendations?',
  'Who is the speaker addressing?',
];

function Inlines({
  inlines,
  cites,
}: {
  inlines: Inline[];
  cites: Map<number, Citation> | null;
}): ReactNode {
  return inlines.map((x, i) => {
    if (x.kind === 'text') return x.text;
    if (x.kind === 'bold') return <strong key={i}>{x.text}</strong>;
    if (x.kind === 'inferred')
      return (
        <span
          key={i}
          className={styles.inferred}
          title="Inferred by the AI, not said directly in the video"
        >
          inferred
        </span>
      );
    const c = cites?.get(x.idx);
    // While streaming, citations aren't validated yet: show a placeholder, not a link.
    return c ? (
      <TimeChip key={i} ms={c.startMs} evidence={[c]} />
    ) : cites ? null : (
      <span key={i} className={styles.pendingCite}>
        …
      </span>
    );
  });
}

function Answer({ text, citations }: { text: string; citations: Citation[] | null }) {
  const cites = citations ? new Map(citations.map((c) => [c.idx, c])) : null;
  return (
    <>
      {parseAnswer(text).map((b, i) =>
        b.kind === 'p' ? (
          <p key={i}>
            <Inlines inlines={b.inlines} cites={cites} />
          </p>
        ) : b.ordered ? (
          <ol key={i}>
            {b.items.map((it, j) => (
              <li key={j}>
                <Inlines inlines={it} cites={cites} />
              </li>
            ))}
          </ol>
        ) : (
          <ul key={i}>
            {b.items.map((it, j) => (
              <li key={j}>
                <Inlines inlines={it} cites={cites} />
              </li>
            ))}
          </ul>
        ),
      )}
    </>
  );
}

const LABELS = {
  stated: {
    text: 'Stated in the video',
    hint: 'Every claim cites a moment where the speaker says it.',
  },
  interpretation: {
    text: 'Includes AI interpretation',
    hint: 'Parts marked “inferred”, or claims that didn’t match the moments they cite, go beyond what the speaker said.',
  },
  'not-covered': {
    text: 'Not covered in this video',
    hint: 'The AI found nothing in the video that answers this.',
  },
  unsupported: {
    text: 'No supporting moment',
    hint: 'This answer cites no moment in the video. Check it before relying on it.',
  },
} as const;

function GroundingBadge({ message }: { message: Message }) {
  const label = groundingLabel(message.grounding);
  if (!label) return null;
  const { text, hint } = LABELS[label];
  return (
    <p className={`${styles.badge} ${styles[`badge-${label}`]}`} title={hint}>
      {text}
      <span className="visually-hidden">. {hint}</span>
    </p>
  );
}

export function AskPanel({ videoId }: { videoId: string }) {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState('');
  const [streaming, setStreaming] = useState<{ text: string; thinking: boolean } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api<Conversation[]>(`/v1/videos/${videoId}/conversations`).then(
      (list) => {
        setConversations(list);
        if (list[0]) void open(list[0].id);
      },
      () => undefined,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [videoId]);

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' });
  }, [messages, streaming?.text]);

  async function open(id: string) {
    setConversationId(id);
    const data = await api<{ messages: Message[] }>(`/v1/conversations/${id}`);
    setMessages(data.messages);
  }

  function newConversation() {
    setConversationId(null);
    setMessages([]);
    setError(null);
  }

  async function ask(question: string) {
    const q = question.trim();
    if (!q || streaming) return;
    setError(null);
    setDraft('');
    let id = conversationId;
    try {
      if (!id) {
        const created = await api<Conversation>(`/v1/videos/${videoId}/conversations`, {
          method: 'POST',
        });
        id = created.id;
        setConversationId(id);
        setConversations((c) => [{ ...created, title: q }, ...c]);
      }
      setStreaming({ text: '', thinking: false });
      const res = await apiFetch(`/v1/conversations/${id}/messages`, {
        method: 'POST',
        body: JSON.stringify({ content: q }),
        headers: { accept: 'text/event-stream' },
      });
      if (!res.ok)
        throw new ApiError(
          res.status,
          (await res.json().catch(() => ({})))?.detail ?? 'Could not ask',
        );
      await readSse(res, ({ event, data }) => {
        const payload = JSON.parse(data);
        if (event === 'user') setMessages((m) => [...m, payload as Message]);
        else if (event === 'status') setStreaming((s) => (s ? { ...s, thinking: true } : s));
        else if (event === 'delta')
          setStreaming((s) => ({ text: (s?.text ?? '') + payload.text, thinking: false }));
        else if (event === 'done') setMessages((m) => [...m, payload as Message]);
        else if (event === 'error') setError(payload.message);
      });
    } catch (e) {
      setError((e as Error).message);
      setDraft(q);
    } finally {
      setStreaming(null);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(draft);
  }

  return (
    <div className={styles.ask}>
      <div className={styles.askBar}>
        <label className={styles.convPick}>
          <span className="visually-hidden">Conversation</span>
          <select
            value={conversationId ?? ''}
            onChange={(e) => (e.target.value ? void open(e.target.value) : newConversation())}
          >
            <option value="">New conversation</option>
            {conversations.map((c) => (
              <option key={c.id} value={c.id}>
                {c.title ?? 'Untitled conversation'}
              </option>
            ))}
          </select>
        </label>
        {conversationId && (
          <button type="button" className="btn btn-quiet" onClick={newConversation}>
            New conversation
          </button>
        )}
      </div>

      <div className={styles.thread} aria-live="polite" aria-busy={streaming !== null}>
        {messages.length === 0 && !streaming && (
          <div className={styles.askEmpty}>
            <p>
              Ask anything about this video. Answers cite the moments they come from; click a time
              to check it.
            </p>
            <div className={styles.chips}>
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="btn btn-quiet" onClick={() => void ask(s)}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {messages.map((m) =>
          m.role === 'user' ? (
            <p key={m.id} className={styles.q}>
              {m.content}
            </p>
          ) : (
            <div
              key={m.id}
              className={`${styles.a} ${m.grounding?.notCovered ? styles.notCovered : ''}`}
            >
              <GroundingBadge message={m} />
              <Answer text={m.content} citations={m.citations ?? []} />
            </div>
          ),
        )}
        {streaming && (
          <div className={styles.a}>
            {streaming.text ? (
              <Answer text={streaming.text} citations={null} />
            ) : (
              <p className={styles.dim}>
                {streaming.thinking ? 'Thinking…' : 'Finding the relevant moments…'}
              </p>
            )}
          </div>
        )}
        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}
        <div ref={end} />
      </div>

      <form className={styles.askForm} onSubmit={onSubmit}>
        <label className="visually-hidden" htmlFor="ask-input">
          Your question
        </label>
        <textarea
          id="ask-input"
          rows={2}
          value={draft}
          maxLength={2000}
          placeholder="Ask about this video"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void ask(draft);
            }
          }}
        />
        <button className="btn" type="submit" disabled={!draft.trim() || streaming !== null}>
          Ask
        </button>
      </form>
    </div>
  );
}
