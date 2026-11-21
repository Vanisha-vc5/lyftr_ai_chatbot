"""LiteLLM Router wrapper: ordered key fallback, response caching, failure simulation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import litellm
from litellm import Router
from litellm.caching.caching import Cache

from app.config import Settings
from app.llm.simulation import ATTEMPTS, SIM_PLAN, FailureSimulator

logger = logging.getLogger("vani.gateway")

PROBE = "__vani_probe_miss__"
WARMUP = "__vani_warmup__"
PROBE_INTERVAL_S = 0.005
PROBE_BUDGET_S = 0.3


class ProviderUnavailable(Exception):
    """Every key failed, or the provider returned something unusable (e.g. empty content)."""


@dataclass
class LLMResult:
    reply: str
    cache_hit: bool
    key_id: str | None
    fallback_attempts: int
    model: str
    input_tokens: int
    output_tokens: int
    cache_key: str
    cost_usd: float
    attempts: list[dict[str, Any]] = field(default_factory=list)


def _configure_litellm_globals() -> None:
    litellm.suppress_debug_info = True
    litellm.drop_params = True
    litellm.set_verbose = False
    for name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "litellm"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


class LLMGateway:
    def __init__(self, settings: Settings, deployment_overrides: dict[str, dict[str, Any]] | None = None) -> None:
        if not settings.api_keys:
            raise ValueError("LLM_API_KEYS is empty")
        if len(settings.api_keys) < 2:
            logger.warning("Only %d LLM key configured; failover needs at least 2", len(settings.api_keys))

        _configure_litellm_globals()
        self.settings = settings
        self.key_ids = settings.key_ids
        self.models = settings.models_by_key
        overrides = deployment_overrides or {}

        # Cache must be configured before the Router, otherwise the Router installs its own.
        if settings.cache_enabled:
            cache_type = settings.cache_type.lower()
            cache_kwargs: dict[str, Any] = {"ttl": settings.cache_ttl_seconds, "max_messages": None}
            if cache_type == "disk":
                Path(settings.cache_dir).mkdir(parents=True, exist_ok=True)
                cache_kwargs["disk_cache_dir"] = settings.cache_dir
            litellm.cache = Cache(type=cache_type, **cache_kwargs)
        else:
            litellm.cache = None

        deployments = []
        for key_id, api_key in zip(self.key_ids, settings.api_keys, strict=True):
            params: dict[str, Any] = {
                "model": self.models[key_id],
                "api_key": api_key,
                "timeout": settings.llm_timeout_seconds,
            }
            params.update(overrides.get(key_id, {}))
            deployments.append({"model_name": key_id, "litellm_params": params, "model_info": {"id": key_id}})

        # Keys sharing a model share one cache group, so "model + messages" is the cache key
        # no matter which key produced the answer.
        groups: dict[str, list[str]] = {}
        for key_id in self.key_ids:
            groups.setdefault(self.models[key_id], []).append(key_id)
        self.group_of = {k: self.models[k] + "|" + ",".join(ids) for ids in groups.values() for k in ids}
        caching_groups = [tuple(ids) for ids in groups.values() if len(ids) > 1] or None

        self.router = Router(
            model_list=deployments,
            num_retries=0,
            fallbacks=[{self.key_ids[0]: self.key_ids[1:]}] if len(self.key_ids) > 1 else [],
            caching_groups=caching_groups,
            cache_responses=settings.cache_enabled,
            disable_cooldowns=True,
        )
        litellm.callbacks = [FailureSimulator(dict(zip(settings.api_keys, self.key_ids, strict=True)))]

        self._inflight: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    # ------------------------------------------------------------------
    @property
    def keys_configured(self) -> int:
        return len(self.key_ids)

    def _common_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
        }
        if self.settings.llm_reasoning_effort:
            params["reasoning_effort"] = self.settings.llm_reasoning_effort
        return params

    def fingerprint(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "group": self.group_of[self.key_ids[0]],
            "messages": messages,
            "temperature": self.settings.llm_temperature,
            "max_tokens": self.settings.llm_max_tokens,
            "reasoning_effort": self.settings.llm_reasoning_effort,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    async def warm_up(self) -> None:
        """Load LiteLLM's lazily imported cache-hit path so the first real hit is fast."""
        if not self.settings.cache_enabled:
            return
        token = SIM_PLAN.set({})
        try:
            for _ in range(2):
                await self.router.acompletion(
                    model=self.key_ids[0],
                    messages=[{"role": "user", "content": WARMUP}],
                    mock_response="ok",
                    **self._common_params(),
                )
                await asyncio.sleep(0.1)
        except Exception:  # never block startup on warm-up
            logger.warning("cache warm-up failed", exc_info=True)
        finally:
            SIM_PLAN.reset(token)

    async def _cache_has(self, messages: list[dict[str, str]]) -> bool:
        resp = await self.router.acompletion(
            model=self.key_ids[0],
            messages=messages,
            mock_response=PROBE,
            mock_delay=0,
            cache={"no-store": True},
            **self._common_params(),
        )
        content = resp.choices[0].message.content
        return resp._hidden_params.get("cache_hit") is True and content != PROBE

    async def _wait_for_cache_entry(self, messages: list[dict[str, str]]) -> None:
        token = SIM_PLAN.set({})
        try:
            deadline = time.perf_counter() + PROBE_BUDGET_S
            while time.perf_counter() < deadline:
                if await self._cache_has(messages):
                    return
                await asyncio.sleep(PROBE_INTERVAL_S)
            logger.info("cache entry not visible within %.0f ms; continuing", PROBE_BUDGET_S * 1000)
        except Exception:
            logger.warning("cache probe failed; continuing", exc_info=True)
        finally:
            SIM_PLAN.reset(token)

    async def _call(self, messages: list[dict[str, str]], simulate: dict[str, int] | None) -> Any:
        kwargs = self._common_params()
        if simulate:
            kwargs["cache"] = {"no-cache": True, "no-store": True}
        return await self.router.acompletion(model=self.key_ids[0], messages=messages, **kwargs)

    async def complete(self, messages: list[dict[str, str]], simulate: dict[str, int] | None = None) -> LLMResult:
        attempts: list[dict[str, Any]] = []
        plan_token = SIM_PLAN.set(dict(simulate or {}))
        attempts_token = ATTEMPTS.set(attempts)
        fp = self.fingerprint(messages)
        try:
            if simulate or not self.settings.cache_enabled:
                resp = await self._call(messages, simulate)
            else:
                lock = self._inflight.get(fp)
                if lock is None:
                    lock = asyncio.Lock()
                    self._inflight[fp] = lock
                async with lock:
                    resp = await self._call(messages, None)
                    if resp._hidden_params.get("cache_hit") is not True:
                        await self._wait_for_cache_entry(messages)
        except Exception as exc:
            logger.warning("all providers failed: %s", type(exc).__name__)
            raise ProviderUnavailable from exc
        finally:
            SIM_PLAN.reset(plan_token)
            ATTEMPTS.reset(attempts_token)

        return self._to_result(resp, fp, attempts)

    def _to_result(self, resp: Any, fp: str, attempts: list[dict[str, Any]]) -> LLMResult:
        hidden = getattr(resp, "_hidden_params", {}) or {}
        try:
            content = resp.choices[0].message.content
        except (AttributeError, IndexError):
            content = None
        reply = (content or "").strip()
        if not reply:
            logger.warning("provider returned empty content")
            raise ProviderUnavailable("empty reply")

        cache_hit = hidden.get("cache_hit") is True
        model_id = hidden.get("model_id")
        model_id = model_id if model_id in self.models else self.key_ids[0]

        if cache_hit:
            key_id: str | None = None
            fallback_attempts = 0
        else:
            key_id = model_id
            headers = hidden.get("additional_headers") or {}
            try:
                fallback_attempts = int(headers["x-litellm-attempted-fallbacks"])
            except (KeyError, TypeError, ValueError):
                fallback_attempts = self.key_ids.index(model_id)

        usage = getattr(resp, "usage", None)
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)

        cost = 0.0
        if not cache_hit:
            try:
                cost = float(litellm.completion_cost(completion_response=resp, model=self.models[model_id]) or 0.0)
            except Exception:
                cost = 0.0

        return LLMResult(
            reply=reply,
            cache_hit=cache_hit,
            key_id=key_id,
            fallback_attempts=fallback_attempts,
            model=self.models[model_id],
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_key=fp[:12],
            cost_usd=round(cost, 8),
            attempts=attempts,
        )
