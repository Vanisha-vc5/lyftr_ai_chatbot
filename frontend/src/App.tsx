import { KeyboardEvent, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { API_BASE, ApiError, ChatMetadata, sendChat } from "./api";

type Item =
  | { id: string; kind: "user"; text: string; simulate?: string }
  | { id: string; kind: "assistant"; text: string; meta: ChatMetadata }
  | { id: string; kind: "error"; text: string; retryText: string };

const newSessionId = () => "s-" + crypto.randomUUID();
const uid = () => Math.random().toString(36).slice(2);

const SIM_OPTIONS = [
  { value: "", label: "None (normal request)" },
  { value: "key_1=429", label: "key_1 = 429 (rate limited)" },
  { value: "key_1=500", label: "key_1 = 500 (server error)" },
  { value: "key_1=502", label: "key_1 = 502 (bad gateway)" },
  { value: "key_1=503", label: "key_1 = 503 (unavailable)" },
  { value: "*=429", label: "Fail all keys (*=429)" },
  { value: "custom", label: "Custom header…" },
];

function MetaBar({ meta }: { meta: ChatMetadata }) {
  const isCache = meta.resolution_source === "LLM_CACHE";
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-slate-100 pt-2 text-xs text-slate-500">
      <span
        data-testid="meta-source"
        className={
          "rounded-full px-2 py-0.5 font-mono font-semibold " +
          (isCache ? "bg-emerald-100 text-emerald-800" : "bg-blue-100 text-blue-800")
        }
      >
        {meta.resolution_source}
      </span>
      <span data-testid="meta-model" className="font-mono">
        {meta.model}
      </span>
      <span data-testid="meta-latency">{meta.latency_ms} ms</span>
      <span>
        <span>in </span>
        <span data-testid="meta-input-tokens">{meta.input_tokens}</span>
        <span> · out </span>
        <span data-testid="meta-output-tokens">{meta.output_tokens}</span>
      </span>
      <span className="font-mono">{meta.key_id ?? "no key (cache)"}</span>
      {meta.fallback_attempts > 0 && (
        <span className="font-medium text-amber-700">
          ↻ {meta.fallback_attempts} fallback{meta.fallback_attempts > 1 ? "s" : ""}
        </span>
      )}
    </div>
  );
}

export default function App() {
  const [sessionId, setSessionId] = useState(newSessionId);
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [pending, setPending] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState("");
  // Open by default so the system-prompt-input is visible (and fillable) on load.
  const [showPrompt, setShowPrompt] = useState(true);
  const [simChoice, setSimChoice] = useState("");
  const [customSim, setCustomSim] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items, pending]);

  async function send(text: string) {
    const message = text.trim();
    if (!message || pending) return;
    const simulate = simChoice === "custom" ? customSim.trim() : simChoice;
    setInput("");
    setSimChoice(""); // the Failover Lab applies to the next message only
    setItems((prev) => [...prev, { id: uid(), kind: "user", text: message, simulate: simulate || undefined }]);
    setPending(true);
    try {
      const res = await sendChat(sessionId, message, systemPrompt, simulate);
      setItems((prev) => [...prev, { id: uid(), kind: "assistant", text: res.reply, meta: res.metadata }]);
    } catch (err) {
      const text = err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
      setItems((prev) => [...prev, { id: uid(), kind: "error", text, retryText: message }]);
    } finally {
      setPending(false);
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void send(input);
    }
  }

  function newChat() {
    setSessionId(newSessionId());
    setItems([]);
    setInput("");
    inputRef.current?.focus();
  }

  return (
    <div className="mx-auto flex h-[100dvh] max-w-3xl flex-col px-3 sm:px-4">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 py-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">Vani</h1>
          <p className="text-xs text-slate-500">resilient LLM chat · key failover · response caching</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="hidden max-w-[11rem] truncate font-mono text-[11px] text-slate-400 sm:inline" title={sessionId}>
            {sessionId}
          </span>
          <button
            type="button"
            onClick={newChat}
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            New chat
          </button>
        </div>
      </header>

      <section className="space-y-2 border-b border-slate-200 py-2 text-sm">
        <button
          type="button"
          onClick={() => setShowPrompt((v) => !v)}
          aria-expanded={showPrompt}
          className="text-slate-600 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        >
          {showPrompt ? "▾" : "▸"} System prompt{systemPrompt.trim() ? " (custom)" : ""}
        </button>
        <div className={showPrompt ? "" : "hidden"}>
          <label htmlFor="system-prompt" className="sr-only">
            System prompt
          </label>
          <textarea
            id="system-prompt"
            data-testid="system-prompt-input"
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            rows={2}
            placeholder="Optional. Leave blank for the default Vani persona."
            className="w-full resize-y rounded-lg border border-slate-300 bg-white p-2 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          />
          <p className="text-xs text-slate-500">
            Applies to this session. A different system prompt means a different cache key.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor="failover-lab" className="font-medium text-slate-700">
            Failover Lab
          </label>
          <select
            id="failover-lab"
            data-testid="failover-select"
            value={simChoice}
            onChange={(e) => setSimChoice(e.target.value)}
            className="rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
          >
            {SIM_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {simChoice === "custom" && (
            <input
              aria-label="Custom X-Simulate-Failure header"
              value={customSim}
              onChange={(e) => setCustomSim(e.target.value)}
              placeholder="key_1=429,key_2=500"
              className="w-44 rounded-lg border border-slate-300 bg-white px-2 py-1 font-mono text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            />
          )}
          <span className="text-xs text-slate-500">sends X-Simulate-Failure on the next message only</span>
        </div>
      </section>

      <main className="flex-1 space-y-3 overflow-y-auto py-4" aria-live="polite" aria-label="Conversation">
        {items.length === 0 && (
          <p className="mt-10 text-center text-sm text-slate-400">
            Ask anything. Send the same first message in a new chat to see a cache hit.
          </p>
        )}
        {items.map((item) => {
          if (item.kind === "user") {
            return (
              <div key={item.id} className="flex flex-col items-end">
                <div className="max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm bg-blue-600 px-4 py-2 text-white">
                  {item.text}
                </div>
                {item.simulate && (
                  <span className="mt-1 font-mono text-[11px] text-amber-700">X-Simulate-Failure: {item.simulate}</span>
                )}
              </div>
            );
          }
          if (item.kind === "assistant") {
            return (
              <div key={item.id} data-testid="assistant-message" className="flex justify-start">
                <div className="max-w-[90%] rounded-2xl rounded-bl-sm border border-slate-200 bg-white px-4 py-2 text-slate-800 shadow-sm">
                  <div className="prose-reply break-words text-[15px] leading-relaxed">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
                  </div>
                  <MetaBar meta={item.meta} />
                </div>
              </div>
            );
          }
          return (
            <div key={item.id} className="flex justify-start">
              <div
                data-testid="error-message"
                role="alert"
                className="max-w-[90%] rounded-2xl rounded-bl-sm border border-red-200 bg-red-50 px-4 py-2 text-sm text-red-800"
              >
                <p>{item.text}</p>
                <button
                  type="button"
                  onClick={() => void send(item.retryText)}
                  disabled={pending}
                  className="mt-2 rounded-md border border-red-300 bg-white px-2 py-0.5 text-xs font-medium text-red-700 hover:bg-red-100 disabled:opacity-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
                >
                  Retry
                </button>
              </div>
            </div>
          );
        })}
        {pending && (
          <div className="flex justify-start" aria-label="Vani is typing">
            <div className="rounded-2xl rounded-bl-sm border border-slate-200 bg-white px-4 py-3 text-slate-400">
              <span className="animate-pulse">Vani is thinking…</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </main>

      <form
        className="flex items-end gap-2 border-t border-slate-200 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <label htmlFor="chat-input" className="sr-only">
          Message
        </label>
        <textarea
          id="chat-input"
          ref={inputRef}
          data-testid="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          rows={2}
          maxLength={4000}
          placeholder="Message Vani… (Enter to send, Shift+Enter for a new line)"
          autoFocus
          className="flex-1 resize-none rounded-xl border border-slate-300 bg-white p-3 text-[15px] focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        />
        <button
          type="submit"
          data-testid="send-button"
          disabled={pending || !input.trim()}
          className="rounded-xl bg-blue-600 px-5 py-3 font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
        >
          Send
        </button>
      </form>
      <p className="pb-2 text-center text-[10px] text-slate-400">API: {API_BASE}</p>
    </div>
  );
}
