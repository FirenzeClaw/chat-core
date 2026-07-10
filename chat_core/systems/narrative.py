"""NarrativeEngine — 自我叙事系统 (Spec 010)

定期 LLM 生成完整叙述 + 事件驱动规则追加章节。
全局单例：自我叙述是"我是谁"的故事，不随对话者变化。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import NarrativeEntry, NarrativeState

logger = logging.getLogger(__name__)


class NarrativeEngine:
    """自我叙事引擎 — 定期生成 + 事件章节 + 存储管理。"""

    def __init__(self) -> None:
        cfg = get_config()
        nc = cfg.narrative_config()
        self._enabled: bool = bool(nc.get("enabled", True))
        self._periodic_interval: int = int(nc.get("periodic_interval", 10))
        self._max_length: int = int(nc.get("max_length", 300))
        self._timeline_keep: int = int(nc.get("storage", {}).get("timeline_keep", 30))

        ed = nc.get("event_driven", {})
        self._ev_stage_change: bool = bool(ed.get("stage_change", True))
        self._ev_moral_conflict: bool = bool(ed.get("moral_conflict", True))
        self._ev_silence_streak: int = int(ed.get("silence_streak", 3))
        self._ev_deep_memory: bool = bool(ed.get("deep_memory_new", True))
        self._ev_vulnerability: bool = bool(ed.get("vulnerability", True))

        self._state = NarrativeState()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def state(self) -> NarrativeState:
        return self._state

    # ── 事件驱动追加 ──────────────────────────────────────

    def append_chapter(self, event_type: str, text: str, turn: int = 0) -> None:
        """追加事件驱动的叙事章节（纯规则，零 LLM）。

        Args:
            event_type: "stage_change" | "silence_streak" | "deep_memory" | "moral_conflict" | "vulnerability"
            text: 章节文本
            turn: 触发 turn
        """
        if not self._enabled:
            return

        if event_type == "stage_change" and not self._ev_stage_change:
            return
        if event_type == "vulnerability" and not self._ev_vulnerability:
            return

        entry = NarrativeEntry(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            event_type=event_type,
            text=text,
            turn=turn,
        )
        self._state.chapters.append(entry)
        if len(self._state.chapters) > 50:
            self._state.chapters = self._state.chapters[-50:]

    # ── 定期生成 (LLM — 由 LogicBrain.narrative_pass() 执行) ──

    def build_narrative_context(self, value_engine: Any = None) -> str:
        """构建传给 LogicBrain 的叙事生成上下文。"""
        values_text = ""
        if value_engine:
            v = value_engine.values
            values_text = (
                f"Honesty={v.honesty:.2f}, Care={v.care:.2f}, Growth={v.growth:.2f}\n"
                f"  self_honesty={v.self_honesty:.2f}, loyalty={v.loyalty:.2f}, "
                f"nurturing={v.nurturing:.2f}"
            )

        recent_chapters = [c.text for c in self._state.chapters[-5:]]
        chapters_text = "\n".join(f"  - {c}" for c in recent_chapters) if recent_chapters else "无"

        return (
            f"请更新你对自己的认知叙述（≤{self._max_length}字）：\n\n"
            f"【当前价值观】\n{values_text}\n\n"
            f"【最近的经历】\n{chapters_text}\n\n"
            f"【上一版自我叙述】\n{self._state.latest or '（尚无）'}\n\n"
            f"请生成新的自我叙述，包含：1) 核心自我认知 (1句) "
            f"2) 最近的变化 (2-3句) 3) 正在努力的方向 (1句)"
        )

    def update_latest(self, text: str) -> None:
        """更新最新完整叙述（由 narrative_pass 回调）。"""
        if text:
            self._state.latest = text

    # ── System Prompt 注入 ────────────────────────────────

    def get_system_injection(self) -> str:
        """返回可注入 system prompt 的叙述文本。"""
        if not self._state.latest:
            return ""
        parts = [f"[自我叙述] {self._state.latest}"]
        recent = [c.text for c in self._state.chapters[-3:]]
        if recent:
            parts.append("[最近的思考] " + " / ".join(recent))
        return "\n".join(parts)
