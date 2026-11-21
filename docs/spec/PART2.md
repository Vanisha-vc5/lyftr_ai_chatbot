# Vani build spec: PART 2 of 4: Verified LiteLLM findings, backend spec and tests

Save this verbatim to `docs/spec/PART2.md`. Then do the walking skeleton (§2.7) if it isn't done yet, and build the **backend completely**: config, SQLite, LiteLLM Router + failure simulation + cache, the 3 endpoints, error handlers, the fast-mode pytest subset, and `scripts/smoke_test.py`. Run pytest until it's green, run the app locally and hit it with curl, then commit. Reply with the test results and "Ready for PART 3".

---

## 2. Verified technical findings. Read before writing any LiteLLM code.

These were verified hands-on against LiteLLM in a sandbox using `mock_response`, with no network calls. They are not guesses. Each one prevents a real failure.

### 2.1 Pin LiteLLM to `1.108.14`, because the disk cache was removed in 1.109+
- In LiteLLM **1.109.x and 1.110.x**, `Cache(type="disk")` raises `ValueError: Unsupported cache type 'disk'`; the disk backend was deleted. **1.108.14** still ships `caching/disk_cache.py`.
- The assignment allows "in-memory or disk". Use the **disk cache on the `/data` volume** so cached responses survive restarts. Make it configurable with `CACHE_TYPE=disk|local` (default `disk`) and `CACHE_DIR=/data/litellm_cache`.
- The **cache-hit code path in 1.108.14 lazily imports proxy-only modules**. Without these packages, the first cache hit crashes with `ModuleNotFoundError` (`fastapi`, then `orjson`, then `soundfile`). Pin:
  ```
  litellm[caching]==1.108.14
  fastapi
  uvicorn[standard]
  orjson
  soundfile
  ```
  (`litellm[proxy]` also works but roughly doubles the image size, about 640 MB vs 345 MB of site-packages. Prefer the minimal set.)
- Add a test that performs a **real cache hit through the Router** so this regression can never ship.

### 2.2 Router design that passed every case: one model group per key, plus ordered fallbacks
```python
from litellm import Router

deployments = [
    {
        "model_name": f"key_{i}",                         # one model group per key
        "litellm_params": {"model": model_for_key[i], "api_key": key, "timeout": LLM_TIMEOUT_SECONDS},
        "model_info": {"id": f"key_{i}"},                 # becomes response._hidden_params["model_id"]
    }
    for i, key in enumerate(keys, start=1)
]
router = Router(
    model_list=deployments,
    num_retries=0,                    # don't retry the same key; move to the next one
    fallbacks=[{"key_1": [f"key_{i}" for i in range(2, len(keys) + 1)]}],   # strict order key_1 → key_2 → …
    caching_groups=[tuple(ids_sharing_same_model)],   # share cache across keys that use the SAME model
    cache_responses=True,
    disable_cooldowns=True,           # a simulated failure must not bench a real key for later requests
)
# every request: await router.acompletion(model="key_1", messages=..., temperature=T, max_tokens=M, **extra)
```
Observed behaviour in the sandbox:
- Success via key_1: `_hidden_params["model_id"] == "key_1"`, `additional_headers["x-litellm-attempted-fallbacks"] == 0`.
- key_1 fails, key_2 answers: `model_id == "key_2"`, `x-litellm-attempted-fallbacks == 1`.
- Every key fails: the Router raises (e.g. `litellm.RateLimitError`). Catch it and return **503** `{"detail": "all providers unavailable"}`.
- Cache hit: `_hidden_params["cache_hit"] is True`, identical text, `usage` preserved. `model_id` still says `key_1`, so **force `key_id=None` and `fallback_attempts=0`** yourself.
- Without `caching_groups`, a reply produced by key_2 is cached under group "key_2" and misses on the next request, which starts at key_1. With `caching_groups`, it hits. Build the groups **per model**, so the cache key remains "model + messages" when keys use different models.
- **Why not one group with N deployments and `order`?** That was tried. The per-deployment hook fired twice per deployment and accounting was noisier. Use the per-key groups design above.
- Use `fallback_attempts = int(additional_headers["x-litellm-attempted-fallbacks"])`, falling back to `position(key_id) - 1` if the header is missing. Both agree because the order is deterministic.

### 2.3 Failure simulation that goes through LiteLLM's real failover path
Inject failures in a `CustomLogger.async_pre_call_deployment_hook`, driven by a per-request `ContextVar`. The hook raises a genuine LiteLLM exception for the chosen deployment, so the **Router's own fallback logic** handles it exactly like a real provider error.
```python
import contextvars, litellm
from litellm.integrations.custom_logger import CustomLogger

SIM_PLAN: contextvars.ContextVar[dict[str, int]] = contextvars.ContextVar("SIM_PLAN", default={})
_EXC = {429: litellm.RateLimitError, 500: litellm.InternalServerError,
        502: litellm.BadGatewayError, 503: litellm.ServiceUnavailableError}

class FailureSimulator(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs, call_type):
        info = kwargs.get("model_info") or (kwargs.get("litellm_metadata") or {}).get("model_info") or {}
        key_id, plan = info.get("id"), SIM_PLAN.get()
        status = plan.get(key_id, plan.get("*"))
        if status:
            # record (key_id, status, simulated=True) in a per-request attempts ContextVar for the trace
            raise _EXC[status](message=f"simulated {status} for {key_id}",
                               llm_provider="simulated", model=kwargs.get("model", "unknown"))
        return kwargs

litellm.callbacks = [FailureSimulator()]
```
Check each exception class's constructor signature in 1.108.14. If one needs extra args (e.g. `response=`), adapt. Verified in the sandbox: 429, 500, 502 and 503 all trigger fallback to key_2, and `*=429` makes the Router raise.

**Critical: simulated requests must bypass the cache.** It was verified that when a prompt is already cached, `key_1=429` without bypass returns **a cache hit via key_2**. The grader expects `key_id=key_2`, and for `*=429` it expects **503**, not a cached 200. So whenever a valid simulation plan is active, pass `cache={"no-cache": True, "no-store": True}`. `no-store` also prevents a simulated request from overwriting the text of a cached reply.

### 2.4 Cache-hit latency: warm up at startup
The first cache hit in a fresh process took **~700 ms**, slower than a live call, because of lazy imports. After warm-up it took **~2 ms**. In the FastAPI lifespan, after building the Router:
```python
for _ in range(2):  # 1st call stores, 2nd call hits; this loads the whole cache-hit import chain
    await router.acompletion(model="key_1", messages=[{"role": "user", "content": "__vani_warmup__"}],
                             mock_response="ok", temperature=T, max_tokens=M)
    await asyncio.sleep(0.1)
```
`mock_response` never calls a provider and costs nothing.

### 2.5 The cache write is asynchronous, so close the race deterministically
LiteLLM writes the cache in a background success callback. It was verified that two identical requests sent back to back **both** went live (the second fired before the write landed), and the second write can overwrite the stored text. The grader may send the same first message in a new session immediately. Fix it like this:
1. **Single-flight per prompt.** Compute an app-level fingerprint `sha256(json.dumps({"group": caching_group, "messages": messages, "temperature": T, "max_tokens": M}, sort_keys=True))` and hold an `asyncio.Lock` per fingerprint (a `WeakValueDictionary` or a bounded dict) around the LLM call.
2. **After a LIVE response, wait until the entry is readable before releasing the lock.** Probe with LiteLLM's own key computation:
   ```python
   PROBE = "__vani_probe_miss__"
   async def cache_has(messages) -> bool:
       r = await router.acompletion(model="key_1", messages=messages, temperature=T, max_tokens=M,
                                    mock_response=PROBE, cache={"no-store": True})
       return r._hidden_params.get("cache_hit") is True and r.choices[0].message.content != PROBE
   ```
   On a miss this returns the sentinel instantly. It never calls a provider and never stores anything because of `no-store`. Poll every ~5 ms, for at most ~300 ms. If time runs out, continue; never fail the user request over this. Make sure the simulation hook ignores probe calls (clear `SIM_PLAN` for the probe). Verified in the sandbox: the entry became readable within 2 probes.
3. The test suite must include "two identical first messages in two new sessions, sent concurrently and back to back → exactly one LIVE_INFERENCE and the rest LLM_CACHE with identical text."

### 2.6 Other verified facts
- `cache_hit` is `None` (not `False`) on live calls. Test with `is True`.
- The cache key includes `temperature`, `max_tokens` and other params. **Keep them fixed per deployment** (env-configured) so the same messages always produce the same key. Never put timestamps, dates or session ids into the system prompt or the messages, because that would break cross-session cache hits.
- The `model` group value used in the cache key is the caching group or model group, so the cache is shared across keys of the same model.
- Set `litellm.suppress_debug_info = True` and lower LiteLLM's loggers to WARNING. Router fallback failures log ERROR lines that would spam the logs during simulation.
- Run **one uvicorn worker**. The in-process locks and the SQLite writer assume a single process. Say so in the README.

### 2.7 Provider and platform facts (researched November 2026)
- **Provider: Groq** (free tier, OpenAI-compatible, very fast, no card needed). LiteLLM model strings use the `groq/` prefix.
- **Groq retired `llama-3.1-8b-instant` and `llama-3.3-70b-versatile` on 16 Aug 2026. Do not use them.** Current free-tier text models: `openai/gpt-oss-20b`, `openai/gpt-oss-120b` and `qwen/qwen3.8-27b` (each roughly 30 RPM, 1K requests/day, 8K tokens/min, 200K tokens/day).
- Default **`LLM_MODEL=groq/openai/gpt-oss-20b`** (fastest). Fallback option: `groq/openai/gpt-oss-120b`. Verified: LiteLLM 1.108.14 has both in its model/cost map and supports `reasoning_effort` for them, and the full Router failover and cache prototype passed with these model strings.
- gpt-oss models are **reasoning models**. Set **`LLM_REASONING_EFFORT=low`** by default (constant, so the cache key stays stable) for speed and lower token use. Keep `LLM_MAX_TOKENS=1024`, because reasoning tokens count against max_tokens; too small a value can produce an empty `content`. If `content` is empty, treat the request as a provider failure (503). Never return an empty reply. `output_tokens` = `usage.completion_tokens`, which includes reasoning tokens; mention this in the README.
- Set `litellm.drop_params = True` so any parameter Groq doesn't accept is dropped, not raised.
- **Groq rate limits apply per organization.** Two keys from one Groq account share one quota. That is fine for the graded demo, because `X-Simulate-Failure` fakes the failures, but for *real* 429 failover the human should create key_2 in a second Groq account. Support mixing via `LLM_MODELS` (e.g. key_2 on `groq/openai/gpt-oss-120b`).
- 8K tokens/min is small. Keep `HISTORY_MAX_MESSAGES=20` by default, keep the default system prompt short (2–3 sentences), and don't fire bulk requests in tests against the real API. Tests use `mock_response` only.
- **Walking skeleton first:** before writing the app, run a 10-line script with the human's real key: `litellm.completion(model="groq/openai/gpt-oss-20b", messages=[...], api_key=..., reasoning_effort="low", max_tokens=256)`. Print the content and usage. If it fails, fix the model ID or key right then (check https://console.groq.com/docs/models), before building anything else.
- Groq free tier: OpenAI-compatible, very fast, with per-model RPM/RPD limits. A LiteLLM model string looks like `groq/llama-3.3-70b-versatile`. Confirm the current model list in the Groq console.
- LiteLLM's cost map may not know brand-new model names. Wrap `litellm.completion_cost` in try/except, and never let cost calculation break a request.
- **Backend host: Railway.** It supports Docker deploys with persistent **volumes** (trial: one-time $5 credit for 30 days; then a free plan with $1/month credit and a small volume). Railway mounts volumes **owned by root**. Either run the container as root, or keep a non-root user and set the service variable `RAILWAY_RUN_UID=0`. Without this, SQLite fails with permission or "readonly database" errors. Railway injects `PORT`, so the server must bind `0.0.0.0:${PORT:-8000}`.
- **Hosts NOT to use for the backend:** Render free (ephemeral filesystem, no disks on the free tier, spins down after 15 minutes, so SQLite history would vanish); Koyeb free (volumes can't attach to the free instance); Fly.io (no free tier for new accounts); Hugging Face Spaces (Docker Spaces now need a paid plan, and legacy persistent storage was sunset). If Railway is unavailable, the fallback is Render Starter with a persistent disk (paid).
- **Frontend host: Vercel** (free Hobby plan), with root directory `frontend` and env `VITE_API_BASE_URL`.


## 3. Architecture

```
Browser (React, Vite, TS) ──HTTPS──► FastAPI (uvicorn, 1 worker)
  data-testid contract                 ├─ validation (Pydantic v2) → 422
  Failover Lab (sends header)          ├─ ChatService
                                       │    ├─ SessionRepo (SQLite, WAL) ── /data/app.db
                                       │    ├─ per-session lock + per-prompt single-flight
                                       │    └─ LLMGateway (LiteLLM Router)
                                       │          ├─ key_1 → key_2 → … ordered fallbacks
                                       │          ├─ FailureSimulator (pre-call hook, ContextVar)
                                       │          └─ LiteLLM disk cache ── /data/litellm_cache
                                       └─ error handlers → JSON only
```

### 3.1 Backend layout
```
backend/
  app/
    main.py            # app factory, lifespan (DB init, router build, warm-up), CORS, exception handlers
    config.py          # pydantic-settings Settings; parse LLM_API_KEYS, LLM_MODELS, DATABASE_URL, etc.
    schemas.py         # ChatRequest, ChatResponse, Metadata, HistoryResponse, HealthResponse
    db.py              # sqlite3 connection helpers, schema creation, WAL, migrations-lite
    repository.py      # SessionRepository: get_history, get_system_prompt, save_exchange (single txn)
    llm/
      gateway.py       # Router construction, complete(), cache probe, metadata extraction
      simulation.py    # header parser + FailureSimulator + ContextVars
      prompts.py       # DEFAULT_SYSTEM_PROMPT (static text, no dates)
    services/chat.py   # orchestration: validate → load history → build messages → call → persist
    logging.py         # structured JSON logs, request id, key redaction filter
  tests/               # pytest (see Section 7)
  pyproject.toml or requirements.txt (+ requirements-dev.txt)
  Dockerfile
```

### 3.2 Frontend layout
```
frontend/
  src/
    main.tsx, App.tsx
    api/client.ts        # fetch wrapper, typed responses, error normalisation, base URL resolution
    components/ChatWindow, MessageBubble, MetaBar, Composer, SystemPromptPanel, FailoverLab,
               HealthBadge, SessionInspector, ErrorBubble
    hooks/useChat.ts     # session id per page load, message list, send(), pending state
    types.ts
  public/config.js       # runtime config placeholder (overwritten in Docker)
  index.html, vite.config.ts, tailwind config, Dockerfile, nginx.conf, docker-entrypoint.sh
```


## 4. Backend specification

### 4.1 Config (`pydantic-settings`)
| Variable | Default | Notes |
|---|---|---|
| `LLM_API_KEYS` | required | split on commas, strip each, drop empties; ids `key_1..key_n`; log a warning if fewer than 2 |
| `LLM_MODEL` | `groq/openai/gpt-oss-20b` | default model for every key |
| `LLM_MODELS` | empty | optional comma list aligned with keys (e.g. `groq/openai/gpt-oss-20b,groq/openai/gpt-oss-120b`); a blank entry falls back to `LLM_MODEL` |
| `LLM_TEMPERATURE` | `0.3` | fixed so the cache key is stable |
| `LLM_MAX_TOKENS` | `1024` | fixed |
| `LLM_TIMEOUT_SECONDS` | `30` | per attempt |
| `LLM_REASONING_EFFORT` | `low` | passed when non-empty; fixed value keeps the cache key stable |
| `DATABASE_URL` | `sqlite:////data/app.db` | parse `sqlite:////abs` and `sqlite:///rel`; create the parent dir |
| `CACHE_TYPE` | `disk` | `disk` or `local` |
| `CACHE_DIR` | `/data/litellm_cache` | |
| `CACHE_TTL_SECONDS` | `86400` | passed as the cache ttl |
| `ALLOWED_ORIGINS` | `http://localhost:3000` | comma list; also support an optional `ALLOWED_ORIGIN_REGEX` for Vercel preview URLs |
| `ENABLE_FAILURE_SIMULATION` | `false` | **set `true` on the deployed backend** |
| `DEFAULT_SYSTEM_PROMPT` | built-in Vani persona | static text |
| `HISTORY_MAX_MESSAGES` | `20` | most recent N messages sent to the LLM (all are still stored) |
| `LOG_LEVEL` | `INFO` | |
| `ENABLE_API_DOCS` | `false` | `/docs` and `/openapi.json` off by default, because the contract says "exactly three endpoints" |
| `PORT` | `8000` | Railway injects it |

### 4.2 SQLite schema (auto-created on startup, idempotent)
```sql
CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id  TEXT NOT NULL,
  role        TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content     TEXT NOT NULL,
  metadata    TEXT,                 -- JSON string, assistant messages only
  created_at  TEXT NOT NULL         -- server time, ISO-8601 UTC
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS sessions (
  session_id    TEXT PRIMARY KEY,
  system_prompt TEXT,               -- NULL = use default
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
```
- `PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;`
- Use the stdlib `sqlite3` driver via `asyncio.to_thread`, or `aiosqlite`. Either is fine; keep it simple.
- `save_exchange(session_id, user_msg, assistant_msg, metadata, system_prompt_override)` inserts or updates the session row and inserts both messages in **one transaction**, and runs only after LLM success. Nothing is written on 422 or 503.

### 4.3 `POST /chat` flow
1. `t0 = time.perf_counter()` at the very start.
2. Validate with Pydantic v2: `session_id` is `str` with min 1 and max 128 characters (reject whitespace-only); `message` is stripped and then must be 1–4000 characters; `system_prompt` is optional, with max 4000 characters after stripping, and blank is treated as absent. Invalid JSON or wrong types produce FastAPI's default 422.
3. Parse `X-Simulate-Failure` only if `ENABLE_FAILURE_SIMULATION` is true. This produces the plan `{key_id|*: status}`.
4. Acquire a **per-session `asyncio.Lock`**, so concurrent sends in one session don't interleave history.
5. Load the session's stored system prompt and history (last `HISTORY_MAX_MESSAGES`). Effective system prompt = the request's `system_prompt` if present, else the stored one, else the default.
6. Build `messages = [{"role":"system",...}] + history + [{"role":"user","content": message}]`.
7. Call the gateway. If a simulation plan is active, set `SIM_PLAN` and pass `cache={"no-cache": True, "no-store": True}`. Otherwise use the single-flight lock and, after a LIVE result, the cache-landing probe.
8. Map exceptions: when every key fails (any `litellm` exception or timeout after the fallbacks are exhausted), return **503 `{"detail":"all providers unavailable"}`**. Also return 503 with that same detail for any other upstream LLM failure, including an empty reply. Never 500 for provider issues, and never echo provider messages, which can contain key fragments.
9. Extract the metadata:
   - `cache_hit = resp._hidden_params.get("cache_hit") is True`
   - `resolution_source = "LLM_CACHE" if cache_hit else "LIVE_INFERENCE"`
   - `key_id = None if cache_hit else resp._hidden_params["model_id"]`
   - `fallback_attempts = 0 if cache_hit else int(additional_headers.get("x-litellm-attempted-fallbacks", pos-1))`
   - `model = configured_model[resp._hidden_params["model_id"]]`. Use this on cache hits too: the hit was found in that deployment's caching group, so it is that model's response.
   - `input_tokens = resp.usage.prompt_tokens`, `output_tokens = resp.usage.completion_tokens` (ints, default 0)
   - `latency_ms = int(round((time.perf_counter() - t0) * 1000))`, computed **after** persistence, just before returning
   - Extra fields (allowed): `cache_key` (first 12 hex chars of the app fingerprint), `cost_usd` (0.0 on a cache hit), `attempts` (list of `{key_id, status, simulated}` for failed keys), `simulated_failures` (the parsed plan, or null).
10. Persist the user and assistant messages in one transaction, with the metadata JSON on the assistant row.
11. Return the response.

### 4.4 `GET /sessions/{session_id}/messages`
Return 404 `{"detail":"session not found"}` if no messages exist for that session. Otherwise return the rows ordered by `id`. User rows carry only role and content. Assistant rows carry role, content and parsed metadata.

### 4.5 `GET /health`
Run `SELECT 1` on the DB and check that the Router is built with ≥1 key. Return `{"status":"ok","keys_configured":N,"cache_enabled":bool}`. Extra fields are allowed: `failure_simulation`, `cache_type`, `version`. Never return keys.

### 4.6 CORS
`CORSMiddleware(allow_origins=ALLOWED_ORIGINS, allow_origin_regex=ALLOWED_ORIGIN_REGEX or None, allow_methods=["GET","POST","OPTIONS"], allow_headers=["Content-Type","X-Simulate-Failure"])`. The custom header triggers a preflight, so if it's not in `allow_headers` the UI's Failover Lab breaks in the browser. Expose no credentials.

### 4.7 Security and hygiene
- Keys appear only in memory and in the env. Add a logging filter that redacts anything matching a configured key, and any `gsk_[A-Za-z0-9]+` pattern (Groq key format). Never log full prompts at INFO; log lengths and hashes.
- Every exception handler returns JSON. Register handlers for `RequestValidationError` (keep the default shape), `StarletteHTTPException`, and a catch-all `Exception` that returns 500 JSON.
- Run as a non-root user locally, and document `RAILWAY_RUN_UID=0` for Railway, or run as root if you choose that; explain the choice in the README.
- Use type hints throughout. Use `ruff` for lint and format.


## 7. Tests

### 7.1 Backend pytest (no network; uses LiteLLM `mock_response`)
In tests, build the Router with `mock_response` in each deployment's `litellm_params` (e.g. `"mock_response": "Paris (key_1)"`, plus a small `mock_delay` such as 0.2 s so "cache faster than live" is measurable). Use a temp dir for the SQLite file and the disk cache. Use `httpx.AsyncClient` with `ASGITransport` against the real app factory. Required cases:
1. `/health` returns 200 with exact keys and `keys_configured == 2`.
2. First chat produces LIVE_INFERENCE, `key_id == "key_1"`, `fallback_attempts == 0`, integer latency, `input_tokens > 0`.
3. The same first message in a **new session** produces LLM_CACHE, **identical reply**, `key_id is None`, `fallback_attempts == 0`, tokens equal to the stored usage, and `latency_ms` lower than the live call's.
4. A different message is never served from cache.
5. Concurrent identical first messages in 3 new sessions produce exactly one LIVE and the rest CACHE (the single-flight from §2.5).
6. `X-Simulate-Failure: key_1=429` gives 200, `key_id == "key_2"`, `fallback_attempts == 1`, a real reply. Same for 500, 502, 503.
7. `key_1=429` on an **already cached** prompt still gives `key_id == "key_2"` and LIVE (cache bypass).
8. `*=429` gives 503 with exactly `{"detail":"all providers unavailable"}`, and the history is unchanged (verify via GET).
9. `key_1=429,key_2=500` with 2 keys gives 503.
10. The header is ignored when `ENABLE_FAILURE_SIMULATION=false`.
11. Malformed or unknown header entries are ignored, never a 500.
12. 422 cases: missing message, whitespace-only message, 4001 chars, empty session_id, 129-char session_id, malformed JSON body, wrong types. None of them write to the DB.
13. Multi-turn: the second message's `messages` list sent to the LLM includes the first exchange. Spy on the gateway, or assert that `input_tokens` grows. Sessions are isolated.
14. `system_prompt` override persists for the session, changes the cache key, and is excluded from GET history.
15. GET history for an unknown session gives 404 `{"detail":"session not found"}`. A known session returns oldest first with metadata on assistant rows only.
16. Persistence: write, re-create the app with the same DB path, and confirm the history is still there.
17. No response body ever contains a key string (scan every response in the suite).
18. Real cache hit through the Router (guards the lazy-import regression from §2.1).

### 7.2 `scripts/smoke_test.py` (stdlib or httpx; runs against any BASE_URL)
Walk the whole contract against a live backend and print a ✅/❌ table: health; live chat; cache hit in a new session (identical text, faster); failover 429→key_2; 500→key_2; `*=429`→503 with the history unchanged; 422s; 404. Exit non-zero on any failure. Run it locally and against the Railway URL before declaring done.

### 7.3 Optional frontend e2e (bonus)
Playwright test that loads the page, types, sends, waits for `assistant-message`, and asserts the test-id texts (`meta-source` is one of the two values; tokens match `^\d+$`; latency matches `^\d+ ms$`; input is empty after send).

### 7.4 CI (bonus)
GitHub Actions: ruff + pytest (backend), `npm ci && npm run build && tsc --noEmit` (frontend), and `docker compose build`.

---
**End of PART 2.** Follow the instruction at the top of this part.
