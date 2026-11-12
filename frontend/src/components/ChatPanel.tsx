import { useEffect, useReducer, useRef, useState } from "react";
import { ask, RequestFailed } from "../api";
import type { ApiError, AskResponse, Turn } from "../types";
import { AnswerCard } from "./AnswerCard";

const EXAMPLES = [
  "What are our top 5 vendors by total spend in 2025?",
  "What is our total spend with vendors based in Spain?",
  "Who is allowed to approve an invoice of EUR 60,000?",
  "Can we pay INV-20118?",
];
const HISTORY_TURNS = 3; // question/answer pairs sent for follow-ups

type Exchange =
  | { id: number; question: string; state: "pending" }
  | { id: number; question: string; state: "done"; response: AskResponse }
  | { id: number; question: string; state: "error"; error: ApiError };

type Action =
  | { type: "sent"; id: number; question: string }
  | { type: "answered"; id: number; response: AskResponse }
  | { type: "failed"; id: number; error: ApiError };

function reducer(state: Exchange[], action: Action): Exchange[] {
  switch (action.type) {
    case "sent":
      return [...state.filter((e) => e.id !== action.id), { id: action.id, question: action.question, state: "pending" }];
    case "answered":
      return state.map((e) => (e.id === action.id ? { ...e, state: "done", response: action.response } : e));
    case "failed":
      return state.map((e) => (e.id === action.id ? { ...e, state: "error", error: action.error } : e));
  }
}

// Recent answered exchanges, as alternating user/assistant turns.
function historyFrom(exchanges: Exchange[]): Turn[] {
  return exchanges
    .filter((e): e is Extract<Exchange, { state: "done" }> => e.state === "done")
    .slice(-HISTORY_TURNS)
    .flatMap((e) => [
      { role: "user" as const, content: e.question },
      { role: "assistant" as const, content: e.response.answer },
    ]);
}

export function ChatPanel() {
  const [exchanges, dispatch] = useReducer(reducer, []);
  const [draft, setDraft] = useState("");
  const nextId = useRef(1);
  const bottom = useRef<HTMLDivElement>(null);
  const pending = exchanges.some((e) => e.state === "pending");

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [exchanges]);

  async function send(question: string, retryId?: number) {
    const q = question.trim();
    if (!q || pending) return;
    const id = retryId ?? nextId.current++;
    const history = historyFrom(exchanges.filter((e) => e.id !== id));
    dispatch({ type: "sent", id, question: q });
    setDraft("");
    try {
      dispatch({ type: "answered", id, response: await ask(q, history) });
    } catch (e) {
      const error = e instanceof RequestFailed ? e.error : { code: "unknown", message: String(e), retryable: true };
      dispatch({ type: "failed", id, error });
    }
  }

  return (
    <section className="chat">
      <div className="thread" aria-live="polite">
        {exchanges.length === 0 && (
          <div className="empty">
            <p>Ask about 2025 invoices or the procurement policy. Every answer shows what it is based on.</p>
            <div className="examples">
              {EXAMPLES.map((q) => <button key={q} onClick={() => send(q)}>{q}</button>)}
            </div>
          </div>
        )}
        {exchanges.map((e) => (
          <div key={e.id} className="exchange">
            <div className="question">{e.question}</div>
            {e.state === "pending" && <div className="thinking">Checking the data and policy…</div>}
            {e.state === "error" && (
              <div className="error-box" role="alert">
                <p>{e.error.message}</p>
                {e.error.retryable && <button onClick={() => send(e.question, e.id)} disabled={pending}>Try again</button>}
              </div>
            )}
            {e.state === "done" && <AnswerCard r={e.response} />}
          </div>
        ))}
        <div ref={bottom} />
      </div>

      <form className="composer" onSubmit={(ev) => { ev.preventDefault(); send(draft); }}>
        <textarea
          value={draft}
          maxLength={1000}
          rows={2}
          placeholder="e.g. Which invoices from inactive vendors are over EUR 50,000?"
          onChange={(ev) => setDraft(ev.target.value)}
          onKeyDown={(ev) => {
            if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send(draft); }
          }}
        />
        <button type="submit" disabled={pending || !draft.trim()}>{pending ? "Working…" : "Ask"}</button>
      </form>
    </section>
  );
}
