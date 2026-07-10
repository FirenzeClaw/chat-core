"""DefenseEngine — 心理防御机制：DENIAL / RATIONALIZE / PROJECT

Spec 005: 审查发现错误时，基于 impulsiveness 和条件修饰决定是直接纠正
还是启动三种防御之一。防御判定在 _async_review_and_decide() 中接入，
通过 DefenseResult 驱动后续的 subconscious 写入、情绪调整和沉默累积。
"""

from __future__ import annotations

import random
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import DefenseResult, DefenseType, MetaParamOverrides, ReviewResult


class DefenseEngine:
    """心理防御引擎。

    读取 config.yaml → systems.emotion.defense 配置。
    evaluate() 返回 DefenseResult，由 TurnManager._apply_defense() 执行。
    """

    def __init__(self) -> None:
        cfg = get_config()
        dc = cfg.emotion_config().get("defense", {})
        self._enabled: bool = bool(dc.get("enabled", True))

        mods = dc.get("condition_modifiers", {})
        self._self_threat_boost: float = float(mods.get("self_threat_boost", 2.0))
        self._repeat_error_boost: float = float(mods.get("repeat_error_boost", 1.5))
        self._emotion_shock_boost: float = float(mods.get("emotion_shock_boost", 2.0))

        # 防御类型权重（从 config 读取，默认按设计文档）
        tw = dc.get("type_weights", {})
        self._type_weights: dict[DefenseType, float] = {
            DefenseType.DENIAL: float(tw.get("denial", 0.35)),
            DefenseType.RATIONALIZE: float(tw.get("rationalize", 0.40)),
            DefenseType.PROJECT: float(tw.get("projection", 0.25)),
        }

        # 脆弱感调制因子 (来自 config → systems.emotion.vulnerability.modulation.defense_prob)
        vc = cfg.emotion_config().get("vulnerability", {})
        vmod = vc.get("modulation", {})
        self._vulnerability_defense_mod: float = float(vmod.get("defense_prob", 0.3))

    # ── 公共 API ───────────────────────────────────────────────

    def evaluate(
        self,
        review: ReviewResult,
        error_history: dict[str, int],
        impulsiveness: float,
        last_compound_delta: float = 0.0,
        is_vulnerable: bool = False,
        meta_overrides: "MetaParamOverrides | None" = None,
        turn_counter: int = 0,
        value_engine: Any = None,  # Spec 010
        relationship_modulation: Any = None,  # Spec 008: RelationshipModulation
    ) -> DefenseResult:
        """返回防御判定结果。

        Args:
            review: 审查结果
            error_history: error_type → 累计次数（由 TurnManager 维护，DefenseEngine 只读）
            impulsiveness: 人格 impulse 值
            last_compound_delta: EmotionEngine 上一 tick 的最大复合情绪变动
            is_vulnerable: 脆弱状态标志
            meta_overrides: Spec 006 元认知参数覆盖
            turn_counter: 当前 turn 编号（用于过期检查）

        Returns:
            DefenseResult，defense_type=DIRECT 表示不触发防御
        """
        # 防御禁用或静默决策 → 直接返回 DIRECT
        if not self._enabled or review.decision.value == "silence":
            return DefenseResult(defense_type=DefenseType.DIRECT)

        # base_prob = 1.0 - impulsiveness
        base_prob = max(0.0, 1.0 - impulsiveness)

        # 条件修饰乘法链
        modifier = 1.0
        if self._is_self_threatened(review):
            modifier *= self._self_threat_boost
        for count in error_history.values():
            if count >= 2:
                modifier *= self._repeat_error_boost
                break
        if abs(last_compound_delta) > 0.4:
            modifier *= self._emotion_shock_boost
        if is_vulnerable:
            modifier *= self._vulnerability_defense_mod

        final_prob = min(base_prob * modifier, 0.95)

        # Spec 010: 价值观基线调制 (self_honesty factor)
        if value_engine is not None:
            final_prob *= value_engine.get_modulation("defense_prob_multiplier")

        # Spec 008: 关系阶段调制防御概率
        if relationship_modulation is not None:
            final_prob *= relationship_modulation.defense_prob_mult

        # Spec 006: 元认知参数调制
        if meta_overrides is not None and not meta_overrides.is_expired(turn_counter):
            final_prob *= meta_overrides.defense_prob_multiplier

        final_prob = min(final_prob, 0.95)
        if random.random() > final_prob:
            return DefenseResult(defense_type=DefenseType.DIRECT)

        # 按 type_weights 随机抽样防御类型
        return self._select_defense(review, error_history)

    # ── 内部判定逻辑 ──────────────────────────────────────────

    def _is_self_threatened(self, review: ReviewResult) -> bool:
        """判定审查错误是否威胁自我认知。

        条件：审查中涉及的冲突 memory key 属于 self/* 命名空间。
        conflicting_memory_key 格式为 "self/feelings/..." 或类似路径。
        若为 True，DENIAL 概率 × self_threat_boost。
        """
        for e in review.logic_errors:
            key = e.conflicting_memory_key
            if not key:
                continue
            # 关键纠错：使用 startswith("self/") 而非不存在的 namespace 属性
            if key.startswith("self/"):
                return True
        return False

    def _select_defense(
        self, review: ReviewResult, error_history: dict[str, int]
    ) -> DefenseResult:
        """按 type_weights 随机抽样防御类型，构造 DefenseResult。

        条件加权：
        - self_threat → DENIAL 权重 × self_threat_boost
        - repeat_error ≥ 2 → RATIONALIZE 权重 × repeat_error_boost
        """
        types = list(self._type_weights.keys())
        weights = list(self._type_weights.values())

        # 条件加权
        if self._is_self_threatened(review):
            idx = types.index(DefenseType.DENIAL)
            weights[idx] *= self._self_threat_boost
        if any(c >= 2 for c in error_history.values()):
            idx = types.index(DefenseType.RATIONALIZE)
            weights[idx] *= self._repeat_error_boost

        chosen = random.choices(types, weights=weights, k=1)[0]
        return self._build_result(chosen, review)

    def _build_result(self, defense_type: DefenseType, review: ReviewResult) -> DefenseResult:
        """构造指定类型的 DefenseResult。

        三种路径的核心差异：
        - DENIAL: 不写 correction, silence_increment=1
        - RATIONALIZE: correction 含自我辩护文本, silence_increment=0
        - PROJECT: correction 归因转向用户, 情绪偏移 guilt↓ anger↑
        """
        errors = [e.description for e in review.logic_errors[:2]]
        error_str = "；".join(errors) if errors else "审查发现问题"

        if defense_type == DefenseType.DENIAL:
            return DefenseResult(
                defense_type=DefenseType.DENIAL,
                correction_text=None,
                inner_reflection=f"否认了关于{error_str}的错误，认为记忆可能不准确",
                defense_awareness=f"[自我感知] 你之前有防御反应，拒绝了关于{error_str}的纠正",
                emotion_delta={"guilt": 0.05, "resentment": 0.02},
                silence_increment=1,
            )
        elif defense_type == DefenseType.RATIONALIZE:
            return DefenseResult(
                defense_type=DefenseType.RATIONALIZE,
                correction_text=f"{error_str} (自我辩护: 这种情况很复杂，不完全是我的错)",
                inner_reflection=f"意识到了{error_str}，但做了合理化解释",
                defense_awareness=f"[自我感知] 你意识到{error_str}但仍做了合理化解释",
                emotion_delta={"guilt": -0.02, "gratification": 0.03},
                silence_increment=0,
            )
        else:  # PROJECT
            return DefenseResult(
                defense_type=DefenseType.PROJECT,
                correction_text=f"用户表达可能造成误解（原问题: {error_str}），后续注意澄清",
                inner_reflection="把部分错误归因于外部因素",
                defense_awareness="[自我感知] 你把部分错误归因于外部因素",
                emotion_delta={"guilt": -0.05, "anger": 0.03},
                silence_increment=0,
            )
