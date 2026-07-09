"""竞态追踪器 — 监控活跃子 Session 数量，向 EmotionEngine 输出竞态指标"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("chat_core.qq.race_tracker")


class RaceTracker:
    """追踪并发活跃子 Session 数量，计算竞态严重程度。

    竞态指标：
    - active_count: 当前活跃子 Session 数量
    - severity: "low" (1-2), "medium" (3-4), "high" (5+)
    """

    def __init__(self):
        self._active_count: int = 0
        self._emotion_engine: Any = None

    @property
    def active_count(self) -> int:
        return self._active_count

    @property
    def severity(self) -> str:
        if self._active_count <= 2:
            return "low"
        elif self._active_count <= 4:
            return "medium"
        return "high"

    def attach_emotion(self, emotion_engine: Any) -> None:
        """绑定全局 EmotionEngine，竞态变化时联动情绪"""
        self._emotion_engine = emotion_engine

    def enter(self) -> None:
        """子 Session 开始 — 竞态计数 +1，联动情绪"""
        self._active_count += 1
        if self._emotion_engine:
            try:
                factor = 0.1 * self._active_count
                self._emotion_engine.accelerate("anger", factor)
            except Exception:
                pass
        logger.debug("RaceTracker enter: count=%d severity=%s", self._active_count, self.severity)

    def exit(self) -> None:
        """子 Session 结束 — 竞态计数 -1"""
        if self._active_count > 0:
            self._active_count -= 1
        logger.debug("RaceTracker exit: count=%d severity=%s", self._active_count, self.severity)
