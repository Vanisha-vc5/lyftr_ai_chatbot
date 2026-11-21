from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import httpx
import pytest

from app.llm.simulation import parse_simulation_header
from tests.conftest import KEYS, RecordingClient, make_settings, running_app


def sid() -> str:
    return "s-" + uuid.uuid4().hex[:12]


async def chat(client: httpx.AsyncClient, message: str, session_id: str | None = None, headers=None, **extra):
    body = {"session_id": session_id or sid(), "message": message, **extra}
    return await client.post("/chat", json=body, headers=headers or {})


# 1
async def test_health(client: httpx.AsyncClient) -> None:
    r = await client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"
    assert data["keys_configured"] == 2
    assert data["cache_enabled"] is True


# 2
async def test_first_chat_is_live(client: httpx.AsyncClient) -> None:
    s = sid()
    r = await chat(client, "What is the capital of France?", s)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["session_id"] == s
    assert data["reply"]
    md = data["metadata"]
    assert md["resolution_source"] == "LIVE_INFERENCE"
    assert md["key_id"] == "key_1"
    assert md["fallback_attempts"] == 0
    assert md["model"] == "groq/openai/gpt-oss-20b"
    assert isinstance(md["latency_ms"], int)
    assert isinstance(md["input_tokens"], int) and md["input_tokens"] > 0
    assert isinstance(md["output_tokens"], int)


# 3 + 18 (real cache hit through the Router)
async def test_same_message_new_session_hits_cache(client: httpx.AsyncClient) -> None:
    live = (await chat(client, "Capital of Japan?")).json()
    hit = (await chat(client, "Capital of Japan?")).json()
    assert live["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    assert hit["metadata"]["resolution_source"] == "LLM_CACHE"
    assert hit["reply"] == live["reply"]
    assert hit["metadata"]["key_id"] is None
    assert hit["metadata"]["fallback_attempts"] == 0
    assert hit["metadata"]["input_tokens"] == live["metadata"]["input_tokens"]
    assert hit["metadata"]["output_tokens"] == live["metadata"]["output_tokens"]
    assert hit["metadata"]["latency_ms"] < live["metadata"]["latency_ms"]


# 4
async def test_different_message_not_cached(client: httpx.AsyncClient) -> None:
    await chat(client, "Message A")
    r = (await chat(client, "Message B")).json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"


# 5
async def test_concurrent_identical_first_messages_single_flight(client: httpx.AsyncClient) -> None:
    results = await asyncio.gather(*(chat(client, "Concurrent question?") for _ in range(3)))
    data = [r.json() for r in results]
    sources = sorted(d["metadata"]["resolution_source"] for d in data)
    assert sources == ["LIVE_INFERENCE", "LLM_CACHE", "LLM_CACHE"]
    assert len({d["reply"] for d in data}) == 1
    # back to back as well
    a = (await chat(client, "Back to back?")).json()
    b = (await chat(client, "Back to back?")).json()
    assert [a["metadata"]["resolution_source"], b["metadata"]["resolution_source"]] == ["LIVE_INFERENCE", "LLM_CACHE"]
    assert a["reply"] == b["reply"]


# 6
@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_simulated_failover_to_key_2(client: httpx.AsyncClient, status: int) -> None:
    r = await chat(client, f"Failover {status}", headers={"X-Simulate-Failure": f"key_1={status}"})
    assert r.status_code == 200, r.text
    md = r.json()["metadata"]
    assert md["key_id"] == "key_2"
    assert md["fallback_attempts"] == 1
    assert md["resolution_source"] == "LIVE_INFERENCE"
    assert r.json()["reply"] == "Paris (key_2)"


# 7
async def test_simulation_bypasses_cache(client: httpx.AsyncClient) -> None:
    first = (await chat(client, "Cached already?")).json()
    assert (await chat(client, "Cached already?")).json()["metadata"]["resolution_source"] == "LLM_CACHE"
    r = await chat(client, "Cached already?", headers={"X-Simulate-Failure": "key_1=429"})
    md = r.json()["metadata"]
    assert md["key_id"] == "key_2" and md["resolution_source"] == "LIVE_INFERENCE"
    # simulated request must not overwrite the cached text
    again = (await chat(client, "Cached already?")).json()
    assert again["metadata"]["resolution_source"] == "LLM_CACHE"
    assert again["reply"] == first["reply"]


# 8 + 9
@pytest.mark.parametrize("header", ["*=429", "key_1=429,key_2=500", " KEY_1 = 503 , key_2=502 "])
async def test_all_keys_fail_returns_503_and_no_history(client: httpx.AsyncClient, header: str) -> None:
    s = sid()
    ok = await chat(client, "first turn", s)
    assert ok.status_code == 200
    before = (await client.get(f"/sessions/{s}/messages")).json()
    r = await chat(client, "second turn", s, headers={"X-Simulate-Failure": header})
    assert r.status_code == 503
    assert r.json() == {"detail": "all providers unavailable"}
    after = (await client.get(f"/sessions/{s}/messages")).json()
    assert after == before


async def test_503_on_new_session_leaves_no_session(client: httpx.AsyncClient) -> None:
    s = sid()
    r = await chat(client, "hello", s, headers={"X-Simulate-Failure": "*=429"})
    assert r.status_code == 503
    assert (await client.get(f"/sessions/{s}/messages")).status_code == 404


# 10
async def test_header_ignored_when_flag_off(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path, ENABLE_FAILURE_SIMULATION="false")) as (_, c):
        r = await chat(c, "flag off", headers={"X-Simulate-Failure": "*=429"})
        assert r.status_code == 200
        assert r.json()["metadata"]["key_id"] == "key_1"


# 11
@pytest.mark.parametrize("header", ["garbage", "key_9=429", "key_1=418", "key_1=abc", "=,,=", "key_1"])
async def test_malformed_header_entries_ignored(client: httpx.AsyncClient, header: str) -> None:
    r = await chat(client, f"malformed {header}", headers={"X-Simulate-Failure": header})
    assert r.status_code == 200
    assert r.json()["metadata"]["key_id"] == "key_1"


def test_header_parser_policy() -> None:
    ids = ["key_1", "key_2"]
    assert parse_simulation_header("key_1=429", ids) == {"key_1": 429}
    assert parse_simulation_header("*=429, key_2=500", ids) == {"*": 429, "key_2": 500}
    assert parse_simulation_header("key_3=429,key_1=999,bad", ids) == {}


# 12
@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "s-1"},
        {"session_id": "s-1", "message": "   \n\t "},
        {"session_id": "s-1", "message": "x" * 4001},
        {"session_id": "", "message": "hi"},
        {"session_id": "   ", "message": "hi"},
        {"session_id": "s" * 129, "message": "hi"},
        {"message": "hi"},
        {"session_id": 123, "message": "hi"},
        {"session_id": "s-1", "message": ["hi"]},
        {"session_id": "s-1", "message": "hi", "system_prompt": 5},
    ],
)
async def test_validation_422(client: httpx.AsyncClient, payload: dict) -> None:
    r = await client.post("/chat", json=payload)
    assert r.status_code == 422
    assert "detail" in r.json()
    assert (await client.get("/sessions/s-1/messages")).status_code == 404


async def test_malformed_json_422(client: httpx.AsyncClient) -> None:
    r = await client.post("/chat", content=b"{not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422
    assert "detail" in r.json()


async def test_message_trimmed_and_4000_after_trim_ok(client: httpx.AsyncClient) -> None:
    s = sid()
    r = await chat(client, "  " + "y" * 4000 + "  ", s)
    assert r.status_code == 200
    hist = (await client.get(f"/sessions/{s}/messages")).json()
    assert hist["messages"][0]["content"] == "y" * 4000


# 13
async def test_multi_turn_history_is_sent(client: httpx.AsyncClient, monkeypatch) -> None:
    app_gateway_calls: list[list[dict]] = []
    s = sid()
    await chat(client, "My name is Asha.", s)
    transport = client._transport  # type: ignore[attr-defined]
    gateway = transport.app.state.gateway  # type: ignore[attr-defined]
    original = gateway.complete

    async def spy(messages, simulate=None):
        app_gateway_calls.append(messages)
        return await original(messages, simulate)

    monkeypatch.setattr(gateway, "complete", spy)
    await chat(client, "What is my name?", s)
    sent = app_gateway_calls[-1]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "My name is Asha."
    # isolation: another session only sees its own message
    await chat(client, "Unrelated", sid())
    assert [m["role"] for m in app_gateway_calls[-1]] == ["system", "user"]


# 14
async def test_system_prompt_override(client: httpx.AsyncClient, monkeypatch) -> None:
    s = sid()
    base = (await chat(client, "Prompted question")).json()
    r = (await chat(client, "Prompted question", s, system_prompt="You are a pirate.")).json()
    assert r["metadata"]["resolution_source"] == "LIVE_INFERENCE"  # different cache key
    assert r["metadata"]["cache_key"] != base["metadata"]["cache_key"]

    gateway = client._transport.app.state.gateway  # type: ignore[attr-defined]
    seen: list[list[dict]] = []
    original = gateway.complete

    async def spy(messages, simulate=None):
        seen.append(messages)
        return await original(messages, simulate)

    monkeypatch.setattr(gateway, "complete", spy)
    await chat(client, "follow-up", s)  # no system_prompt sent: stored one is used
    assert seen[-1][0] == {"role": "system", "content": "You are a pirate."}
    hist = (await client.get(f"/sessions/{s}/messages")).json()
    assert all(m["role"] != "system" for m in hist["messages"])
    assert all("pirate" not in m["content"] for m in hist["messages"])


# 15
async def test_history_endpoint(client: httpx.AsyncClient) -> None:
    r = await client.get("/sessions/does-not-exist/messages")
    assert r.status_code == 404
    assert r.json() == {"detail": "session not found"}

    s = sid()
    await chat(client, "one", s)
    await chat(client, "two", s)
    data = (await client.get(f"/sessions/{s}/messages")).json()
    assert data["session_id"] == s
    roles = [m["role"] for m in data["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert data["messages"][0]["content"] == "one" and data["messages"][2]["content"] == "two"
    for m in data["messages"]:
        if m["role"] == "user":
            assert m.get("metadata") is None
        else:
            for k in ("resolution_source", "input_tokens", "output_tokens"):
                assert k in m["metadata"]


# 16
async def test_history_persists_across_restart(tmp_path: Path) -> None:
    s = sid()
    settings = make_settings(tmp_path)
    async with running_app(settings) as (_, c):
        assert (await chat(c, "persist me", s)).status_code == 200
    async with running_app(make_settings(tmp_path)) as (_, c):
        data = (await c.get(f"/sessions/{s}/messages")).json()
        assert data["messages"][0]["content"] == "persist me"
        # disk cache survives the restart too
        hit = (await chat(c, "persist me")).json()
        assert hit["metadata"]["resolution_source"] == "LLM_CACHE"


async def test_unknown_route_json_404(client: httpx.AsyncClient) -> None:
    r = await client.get("/nope")
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


async def test_local_cache_mode(tmp_path: Path) -> None:
    async with running_app(make_settings(tmp_path, CACHE_TYPE="local")) as (_, c):
        a = (await chat(c, "local cache?")).json()
        b = (await chat(c, "local cache?")).json()
        assert a["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert b["metadata"]["resolution_source"] == "LLM_CACHE"


async def test_empty_provider_reply_is_503(tmp_path: Path) -> None:
    overrides = {"key_1": {"mock_response": ""}, "key_2": {"mock_response": ""}}
    async with running_app(make_settings(tmp_path), overrides) as (_, c):
        r = await chat(c, "empty please")
        assert r.status_code == 503
        assert r.json() == {"detail": "all providers unavailable"}


# 17 (runs last in this module: scans every body recorded so far)
def test_zz_no_key_material_in_any_response() -> None:
    assert RecordingClient.bodies, "no responses recorded"
    for body in RecordingClient.bodies:
        for key in KEYS:
            assert key not in body
