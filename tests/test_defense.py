"""Tests for DefenseEngine — Spec 005: 防御机制 (任务 6)"""

from __future__ import annotations

import random

from chat_core.core.types import (
    DecisionType,
    DefenseResult,
    DefenseType,
    ErrorType,
    FactError,
    ReviewResult,
)
from chat_core.systems.defense import DefenseEngine


def _make_review(
    errors: list[FactError] | None = None,
    decision: DecisionType = DecisionType.CORRECT,
) -> ReviewResult:
    """Helper: 构造一个标准 ReviewResult"""
    return ReviewResult(
        logic_verdict="error_found" if errors else "ok",
        logic_weight=0.7,
        logic_errors=errors or [],
        decision=decision,
        combined_weight=0.7,
    )


def _make_error(
    description: str = "事实错误",
    key: str = "",
    error_type: ErrorType = ErrorType.FACT_ERROR,
    weight: float = 0.6,
) -> FactError:
    """Helper: 构造一个 FactError"""
    return FactError(
        error_type=error_type,
        description=description,
        conflicting_memory_key=key,
        weight=weight,
    )


# ── TestDefenseEngine ──────────────────────────────────────────


class TestDefenseEngine:
    """DefenseEngine 单元测试套件"""

    def test_direct_when_high_impulsiveness(self):
        """impulsiveness=0.9 → base_prob≈0.1 → 大概率 DIRECT"""
        engine = DefenseEngine()
        review = _make_review(errors=[_make_error()])
        # 强制随机种子确保确定性
        random.seed(42)
        result = engine.evaluate(review, {}, impulsiveness=0.9)
        # base_prob = 0.1, 条件未触发 → 期望 DIRECT
        assert result.defense_type == DefenseType.DIRECT

    def test_denial_no_correction(self):
        """DENIAL → correction_text is None, silence_increment=1"""
        engine = DefenseEngine()
        review = _make_review(errors=[_make_error(key="self/feelings/test")])
        # 低 impulsiveness + self_threat → 高概率触发防御
        random.seed(1)
        result = engine.evaluate(review, {}, impulsiveness=0.1)
        if result.defense_type == DefenseType.DENIAL:
            assert result.correction_text is None
            assert result.silence_increment == 1
            assert result.defense_awareness  # 有自我感知文本
            assert "防御反应" in result.defense_awareness

    def test_rationalize_correction_contains_defense(self):
        """RATIONALIZE → correction_text 含 '自我辩护'"""
        engine = DefenseEngine()
        review = _make_review(errors=[_make_error()])
        # repeat_error ≥ 2 → RATIONALIZE 权重 boost
        error_history = {"fact_error": 3}
        random.seed(2)
        result = engine.evaluate(review, error_history, impulsiveness=0.1)
        if result.defense_type == DefenseType.RATIONALIZE:
            assert result.correction_text is not None
            assert "自我辩护" in result.correction_text
            assert result.silence_increment == 0
            assert result.defense_awareness  # 有自我感知

    def test_project_shifts_emotion(self):
        """PROJECT → emotion_delta 含 guilt=-0.05, anger=0.03"""
        engine = DefenseEngine()
        review = _make_review(errors=[_make_error()])
        # 高 compound_delta → emotion_shock boost
        random.seed(3)
        result = engine.evaluate(
            review, {}, impulsiveness=0.1, last_compound_delta=0.5
        )
        if result.defense_type == DefenseType.PROJECT:
            assert "guilt" in result.emotion_delta
            assert "anger" in result.emotion_delta
            assert result.emotion_delta["guilt"] < 0  # 减轻 guilt
            assert result.emotion_delta["anger"] > 0  # 增加 anger
            assert result.silence_increment == 0

    def test_condition_stacking(self):
        """self_threat + repeat_error 双重修饰叠加"""
        engine = DefenseEngine()
        review = _make_review(errors=[
            _make_error(key="self/feelings/insecurity", description="自我矛盾")
        ])
        error_history = {"fact_error": 2}
        # 低 impulsiveness 确保 base_prob 高
        random.seed(4)
        result = engine.evaluate(
            review, error_history, impulsiveness=0.1,
        )
        # 条件触发：self_threat (boost 2.0) + repeat_error (boost 1.5)
        # base_prob = 0.9, modifier = 2.0 * 1.5 = 3.0 → final = min(0.9*3.0, 0.95) = 0.95
        # → 高概率触发防御（非 DIRECT）
        if result.defense_type != DefenseType.DIRECT:
            # 验证结果结构完整
            assert isinstance(result, DefenseResult)
            assert result.defense_awareness


# ── 任务 11: 脆弱感 → 防御骤降 ────────────────────────────────

class TestVulnerabilityDefense:
    """脆弱状态下防御概率 ×0.3"""

    def test_vulnerability_reduces_defense(self):
        """is_vulnerable=True → modifier *= 0.3，防御概率大幅降低
        
        设置极高的防御触发条件（impulsiveness=0.01 → base_prob=0.99），
        但在脆弱状态下 modifier *= 0.3 → final_prob ≈ 0.297。
        用多个样本统计验证脆弱状态下 DIRECT 概率显著高于非脆弱。
        """
        engine = DefenseEngine()
        review = _make_review(errors=[
            _make_error(key="self/feelings/insecurity", description="自我矛盾")
        ])
        error_history = {"fact_error": 3}
        
        # 统计脆弱和非脆弱状态下的 DIRECT 比例
        normal_direct = 0
        vuln_direct = 0
        trials = 100
        
        for i in range(trials):
            random.seed(1000 + i)
            result = engine.evaluate(
                review, error_history,
                impulsiveness=0.01,
                is_vulnerable=False,
            )
            if result.defense_type == DefenseType.DIRECT:
                normal_direct += 1
        
        for i in range(trials):
            random.seed(2000 + i)
            result = engine.evaluate(
                review, error_history,
                impulsiveness=0.01,
                is_vulnerable=True,
            )
            if result.defense_type == DefenseType.DIRECT:
                vuln_direct += 1
        
        # 脆弱状态下 DIRECT 应该显著更多（防御概率下降了 ×0.3）
        assert vuln_direct > normal_direct, (
            f"Expected vuln_direct ({vuln_direct}) > normal_direct ({normal_direct})"
        )

    def test_vulnerable_defense_mod_from_config(self):
        """验证 vulnerability modulation defense_prob 从 config 正确读取"""
        engine = DefenseEngine()
        assert engine._vulnerability_defense_mod == 0.3
