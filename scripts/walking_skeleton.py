"""One real Groq call through LiteLLM, for each configured key (PART 2 §2.7).

Run from the repo root:  backend/.venv/Scripts/python scripts/walking_skeleton.py
Reads LLM_API_KEYS / LLM_MODEL from the environment or from ./.env. Never prints the keys.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import litellm


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(Path(".env"))
keys = [k.strip() for k in os.environ.get("LLM_API_KEYS", "").split(",") if k.strip()]
model = os.environ.get("LLM_MODEL", "groq/openai/gpt-oss-20b")
assert keys, "Set LLM_API_KEYS in .env or the environment"
litellm.suppress_debug_info = True

for i, key in enumerate(keys, start=1):
    t = time.perf_counter()
    try:
        resp = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": "Reply with one short sentence: what is the capital of France?"}],
            api_key=key,
            reasoning_effort="low",
            max_tokens=256,
        )
        ms = int((time.perf_counter() - t) * 1000)
        u = resp.usage
        print(f"key_{i}: OK {ms} ms | {resp.choices[0].message.content!r}")
        print(f"        usage: prompt={u.prompt_tokens} completion={u.completion_tokens} total={u.total_tokens}")
    except Exception as exc:  # show the type and a trimmed message, never the key
        print(f"key_{i}: FAILED {type(exc).__name__}: {str(exc).replace(key, '***')[:300]}")
