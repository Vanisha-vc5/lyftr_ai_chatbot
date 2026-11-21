"""Headless browser check of the Vani UI contract (data-testids, cache, failover, 503 bubble).

Usage: python scripts/ui_check.py [FRONTEND_URL] [--offline]
  --offline  backend has no working keys: only checks the contract elements, 503 bubble and Retry.
Requires: pip install playwright && python -m playwright install chromium
"""

from __future__ import annotations

import re
import sys
import uuid

from playwright.sync_api import Page, expect, sync_playwright

URL = next((a for a in sys.argv[1:] if not a.startswith("--")), "http://localhost:3000")
OFFLINE = "--offline" in sys.argv
rows: list[tuple[str, bool, str]] = []
cors_errors: list[str] = []


def ok(name: str, cond: bool, info: str = "") -> None:
    rows.append((name, bool(cond), info))


def send(page: Page, text: str) -> None:
    page.get_by_test_id("chat-input").fill(text)
    page.get_by_test_id("send-button").click()


def last_assistant(page: Page, n: int):
    loc = page.get_by_test_id("assistant-message")
    expect(loc).to_have_count(n, timeout=90_000)
    return loc.nth(n - 1)


def check_meta(msg, label: str) -> dict:
    src = msg.get_by_test_id("meta-source").inner_text().strip()
    model = msg.get_by_test_id("meta-model").inner_text().strip()
    lat = msg.get_by_test_id("meta-latency").inner_text().strip()
    tin = msg.get_by_test_id("meta-input-tokens").inner_text().strip()
    tout = msg.get_by_test_id("meta-output-tokens").inner_text().strip()
    good = (
        src in ("LIVE_INFERENCE", "LLM_CACHE")
        and bool(model)
        and re.fullmatch(r"\d+ ms", lat) is not None
        and tin.isdigit()
        and tout.isdigit()
    )
    ok(f"{label}: meta test-ids valid", good, f"{src} | {model} | {lat} | in {tin} | out {tout}")
    return {"src": src, "lat": int(lat.split()[0]) if lat.split()[0].isdigit() else -1, "text": msg.inner_text()}


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.on(
            "console",
            lambda m: cors_errors.append(m.text) if m.type == "error" and "CORS" in m.text else None,
        )
        page.goto(URL)
        for tid in ("chat-input", "send-button", "system-prompt-input"):
            ok(f"{tid} present and visible", page.get_by_test_id(tid).is_visible())
        ok("send-button disabled while input blank", page.get_by_test_id("send-button").is_disabled())

        nonce = uuid.uuid4().hex[:6]
        q = f"In one short sentence, what is the capital of France? (ui {nonce})"

        if OFFLINE:
            send(page, q)
            ok("chat-input empty after send", page.get_by_test_id("chat-input").input_value() == "")
            err = page.get_by_test_id("error-message").first
            expect(err).to_be_visible(timeout=60_000)
            ok("error shows as readable error-message bubble", "unavailable" in err.inner_text().lower(), err.inner_text())
            ok("no assistant-message for an error", page.get_by_test_id("assistant-message").count() == 0)
        else:
            send(page, q)
            ok("chat-input empty after send", page.get_by_test_id("chat-input").input_value() == "")
            first = check_meta(last_assistant(page, 1), "first message")
            ok("first message is LIVE_INFERENCE", first["src"] == "LIVE_INFERENCE", first["src"])

            send(page, "What did I just ask you? Answer in one short sentence.")
            check_meta(last_assistant(page, 2), "follow-up")

            page.get_by_role("button", name="New chat").click()
            expect(page.get_by_test_id("assistant-message")).to_have_count(0)
            send(page, q)
            hit = check_meta(last_assistant(page, 1), "repeat in new chat")
            ok("repeat in new chat is LLM_CACHE", hit["src"] == "LLM_CACHE", hit["src"])
            ok("cache hit faster than live", 0 <= hit["lat"] < first["lat"], f"{hit['lat']} < {first['lat']}")

            page.get_by_test_id("failover-select").select_option("key_1=429")
            send(page, f"Say hi in Spanish. ({nonce})")
            fo = last_assistant(page, 2)
            check_meta(fo, "failover key_1=429")
            ok("failover answered by key_2 with 1 fallback", "key_2" in fo.inner_text() and "1 fallback" in fo.inner_text())

        page.get_by_test_id("failover-select").select_option("*=429")
        send(page, "this should fail")
        err = page.get_by_test_id("error-message").last
        expect(err).to_be_visible(timeout=60_000)
        ok("*=429 shows readable 503 bubble", "every API key failed" in err.inner_text(), err.inner_text()[:80])
        ok("Retry button present", err.get_by_role("button", name="Retry").is_visible())
        ok("no CORS errors in console", not cors_errors, "; ".join(cors_errors)[:200])
        browser.close()

    width = max(len(r[0]) for r in rows)
    for name, good, info in rows:
        print(f"{'✅' if good else '❌'} {name.ljust(width)}  {info}")
    failed = sum(1 for r in rows if not r[1])
    print(f"\n{len(rows) - failed}/{len(rows)} UI checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    sys.exit(main())
