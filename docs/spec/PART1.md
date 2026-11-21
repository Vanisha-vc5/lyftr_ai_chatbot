# Vani build spec: PART 1 of 4: Deadline plan, the exact contract, and inputs

You will receive this spec in 4 parts in this same chat. **First, save each part verbatim to `docs/spec/PART<N>.md`**, so it survives context compaction, and re-read those files whenever you are unsure.

After saving PART 1: create the repo skeleton (`/backend`, `/frontend`, `docs/`, `.gitignore`), `git init`, and ask me for the Groq keys so you can run the walking-skeleton call (one real `litellm.completion` to `groq/openai/gpt-oss-20b`; details in PART 2 §2.7). Do NOT build the app yet. Reply "Ready for PART 2" when done.

---

## ⏱ FAST MODE: hard deadline TODAY, ~8 PM. This section overrides the scope everywhere below.

The human must submit by about 8 PM today and has to record a demo video before that. Optimise for **a working, deployed, contract-exact app**, not polish. Work autonomously: don't stop to ask for approval of plans, file layouts or design choices; decide and move on. Stop only for credentials and logins.

**Rule #1: it must work.** A plain app that passes the whole contract live beats a fancy one that half-works. At every checkpoint the repo must be runnable, so never leave it broken between steps. When an approach fights you for more than 15 minutes, take the simpler path listed in "Fallbacks" below and note it in the README.

**LLM provider: Groq** (`groq/openai/gpt-oss-20b`, two Groq keys). See §2.7. Do not use Gemini or the retired Groq Llama models.

**Build only this (in this order), with these checkpoints counted from when you start:**
| By | Deliverable | Done when |
|---|---|---|
| +0.25 h | Walking skeleton: one real Groq call through LiteLLM with the human's key (§2.7) | Real reply and usage printed |
| +1.5 h | Backend core: config, SQLite, LiteLLM Router + simulation hook + disk cache + warm-up + single-flight/probe, 3 endpoints, error handlers | Fast-mode pytest subset green (below) |
| +2 h | Backend Dockerfile, frontend placeholder, `docker-compose.yml`, `Makefile`, `.env.example` | `make up` → `/health` OK on :8000 |
| +2.5 h | **Deploy backend to Railway now, not at the end** (volume `/data`, env, `ENABLE_FAILURE_SIMULATION=true`) | `scripts/smoke_test.py` green against the live URL |
| +4 h | Frontend core (§5.1 contract + system prompt + **Failover Lab** only), Docker image, **deploy to Vercel**, CORS set | Live UI shows LIVE then CACHE, key_2 failover and a 503 bubble |
| +4.5 h | README (6 headings, live URLs, design decisions, curl verification), `docs/DEMO_SCRIPT.md` | Smoke green on live; repo public; no secrets committed |

Freeze features no later than **7:00 PM**. After that, only fix bugs, so the human has time to record the demo.

**Fast-mode pytest subset** (required; the rest of §7.1 is optional): cases 1, 2, 3, 5, 6, 7, 8, 12, 15 and 18.

**Cut entirely (do not build):** Session Inspector, cost/savings, session stats, health badge, dark mode, prompt presets, structured JSON logging, CI, Playwright, attempt-trace UI. Keep the `attempts` field in the response metadata only if it costs under 10 minutes.

**Keep the frontend simple:** one page, Tailwind, `react-markdown` is optional (plain text with `whitespace-pre-wrap` is fine), and the Failover Lab is just a `<select>` (none / key_1=429 / key_1=500 / *=429 / custom) applied to the next message.

**Fallbacks (use them without hesitation):**
- Disk cache problems in Docker or on Railway → `CACHE_TYPE=local` (LiteLLM's in-memory cache, explicitly allowed).
- `async_pre_call_deployment_hook` doesn't fire in your build → raise the simulated exception from a `CustomLogger.async_pre_call_check(deployment)` override, or as a last resort pass a per-call `mock_response=litellm.RateLimitError(...)` for the simulated key. The Router's fallback must still be what moves to key_2.
- The cache-landing probe (§2.5) misbehaves → keep the single-flight lock and simply `await asyncio.sleep(0.15)` after a LIVE response before releasing it.
- Railway blocked (verification or billing) → Render **Starter** with a disk at `/data`, or Railway Hobby. Tell the human immediately; don't spend time debugging account issues.
- gpt-oss-20b returns empty content or behaves oddly → switch `LLM_MODEL` to `groq/openai/gpt-oss-120b`.

**Speed tips:** if your environment supports subagents, build the frontend in parallel once the API schemas exist. Use `railway` and `vercel` CLIs if they're logged in; otherwise print the exact dashboard steps for the human and keep coding while they click. If anything blocks for more than 15 minutes (e.g. the 1.108.14 disk cache misbehaves in Docker), fall back immediately (`CACHE_TYPE=local`, LiteLLM's in-memory cache, which the assignment explicitly allows) and note the choice in the README.


## 0. Your role and mission

You are a senior full-stack engineer shipping a take-home assignment for **Lyftr AI**. The result is graded by automated checks and a human reviewer against an **exact API and UI contract**: a renamed field, a different path or a missing `data-testid` counts as a missing requirement. The deliverables are:

1. A public GitHub repo with `/backend` (Python, FastAPI, LiteLLM, SQLite), `/frontend` (React), `docker-compose.yml`, `Makefile`, `.env.example` and `README.md`.
2. A **live backend** (Railway, with a persistent volume at `/data`) and a **live frontend** (Vercel), connected to each other.
3. Material for a 2–3 minute demo video (you write the script; the human records it).

Product name: **Vani** (Sanskrit/Hindi for "speech, voice"). Use it in the UI title and README.

**Priorities, in order. Do not start a later tier until the earlier one passes its tests:**

1. **Contract correctness**: endpoints, fields, status codes, headers and test IDs exactly as specified.
2. **Resilience**: failover, a clean 503, 422 validation, no stack traces ever.
3. **Caching accuracy**: correct labels, identical text on hits, hits faster than live calls.
4. **Docker**: `make up` brings up the backend on :8000 and the frontend on :3000.
5. **Deployment**: live and connected, with a smoke test passing against the live URLs.
6. **Docs and tests**: README with the exact headings, backend pytest suite, CI.
7. **Bonus features** (Section 10), only after 1–6 are green.

**Working style**
- First write a short plan and a task list. Then build in small, verifiable steps and commit after each green step with a conventional commit message.
- Never claim something works without running it. Run pytest, `docker compose up`, and the smoke script, and look at the UI in a browser if you have one.
- Never write real API keys into any file that gets committed. Real keys go only in a local `.env` (gitignored) and in the Railway dashboard or CLI.
- When you need something only the human can provide (API keys, `railway login`, `vercel login`, `gh auth login`), stop and ask for exactly that, then continue.


## 1. The contract, restated in full. It is the source of truth.

### 1.1 Stack (mandatory)
- Frontend: **React**. Backend: **Python + FastAPI**. Storage: **SQLite only** (no Redis, no Postgres, no other external store).
- LLM gateway: **LiteLLM** (Python SDK Router), used for **both key fallback and response caching**.

### 1.2 Backend endpoints: exactly these three
All bodies are JSON. Extra response fields are allowed; missing or renamed fields are not.

**`GET /health`**: returns 200 once the app is running, the database is reachable and the key pool is loaded.
```json
{ "status": "ok", "keys_configured": 2, "cache_enabled": true }
```
If the DB is unreachable or no keys are loaded, return 503 `{"detail": "..."}`.

**`POST /chat`**
Request:
```json
{ "session_id": "s-7f3a", "message": "What is the capital of France?", "system_prompt": "optional" }
```
| Field | Type | Rule |
|---|---|---|
| session_id | string | Required, 1–128 chars, generated by the client. An unknown id starts a new session. |
| message | string | Required, 1–4000 chars **after trimming whitespace** |
| system_prompt | string | Optional; overrides the default system prompt **for this session** |

Response 200:
```json
{
  "session_id": "s-7f3a",
  "reply": "The capital of France is Paris.",
  "metadata": {
    "resolution_source": "LIVE_INFERENCE",
    "model": "groq/openai/gpt-oss-20b",
    "latency_ms": 812,
    "input_tokens": 42,
    "output_tokens": 9,
    "key_id": "key_1",
    "fallback_attempts": 0
  }
}
```
| Field | Meaning |
|---|---|
| session_id | Echo of the request value |
| reply | Assistant text, non-empty |
| resolution_source | Exactly `LIVE_INFERENCE` or `LLM_CACHE` |
| model | LiteLLM model name that produced the reply (provider-prefixed, e.g. `groq/openai/gpt-oss-20b`). Use the configured LiteLLM model string, **not** `response.model`, which drops the provider prefix. |
| latency_ms | Integer; server-measured time for this request, in ms |
| input_tokens | Prompt tokens of **this request** (system + history + new message) |
| output_tokens | Completion tokens of this reply |
| key_id | `key_1`, `key_2`, … for the key that answered; **`null` on LLM_CACHE**; never the key itself |
| fallback_attempts | Number of keys that failed before this one succeeded; **0 on LLM_CACHE** |

**`GET /sessions/{session_id}/messages`**: the stored history of one session from SQLite, oldest first, **without the system prompt**.
```json
{
  "session_id": "s-7f3a",
  "messages": [
    { "role": "user", "content": "What is the capital of France?" },
    { "role": "assistant", "content": "The capital of France is Paris.",
      "metadata": { "resolution_source": "LIVE_INFERENCE", "input_tokens": 42, "output_tokens": 9 } }
  ]
}
```
User messages carry no `metadata` key (or `null`). Assistant messages include the full stored metadata object, which contains at least the three fields shown.

### 1.3 Errors
Every error is JSON with a `detail` field. **Never** return a stack trace or an HTML error page. A request that fails (422 or 503) **is not added to the session history**.
| Case | Status | Body |
|---|---|---|
| Missing or empty message/session_id, message over 4000 chars, malformed JSON | 422 | `{"detail": ...}` (FastAPI default shape is fine) |
| Every key failed (rate-limited, quota exhausted or 5xx) | 503 | `{"detail": "all providers unavailable"}` (exact string) |
| Unknown session in GET /sessions/... | 404 | `{"detail": "session not found"}` (exact string) |

Also add a catch-all exception handler that returns `500 {"detail": "internal server error"}` without leaking internals. Unknown routes return FastAPI's JSON 404.

### 1.4 Behavior rules
- **Cache key** = model + the full message list sent to the LLM (system prompt + history + new message). The same first message sent in a **new session** must return `LLM_CACHE` the second time. A different message must **never** come from cache. **Exact-match only. No semantic cache.**
- **Cache hit** returns the identical reply text. Tokens are the usage stored with the cached response; `key_id` is null and `fallback_attempts` is 0.
- **Multi-turn**: history is stored in SQLite per `session_id`. The client sends only the new message.
- **Tokens** are per request, never a running total. Two equal-length first messages in two new sessions report about the same `input_tokens`.
- **Latency** is an integer in ms. A cache hit must be faster than a live call.

### 1.5 Failover test hook: `X-Simulate-Failure`
`POST /chat` accepts an `X-Simulate-Failure` request header that makes chosen keys fail **inside that one request only**:
```
X-Simulate-Failure: key_1=429
X-Simulate-Failure: key_1=429,key_2=500
X-Simulate-Failure: *=429
```
- Format: comma-separated `<key_id>=<status>` pairs; `*` means every key. Allowed statuses: 429, 500, 502, 503.
- Effect: each listed key behaves as if its provider returned that status, for this request only.
- It is honored only when `ENABLE_FAILURE_SIMULATION=true`, which **must be set on the deployed backend**.

| Header (2 keys configured) | Expected |
|---|---|
| `key_1=429` | 200, `key_id = key_2`, `fallback_attempts = 1` |
| `key_1=500` | 200, `key_id = key_2`, `fallback_attempts = 1` |
| `*=429` | 503 `{"detail": "all providers unavailable"}`, nothing added to history |

A simulated request must return a **real answer**, never an error message wrapped in a 200.

Parsing policy (document it in the README): trim whitespace and ignore case in the header name. A specific key entry overrides `*`. Malformed pairs, unknown key ids and unsupported statuses are ignored and logged at WARNING; they never cause a 500. When the flag is off, the header is ignored entirely.

### 1.6 Frontend contract (`data-testid`s are mandatory)
| data-testid | Element | Content |
|---|---|---|
| `chat-input` | text input or textarea | **Empty after sending** |
| `send-button` | send button | Enter may also send |
| `assistant-message` | container of each assistant reply | reply text plus the meta elements below |
| `meta-source` | inside each assistant-message | text is **exactly** `LIVE_INFERENCE` or `LLM_CACHE` |
| `meta-model` | inside each assistant-message | the model value |
| `meta-latency` | inside each assistant-message | e.g. `812 ms` |
| `meta-input-tokens` | inside each assistant-message | the input_tokens integer only |
| `meta-output-tokens` | inside each assistant-message | the output_tokens integer only |
| `system-prompt-input` | optional system prompt field | build this feature, so include it |

- A **new session starts on each page load** (`"s-" + crypto.randomUUID()`).
- Errors (422, 503, network) show as a **readable message in the chat**, never a blank screen.
- The deployed frontend talks to the deployed backend, and the backend allows the frontend origin (CORS).
- Put labels **outside** the test-id elements. Example: `<span>in </span><span data-testid="meta-input-tokens">42</span>`. Never put icons, emoji or labels inside `meta-source`, `meta-input-tokens` or `meta-output-tokens`.
- **Error bubbles must NOT use `data-testid="assistant-message"`.** Use `data-testid="error-message"`. A grader that iterates over assistant-messages expects valid meta elements in every one.

### 1.7 Docker, Makefile, env (mandatory)
- `make up` starts the whole stack: backend on http://localhost:8000, frontend on http://localhost:3000. `make down` stops it.
- Multi-stage Dockerfiles for backend and frontend, with small runtime images.
- Root `docker-compose.yml` runs both services, with a **named volume mounted at `/data`** for the SQLite file.
- All configuration comes from environment variables. `.env.example` lists **every** variable with placeholder values.

Required backend env vars (add more as needed):
| Variable | Meaning |
|---|---|
| `LLM_API_KEYS` | Comma-separated key pool, at least 2 keys. Key ids are `key_1`, `key_2`, … in this order. |
| `DATABASE_URL` | SQLite location, e.g. `sqlite:////data/app.db` |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `ENABLE_FAILURE_SIMULATION` | `true` enables the failover test hook |

### 1.8 Repository structure and README
```
/backend
/frontend
docker-compose.yml
Makefile
.env.example
README.md
```
README headings, exactly these, in this order:
1. Live URLs
2. Run locally: with Docker and without
3. Design decisions: key fallback, caching, SQLite session storage
4. Verification: how to see failover, cache hits and per-message token usage
5. Setup Used: your editor and AI tools
6. Bonus features (if any): what you added beyond the requirements and why

### 1.9 Scoring (10 points)
System resilience · Caching accuracy · Token usage tracking · Multi-turn state · Full-stack deployment · Code quality, security and docs. Partial credit is possible, but aim for full marks on every line.


## 12. Inputs the human will provide (ask for them when needed)
- `LLM_API_KEYS`: **two Groq API keys** (https://console.groq.com/keys). Ideally create them in two different Groq accounts, so real rate limits fail over too. Two keys from one account are acceptable; the simulated failover demo works either way.
- Confirmation that the walking-skeleton call succeeded with `groq/openai/gpt-oss-20b`.
- CLI logins: `gh`, `railway`, `vercel` (or the human does the dashboard steps).
- The final demo video link, for the README.

No dataset is needed. This project has no training or retrieval data, and every test uses LiteLLM `mock_response`.

Begin now: print your plan and task list, then scaffold the repo.

---
**End of PART 1.** Follow the instruction at the top of this part.
