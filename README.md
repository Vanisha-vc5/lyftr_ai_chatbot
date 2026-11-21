# Vani

**Vani** (Sanskrit/Hindi for "speech, voice") is a resilient LLM chat app. It keeps several Groq API keys in a pool and fails over between them in order, caches responses with an exact-match cache, and stores multi-turn sessions in SQLite.

- **Stack:** React (Vite, TypeScript, Tailwind) → FastAPI → LiteLLM Router (Groq `openai/gpt-oss-20b`) → SQLite
- **API:** exactly three endpoints: `GET /health`, `POST /chat`, `GET /sessions/{session_id}/messages`

## Live URLs

| What | URL |
|---|---|
| Frontend (Vercel) | `<FRONTEND_URL>` |
| Backend (Railway) | `<BACKEND_URL>` |
| Health check | `<BACKEND_URL>/health` |
| Repository | `<REPO_URL>` |
| Demo video | `<DEMO_VIDEO_URL>` |

The deployed backend has `ENABLE_FAILURE_SIMULATION=true`, so the `X-Simulate-Failure` header works on the live URL.

## Run locally: with Docker and without

### With Docker (recommended)

```bash
cp .env.example .env          # then put two real Groq keys in LLM_API_KEYS
make up                       # backend http://localhost:8000, frontend http://localhost:3000
make smoke                    # walks the whole API contract against localhost
make down                     # stop (history stays in the vani_data volume)
```

Other targets: `make logs`, `make ps`, `make restart`, `make test` (pytest in a Python 3.12 container), `make lint`, and `make clean` (deletes the volume after asking for confirmation). If `.env` is missing, `make up` copies `.env.example` and prints a warning. If port 3000 or 8000 is already in use, run `FRONTEND_PORT=3001 ALLOWED_ORIGINS=http://localhost:3001 make up`; `BACKEND_PORT` works the same way.

The frontend image reads `API_BASE_URL` when the container starts and writes it to `/config.js`, so the same image works against any backend without a rebuild.

### Without Docker

Backend (Python 3.12 recommended):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
export LLM_API_KEYS=gsk_key1,gsk_key2 DATABASE_URL=sqlite:///./data/app.db CACHE_DIR=./data/litellm_cache ENABLE_FAILURE_SIMULATION=true
uvicorn app.main:create_app --factory --reload --port 8000
pytest -q                                                 # 44 tests, no network (LiteLLM mock_response)
```

Frontend (Node 20+):

```bash
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev       # http://localhost:3000
```

All configuration comes from environment variables. [`.env.example`](.env.example) lists every variable and documents each one.

## Design decisions: key fallback, caching, SQLite session storage

### Key fallback

- **One LiteLLM model group per key** (`key_1`, `key_2`, …), each with `model_info.id = key_N`. Every request targets `key_1`, and the Router's `fallbacks=[{"key_1": ["key_2", …]}]` tries the other keys in strict order. The key that answered is read from `response._hidden_params["model_id"]`. `fallback_attempts` comes from LiteLLM's `x-litellm-attempted-fallbacks` header; if that header is missing, the key's position in the order is used instead.
- **`num_retries=0`:** a failing key is not retried; the request moves straight to the next key, which is faster and deterministic.
- **`disable_cooldowns=True`:** each request starts from `key_1`, so behaviour stays the same from one request to the next. A simulated failure must never bench a real key for later requests.
- **Failure simulation goes through the real failover path.** `X-Simulate-Failure` is parsed into a per-request plan held in a `ContextVar`. A LiteLLM `CustomLogger.async_pre_call_deployment_hook` raises a genuine `litellm.RateLimitError` (or `InternalServerError`, `BadGatewayError`, `ServiceUnavailableError`) for the chosen deployment. The Router handles it exactly as it would a real provider error, so the code under test is LiteLLM's own fallback logic, not a mock of it.
- **Simulated requests bypass the cache** (`cache={"no-cache": True, "no-store": True}`). Without this, `key_1=429` on a prompt that is already cached would return a cache hit instead of a key_2 answer, and `*=429` would return a cached 200 instead of a 503. `no-store` also stops a simulated answer from overwriting a cached reply.
- When every key fails, the Router raises an error. We map it, and any other upstream failure including an empty reply, to `503 {"detail": "all providers unavailable"}`. Provider error text is never echoed, because it can contain key fragments.
- **Header parsing policy:** the header name is case-insensitive and whitespace is trimmed. Format is comma-separated `key_id=status` pairs; `*` means every key, and a specific key entry overrides `*`. Allowed statuses are 429, 500, 502 and 503. Malformed pairs, unknown key ids and unsupported statuses are ignored and logged at WARNING; they never cause a 500. When `ENABLE_FAILURE_SIMULATION` is not `true`, the header is ignored entirely.

### Caching

- We use **LiteLLM's built-in disk cache** (`Cache(type="disk")`) stored on the `/data` volume, so cached replies survive restarts and redeploys. LiteLLM is **pinned to 1.108.14** because the disk backend was removed in 1.109. `CACHE_TYPE=local` switches to LiteLLM's in-memory cache.
- **Cache key:** an exact match on model (the caching group) + the full message list (system prompt + history + new message) + fixed generation params (`temperature`, `max_tokens`, `reasoning_effort`, all constant and set by environment variables). There is no semantic cache, so a different message can never be served from the cache. The system prompt contains no dates or ids, so the same first message in a new session hits the cache.
- **`caching_groups` per model:** a reply produced by key_2 is found again when the next request starts at key_1, as long as both keys use the same model. Keys that use different models (`LLM_MODELS`) do not share cache entries.
- **`max_messages=None`:** LiteLLM 1.108.14 defaults to `max_messages=4`, which silently skips the cache for any conversation longer than 4 messages. We turn that limit off so later turns are cached too.
- **On a cache hit** we return the stored text and the stored `usage`, with `key_id=null` and `fallback_attempts=0`.
- **Warm-up:** at startup the app makes two `mock_response` calls (one stores, one hits). These load LiteLLM's lazily imported cache-hit code. Without them, the first real cache hit takes about 700 ms; after warm-up a hit takes a few ms. The pinned `fastapi`, `orjson` and `soundfile` packages are needed by that same lazy import path.
- **Closing the async-write race:** LiteLLM writes to the cache in a background callback, so two identical requests sent back to back could both go live. We hold a **single-flight `asyncio.Lock` per prompt fingerprint**. After a live answer, a **cache-landing probe** (a `mock_response` call with `no-store`, which uses LiteLLM's own key computation) polls every 5 ms, for at most 300 ms, until the entry can be read. Only then is the lock released. Concurrent identical first messages therefore produce exactly one `LIVE_INFERENCE`, and the rest are `LLM_CACHE` with identical text; a test covers this.
- Cache TTL is `CACHE_TTL_SECONDS`, 24 h by default.

### SQLite session storage

- Two tables: `messages(id, session_id, role, content, metadata JSON, created_at)` with an index on `(session_id, id)`, and `sessions(session_id, system_prompt, created_at, updated_at)`. The database runs in WAL mode with `busy_timeout`, accessed through stdlib `sqlite3` on a worker thread.
- The user message and the assistant message (with its full metadata JSON) are written in **one transaction, and only after the LLM succeeds**. A request that fails with 422 or 503 writes nothing.
- A **per-session `asyncio.Lock`** keeps concurrent sends to one session from interleaving the history. The most recent `HISTORY_MAX_MESSAGES` messages (20) go to the LLM; the full history stays stored. `GET /sessions/{id}/messages` never includes the system prompt.
- A `system_prompt` sent with a request overrides the default for that session and is stored, so later turns reuse it.
- The database lives at `/data/app.db` on a named Docker volume locally and on a Railway volume in production. The app runs **one uvicorn worker** on purpose, because the in-process locks and the single SQLite writer assume a single process.

### Metrics

`latency_ms` is server-measured, from request arrival until just before the database commit, so the value stored in history equals the value returned. `input_tokens` and `output_tokens` are per request, taken from the provider's `usage`. gpt-oss is a reasoning model, so `output_tokens` (`completion_tokens`) includes reasoning tokens. `model` is the configured LiteLLM model string, for example `groq/openai/gpt-oss-20b`.

### Security

- Keys exist only in environment variables and memory, and responses only ever show key ids (`key_1`).
- A logging filter redacts configured keys and any `gsk_…` pattern.
- Every error is JSON (`detail`), with a catch-all `500 {"detail":"internal server error"}`.
- FastAPI docs are off by default (`ENABLE_API_DOCS`), so only the three contract endpoints are exposed.
- The app runs as a non-root user (`vani`, uid 10001). Railway mounts volumes owned by root, so the entrypoint starts as root only long enough to `chown /data`, then drops privileges with `setpriv`. No platform-specific setting is needed.

### Trade-offs and what would change at scale

- **Multiple workers or hosts:** the in-process locks and the disk cache only work within one process. Scaling out would need Redis (cache, single-flight locks and session locks) and Postgres for history.
- **Cooldowns:** for real traffic I would enable cooldowns for real 429s so a rate-limited key is skipped for a while, and keep them off for simulated failures.
- **Shared quota:** Groq rate limits apply per organization, so for real (not simulated) 429 failover the two keys should come from two different Groq accounts.

## Verification: how to see failover, cache hits and per-message token usage

Set `B=<BACKEND_URL>` (or `B=http://localhost:8000`).

```bash
# Health: 2 keys, cache on
curl -s $B/health

# Live inference (first time this exact message is seen)
curl -s -X POST $B/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-demo-1","message":"What is the capital of France?"}'

# Cache hit: same first message, NEW session id -> LLM_CACHE, identical reply, key_id null, lower latency
curl -s -X POST $B/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-demo-2","message":"What is the capital of France?"}'

# Multi-turn: follow-up in session 1 (input_tokens grows because the history is sent)
curl -s -X POST $B/chat -H 'Content-Type: application/json' \
  -d '{"session_id":"s-demo-1","message":"What did I just ask you?"}'

# Failover: key_1 fails -> answered by key_2, fallback_attempts 1
curl -s -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"s-demo-3","message":"Say hello in French"}'
curl -s -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: key_1=500' \
  -d '{"session_id":"s-demo-3","message":"Now in Spanish"}'

# Every key fails -> 503 {"detail":"all providers unavailable"}, nothing stored
curl -s -i -X POST $B/chat -H 'Content-Type: application/json' -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"s-demo-1","message":"this is not saved"}'

# History (oldest first, metadata on assistant rows, no system prompt)
curl -s $B/sessions/s-demo-1/messages
curl -s $B/sessions/does-not-exist/messages        # 404 {"detail":"session not found"}

# Validation -> 422
curl -s -X POST $B/chat -H 'Content-Type: application/json' -d '{"session_id":"s-x","message":"   "}'
curl -s -X POST $B/chat -H 'Content-Type: application/json' -d '{"message":"no session"}'
curl -s -X POST $B/chat -H 'Content-Type: application/json' -d '{oops'
```

Or run the whole contract in one go: `make smoke BASE_URL=$B` (or `python scripts/smoke_test.py $B`) prints a ✅/❌ table and exits non-zero on any failure.

**In the UI:**
- Each assistant reply has a meta bar. It shows the source pill (`LIVE_INFERENCE` in blue, `LLM_CACHE` in green), the model, the latency, `in N · out M` tokens, the key that answered, and `↻ 1 fallback` when failover happened.
- To see a cache hit, click **New chat** and send the same first message again.
- The **Failover Lab** dropdown sends `X-Simulate-Failure` on the next message only. Choosing `key_1 = 429` shows a key_2 answer; choosing **Fail all keys** shows a readable 503 error bubble with a Retry button.
- `scripts/ui_check.py <FRONTEND_URL>` drives all of this in headless Chromium and checks every `data-testid`.

## Setup Used: your editor and AI tools

- Editor: `<EDITOR>`
- AI tools: `<AI_TOOLS>`
- Model: `<MODEL>`

## Bonus features (if any): what you added beyond the requirements and why

- **Failover Lab** (UI dropdown, plus a custom header option): turns the failover demo into a few clicks and exercises the real Router fallback path.
- **Extra metadata fields** (additive only): `cache_key` (fingerprint prefix), `cost_usd` (from a guarded `litellm.completion_cost`, 0 on cache hits), `attempts` (simulated failures before the answer) and `simulated_failures`. They make each answer auditable without changing the contract fields.
- **Persistent disk cache** on the volume: cached answers survive restarts and redeploys, not just the process.
- **Model-diverse failover:** `LLM_MODELS` can give key_2 a different model, for example `groq/openai/gpt-oss-120b`. The cache is shared only between keys that use the same model.
- **Key redaction in logs:** a defence in depth, so a provider error can never leak a key into the logs.
- **Smoke test and headless UI check** (`scripts/smoke_test.py`, `scripts/ui_check.py`): every contract claim can be checked against any deployment with a single command.
