"""ValueEngine — 价值体系引擎 (Spec 010)

三层美德树 (Honesty/Care/Growth × 3 子价值观) + 动态调权 + 决策调制。
全局单例：价值观是 AI 的"核心自我"，所有用户共享。
"""

from __future__ import annotations

import logging
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import ValueSystem

logger = logging.getLogger(__name__)

# 美德 → 子价值观映射
VIRTUE_CHILDREN: dict[str, list[str]] = {
    "honesty": ["truthfulness", "self_honesty", "transparency"],
    "care": ["empathy_protection", "loyalty", "nurturing"],
    "growth": ["curiosity_drive", "self_improvement", "openness"],
}


class ValueEngine:
    """价值观引擎 — 三层树维护 + 事件调权 + 决策调制。"""

    def __init__(self) -> None:
        cfg = get_config()
        vc = cfg.value_config()
        self._enabled: bool = bool(vc.get("enabled", True))

        # 加载初始权重
        virtues = vc.get("virtues", {})
        self._values = ValueSystem(
            honesty=float(virtues.get("honesty", {}).get("weight", 0.7)),
            care=float(virtues.get("care", {}).get("weight", 0.6)),
            growth=float(virtues.get("growth", {}).get("weight", 0.8)),
        )
        for virtue, children in VIRTUE_CHILDREN.items():
            vcfg = virtues.get(virtue, {}).get("children", {})
            for child in children:
                setattr(self._values, child, float(vcfg.get(child, 0.5)))

        # 动态调权参数
        dyn = vc.get("dynamics", {})
        self._dyn_hurt_relation: dict[str, float] = dyn.get("honesty_hurt_relation", {"honesty": -0.03, "care": 0.05})
        self._dyn_silence_regret: dict[str, float] = dyn.get("silence_regret", {"honesty": 0.03, "self_honesty": 0.02})
        self._dyn_metacog_defense: dict[str, float] = dyn.get("metacognition_defense_found", {"self_honesty": 0.05})
        self._dyn_positive_impact: dict[str, float] = dyn.get("positive_impact", {"nurturing": 0.05})
        self._dyn_stage_upgrade: dict[str, float] = dyn.get("stage_upgrade", {"loyalty": 0.05, "honesty": 0.03})

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def values(self) -> ValueSystem:
        return self._values

    # ── 动态调权 ──────────────────────────────────────────

    def adjust(self, event: str, **kwargs: Any) -> None:
        """响应事件调权。

        已实现事件：
          - "metacognition_defense": Spec 006 发现防御 → self_honesty↑
          - "vulnerability": Spec 005 §9 脆弱暴露 → (当前无调权，供 narrative 使用)
          - "positive_impact": 用户积极反馈 → nurturing↑

        Future: Spec 008/009/011 事件钩子就绪
        """
        if not self._enabled:
            return

        deltas: dict[str, float] = {}

        if event == "metacognition_defense":
            deltas = dict(self._dyn_metacog_defense)
        elif event == "positive_impact":
            deltas = dict(self._dyn_positive_impact)
        elif event == "honesty_hurt_relation":
            deltas = dict(self._dyn_hurt_relation)
        elif event == "silence_regret":
            deltas = dict(self._dyn_silence_regret)
        elif event == "stage_upgrade":
            deltas = dict(self._dyn_stage_upgrade)
        elif event == "vulnerability":
            # 脆弱事件不直接调权（情感冲击是 transient），仅用于 narrative
            return

        for attr, delta in deltas.items():
            current = getattr(self._values, attr, 0.0)
            setattr(self._values, attr, max(0.0, min(1.0, current + delta)))

        if deltas:
            logger.debug(f"ValueEngine.adjust({event}): {deltas}")

    # ── 决策调制 ──────────────────────────────────────────

    def get_modulation(self, param: str) -> float:
        """价值观权重 → 决策参数。

        Args:
            param: "review_threshold" | "defense_prob_multiplier" | "moral_bias"

        Returns:
            调制系数或偏移值
        """
        if not self._enabled:
            if param == "review_threshold":
                return 1.0
            elif param == "defense_prob_multiplier":
                return 1.0
            elif param == "moral_bias":
                return 0.5
            return 1.0

        if param == "review_threshold":
            # 高诚实 → 审查更严格 (threshold = base × honesty)
            return self._values.honesty

        elif param == "defense_prob_multiplier":
            # 高自我诚实 → 更少防御 (2.0 - self_honesty)
            return 2.0 - self._values.self_honesty

        elif param == "moral_bias":
            # 道德困境：诚实 vs 保护的倾向
            t = self._values.truthfulness
            e = self._values.empathy_protection
            denom = t + e
            return t / denom if denom > 0 else 0.5

        return 1.0
