"""Spec 005: 复合情绪系统测试 — 交互矩阵、衰减、传染、alert、summary"""

from __future__ import annotations

import datetime
import time

import pytest

from chat_core.systems.emotion import (
    BRAIN_NAMES,
    COMPOUND_DIMS,
    EMOTION_DIMS,
    INTERACTION_MATRIX,
    EmotionEngine,
    _clamp,
)
from chat_core.core.types import EmotionState, DefenseType, DefenseResult


# ── 任务 3: 交互矩阵 → 复合情绪生成 ───────────────────────────

class TestCompoundEmotion:
    def test_interaction_matrix_generates_gratification(self):
        """joy=0.5 trust=0.5 → gratification > 0 after tick"""
        engine = EmotionEngine()
        engine._states["sub"].joy = 0.5
        engine._states["sub"].trust = 0.5
        engine.tick()
        assert engine._states["sub"].gratification > 0

    def test_interaction_below_threshold_no_effect(self):
        """dim < 0.3 → 不触发交互（所有脑均低于阈值避免传染）"""
        engine = EmotionEngine()
        for bn in BRAIN_NAMES:
            engine._states[bn].joy = 0.2
            engine._states[bn].trust = 0.5
            engine._states[bn].interest = 0.0  # interest 默认 0.5 也会触发
        engine.tick()
        assert engine._states["sub"].gratification == 0.0

    def test_disabled_compound_no_generation(self):
        """compound.enabled=false → 无复合生成"""
        engine = EmotionEngine()
        engine._compound_enabled = False
        engine._states["sub"].joy = 0.8
        engine._states["sub"].trust = 0.8
        engine.tick()
        assert engine._states["sub"].gratification == 0.0

    def test_anxiety_from_fear_anticipation(self):
        """fear=0.5 anticipation=0.6 → anxiety > 0"""
        engine = EmotionEngine()
        engine._states["sub"].fear = 0.5
        engine._states["sub"].anticipation = 0.6
        engine.tick()
        assert engine._states["sub"].anxiety > 0

    def test_contempt_from_anger_disgust(self):
        """anger=0.7 disgust=0.5 → contempt > 0"""
        engine = EmotionEngine()
        engine._states["sub"].anger = 0.7
        engine._states["sub"].disgust = 0.5
        engine.tick()
        assert engine._states["sub"].contempt > 0

    def test_bewilderment_from_confusion_fear(self):
        """confusion=0.8 fear=0.6 → bewilderment > 0"""
        engine = EmotionEngine()
        engine._states["sub"].confusion = 0.8
        engine._states["sub"].fear = 0.6
        engine.tick()
        assert engine._states["sub"].bewilderment > 0

    def test_multiple_compounds_per_tick(self):
        """多个交互对同时生成不同复合情绪"""
        engine = EmotionEngine()
        engine._states["sub"].joy = 0.6
        engine._states["sub"].trust = 0.6
        engine._states["sub"].fear = 0.5
        engine._states["sub"].anticipation = 0.5
        engine.tick()
        assert engine._states["sub"].gratification > 0
        assert engine._states["sub"].anxiety > 0

    def test_compound_capped_at_one(self):
        """复合情绪值不会超过 1.0"""
        engine = EmotionEngine()
        engine._states["sub"].joy = 1.0
        engine._states["sub"].trust = 1.0
        engine._states["sub"].gratification = 0.99
        engine.tick()
        assert engine._states["sub"].gratification <= 1.0


# ── 任务 4: 复合衰减 ─────────────────────────────────────────

class TestCompoundDecay:
    def test_compound_half_lives_all_12(self):
        """_compound_half_lives() 返回 12 个维度的半衰期"""
        engine = EmotionEngine()
        halflives = engine._compound_half_lives()
        assert len(halflives) == 12, f"Expected 12, got {len(halflives)}: {sorted(halflives.keys())}"
        for dim in COMPOUND_DIMS:
            assert dim in halflives, f"{dim} missing from half lives"
            assert halflives[dim] > 0, f"{dim} half life should be positive"

    def test_compound_decay_reduces_value(self):
        """复合情绪随时间衰减"""
        engine = EmotionEngine()
        engine._states["sub"].gratification = 0.5
        # 设置 last_tick 到过去以触发衰减
        past = datetime.datetime.fromtimestamp(time.time() - 600)
        engine._states["sub"].last_tick = past
        engine.tick()
        assert engine._states["sub"].gratification < 0.5

    def test_disabled_no_compound_decay(self):
        """compound.enabled=false → 复合不衰减也不更新"""
        engine = EmotionEngine()
        engine._compound_enabled = False
        engine._states["sub"].gratification = 0.5
        engine.tick()
        # 不衰减但也不生成 — 保持原值（基础维会衰减）
        # gratification 不变因为复合衰减被跳过
        assert engine._states["sub"].gratification == 0.5


# ── 任务 4: 复合跨脑传染 ─────────────────────────────────────

class TestCompoundContagion:
    def test_compound_contagion_propagates(self):
        """复合维度也在跨脑间传染"""
        engine = EmotionEngine()
        engine._states["logic"].gratification = 0.8
        engine._states["emotion"].gratification = 0.0
        # 设 last_tick 为 now 使衰减为 0
        now = datetime.datetime.fromtimestamp(time.time())
        for bn in BRAIN_NAMES:
            engine._states[bn].last_tick = now
        engine.tick()
        # logic → emotion: 0.8 * 0.1 = 0.08
        assert engine._states["emotion"].gratification > 0.0

    def test_compound_contagion_disabled(self):
        """compound.enabled=false → 复合不传染"""
        engine = EmotionEngine()
        engine._compound_enabled = False
        engine._states["logic"].gratification = 0.8
        engine._states["emotion"].gratification = 0.0
        now = datetime.datetime.fromtimestamp(time.time())
        for bn in BRAIN_NAMES:
            engine._states[bn].last_tick = now
        engine.tick()
        assert engine._states["emotion"].gratification == 0.0


# ── 任务 4: set_dimension 支持复合维度 ──────────────────────────

class TestSetDimensionCompound:
    def test_set_compound_dimension(self):
        """set_dimension 支持复合维度"""
        engine = EmotionEngine()
        engine.set_dimension("sub", "guilt", 0.5)
        assert engine._states["sub"].guilt == 0.5

    def test_set_compound_clamps(self):
        """复合维度值自动钳制到 [0, 1]"""
        engine = EmotionEngine()
        engine.set_dimension("sub", "guilt", 1.5)
        assert engine._states["sub"].guilt == 1.0
        engine.set_dimension("sub", "guilt", -0.5)
        assert engine._states["sub"].guilt == 0.0

    def test_set_unknown_compound_raises(self):
        """不存在的维度（无论基维度还是复合维度）都抛异常"""
        engine = EmotionEngine()
        with pytest.raises(ValueError, match="Unknown dimension"):
            engine.set_dimension("logic", "nonexistent", 0.5)

    def test_accelerate_works_on_compound(self):
        """accelerate 可操作复合维度"""
        engine = EmotionEngine()
        engine.set_dimension("sub", "guilt", 0.3)
        engine.accelerate("sub", "guilt", 0.1)
        assert engine._states["sub"].guilt == 0.4


# ── 任务 5: compound_alert + get_emotion_summary ─────────────────

class TestCompoundAlert:
    def test_last_compound_delta_initial(self):
        """初始 last_compound_delta = 0"""
        engine = EmotionEngine()
        assert engine.last_compound_delta == 0.0

    def test_compound_delta_updated_after_tick(self):
        """tick 后 _prev_compound 被追踪"""
        engine = EmotionEngine()
        engine._states["sub"].joy = 0.6
        engine._states["sub"].trust = 0.6
        engine.tick()
        # 第一次 tick 后 _prev_compound 应该有值
        assert hasattr(engine, "_prev_compound")

    def test_empty_tick_no_alert(self):
        """无显著变化时不发布 compound_alert"""
        engine = EmotionEngine()
        engine._states["sub"].joy = 0.5
        engine._states["sub"].trust = 0.5
        # 两次 tick，复合从 0 开始，首次 delta 可能大（从 0 → something）
        engine.tick()
        # 第二次 tick 时 prev 已建立，delta 小
        engine.tick()
        assert engine.last_compound_delta < 0.4


class TestEmotionSummaryCompound:
    def test_summary_includes_compound(self):
        """get_emotion_summary 含复合维度文本"""
        engine = EmotionEngine()
        engine.set_dimension("sub", "joy", 0.6)
        engine.set_dimension("sub", "trust", 0.7)
        engine.set_dimension("sub", "gratification", 0.15)
        summary = engine.get_emotion_summary("sub")
        assert "joy" in summary
        assert "gratification" in summary
        assert "|" in summary  # 分隔符

    def test_summary_no_compound_when_zero(self):
        """复合全为 0 时不出现分隔符"""
        engine = EmotionEngine()
        engine.set_dimension("sub", "joy", 0.6)
        summary = engine.get_emotion_summary("sub")
        assert "|" not in summary

    def test_summary_neutral_empty(self):
        """全为 0 时返回 neutral"""
        engine = EmotionEngine()
        # 将所有维度设为 0
        for bn in BRAIN_NAMES:
            for dim in EMOTION_DIMS + COMPOUND_DIMS:
                try:
                    engine.set_dimension(bn, dim, 0.0)
                except ValueError:
                    pass
        summary = engine.get_emotion_summary("sub")
        assert summary == "neutral"


# ── 任务 1: EmotionState 向后兼容 ──────────────────────────────

class TestEmotionStateCompat:
    def test_default_compound_zeros(self):
        """新 EmotionState 默认复合全为 0"""
        s = EmotionState()
        for dim in COMPOUND_DIMS:
            assert getattr(s, dim, 0.0) == 0.0, f"{dim} should default to 0"

    def test_get_state_includes_compound(self):
        """get_state() 返回的 EmotionState 包含复合字段"""
        engine = EmotionEngine()
        engine._states["sub"].gratification = 0.3
        state = engine.get_state("sub")
        assert state.gratification == 0.3


# ── DefenseType/DefenseResult 基础校验 ─────────────────────────

class TestDefenseTypes:
    def test_defense_type_enum(self):
        assert DefenseType.DIRECT.value == "direct"
        assert DefenseType.DENIAL.value == "denial"
        assert DefenseType.RATIONALIZE.value == "rationalize"
        assert DefenseType.PROJECT.value == "project"

    def test_defense_result_defaults(self):
        r = DefenseResult(defense_type=DefenseType.DIRECT)
        assert r.defense_type == DefenseType.DIRECT
        assert r.correction_text is None
        assert r.defense_awareness == ""
        assert r.emotion_delta == {}
        assert r.silence_increment == 0

    def test_defense_result_with_data(self):
        r = DefenseResult(
            defense_type=DefenseType.RATIONALIZE,
            correction_text="error (自我辩护)",
            inner_reflection="意识到了",
            defense_awareness="[自我感知] ...",
            emotion_delta={"guilt": -0.02, "gratification": 0.03},
            silence_increment=0,
        )
        assert r.correction_text is not None
        assert "自我辩护" in r.correction_text
        assert r.emotion_delta["guilt"] == -0.02


# ── 任务 9: 脆弱感 — _check_vulnerability() ──────────────────────

class TestVulnerability:
    def test_vulnerability_triggered_by_extreme_compound(self):
        """复合情绪 ≥ 阈值 → is_vulnerable = True"""
        engine = EmotionEngine()
        engine._states["sub"].guilt = 0.8
        engine._check_vulnerability()
        assert engine.is_vulnerable is True

    def test_vulnerability_not_triggered_below_threshold(self):
        """复合情绪 < 阈值 → is_vulnerable = False"""
        engine = EmotionEngine()
        engine._states["sub"].guilt = 0.5
        engine._check_vulnerability()
        assert engine.is_vulnerable is False

    def test_vulnerability_cooldown_prevents_spam(self):
        """冷却期内不重复触发脆弱"""
        engine = EmotionEngine()
        engine._states["sub"].guilt = 0.8
        # 第一次触发
        result = engine._check_vulnerability()
        assert result is True
        assert engine.is_vulnerable is True
        assert engine._vulnerability_cooldown > 0
        # 冷却期内不触发
        result2 = engine._check_vulnerability()
        assert result2 is False
        assert engine.is_vulnerable is False

    def test_vulnerability_cooldown_decrements(self):
        """冷却计数器每次调用递减"""
        engine = EmotionEngine()
        engine._states["sub"].guilt = 0.9
        engine._check_vulnerability()
        cooldown_after_trigger = engine._vulnerability_cooldown
        assert cooldown_after_trigger > 0
        engine._check_vulnerability()
        assert engine._vulnerability_cooldown == cooldown_after_trigger - 1

    def test_vulnerability_disabled(self):
        """vulnerability.enabled=false → 不触发"""
        engine = EmotionEngine()
        engine._vulnerability_enabled = False
        engine._states["sub"].guilt = 0.9
        result = engine._check_vulnerability()
        assert result is False
        assert engine.is_vulnerable is False

    def test_multiple_compound_triggers(self):
        """多个复合情绪任一超过阈值即触发"""
        engine = EmotionEngine()
        engine._states["sub"].anxiety = 0.75  # ≥ 0.7
        engine._check_vulnerability()
        assert engine.is_vulnerable is True

    def test_tick_calls_check_vulnerability(self):
        """tick() 末尾调用 _check_vulnerability()"""
        engine = EmotionEngine()
        engine._states["sub"].guilt = 0.8
        engine.tick()
        # tick() 会自动调用 _check_vulnerability
        assert engine.is_vulnerable is True
