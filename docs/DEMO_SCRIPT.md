# Vani demo script (2–3 minutes)

**Before recording:**
- Open two browser tabs: the live UI (https://lyftr-ai-chatbot-sage.vercel.app) and https://lyftraichatbot-production.up.railway.app/health.
- Open a terminal with `B=https://lyftraichatbot-production.up.railway.app` exported.
- Pick a question you haven't asked before. The cache persists, so a question that's already been asked would show `LLM_CACHE` on the first try. Adding a word like "today" or a number makes it new.

| Time | Do | Say |
|---|---|---|
| 0:00 | Show the `/health` tab: `keys_configured: 2`, `cache_enabled: true`, `failure_simulation: true`. Switch to the UI. | "This is Vani: a React front end, a FastAPI back end, a LiteLLM Router over two Groq keys, and SQLite for sessions. Health shows two keys loaded and the cache on." |
| 0:15 | Type e.g. *"In two sentences, why is the sky blue?"* and press Enter. Point at the meta bar. | "First answer: `LIVE_INFERENCE` from key_1. Here's the model string, the server-measured latency, and the input and output tokens for this message only." |
| 0:35 | Send *"What did I just ask you?"* | "It remembers: history is stored per session in SQLite and sent with each turn. Input tokens went up because the earlier exchange is included." |
| 0:55 | Click **New chat** and send the exact first question again. | "New session, same first message: `LLM_CACHE`. The text is identical, latency drops to a few milliseconds, key_id is empty because no key was used, and the stored token usage is shown." |
| 1:20 | Failover Lab → `key_1 = 429` → send any question. Then `key_1 = 500` → send another. | "Now I simulate key_1 being rate-limited. The header makes a LiteLLM hook raise a real RateLimitError, so the Router's own fallback runs: key_2 answers, one fallback. Same for a 500." |
| 1:45 | Failover Lab → **Fail all keys (*=429)** → send. Then in the terminal: `curl -s $B/sessions/<session id from the header>/messages` | "When every key fails, the user gets a readable 503 bubble with a Retry button, never a blank screen. The history endpoint shows the failed message was not stored." |
| 2:05 | Terminal: `curl -s $B/health`, then a `POST /chat` with `-H "X-Simulate-Failure: *=429"`, then `python scripts/smoke_test.py $B`. | "The same contract from the terminal: a 503 with the exact detail string, and the smoke test walks every endpoint, failover case, cache case and 422 against the live URL." |
| 2:30 | Show the repo tree, the README's *Design decisions* section, and `make up` (or `docker compose ps`). | "One command runs the whole stack locally. The README covers the design: ordered key fallback, simulation that bypasses the cache, the LiteLLM disk cache with single-flight so identical requests can't race, and SQLite with single-transaction writes. Thanks!" |

**Fallback if something misbehaves live:**
- **A Groq rate limit (a real 429 on both keys):** wait 60 s. The free tier allows about 30 requests per minute and 8K tokens per minute.
- **A cold start:** the first request after a redeploy can be slower. Send one warm-up message before recording.
