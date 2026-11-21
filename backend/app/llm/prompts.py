"""Static prompts. Never put dates, session ids or anything request-specific here:
the system prompt is part of the cache key, so it must be identical across sessions."""

DEFAULT_SYSTEM_PROMPT = (
    "You are Vani, a helpful and concise AI assistant. "
    "Answer clearly and accurately, and keep replies short unless the user asks for detail."
)
