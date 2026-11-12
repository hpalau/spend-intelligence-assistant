import type { ApiError, AskResponse, Summary, Turn } from "./types";

const BASE = "/api";
const TIMEOUT_MS = 120_000;

export class RequestFailed extends Error {
  constructor(public readonly error: ApiError) {
    super(error.message);
  }
}

// Every failure becomes a RequestFailed with a message fit to show the analyst,
// and a `retryable` flag the UI uses to decide whether to offer "Try again".
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let res: Response;
  try {
    res = await fetch(BASE + path, { ...init, signal: controller.signal });
  } catch (e) {
    const timedOut = (e as Error).name === "AbortError";
    throw new RequestFailed({
      code: timedOut ? "timeout" : "network",
      message: timedOut ? "The request took too long." : "Could not reach the server. Is the backend running?",
      retryable: true,
    });
  } finally {
    clearTimeout(timer);
  }
  if (res.ok) return (await res.json()) as T;

  const body = await res.json().catch(() => null);
  if (body && typeof body.code === "string") throw new RequestFailed(body as ApiError);
  if (res.status === 422) {
    throw new RequestFailed({ code: "invalid_request", message: "The question could not be sent (check its length).", retryable: false });
  }
  // No structured body: the request never reached the API (e.g. the dev proxy
  // answering for a backend that is down).
  throw new RequestFailed({
    code: `http_${res.status}`,
    message: `The server did not respond (${res.status}). Is the backend running?`,
    retryable: res.status >= 500,
  });
}

export const getSummary = () => request<Summary>("/summary");

export const ask = (question: string, history: Turn[]) =>
  request<AskResponse>("/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, history }),
  });
