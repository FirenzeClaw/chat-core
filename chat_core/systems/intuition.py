"""IntuitionEngine — 三级降级推理 (Spec 009)

L1: 记忆命中 → 零 LLM 快速回复
L2: Fast Path → 单次 Flash 调用
L3: 完整 ReAct → 原始路径
"""

from __future__ import annotations

import random
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import (
    AttentionStateEnum,
    ChainedMemory,
    IntuitionLevel,
    IntuitionResult,
)


class IntuitionEngine:
    """直觉引擎：根据记忆命中、注意力和精力状态选择推理深度。"""

    def __init__(self) -> None:
        cfg = get_config()
        ic = cfg.intuition_config()
        self._enabled: bool = bool(ic.get("enabled", True))

        l1 = ic.get("level1", {})
        self._l1_min_hits: int = int(l1.get("min_memory_hits", 5))
        self._l1_min_salience: float = float(l1.get("min_salience", 7))

        l2 = ic.get("level2", {})
        self._l2_confidence_threshold: float = float(l2.get("confidence_threshold", 0.7))

        sm = ic.get("state_modulation", {})
        self._focused_l1_boost: float = float(sm.get("focused_l1_boost", 1.5))
        self._dull_l3_boost: float = float(sm.get("dull_l3_boost", 2.0))
        self._low_energy_l1_boost: float = float(sm.get("low_energy_l1_boost", 1.3))

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── 主入口 ──────────────────────────────────────────────

    def evaluate(
        self,
        memory_results: list[ChainedMemory],
        attention_state: AttentionStateEnum | None = None,
        energy: float = 1.0,
        user_message: str = "",
    ) -> IntuitionResult:
        """评估直觉级别，返回是否可跳过完整 ReAct。

        Args:
            memory_results: Spec 003 search_chained 结果
            attention_state: 当前注意力状态
            energy: 当前精力值 [0, 1]
            user_message: 用户消息原文

        Returns:
            IntuitionResult: 包含推荐级别和可能的快速回复
        """
        if not self._enabled:
            return IntuitionResult()

        # ① L1 检测：强记忆命中
        l1_result = self._check_l1(memory_results, attention_state, energy)
        if l1_result.skip_react:
            return l1_result

        # ② L2 检测：中置信度 → Fast Path
        l2_result = self._check_l2(memory_results, attention_state, energy, user_message)
        if l2_result.skip_react:
            return l2_result

        # ③ L3：完整 ReAct（状态调制概率）
        return self._check_l3(attention_state, energy)

    # ── L1: 记忆命中 ───────────────────────────────────────

    def _check_l1(
        self,
        memory_results: list[ChainedMemory],
        attention_state: AttentionStateEnum | None,
        energy: float,
    ) -> IntuitionResult:
        """强记忆命中 ≥ 5 条且 max salience ≥ 7 → 直接快速回复"""
        if len(memory_results) < self._l1_min_hits:
            return IntuitionResult()

        max_salience = max((cm.entry.salience for cm in memory_results), default=0)
        if max_salience < self._l1_min_salience:
            return IntuitionResult()

        # 状态调制
        prob = self._l1_base_prob(attention_state, energy)
        if random.random() > prob:
            return IntuitionResult()

        # 合成快速回复
        replies = self._synthesize_reply(memory_results)
        return IntuitionResult(
            level=IntuitionLevel.L1_MEMORY_MATCH,
            fast_reply=replies,
            inner_thoughts="[直觉回复] 基于强记忆直接反应",
            skip_react=True,
        )

    def _l1_base_prob(
        self,
        attention_state: AttentionStateEnum | None,
        energy: float,
    ) -> float:
        """计算 L1 实际触发概率（状态调制）"""
        prob = 0.8  # base probability when conditions met

        if attention_state == AttentionStateEnum.FOCUSED:
            prob *= self._focused_l1_boost
        elif attention_state == AttentionStateEnum.DULL:
            prob *= 0.5

        if energy < 0.3:
            prob *= self._low_energy_l1_boost

        return min(prob, 0.95)

    def _synthesize_reply(self, memory_results: list[ChainedMemory]) -> str:
        """模板拼接高 salience 记忆摘要生成快速回复"""
        top = sorted(memory_results, key=lambda cm: cm.entry.salience, reverse=True)[:3]
        lines = ["我记得这些："]
        for cm in top:
            e = cm.entry
            val = e.value
            if isinstance(val, dict):
                text = next((str(v) for v in val.values() if isinstance(v, str) and v.strip()), "")
            else:
                text = str(val)
            if text:
                lines.append(f"- {text[:100]}")
        return "基于我们的过往交流，" + "；".join(lines).replace("我记得这些：基于我们的过往交流，", "")

    # ── L2: Fast Path ──────────────────────────────────────

    def _check_l2(
        self,
        memory_results: list[ChainedMemory],
        attention_state: AttentionStateEnum | None,
        energy: float,
        user_message: str,
    ) -> IntuitionResult:
        """中等置信度 → 单次 Flash 调用（置信度由调用方通过 Flash 返回值判定）"""
        # L2 的 LLM 调用由调用方（loop.py）执行
        # 这里只做判定：是否 ATTEMPT L2
        if attention_state == AttentionStateEnum.DULL and random.random() > 0.3:
            return IntuitionResult()  # DULL 态大概率跳过 L2

        # L2 attempt: 返回结果标记需要 Fast Path 调用
        return IntuitionResult(
            level=IntuitionLevel.L2_FAST_PATH,
            skip_react=False,  # 由调用方在 Flash 调用后判定
            confidence=0.0,    # 调用方会填充
        )

    # ── L3: 完整 ReAct ─────────────────────────────────────

    def _check_l3(
        self,
        attention_state: AttentionStateEnum | None,
        energy: float,
    ) -> IntuitionResult:
        """L3 = 原始完整 ReAct"""
        # DULL 态 L3 概率 boost（不是跳过，而是强迫自己认真）
        return IntuitionResult(
            level=IntuitionLevel.L3_FULL_REACT,
            skip_react=False,
        )

    # ── L2 置信度判定 ─────────────────────────────────────

    def eval_fast_path_confidence(self, reply_text: str, inner_thoughts: str) -> float:
        """从 Flash 返回值判定置信度。

        启发式：回复长度 ≥ 50 字符 = 高置信度。
        可扩展为 LLM 自评模式。
        """
        if len(reply_text) >= 50:
            return max(0.7, min(0.95, len(reply_text) / 200))
        return 0.4
