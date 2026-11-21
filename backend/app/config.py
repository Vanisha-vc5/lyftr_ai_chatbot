"""Application settings, loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm.prompts import DEFAULT_SYSTEM_PROMPT


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    llm_api_keys: str = Field(default="", alias="LLM_API_KEYS")
    llm_model: str = Field(default="groq/openai/gpt-oss-20b", alias="LLM_MODEL")
    llm_models: str = Field(default="", alias="LLM_MODELS")
    llm_temperature: float = Field(default=0.3, alias="LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=1024, alias="LLM_MAX_TOKENS")
    llm_timeout_seconds: float = Field(default=30.0, alias="LLM_TIMEOUT_SECONDS")
    llm_reasoning_effort: str = Field(default="low", alias="LLM_REASONING_EFFORT")

    database_url: str = Field(default="sqlite:////data/app.db", alias="DATABASE_URL")

    cache_type: str = Field(default="disk", alias="CACHE_TYPE")
    cache_dir: str = Field(default="/data/litellm_cache", alias="CACHE_DIR")
    cache_ttl_seconds: int = Field(default=86400, alias="CACHE_TTL_SECONDS")

    allowed_origins: str = Field(default="http://localhost:3000", alias="ALLOWED_ORIGINS")
    allowed_origin_regex: str = Field(default="", alias="ALLOWED_ORIGIN_REGEX")

    enable_failure_simulation: bool = Field(default=False, alias="ENABLE_FAILURE_SIMULATION")
    default_system_prompt: str = Field(default=DEFAULT_SYSTEM_PROMPT, alias="DEFAULT_SYSTEM_PROMPT")
    history_max_messages: int = Field(default=20, alias="HISTORY_MAX_MESSAGES")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    enable_api_docs: bool = Field(default=False, alias="ENABLE_API_DOCS")
    port: int = Field(default=8000, alias="PORT")

    # ---- derived values -------------------------------------------------
    @cached_property
    def api_keys(self) -> list[str]:
        return _split_csv(self.llm_api_keys)

    @cached_property
    def key_ids(self) -> list[str]:
        return [f"key_{i}" for i in range(1, len(self.api_keys) + 1)]

    @cached_property
    def models_by_key(self) -> dict[str, str]:
        """Model string per key id; blank / missing entries in LLM_MODELS fall back to LLM_MODEL."""
        overrides = [m.strip() for m in self.llm_models.split(",")] if self.llm_models.strip() else []
        result: dict[str, str] = {}
        for i, key_id in enumerate(self.key_ids):
            override = overrides[i] if i < len(overrides) else ""
            result[key_id] = override or self.llm_model
        return result

    @cached_property
    def origins(self) -> list[str]:
        return _split_csv(self.allowed_origins)

    @cached_property
    def sqlite_path(self) -> Path:
        url = self.database_url.strip()
        prefix = "sqlite:///"
        if not url.startswith(prefix):
            raise ValueError("DATABASE_URL must start with sqlite:///")
        # sqlite:////abs/path -> "/abs/path"; sqlite:///rel/path -> "rel/path"
        return Path(url[len(prefix) :])

    @property
    def cache_enabled(self) -> bool:
        return self.cache_type.lower() in {"disk", "local"}
