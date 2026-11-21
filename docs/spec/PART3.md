# Vani build spec: PART 3 of 4: Frontend, Docker, Compose, Makefile

Save this verbatim to `docs/spec/PART3.md`. FAST MODE (PART 1) still applies: from §10, build only the Failover Lab dropdown. Build the frontend to the exact `data-testid` contract, both Dockerfiles, `docker-compose.yml`, the `Makefile` and `.env.example`. Verify that `make up` works (backend :8000, UI :3000, UI talks to backend, including with the X-Simulate-Failure header), then commit. Reply "Ready for PART 4".

---

## 5. Frontend specification

**Stack:** Vite + React 18 + TypeScript + Tailwind CSS. Use `react-markdown` + `remark-gfm` for replies and `lucide-react` icons outside the test-id elements. Keep the bundle lean.

### 5.1 Core UX (contract first)
- Header: "Vani" wordmark, a short tagline ("resilient LLM chat · key failover · response caching"), a `HealthBadge` (polls `/health` every 30 s and shows key count, cache on/off and simulation on/off), the current session id with a copy button, and a **New chat** button that generates a fresh session id and clears the view.
- Message list: user bubbles on the right. Assistant bubbles (`data-testid="assistant-message"`) on the left, with the rendered reply and a compact **MetaBar** underneath:
  - source pill: `<span data-testid="meta-source">LIVE_INFERENCE</span>`, colour-coded with a CSS class only (e.g. green for cache, blue for live)
  - `<span data-testid="meta-model">groq/openai/gpt-oss-20b</span>`
  - `<span data-testid="meta-latency">812 ms</span>`
  - `in <span data-testid="meta-input-tokens">42</span> · out <span data-testid="meta-output-tokens">9</span>`
  - `key_2`, plus "↻ 1 fallback" when `fallback_attempts > 0`, shown outside the test-id elements
- Composer: a textarea with `data-testid="chat-input"`. **Enter sends, Shift+Enter adds a newline.** Clear the input immediately on send. The send button has `data-testid="send-button"` and is disabled while the input is blank or a request is in flight. Show a typing indicator while pending.
- System prompt: a collapsible panel with a textarea `data-testid="system-prompt-input"`. Send its value as `system_prompt` on every request while it is non-blank. Offer 3–4 preset chips (e.g. "Concise tutor", "Hinglish buddy", "Code reviewer", "Default"). Note in the UI that changing it creates a different cache key.
- Errors: an `ErrorBubble` with `data-testid="error-message"` and readable text. For 503: "All providers are unavailable right now (every API key failed). Your message wasn't saved; try again." For 422: show the first validation message in plain English. For network errors: "Can't reach the server." Include a **Retry** button that resends the same text. Never a blank screen; wrap the app in an error boundary.
- Accessibility: labelled controls, focus returns to the input after send, visible focus rings, `aria-live="polite"` on the message list.
- Responsive down to 360 px. Include dark mode (follows the system setting, with a toggle).

### 5.2 API base URL resolution (works on Vercel and in Docker)
`const API_BASE = window.__VANI_CONFIG__?.API_BASE_URL || import.meta.env.VITE_API_BASE_URL || "http://localhost:8000"`.
- `public/config.js` contains `window.__VANI_CONFIG__ = {}` by default and is loaded in `index.html` before the bundle.
- In the Docker image, `docker-entrypoint.sh` rewrites `/usr/share/nginx/html/config.js` from the `API_BASE_URL` env at container start. This is runtime config via env vars, so no rebuild is needed.
- On Vercel, set `VITE_API_BASE_URL` at build time.

### 5.3 Bonus UI (only after the contract works; see Section 10)
`FailoverLab`, `SessionInspector` and the session stats strip. Keep all of them **outside** the `assistant-message` containers.


## 6. Docker, Compose, Makefile

### 6.1 Backend Dockerfile (multi-stage)
- Stage `builder`: `python:3.12-slim`; create a venv at `/opt/venv`; `pip install --no-cache-dir -r requirements.txt`.
- Stage `runtime`: `python:3.12-slim`; copy `/opt/venv` and `app/`; `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH=/opt/venv/bin:$PATH`; create `/data`; add a `HEALTHCHECK` that requests `/health` with python urllib (curl isn't in slim). Set `CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers"]`.
- `soundfile` needs `libsndfile` at runtime only if actually imported for audio. Check whether `import soundfile` works in slim (wheels bundle libsndfile on most platforms). If not, `apt-get install -y --no-install-recommends libsndfile1` in the runtime stage.
- `.dockerignore`: `.venv`, `__pycache__`, `tests` (optional), `.env`, `*.db`.

### 6.2 Frontend Dockerfile (multi-stage)
- Stage `build`: `node:20-alpine`; `npm ci`; `npm run build`.
- Stage `runtime`: `nginx:1.27-alpine`; copy `dist/`, `nginx.conf` (SPA fallback `try_files $uri /index.html`, gzip, cache headers for assets, **no cache** for `config.js`), and `docker-entrypoint.sh`. Listen on **3000**.

### 6.3 `docker-compose.yml` (repo root)
```yaml
services:
  backend:
    build: ./backend
    env_file: .env
    environment:
      DATABASE_URL: sqlite:////data/app.db
      CACHE_DIR: /data/litellm_cache
      ALLOWED_ORIGINS: ${ALLOWED_ORIGINS:-http://localhost:3000}
    ports: ["8000:8000"]
    volumes: ["vani_data:/data"]
    restart: unless-stopped
  frontend:
    build: ./frontend
    environment:
      API_BASE_URL: ${API_BASE_URL:-http://localhost:8000}
    ports: ["3000:3000"]
    depends_on: [backend]
volumes:
  vani_data:
```

### 6.4 Makefile
Targets: `up` (copies `.env.example` to `.env` with a loud warning if `.env` is missing, then `docker compose up -d --build` and prints both URLs), `down`, `logs`, `ps`, `restart`, `test` (backend pytest in a venv or container), `lint`, `smoke` (runs `scripts/smoke_test.py` against `BASE_URL`, default `http://localhost:8000`), `clean` (`down -v`, with a confirmation prompt). Use tabs, and keep `make up` working on Linux and macOS.

### 6.5 `.env.example`
List every backend and frontend variable from Sections 4.1 and 5.2 with placeholder values (`LLM_API_KEYS=your_key_1,your_key_2`). `.gitignore` must exclude `.env`, `*.db`, `data/` and `litellm_cache/`.


## 10. Bonus features (in priority order, only after the core is green)

Each one must stay within the contract: no new endpoints, no renamed fields, only additive response fields.

1. **Failover Lab** (frontend): a small panel with toggles such as "Simulate key_1 = 429 / 500 / 502 / 503", "Fail all keys (*=429)" and "Custom header", applied to the **next** message only. It sends `X-Simulate-Failure` and shows the outcome. This turns the demo video into three clicks.
2. **Attempt trace** in metadata and UI: `attempts: [{key_id:"key_1", status:429, simulated:true}]`, shown as a tiny timeline in the MetaBar ("key_1 ✗429 → key_2 ✓").
3. **Session Inspector** drawer: calls `GET /sessions/{id}/messages` and shows exactly what SQLite stored (proves persistence and the history endpoint in the demo).
4. **Cost and savings**: `cost_usd` per live reply via `litellm.completion_cost` (guarded), plus a client-side "saved by cache" counter in a session stats strip in the header: cache hit rate, average live vs cache latency. Label it clearly as **session stats**, kept away from per-message tokens.
5. **Health badge** and **dark mode** (already in §5.1).
6. **Model-diverse failover**: `LLM_MODELS` lets key_2 use a different model (e.g. `groq/openai/gpt-oss-120b`), or another provider entirely, such as OpenRouter. Document that the cache is shared only between keys of the same model.
7. **Structured logs** with a request id (`X-Request-ID` echoed in responses), plus key redaction.
8. **CI** and a **Playwright** e2e test.

**Do NOT build:**
- Streaming. The contract is a single JSON response.
- Semantic or embedding caching. It violates "a different message must never come from cache".
- Auth or login walls. The URLs must be public.
- Extra public endpoints. Keep FastAPI docs disabled by default.
- Rate limiting on your own API, which could block the grader.
- Redis, Postgres or any external store.

---
**End of PART 3.** Follow the instruction at the top of this part.
