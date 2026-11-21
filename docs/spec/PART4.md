# Vani build spec: PART 4 of 4: Deploy, README, final verification

Save this verbatim to `docs/spec/PART4.md`. Push to a public GitHub repo, deploy the backend to Railway (volume at `/data`, `ENABLE_FAILURE_SIMULATION=true`) and the frontend to Vercel, fix CORS, and run the smoke test against the live backend until every row is green. Write the README (exact 6 headings, live URLs) and `docs/DEMO_SCRIPT.md`. If a CLI isn't logged in, tell me the exact command or dashboard steps and wait. Finish with the Definition of Done checklist, ticked, with evidence.

---

## 8. Deployment runbook (do it, then verify it)

Ask the human to run `gh auth login`, `railway login` and `vercel login` if those CLIs aren't authenticated. If a CLI is unavailable, give precise dashboard steps instead.

### 8.1 GitHub
Create a **public** repo (e.g. `vani-llm-chat`). Push `main`. Make sure `.env` was never committed (`git log -p | grep -i "api_key"` must find nothing real).

### 8.2 Backend on Railway
1. Create a new project from the GitHub repo. Service root directory: `backend` (Dockerfile build).
2. Add a **Volume** mounted at **`/data`**.
3. Variables: `LLM_API_KEYS`, `LLM_MODEL` (and `LLM_MODELS` if mixing providers), `DATABASE_URL=sqlite:////data/app.db`, `CACHE_TYPE=disk`, `CACHE_DIR=/data/litellm_cache`, `ENABLE_FAILURE_SIMULATION=true`, `ALLOWED_ORIGINS=<vercel url>,http://localhost:3000`, `ALLOWED_ORIGIN_REGEX=^https://.*\.vercel\.app$` (optional), and `RAILWAY_RUN_UID=0` if the image runs as non-root.
4. Generate a public domain. Verify with `curl https://<backend>/health`.
5. Restart the service and confirm that a previous session's history survives (volume persistence).

### 8.3 Frontend on Vercel
1. Import the repo, set root directory `frontend`, framework Vite. Set env `VITE_API_BASE_URL=https://<backend>`.
2. Deploy. Add the final Vercel URL to `ALLOWED_ORIGINS` on Railway and redeploy the backend.
3. Open the live site: send a message, repeat it in a new chat (expect cache), and use the Failover Lab (expect key_2 or a 503 bubble).

### 8.4 Final verification
Run `python scripts/smoke_test.py --base-url https://<backend>` until every row is green. Put both URLs at the top of the README.


## 9. README content (the exact headings from §1.8)
1. **Live URLs**: frontend, backend, `/health` link, repo, demo video placeholder.
2. **Run locally**: (a) Docker: `cp .env.example .env`, add keys, `make up`; (b) without Docker: a Python 3.12 venv, `pip install -r requirements.txt`, `uvicorn app.main:app --reload`; `npm i && npm run dev` with `VITE_API_BASE_URL`.
3. **Design decisions**
   - *Key fallback*: one LiteLLM model group per key, strict ordered `fallbacks`, `num_retries=0`, cooldowns disabled (and why: deterministic per-request semantics; a simulated failure must not bench a real key), failure simulation via a LiteLLM pre-call deployment hook so the real Router fallback path is exercised, and why simulated requests bypass the cache.
   - *Caching*: LiteLLM's built-in **disk** cache on the `/data` volume; why it's pinned to 1.108.14; exact-match key = model (caching group) + full messages + fixed params; `caching_groups` per model; warm-up; the single-flight and cache-landing probe that closes the async-write race; TTL.
   - *SQLite session storage*: schema, WAL, single-transaction writes, nothing persisted on failure, per-session locks, a history window for the LLM, the volume, one worker.
   - Trade-offs and what would change at scale (Redis cache, Postgres, multiple workers, cooldowns for real errors).
4. **Verification**: copy-paste `curl` commands for health, chat, cache hit (same message in a new session id), failover (`-H "X-Simulate-Failure: key_1=429"`), all-fail 503, history GET, 422 examples, plus how the UI shows each case and `make smoke`.
5. **Setup Used**: leave placeholders for the human (editor, Claude Code, model).
6. **Bonus features**: each feature, with one line on why.


## 11. Definition of done (check every box and show evidence)
- [ ] `pytest` is green (all §7.1 cases). `ruff` is clean.
- [ ] `make up` works from a fresh clone (with `.env`): backend `/health` OK on :8000, UI on :3000 talks to it.
- [ ] `make down` works. History survives `make down && make up` (named volume).
- [ ] Smoke test is green against **localhost** and against the **live Railway URL**.
- [ ] The live Vercel UI shows LIVE_INFERENCE then LLM_CACHE for the same first message in a new chat, with per-message tokens and latency, and the Failover Lab shows key_2 and a readable 503 bubble.
- [ ] The DevTools network tab shows no CORS errors, including requests with `X-Simulate-Failure`.
- [ ] The repo is public, `.env` is not committed, `.env.example` is complete, the README has the 6 exact headings and the live URLs.
- [ ] `docs/DEMO_SCRIPT.md` is written (below).

### 11.1 `docs/DEMO_SCRIPT.md` (2–3 minutes; the human records it with voice)
1. (0:00) Open the live UI and `/health` (2 keys, cache on).
2. (0:15) Ask a question and point at LIVE_INFERENCE, the model, the latency and the in/out tokens.
3. (0:35) Ask a follow-up that needs memory (e.g. "what did I just ask?"). Multi-turn works.
4. (0:55) Click New chat, send the first question again. LLM_CACHE, identical text, much lower latency, key_id null.
5. (1:20) Failover Lab key_1=429: answered by key_2, 1 fallback. Then key_1=500.
6. (1:45) Fail all keys: readable 503 bubble. Open the Session Inspector and show the failed message wasn't stored.
7. (2:05) Terminal: `curl` the 3 endpoints, including `-H "X-Simulate-Failure: *=429"`, then run `make smoke` against the live URL.
8. (2:30) Show the repo structure, the README design decisions and `make up` locally. Wrap up.

---
**End of PART 4.** Follow the instruction at the top of this part.
