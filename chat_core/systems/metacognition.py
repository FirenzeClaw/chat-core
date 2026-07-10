"""MetacognitionEngine — 元认知深度系统 (Spec 006)

定期 + 异常双触发审视，产出文本洞察 + 结构化参数调节。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import (
    SELF_CRITICISM_KEYWORDS,
    DecisionType,
    MemoryEntry,
    MetacognitionReport,
    MetaParamOverrides,
)
from chat_core.systems.emotion import COMPOUND_DIMS

logger = logging.getLogger(__name__)


class MetacognitionEngine:
    """元认知引擎：触发判定 + 上下文组装。

    在 TurnManager._async_review_and_decide() 结束后调用 check_triggers()。
    触发后调用 build_context() 组装上下文，传给 LogicBrain.metacognition_pass()。
    """

    def __init__(self) -> None:
        cfg = get_config()
        mc = cfg.metacognition_config()
        self._enabled: bool = bool(mc.get("enabled", True))
        self._periodic_interval: int = int(mc.get("periodic_interval", 5))

        ad = mc.get("anomaly_detection", {})
        self._review_streak: int = int(ad.get("review_streak", 3))
        self._defense_streak: int = int(ad.get("defense_streak", 2))
        self._self_criticism_streak: int = int(ad.get("self_criticism_streak", 3))

        kw = ad.get("self_criticism_keywords", [])
        self._criticism_keywords: list[str] = kw if kw else SELF_CRITICISM_KEYWORDS

        self._confidence_threshold: float = float(mc.get("confidence_threshold", 0.6))
        self._expiry_turns: int = int(mc.get("override_expiry_turns", 5))

        # 异常计数器（触发后重置）
        self._review_streak_counter: int = 0
        self._defense_streak_counter: int = 0
        self._self_criticism_counter: int = 0
        self._last_review_decision: str | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── 触发判定 ──────────────────────────────────────────

    def check_triggers(
        self,
        turn_counter: int,
        review_decision: DecisionType | None,
        had_defense: bool,
        inner_thoughts_text: str | None,
        compound_delta: float = 0.0,
    ) -> bool:
        """检查是否应触发元认知审视。

        Args:
            turn_counter: 当前 turn 编号
            review_decision: 本轮审查决策
            had_defense: 本轮是否激活了防御
            inner_thoughts_text: 本轮内心戏文本
            compound_delta: 本轮最大复合情绪变动（来自 EmotionEngine.last_compound_delta）

        Returns:
            True 表示应触发
        """
        if not self._enabled:
            return False

        triggered = False

        # 1. 定期触发
        if turn_counter % self._periodic_interval == 0:
            triggered = True

        # 2. 审查连续同结论
        if review_decision is not None:
            decision_str = review_decision.value
            if self._last_review_decision == decision_str:
                self._review_streak_counter += 1
            else:
                self._review_streak_counter = 1
                self._last_review_decision = decision_str
            if self._review_streak_counter >= self._review_streak:
                triggered = True
        else:
            self._review_streak_counter = 0
            self._last_review_decision = None

        # 3. 防御连续激活
        if had_defense:
            self._defense_streak_counter += 1
            if self._defense_streak_counter >= self._defense_streak:
                triggered = True
        else:
            self._defense_streak_counter = 0

        # 4. 情绪冲击: |Δcompound| > 0.4（复用 Spec 005 compound_alert）
        if abs(compound_delta) > 0.4:
            triggered = True

        # 5. 自我批评连续出现
        if inner_thoughts_text:
            if any(kw in inner_thoughts_text for kw in self._criticism_keywords):
                self._self_criticism_counter += 1
                if self._self_criticism_counter >= self._self_criticism_streak:
                    triggered = True
            else:
                self._self_criticism_counter = 0
        else:
            self._self_criticism_counter = 0

        # 触发后重置所有异常计数器（同 turn 不重复触发）
        if triggered:
            self._review_streak_counter = 0
            self._defense_streak_counter = 0
            self._self_criticism_counter = 0
            self._last_review_decision = None

        return triggered

    # ── 上下文组装 ────────────────────────────────────────

    def build_context(
        self,
        turn_summaries: list[dict[str, Any]],
        compound_trends: dict[str, list[float]],
        defense_mode_summary: dict[str, Any],
        memory_system_state: dict[str, Any],
        attention_state: str,
        energy_state: dict[str, Any] | None = None,
        subjective_time: dict[str, Any] | None = None,
        vulnerability_history: dict[str, Any] | None = None,
    ) -> str:
        """组装传递给 LogicBrain 的元认知审查上下文。

        各数据源全部由 TurnManager 传入。
        """
        parts: list[str] = []

        # 最近 N 轮摘要
        parts.append("## 最近 N 轮")
        for ts in turn_summaries[-self._periodic_interval:]:
            parts.append(f"  - {json.dumps(ts, ensure_ascii=False)}")

        # 复合情绪趋势
        if compound_trends:
            parts.append("## 复合情绪趋势")
            for dim, values in compound_trends.items():
                if values:
                    trend = " → ".join(f"{v:.2f}" for v in values[-5:])
                    parts.append(f"  {dim}: {trend}")

        # 防御模式总结
        if defense_mode_summary:
            parts.append("## 防御模式总结")
            parts.append(f"  近N轮防御激活率: {defense_mode_summary.get('activation_rate', 0)}")
            parts.append(f"  主要防御类型: {defense_mode_summary.get('main_types', '无')}")
            for entry in defense_mode_summary.get("awareness_entries", []):
                parts.append(f"    - {entry}")

        # 记忆系统状态
        if memory_system_state:
            parts.append("## 记忆系统状态")
            parts.append(f"  平均回溯条目: {memory_system_state.get('avg_recall_count', 0)}")
            parts.append(f"  空回溯次数: {memory_system_state.get('empty_recall_count', 0)}")
            parts.append(f"  衰减预警: {memory_system_state.get('decay_warning_count', 0)}条")
            parts.append(f"  深刻记忆稳固: {memory_system_state.get('deep_memory_count', 0)}条")

        # 注意力状态
        if attention_state:
            parts.append(f"## 当前注意力状态: {attention_state}")

        # Spec 007: 精力与主观时间
        if energy_state:
            parts.append("## 精力与主观时间")
            parts.append(f"  当前精力: {energy_state.get('energy', 0):.2f}")
            if subjective_time:
                parts.append(f"  主观时间: speed_factor={subjective_time.get('speed_factor', 1.0):.2f}")

        # Spec 005 §9: 脆弱历史
        if vulnerability_history:
            parts.append("## 脆弱历史")
            parts.append(f"  当前是否脆弱: {vulnerability_history.get('is_vulnerable', False)}")
            parts.append(f"  冷却剩余: {vulnerability_history.get('cooldown_remaining', 0)}轮")

        return "\n".join(parts)
