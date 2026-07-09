"""PersonalityEngine — 人格参数引擎：8 权重 × 行为映射"""

from __future__ import annotations

from chat_core.config import get_config
from chat_core.core.types import PersonalityWeights

# 默认人格权重
DEFAULT_WEIGHTS = PersonalityWeights(
    curiosity=0.7,
    sociability=0.8,
    playfulness=0.6,
    empathy=0.5,
    assertiveness=0.3,
    creativity=0.6,
    impulsiveness=0.2,
    loyalty=0.75,
)


class PersonalityEngine:
    """人格参数引擎。

    管理 8 个人格权重，提供行为映射方法：
    - LLM 温度调制（playfulness）
    - 回复模式选择（empathy → empathetic/normal）
    - 创造力偏置（creativity）
    - 纠正阈值（impulsiveness）
    - 主动发言频率（sociability）
    - 记忆检索增强（loyalty）
    """

    def __init__(self) -> None:
        cfg = get_config()
        pc = cfg.personality_config()
        initial = pc.get("initial", {})

        self._weights = PersonalityWeights(
            curiosity=float(initial.get("curiosity", DEFAULT_WEIGHTS.curiosity)),
            sociability=float(initial.get("sociability", DEFAULT_WEIGHTS.sociability)),
            playfulness=float(initial.get("playfulness", DEFAULT_WEIGHTS.playfulness)),
            empathy=float(initial.get("empathy", DEFAULT_WEIGHTS.empathy)),
            assertiveness=float(initial.get("assertiveness", DEFAULT_WEIGHTS.assertiveness)),
            creativity=float(initial.get("creativity", DEFAULT_WEIGHTS.creativity)),
            impulsiveness=float(initial.get("impulsiveness", DEFAULT_WEIGHTS.impulsiveness)),
            loyalty=float(initial.get("loyalty", DEFAULT_WEIGHTS.loyalty)),
        )

    # ── 公共属性 ──────────────────────────────────────────────

    @property
    def weights(self) -> PersonalityWeights:
        return self._weights

    # ── 温度调制 ──────────────────────────────────────────────

    def get_llm_temperature(self, brain_type: str) -> float:
        """根据 playfulness 调制 LLM 温度。

        base_temp 从 config 中读取对应 brain 的 temperature，
        叠加 playfulness × 0.3 的增额。

        Args:
            brain_type: 脑类型（"logic", "emotion", "sub_session", "action"）

        Returns:
            调制后的 temperature（clamped to [0.1, 2.0]）
        """
        cfg = get_config()
        base_temp = cfg.brain_config(brain_type).get("temperature", 0.7)
        base_temp = float(base_temp)
        delta = self._weights.playfulness * 0.3
        return max(0.1, min(2.0, base_temp + delta))

    # ── 回复模式 ──────────────────────────────────────────────

    def get_response_mode(self) -> str:
        """根据 empathy 选择回复模式。

        empathy > 0.5 → "empathetic"
        否则 → "normal"
        """
        return "empathetic" if self._weights.empathy > 0.5 else "normal"

    # ── 创造力偏置 ────────────────────────────────────────────

    def get_creativity_bias(self) -> float:
        """返回创造力偏置，影响回复多样性。

        Returns:
            creativity × 0.5，范围 [0.0, 0.5]
        """
        return self._weights.creativity * 0.5

    # ── 纠正阈值 ──────────────────────────────────────────────

    def get_correction_threshold(self) -> float:
        """impulsiveness 影响纠正触发阈值。

        impulsiveness 越高，越容易触发纠正（阈值越低）。

        Returns:
            纠正权重阈值，范围 [0.1, 0.9]
        """
        # impulsiveness=0 → threshold=0.9 (很少纠正)
        # impulsiveness=1 → threshold=0.1 (几乎每次都纠正)
        return max(0.1, 0.9 - self._weights.impulsiveness * 0.8)

    # ── 主动频率 ──────────────────────────────────────────────

    def get_proactive_frequency(self) -> float:
        """sociability 影响主动发言频率。

        Returns:
            sociability 本身，范围 [0.0, 1.0]
        """
        return self._weights.sociability

    # ── 记忆增强 ──────────────────────────────────────────────

    def get_memory_boost(self) -> float:
        """loyalty 提升记忆检索相关性。

        Returns:
            记忆检索 bonus 因子，[1.0, 1.75]
        """
        return 1.0 + self._weights.loyalty * 0.75

    # ── 更新权重 ──────────────────────────────────────────────

    def update_weight(self, attr: str, value: float) -> None:
        """更新单个人格权重（自动钳制到 [0.0, 1.0]）。

        Args:
            attr: 权重名（如 "playfulness"）
            value: 新值
        """
        if not hasattr(self._weights, attr):
            raise ValueError(f"Unknown personality weight: {attr}")
        setattr(self._weights, attr, max(0.0, min(1.0, value)))

    # ── 摘要 ──────────────────────────────────────────────────

    def summary(self) -> dict[str, float]:
        """返回所有权重摘要，用于 /mood 命令展示"""
        return {
            "curiosity": self._weights.curiosity,
            "sociability": self._weights.sociability,
            "playfulness": self._weights.playfulness,
            "empathy": self._weights.empathy,
            "assertiveness": self._weights.assertiveness,
            "creativity": self._weights.creativity,
            "impulsiveness": self._weights.impulsiveness,
            "loyalty": self._weights.loyalty,
        }
