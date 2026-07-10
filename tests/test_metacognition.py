"""Tests for Spec 006: MetacognitionDepth — metacognition engine, param overrides, triggers"""

from __future__ import annotations

import pytest
from chat_core.core.types import (
    MetacognitionReport,
    MetaParamOverrides,
    SELF_CRITICISM_KEYWORDS,
)


class TestMetaParamOverrides:
    """MetaParamOverrides 容器行为测试"""

    def test_default_values(self):
        ov = MetaParamOverrides()
        assert ov.review_threshold_offset == 0.0
        assert ov.defense_prob_multiplier == 1.0
        assert ov.interest_modulations == {}
        assert ov.emotion_threshold_offset == 0.0
        assert ov.inner_thoughts_mode == "full"

    def test_apply_with_high_confidence(self):
        ov = MetaParamOverrides()
        param_ov = MetaParamOverrides(
            review_threshold_offset=0.1,
            defense_prob_multiplier=0.7,
        )
        param_ov._review_threshold_set = True
        param_ov._defense_prob_set = True
        report = MetacognitionReport(
            insight_text="test insight",
            confidence=0.8,
            param_overrides=param_ov,
        )
        ov.apply(report, turn_counter=10)
        assert ov.review_threshold_offset == 0.1
        assert ov.defense_prob_multiplier == 0.7
        assert ov._applied_at_turn == 10

    def test_apply_with_low_confidence_does_not_override(self):
        ov = MetaParamOverrides()
        param_ov = MetaParamOverrides(review_threshold_offset=0.1)
        param_ov._review_threshold_set = True
        report = MetacognitionReport(
            insight_text="test insight",
            confidence=0.5,
            param_overrides=param_ov,
        )
        ov.apply(report, turn_counter=10)
        assert ov.review_threshold_offset == 0.0  # unchanged

    def test_is_expired(self):
        ov = MetaParamOverrides()
        ov._applied_at_turn = 5
        ov._expiry_turns = 5
        assert ov.is_expired(9) is False  # turn 9, age 4
        assert ov.is_expired(10) is True   # turn 10, age 5
        assert ov.is_expired(11) is True   # turn 11, age 6

    def test_get_review_threshold_with_offset(self):
        ov = MetaParamOverrides(review_threshold_offset=0.1)
        ov._applied_at_turn = 10
        ov._expiry_turns = 5
        assert ov.get_review_threshold(base=0.5, turn_counter=11) == 0.6

    def test_get_review_threshold_expired_falls_back(self):
        ov = MetaParamOverrides(review_threshold_offset=0.1)
        ov._applied_at_turn = 5
        ov._expiry_turns = 5
        assert ov.get_review_threshold(base=0.5, turn_counter=11) == 0.5

    def test_get_review_threshold_clamped(self):
        ov = MetaParamOverrides(review_threshold_offset=0.5)
        ov._applied_at_turn = 10
        ov._expiry_turns = 5
        assert ov.get_review_threshold(base=0.5, turn_counter=11) == 0.65  # max
        ov2 = MetaParamOverrides(review_threshold_offset=-0.5)
        ov2._applied_at_turn = 10
        ov2._expiry_turns = 5
        assert ov2.get_review_threshold(base=0.5, turn_counter=11) == 0.35  # min

    def test_apply_interest_modulations(self):
        ov = MetaParamOverrides()
        report = MetacognitionReport(
            insight_text="less interest in games",
            confidence=0.7,
            param_overrides=MetaParamOverrides(
                interest_modulations={"游戏": -0.2, "AI": 0.1},
            ),
        )
        ov.apply(report, turn_counter=5)
        assert ov.interest_modulations == {"游戏": -0.2, "AI": 0.1}

    def test_confidence_exact_threshold(self):
        """confidence == 0.6 应触发 (≥ threshold)"""
        ov = MetaParamOverrides()
        param_ov = MetaParamOverrides(review_threshold_offset=0.05)
        param_ov._review_threshold_set = True
        report = MetacognitionReport(
            insight_text="borderline",
            confidence=0.6,
            param_overrides=param_ov,
        )
        ov.apply(report, turn_counter=5)
        assert ov.review_threshold_offset == 0.05


class TestMetacognitionReport:
    def test_default_report(self):
        r = MetacognitionReport()
        assert r.insight_text == ""
        assert r.confidence == 0.0
        assert r.param_overrides is None

    def test_report_with_overrides(self):
        r = MetacognitionReport(
            insight_text="I noticed a pattern",
            confidence=0.75,
            param_overrides=MetaParamOverrides(inner_thoughts_mode="brief"),
        )
        assert r.insight_text == "I noticed a pattern"
        assert r.confidence == 0.75
        assert r.param_overrides.inner_thoughts_mode == "brief"


# ── Task 5: 触发逻辑测试 ────────────────────────────────────

from chat_core.systems.metacognition import MetacognitionEngine
from chat_core.core.types import DecisionType


class TestMetacognitionEngineTriggers:
    """MetacognitionEngine.check_triggers() 测试"""

    @pytest.fixture
    def engine(self):
        return MetacognitionEngine()

    def test_periodic_trigger(self, engine):
        """定期触发：turn_counter % N == 0"""
        assert engine.check_triggers(5, None, False, None) is True
        assert engine.check_triggers(10, None, False, None) is True
        assert engine.check_triggers(15, None, False, None) is True

    def test_periodic_not_trigger_on_non_interval(self, engine):
        """非 N 的倍数不触发"""
        assert engine.check_triggers(1, None, False, None) is False
        assert engine.check_triggers(2, None, False, None) is False
        assert engine.check_triggers(7, None, False, None) is False

    def test_review_streak_trigger(self, engine):
        """审查连续 3 轮同结论 → 触发"""
        # round 1
        assert engine.check_triggers(1, DecisionType.CORRECT, False, None) is False
        # round 2
        assert engine.check_triggers(2, DecisionType.CORRECT, False, None) is False
        # round 3 → trigger
        assert engine.check_triggers(3, DecisionType.CORRECT, False, None) is True

    def test_review_streak_resets_on_different(self, engine):
        """审查结论改变 → 计数器重置"""
        assert engine.check_triggers(1, DecisionType.CORRECT, False, None) is False
        assert engine.check_triggers(2, DecisionType.CORRECT, False, None) is False
        # 改变结论
        assert engine.check_triggers(3, DecisionType.SILENCE, False, None) is False
        # 重置后重新计数
        assert engine.check_triggers(4, DecisionType.SILENCE, False, None) is False
        assert engine.check_triggers(5, DecisionType.SILENCE, False, None) is True  # 第 3 个 SILENCE + 定期

    def test_defense_streak_trigger(self, engine):
        """防御连续 2 轮 → 触发"""
        assert engine.check_triggers(1, None, True, None) is False
        assert engine.check_triggers(2, None, True, None) is True

    def test_defense_streak_resets(self, engine):
        """防御中断 → 计数器重置"""
        assert engine.check_triggers(1, None, True, None) is False
        assert engine.check_triggers(2, None, False, None) is False  # 无防御
        assert engine.check_triggers(3, None, True, None) is False  # 重新计数

    def test_self_criticism_streak_trigger(self, engine):
        """自我批评连 3 轮 → 触发"""
        assert engine.check_triggers(1, None, False, "不该这么说...") is False
        assert engine.check_triggers(2, None, False, "又说错了...") is False
        assert engine.check_triggers(3, None, False, "太机械了...") is True

    def test_self_criticism_resets(self, engine):
        """自我批评中断 → 计数器重置"""
        assert engine.check_triggers(1, None, False, "不该这么说...") is False
        assert engine.check_triggers(2, None, False, "今天天气不错") is False  # 无自我批评
        assert engine.check_triggers(3, None, False, "又说错了...") is False  # 重新计数

    def test_compound_delta_trigger(self, engine):
        """|Δcompound| > 0.4 → 即时触发（情绪冲击）"""
        assert engine.check_triggers(3, None, False, None, compound_delta=0.5) is True

    def test_compound_delta_below_threshold_no_trigger(self, engine):
        """|Δcompound| ≤ 0.4 → 不触发"""
        assert engine.check_triggers(3, None, False, None, compound_delta=0.3) is False
        assert engine.check_triggers(3, None, False, None, compound_delta=0.4) is False

    def test_compound_delta_negative_triggers(self, engine):
        """负向情绪冲击同样触发"""
        assert engine.check_triggers(3, None, False, None, compound_delta=-0.5) is True

    def test_counters_reset_after_trigger(self, engine):
        """触发后计数器全部重置"""
        # 触发审查连判
        engine.check_triggers(1, DecisionType.CORRECT, False, None)
        engine.check_triggers(2, DecisionType.CORRECT, False, None)
        assert engine.check_triggers(3, DecisionType.CORRECT, False, None) is True
        # 触发后计数器应归零
        assert engine._review_streak_counter == 0
        assert engine._defense_streak_counter == 0
        assert engine._self_criticism_counter == 0

    def test_none_review_decision_resets_counter(self, engine):
        """None 审查决策重置计数器"""
        engine.check_triggers(1, DecisionType.CORRECT, False, None)
        engine.check_triggers(2, DecisionType.CORRECT, False, None)
        assert engine.check_triggers(3, None, False, None) is False
        assert engine._review_streak_counter == 0


# ── Spec 006: 集成场景测试 ────────────────────────────────

class TestMetacognitionContext:
    """build_context() 测试"""

    def test_build_context_basic(self):
        engine = MetacognitionEngine()
        ctx = engine.build_context(
            turn_summaries=[{"turn": "turn_001", "topic": "游戏"}],
            compound_trends={"gratification": [0.2, 0.1, 0.05]},
            defense_mode_summary={"activation_rate": 0.4, "main_types": "DENIAL (50%)"},
            memory_system_state={"avg_recall_count": 3.8, "empty_recall_count": 1, "decay_warning_count": 3, "deep_memory_count": 12},
            attention_state="drifting",
            energy_state={"energy": 0.41},
            subjective_time={"speed_factor": 0.65},
            vulnerability_history={"is_vulnerable": False, "cooldown_remaining": 0},
        )
        assert "最近 N 轮" in ctx
        assert "复合情绪趋势" in ctx
        assert "gratification" in ctx
        assert "防御模式总结" in ctx
        assert "记忆系统状态" in ctx
        assert "drifting" in ctx
        assert "0.41" in ctx
        assert "脆弱历史" in ctx

    def test_build_context_minimal(self):
        engine = MetacognitionEngine()
        ctx = engine.build_context(
            turn_summaries=[],
            compound_trends={},
            defense_mode_summary={},
            memory_system_state={},
            attention_state="",
        )
        assert "最近" in ctx or ctx.strip() == ""  # 至少没有崩溃


class TestIntegrationScenarios:
    """端到端场景测试"""

    def test_confidence_below_threshold_no_override(self):
        """confidence < 0.6 → insight_text 可用但 param_overrides 不应用"""
        ov = MetaParamOverrides()
        param_ov = MetaParamOverrides()
        param_ov.review_threshold_offset = 0.1
        param_ov._review_threshold_set = True
        report = MetacognitionReport(
            insight_text="I noticed something",
            confidence=0.5,
            param_overrides=param_ov,
        )
        ov.apply(report, turn_counter=5)
        assert ov.review_threshold_offset == 0.0  # 未应用
        assert report.insight_text == "I noticed something"

    def test_full_override_lifecycle(self):
        """完整生命周期：apply → 有效期 → 过期"""
        ov = MetaParamOverrides()
        param_ov = MetaParamOverrides()
        param_ov.review_threshold_offset = 0.1
        param_ov._review_threshold_set = True
        param_ov.defense_prob_multiplier = 0.7
        param_ov._defense_prob_set = True
        report = MetacognitionReport(
            insight_text="pattern detected",
            confidence=0.8,
            param_overrides=param_ov,
        )
        ov.apply(report, turn_counter=10)
        assert ov.get_review_threshold(0.5, 11) == 0.6  # turn 11, valid
        assert ov.get_review_threshold(0.5, 14) == 0.6  # turn 14, still valid
        assert ov.get_review_threshold(0.5, 15) == 0.5  # turn 15, expired

    def test_compound_trend_provided_by_emotion_engine(self):
        """验证 get_compound_trend 接口约定"""
        from chat_core.systems.emotion import EmotionEngine, COMPOUND_DIMS
        engine = EmotionEngine()
        trends = engine.get_compound_trend("sub")
        assert isinstance(trends, dict)
        for dim in COMPOUND_DIMS:
            assert dim in trends
            assert isinstance(trends[dim], list)
