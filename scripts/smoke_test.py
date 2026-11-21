#!/usr/bin/env python3
"""Walk the whole Vani API contract against a running backend.

Usage:  python scripts/smoke_test.py [BASE_URL]      (default http://localhost:8000)
        BASE_URL=https://... python scripts/smoke_test.py
Optional: ORIGIN=https://your-frontend.vercel.app  -> also checks the CORS preflight.
Exits non-zero if any check fails. Uses only the standard library. Makes ~6 real LLM calls.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BASE_URL", "http://localhost:8000")).rstrip("/")
ORIGIN = os.environ.get("ORIGIN", "")
results: list[tuple[str, bool, str]] = []


def call(method: str, path: str, body: object | None = None, headers: dict | None = None, raw: bytes | None = None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            text = resp.read().decode()
            return resp.status, (json.loads(text) if text else None), dict(resp.headers)
    except urllib.error.HTTPError as e:
        text = e.read().decode()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = {"_raw": text[:200]}
        return e.code, parsed, dict(e.headers)


def check(name: str, ok: bool, info: str = "") -> bool:
    results.append((name, bool(ok), info))
    return bool(ok)


def sid() -> str:
    return "s-smoke-" + uuid.uuid4().hex[:10]


def main() -> int:
    print(f"Smoke testing {BASE}\n")
    nonce = uuid.uuid4().hex[:8]

    # 1. health
    st, body, _ = call("GET", "/health")
    check(
        "GET /health -> 200 with status/keys_configured/cache_enabled",
        st == 200 and body.get("status") == "ok" and body.get("keys_configured", 0) >= 2 and "cache_enabled" in body,
        f"{st} {body}",
    )

    # 2. live chat
    question = f"In one short sentence: what is the capital of France? (smoke {nonce})"
    s1 = sid()
    st, live, _ = call("POST", "/chat", {"session_id": s1, "message": question})
    md = (live or {}).get("metadata", {})
    check(
        "POST /chat first message -> LIVE_INFERENCE via key_1",
        st == 200
        and live.get("session_id") == s1
        and bool(live.get("reply"))
        and md.get("resolution_source") == "LIVE_INFERENCE"
        and md.get("key_id") == "key_1"
        and md.get("fallback_attempts") == 0
        and isinstance(md.get("latency_ms"), int)
        and isinstance(md.get("input_tokens"), int)
        and md.get("input_tokens", 0) > 0
        and isinstance(md.get("output_tokens"), int)
        and str(md.get("model", "")).startswith("groq/"),
        f"{st} {json.dumps(md)}",
    )

    # 3. cache hit in a new session
    st, hit, _ = call("POST", "/chat", {"session_id": sid(), "message": question})
    hmd = (hit or {}).get("metadata", {})
    check(
        "Same message, new session -> LLM_CACHE, identical text, key_id null, faster",
        st == 200
        and hmd.get("resolution_source") == "LLM_CACHE"
        and hit.get("reply") == live.get("reply")
        and hmd.get("key_id") is None
        and hmd.get("fallback_attempts") == 0
        and hmd.get("input_tokens") == md.get("input_tokens")
        and hmd.get("latency_ms", 10**9) < md.get("latency_ms", 0),
        f"{st} live={md.get('latency_ms')}ms cache={hmd.get('latency_ms')}ms src={hmd.get('resolution_source')}",
    )

    # 4. multi-turn in the same session
    st, turn2, _ = call("POST", "/chat", {"session_id": s1, "message": "And of Italy? One short sentence."})
    t2 = (turn2 or {}).get("metadata", {})
    check(
        "Second turn in same session -> input_tokens grow (history sent)",
        st == 200 and t2.get("input_tokens", 0) > md.get("input_tokens", 10**9),
        f"{st} turn1_in={md.get('input_tokens')} turn2_in={t2.get('input_tokens')}",
    )

    # 5-6. simulated failover
    for status in (429, 500):
        st, fo, _ = call(
            "POST",
            "/chat",
            {"session_id": sid(), "message": f"Say hello in French. (failover {status} {nonce})"},
            {"X-Simulate-Failure": f"key_1={status}"},
        )
        fmd = (fo or {}).get("metadata", {})
        check(
            f"X-Simulate-Failure: key_1={status} -> 200 via key_2, fallback_attempts=1",
            st == 200
            and fmd.get("key_id") == "key_2"
            and fmd.get("fallback_attempts") == 1
            and fmd.get("resolution_source") == "LIVE_INFERENCE"
            and bool(fo.get("reply")),
            f"{st} {json.dumps(fmd)[:160]}",
        )

    # 7. all keys fail -> 503 and history unchanged
    _, before, _ = call("GET", f"/sessions/{s1}/messages")
    st, err, _ = call("POST", "/chat", {"session_id": s1, "message": "this must fail"}, {"X-Simulate-Failure": "*=429"})
    _, after, _ = call("GET", f"/sessions/{s1}/messages")
    check(
        "X-Simulate-Failure: *=429 -> 503 exact detail, history unchanged",
        st == 503 and err == {"detail": "all providers unavailable"} and before == after,
        f"{st} {err}",
    )

    # 8. history
    st, hist, _ = call("GET", f"/sessions/{s1}/messages")
    msgs = (hist or {}).get("messages", [])
    check(
        "GET /sessions/{id}/messages -> oldest first, metadata on assistant only, no system",
        st == 200
        and [m.get("role") for m in msgs] == ["user", "assistant", "user", "assistant"]
        and msgs[0].get("content") == question
        and all(m.get("metadata") is None for m in msgs if m["role"] == "user")
        and all(
            {"resolution_source", "input_tokens", "output_tokens"} <= set(m.get("metadata") or {})
            for m in msgs
            if m["role"] == "assistant"
        ),
        f"{st} roles={[m.get('role') for m in msgs]}",
    )

    # 9. 404
    st, nf, _ = call("GET", f"/sessions/{sid()}/messages")
    check("GET unknown session -> 404 session not found", st == 404 and nf == {"detail": "session not found"}, f"{st}")

    # 10. 422s
    bad = {
        "missing message": {"session_id": "s-x"},
        "whitespace message": {"session_id": "s-x", "message": "   "},
        "4001 chars": {"session_id": "s-x", "message": "a" * 4001},
        "empty session_id": {"session_id": "", "message": "hi"},
        "129-char session_id": {"session_id": "s" * 129, "message": "hi"},
    }
    for label, payload in bad.items():
        st, b, _ = call("POST", "/chat", payload)
        check(f"422: {label}", st == 422 and isinstance(b, dict) and "detail" in b, f"{st}")
    st, b, _ = call("POST", "/chat", raw=b"{not json")
    check("422: malformed JSON", st == 422 and "detail" in (b or {}), f"{st}")

    # 11. optional CORS preflight
    if ORIGIN:
        req = urllib.request.Request(BASE + "/chat", method="OPTIONS")
        req.add_header("Origin", ORIGIN)
        req.add_header("Access-Control-Request-Method", "POST")
        req.add_header("Access-Control-Request-Headers", "content-type,x-simulate-failure")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                allow = resp.headers.get("access-control-allow-origin")
                ok = resp.status == 200 and allow in (ORIGIN, "*")
                check(f"CORS preflight from {ORIGIN}", ok, f"{resp.status} allow-origin={allow}")
        except urllib.error.HTTPError as e:
            check(f"CORS preflight from {ORIGIN}", False, f"{e.code}")

    width = max(len(n) for n, _, _ in results)
    for name, ok, info in results:
        print(f"{'✅' if ok else '❌'} {name.ljust(width)}  {info if not ok else ''}".rstrip())
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{len(results) - failed}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    t = time.perf_counter()
    code = main()
    print(f"({time.perf_counter() - t:.1f}s)")
    sys.exit(code)
