"""HistoryManager — JSONL 格式聊天历史持久化"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


class HistoryManager:
    """JSONL 格式聊天历史管理。

    每条记录一行 JSON，格式:
    {
        "timestamp": "ISO-8601",
        "role": "user" | "assistant" | "system",
        "content": "...",
        "turn_id": "...",
        "brain_metadata": { ... }   # assistant only
    }
    """

    def __init__(self, user_id: str = "default", base_dir: str | Path = "./data/history"):
        self._user_id = user_id
        self._base_dir = Path(base_dir)
        self._file_path = self._base_dir / f"{user_id}.jsonl"

    def _ensure_dir(self) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)

    # ── 写入 ──────────────────────────────────────────────────

    def append(
        self,
        role: str,
        content: str,
        turn_id: str = "",
        brain_metadata: dict[str, Any] | None = None,
    ) -> None:
        """追加一条历史记录"""
        self._ensure_dir()
        record: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "role": role,
            "content": content,
            "turn_id": turn_id,
        }
        if brain_metadata and role == "assistant":
            record["brain_metadata"] = brain_metadata

        with open(self._file_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ── 读取 ──────────────────────────────────────────────────

    def read(self, limit: int = 50) -> list[dict[str, Any]]:
        """读取最近 limit 条历史记录"""
        if not self._file_path.exists():
            return []

        records: list[dict[str, Any]] = []
        with open(self._file_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        return records[-limit:]

    def get_turns(self, limit: int = 20) -> list[dict[str, Any]]:
        """获取最近 limit 个完整的 turn（user + assistant 对）"""
        raw = self.read(limit * 2)  # rough estimate
        turns: list[dict[str, Any]] = []
        current_turn: dict[str, Any] | None = None

        for record in raw:
            if record["role"] == "user":
                if current_turn:
                    turns.append(current_turn)
                current_turn = {"user": record, "assistant": None}
            elif record["role"] == "assistant" and current_turn:
                current_turn["assistant"] = record
                turns.append(current_turn)
                current_turn = None

        if current_turn and current_turn.get("assistant"):
            turns.append(current_turn)

        return turns[-limit:]

    @property
    def file_path(self) -> Path:
        return self._file_path
