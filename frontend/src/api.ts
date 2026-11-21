export type ResolutionSource = "LIVE_INFERENCE" | "LLM_CACHE";

export interface ChatMetadata {
  resolution_source: ResolutionSource;
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
  key_id: string | null;
  fallback_attempts: number;
  [extra: string]: unknown;
}

export interface ChatResponse {
  session_id: string;
  reply: string;
  metadata: ChatMetadata;
}

export const API_BASE = (
  window.__VANI_CONFIG__?.API_BASE_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  "http://localhost:8000"
).replace(/\/+$/, "");

export class ApiError extends Error {}

function readable422(detail: unknown): string {
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string; loc?: unknown[] };
    const field = Array.isArray(first.loc) ? String(first.loc[first.loc.length - 1]) : "";
    const msg = (first.msg || "Invalid input").replace(/^Value error,\s*/i, "");
    return field && !msg.toLowerCase().includes(field.toLowerCase()) ? `${field}: ${msg}` : msg;
  }
  if (typeof detail === "string") return detail;
  return "The request was invalid.";
}

export async function sendChat(
  sessionId: string,
  message: string,
  systemPrompt: string,
  simulate: string,
): Promise<ChatResponse> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (simulate.trim()) headers["X-Simulate-Failure"] = simulate.trim();
  const body: Record<string, string> = { session_id: sessionId, message };
  if (systemPrompt.trim()) body.system_prompt = systemPrompt.trim();

  let res: Response;
  try {
    res = await fetch(`${API_BASE}/chat`, { method: "POST", headers, body: JSON.stringify(body) });
  } catch {
    throw new ApiError("Can't reach the server. Check your connection and try again.");
  }

  let data: unknown = null;
  try {
    data = await res.json();
  } catch {
    /* non-JSON body */
  }
  if (res.ok) return data as ChatResponse;

  const detail = (data as { detail?: unknown } | null)?.detail;
  if (res.status === 503) {
    throw new ApiError(
      "All providers are unavailable right now (every API key failed). Your message wasn't saved; try again.",
    );
  }
  if (res.status === 422) throw new ApiError(`Invalid request: ${readable422(detail)}`);
  throw new ApiError(`Something went wrong (HTTP ${res.status}). Please try again.`);
}
