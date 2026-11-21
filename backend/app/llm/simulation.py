"""X-Simulate-Failure support.

Failures are injected from a LiteLLM ``async_pre_call_deployment_hook``: the hook raises a real
LiteLLM exception for the chosen deployment, so the Router's own fallback logic handles it exactly
like a genuine provider error. The plan is carried in a ContextVar, so it only affects the
request that set it.
"""

from __future__ import annotations

import contextvars
import logging
from typing import Any

import litellm
from litellm.integrations.custom_logger import CustomLogger

logger = logging.getLogger("vani.simulation")

ALLOWED_STATUSES = frozenset({429, 500, 502, 503})

SIM_PLAN: contextvars.ContextVar[dict[str, int] | None] = contextvars.ContextVar("SIM_PLAN", default=None)
# Mutable list per request so appends made in copied contexts remain visible to the caller.
ATTEMPTS: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar("ATTEMPTS", default=None)

_EXC = {
    429: litellm.RateLimitError,
    500: litellm.InternalServerError,
    502: litellm.BadGatewayError,
    503: litellm.ServiceUnavailableError,
}


def parse_simulation_header(value: str | None, key_ids: list[str]) -> dict[str, int]:
    """Parse ``key_1=429,key_2=500`` / ``*=429``. Invalid entries are ignored with a WARNING."""
    plan: dict[str, int] = {}
    if not value:
        return plan
    valid_ids = {k.lower(): k for k in key_ids}
    for raw in value.split(","):
        entry = raw.strip()
        if not entry:
            continue
        if "=" not in entry:
            logger.warning("X-Simulate-Failure: ignoring malformed entry %r", entry)
            continue
        key_part, status_part = (p.strip() for p in entry.split("=", 1))
        try:
            status = int(status_part)
        except ValueError:
            logger.warning("X-Simulate-Failure: ignoring non-numeric status in %r", entry)
            continue
        if status not in ALLOWED_STATUSES:
            logger.warning("X-Simulate-Failure: ignoring unsupported status %s", status)
            continue
        if key_part == "*":
            plan["*"] = status
        elif key_part.lower() in valid_ids:
            plan[valid_ids[key_part.lower()]] = status
        else:
            logger.warning("X-Simulate-Failure: ignoring unknown key id %r", key_part)
    return plan


def status_for(plan: dict[str, int], key_id: str | None) -> int | None:
    """A specific key entry overrides the ``*`` wildcard."""
    if not plan or key_id is None:
        return None
    return plan.get(key_id, plan.get("*"))


class FailureSimulator(CustomLogger):
    def __init__(self, key_id_by_api_key: dict[str, str] | None = None) -> None:
        super().__init__()
        self.key_id_by_api_key = key_id_by_api_key or {}

    def _key_id(self, kwargs: dict[str, Any]) -> str | None:
        for container in (kwargs, kwargs.get("metadata") or {}, kwargs.get("litellm_metadata") or {}):
            info = container.get("model_info") if isinstance(container, dict) else None
            if isinstance(info, dict) and info.get("id"):
                return str(info["id"])
        api_key = kwargs.get("api_key")
        if isinstance(api_key, str):
            return self.key_id_by_api_key.get(api_key)
        return None

    async def async_pre_call_deployment_hook(self, kwargs: dict[str, Any], call_type: Any) -> dict[str, Any]:
        plan = SIM_PLAN.get()
        if not plan:
            return kwargs
        key_id = self._key_id(kwargs)
        status = status_for(plan, key_id)
        if status:
            attempts = ATTEMPTS.get()
            if attempts is not None:
                attempts.append({"key_id": key_id, "status": status, "simulated": True})
            raise _EXC[status](
                message=f"simulated {status} for {key_id}",
                llm_provider="simulated",
                model=str(kwargs.get("model", "unknown")),
            )
        return kwargs
