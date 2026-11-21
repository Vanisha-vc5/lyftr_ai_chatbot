"""Session and message persistence."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.db import connect

_INSERT_MESSAGE = "INSERT INTO messages (session_id, role, content, metadata, created_at) VALUES (?, ?, ?, ?, ?)"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SessionRepository:
    def __init__(self, path: Path) -> None:
        self.path = path

    # ---- sync implementations (run in a thread) -------------------------
    def _get_system_prompt(self, session_id: str) -> str | None:
        conn = connect(self.path)
        try:
            row = conn.execute("SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
            return row["system_prompt"] if row else None
        finally:
            conn.close()

    def _get_recent(self, session_id: str, limit: int) -> list[dict[str, str]]:
        conn = connect(self.path)
        try:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        finally:
            conn.close()

    def _get_all(self, session_id: str) -> list[dict[str, Any]]:
        conn = connect(self.path)
        try:
            rows = conn.execute(
                "SELECT role, content, metadata FROM messages WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        finally:
            conn.close()
        out: list[dict[str, Any]] = []
        for r in rows:
            item: dict[str, Any] = {"role": r["role"], "content": r["content"]}
            if r["role"] == "assistant":
                item["metadata"] = json.loads(r["metadata"]) if r["metadata"] else {}
            out.append(item)
        return out

    def _save_exchange(
        self,
        session_id: str,
        user_msg: str,
        assistant_msg: str,
        metadata: dict[str, Any],
        system_prompt_override: str | None,
    ) -> None:
        now = _now()
        conn = connect(self.path)
        try:
            with conn:  # one transaction
                conn.execute(
                    """
                    INSERT INTO sessions (session_id, system_prompt, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                      system_prompt = COALESCE(excluded.system_prompt, sessions.system_prompt),
                      updated_at = excluded.updated_at
                    """,
                    (session_id, system_prompt_override, now, now),
                )
                conn.execute(
                    _INSERT_MESSAGE,
                    (session_id, "user", user_msg, None, now),
                )
                conn.execute(
                    _INSERT_MESSAGE,
                    (session_id, "assistant", assistant_msg, json.dumps(metadata), now),
                )
        finally:
            conn.close()

    # ---- async API -------------------------------------------------------
    async def get_system_prompt(self, session_id: str) -> str | None:
        return await asyncio.to_thread(self._get_system_prompt, session_id)

    async def get_recent_history(self, session_id: str, limit: int) -> list[dict[str, str]]:
        return await asyncio.to_thread(self._get_recent, session_id, limit)

    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._get_all, session_id)

    async def save_exchange(
        self,
        session_id: str,
        user_msg: str,
        assistant_msg: str,
        metadata: dict[str, Any],
        system_prompt_override: str | None,
    ) -> None:
        await asyncio.to_thread(
            self._save_exchange, session_id, user_msg, assistant_msg, metadata, system_prompt_override
        )
