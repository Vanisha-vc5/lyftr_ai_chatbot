"""Logging setup with secret redaction."""

from __future__ import annotations

import logging
import re

_GROQ_KEY = re.compile(r"gsk_[A-Za-z0-9]+")


class RedactSecretsFilter(logging.Filter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if len(s) >= 6]

    def _scrub(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return _GROQ_KEY.sub("gsk_***", text)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        scrubbed = self._scrub(message)
        if record.exc_info:
            # Pre-render the traceback so it is scrubbed too.
            formatter = logging.Formatter()
            scrubbed += "\n" + self._scrub(formatter.formatException(record.exc_info))
            record.exc_info = None
            record.exc_text = None
            record.msg, record.args = scrubbed, ()
        elif scrubbed != message:
            record.msg, record.args = scrubbed, ()
        # Untouched records keep their args: uvicorn's access formatter unpacks them.
        return True


def setup_logging(level: str, secrets: list[str]) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    handler = next((h for h in root.handlers if getattr(h, "_vani", False)), None)
    if handler is None:
        handler = logging.StreamHandler()
        handler._vani = True  # type: ignore[attr-defined]
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
    handler.filters = [RedactSecretsFilter(secrets)]
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for h in logging.getLogger(name).handlers:
            h.filters = [RedactSecretsFilter(secrets)]
