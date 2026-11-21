from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.main import create_app

KEYS = ["sk-test-key-one-AAAA1111", "sk-test-key-two-BBBB2222"]
MOCK_DELAY = 0.2


def make_settings(tmp: Path, **overrides: Any) -> Settings:
    env = {
        "LLM_API_KEYS": ",".join(KEYS),
        "DATABASE_URL": f"sqlite:///{(tmp / 'app.db').as_posix()}",
        "CACHE_TYPE": "disk",
        "CACHE_DIR": str(tmp / "cache"),
        "ENABLE_FAILURE_SIMULATION": "true",
        "LOG_LEVEL": "WARNING",
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return Settings(_env_file=None, **env)  # type: ignore[call-arg]


def mock_overrides(reply: str = "Paris") -> dict[str, dict[str, Any]]:
    return {
        "key_1": {"mock_response": f"{reply} (key_1)", "mock_delay": MOCK_DELAY},
        "key_2": {"mock_response": f"{reply} (key_2)", "mock_delay": MOCK_DELAY},
    }


class RecordingClient(httpx.AsyncClient):
    """Keeps every response body so test 17 can scan them for key material."""

    bodies: list[str] = []

    async def request(self, *args: Any, **kwargs: Any) -> httpx.Response:
        resp = await super().request(*args, **kwargs)
        RecordingClient.bodies.append(resp.text)
        return resp


@asynccontextmanager
async def running_app(settings: Settings, overrides: dict[str, dict[str, Any]] | None = None):
    app = create_app(settings, overrides if overrides is not None else mock_overrides())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with RecordingClient(transport=transport, base_url="http://test") as client:
            yield app, client


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Callable[..., Settings]:
    return lambda **kw: make_settings(tmp_path, **kw)


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    async with running_app(make_settings(tmp_path)) as (_, c):
        yield c
