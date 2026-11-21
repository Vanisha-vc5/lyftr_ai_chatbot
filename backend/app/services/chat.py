"""Chat orchestration: load history -> build messages -> call LLM -> persist."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import weakref
from typing import Any

from app.config import Settings
from app.llm.gateway import LLMGateway
from app.repository import SessionRepository
from app.schemas import ChatRequest

logger = logging.getLogger("vani.chat")


class ChatService:
    def __init__(self, settings: Settings, repo: SessionRepository, gateway: LLMGateway) -> None:
        self.settings = settings
        self.repo = repo
        self.gateway = gateway
        self._session_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    def _session_lock(self, session_id: str) -> asyncio.Lock:
        lock = self._session_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._session_locks[session_id] = lock
        return lock

    async def chat(self, req: ChatRequest, simulate: dict[str, int] | None, t0: float) -> dict[str, Any]:
        """Raises ProviderUnavailable when every key fails; nothing is persisted in that case."""
        async with self._session_lock(req.session_id):
            stored_prompt = await self.repo.get_system_prompt(req.session_id)
            system_prompt = req.system_prompt or stored_prompt or self.settings.default_system_prompt
            history = await self.repo.get_recent_history(req.session_id, self.settings.history_max_messages)
            messages = [
                {"role": "system", "content": system_prompt},
                *history,
                {"role": "user", "content": req.message},
            ]

            result = await self.gateway.complete(messages, simulate or None)

            metadata: dict[str, Any] = {
                "resolution_source": "LLM_CACHE" if result.cache_hit else "LIVE_INFERENCE",
                "model": result.model,
                "latency_ms": 0,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "key_id": result.key_id,
                "fallback_attempts": result.fallback_attempts,
                "cache_key": result.cache_key,
                "cost_usd": result.cost_usd,
                "attempts": result.attempts,
                "simulated_failures": simulate or None,
            }
            # Server-measured latency of the whole request (validation, history, LLM/cache), taken
            # just before the commit so the stored and returned values are identical.
            metadata["latency_ms"] = int(round((time.perf_counter() - t0) * 1000))
            await self.repo.save_exchange(req.session_id, req.message, result.reply, metadata, req.system_prompt)

        logger.info(
            "chat session=%s msg_len=%d msg_sha=%s source=%s key=%s fallbacks=%d latency_ms=%d",
            hashlib.sha256(req.session_id.encode()).hexdigest()[:8],
            len(req.message),
            hashlib.sha256(req.message.encode()).hexdigest()[:8],
            metadata["resolution_source"],
            result.key_id,
            result.fallback_attempts,
            metadata["latency_ms"],
        )
        return {"session_id": req.session_id, "reply": result.reply, "metadata": metadata}
