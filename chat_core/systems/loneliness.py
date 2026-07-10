"""LonelinessDetector — 孤独驱动维度 (Spec 011)"""

from __future__ import annotations

import math, time
from chat_core.config import get_config
from chat_core.core.types import LonelinessState, RelationshipStage


class LonelinessDetector:
    def __init__(self) -> None:
        cfg = get_config()
        lc = cfg.loneliness_config()
        self._enabled = bool(lc.get("enabled", True))
        self._halflife = float(lc.get("decay_halflife", 1200))
        self._require_close = bool(lc.get("require_close_relationship", True))
        self._state = LonelinessState()

    @property
    def enabled(self) -> bool: return self._enabled

    @property
    def level(self) -> float: return self._state.level

    def tick(self, wall_dt: float, relationships: list[tuple[str, str]],
             subjective_speed: float = 1.0) -> float:
        """每 tick 更新孤独水平。

        Args:
            wall_dt: 墙钟流逝秒数
            relationships: [(user_id, stage_value), ...]
            subjective_speed: 主观时钟速度因子 (>1 = 时间过得快)
        """
        if not self._enabled:
            return 0.0

        has_close = any(stage in ("friend", "close_friend") for _, stage in relationships)
        self._state.has_close_relationship = has_close

        if self._require_close and not has_close:
            self._state.level = 0.0
            return 0.0

        effective_dt = wall_dt * subjective_speed
        decay = math.exp(-effective_dt / self._halflife)
        self._state.level = max(0.0, min(1.0, 1.0 - decay * (1.0 - self._state.level)))
        self._state.last_tick = time.time()
        return self._state.level
