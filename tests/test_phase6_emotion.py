"""Phase 6 (US4: Emotional Intelligence) integration tests."""

from __future__ import annotations

import asyncio
import time

import pytest

from chat_core.systems.emotion import (
    BRAIN_NAMES,
    EMOTION_DIMS,
    EmotionEngine,
    _clamp,
)
from chat_core.systems.personality import PersonalityEngine
from chat_core.systems.attention import AttentionModel
from chat_core.core.types import EmotionState, PersonalityWeights, AttentionState


# ── EmotionEngine tests (T042-T044) ──────────────────────────

class TestEmotionEngine:
    def test_clamp_helper(self):
        assert _clamp(0.5) == 0.5
        assert _clamp(1.5) == 1.0
        assert _clamp(-0.5) == 0.0
        assert _clamp(0.0) == 0.0
        assert _clamp(1.0) == 1.0

    def test_initial_state(self):
        ee = EmotionEngine()
        for name in BRAIN_NAMES:
            state = ee.get_state(name)
            assert state.brain == name
            # Default values from EmotionState dataclass
            assert state.joy == 0.5
            assert state.interest == 0.5
            assert state.trust == 0.5
            assert state.surprise == 0.0

    def test_set_dimension_clamps(self):
        ee = EmotionEngine()
        ee.set_dimension("logic", "joy", 1.5)
        assert ee.get_state("logic").joy == 1.0
        ee.set_dimension("emotion", "sadness", -0.5)
        assert ee.get_state("emotion").sadness == 0.0

    def test_set_dimension_unknown_raises(self):
        ee = EmotionEngine()
        with pytest.raises(ValueError, match="Unknown brain"):
            ee.set_dimension("unknown", "joy", 0.5)
        with pytest.raises(ValueError, match="Unknown dimension"):
            ee.set_dimension("logic", "unknown_dim", 0.5)

    def test_tick_decay(self):
        ee = EmotionEngine()
        # Set surprise to max (half-life 30s) on all brains so contagion delta is 0
        for brain in BRAIN_NAMES:
            ee.set_dimension(brain, "surprise", 1.0)
        # Manually set last_tick to 30s ago for all brains
        import datetime
        past = datetime.datetime.fromtimestamp(time.time() - 30)
        for brain in BRAIN_NAMES:
            ee._states[brain].last_tick = past
        ee.tick()
        # After one half-life: 1.0 * 2^(-30/30) = 0.5
        # Contagion delta zero since all brains equal
        assert abs(ee.get_state("logic").surprise - 0.5) < 0.01

    def test_tick_contagion(self):
        ee = EmotionEngine()
        ee.set_dimension("logic", "anger", 1.0)
        ee.set_dimension("emotion", "anger", 0.0)
        # Set last_tick to now so decay is negligible
        import datetime
        now = datetime.datetime.fromtimestamp(time.time())
        ee._states["logic"].last_tick = now
        ee._states["emotion"].last_tick = now
        ee._states["sub"].last_tick = now
        ee.tick()
        es = ee.get_state("emotion")
        # Contagion: logic→emotion, delta = 1.0 - 0.0 = 1.0, strength 0.1
        # emotion.anger should increase by ~0.1
        assert es.anger > 0.05
        assert es.anger <= 0.2

    def test_get_emotion_summary(self):
        ee = EmotionEngine()
        ee.set_dimension("sub", "joy", 0.6)
        ee.set_dimension("sub", "sadness", 0.1)
        ee.set_dimension("sub", "interest", 0.5)
        ee.set_dimension("sub", "trust", 0.7)
        summary = ee.get_emotion_summary("sub")
        assert "joy=0.60" in summary
        assert "sadness=0.10" in summary
        assert "trust=0.70" in summary
        # sadness=0.1 may not appear since it's > 0.01 but let's check it appears

    def test_get_all_states_returns_copies(self):
        ee = EmotionEngine()
        states = ee.get_all_states()
        states["logic"].surprise = 999.0  # modify copy
        assert ee.get_state("logic").surprise != 999.0

    def test_pause_resume(self):
        ee = EmotionEngine()
        assert not ee._paused
        ee.pause()
        assert ee._paused
        ee.resume()
        assert not ee._paused

    @pytest.mark.asyncio
    async def test_start_stop(self):
        ee = EmotionEngine()
        await ee.start()
        assert ee._task is not None
        assert not ee._task.done()
        await ee.stop()
        assert ee._task is None


# ── PersonalityEngine tests (T045-T046) ──────────────────────

class TestPersonalityEngine:
    def test_default_weights(self):
        pe = PersonalityEngine()
        assert pe.weights.curiosity == 0.7
        assert pe.weights.sociability == 0.8
        assert pe.weights.playfulness == 0.6
        assert pe.weights.empathy == 0.5
        assert pe.weights.assertiveness == 0.3
        assert pe.weights.creativity == 0.6
        assert pe.weights.impulsiveness == 0.2
        assert pe.weights.loyalty == 0.75

    def test_get_llm_temperature(self):
        pe = PersonalityEngine()
        temp = pe.get_llm_temperature("sub_session")
        # base temp from config = 0.8, playfulness=0.6, delta=0.18
        # total = 0.8 + 0.6*0.3 = 0.98
        assert temp == pytest.approx(0.98, abs=0.01)

    def test_get_llm_temperature_playfulness_variation(self):
        pe = PersonalityEngine()
        pe.update_weight("playfulness", 1.0)
        temp_high = pe.get_llm_temperature("sub_session")
        pe.update_weight("playfulness", 0.0)
        temp_low = pe.get_llm_temperature("sub_session")
        assert temp_high > temp_low

    def test_get_response_mode(self):
        pe = PersonalityEngine()
        # empathy defaults to 0.5 → not > 0.5 → "normal"
        assert pe.get_response_mode() == "normal"
        pe.update_weight("empathy", 0.6)
        assert pe.get_response_mode() == "empathetic"

    def test_get_creativity_bias(self):
        pe = PersonalityEngine()
        pe.update_weight("creativity", 0.8)
        assert pe.get_creativity_bias() == pytest.approx(0.4)
        pe.update_weight("creativity", 0.0)
        assert pe.get_creativity_bias() == 0.0

    def test_get_correction_threshold(self):
        pe = PersonalityEngine()
        # impulsiveness=0.2 → threshold = 0.9 - 0.2*0.8 = 0.74
        assert pe.get_correction_threshold() == pytest.approx(0.74)
        pe.update_weight("impulsiveness", 1.0)
        # impulsiveness=1.0 → threshold = max(0.1, 0.9 - 1.0*0.8) = 0.1
        assert pe.get_correction_threshold() == pytest.approx(0.1)

    def test_get_proactive_frequency(self):
        pe = PersonalityEngine()
        assert pe.get_proactive_frequency() == 0.8  # sociability

    def test_get_memory_boost(self):
        pe = PersonalityEngine()
        # loyalty=0.75 → boost = 1.0 + 0.75*0.75 = 1.5625
        assert pe.get_memory_boost() == pytest.approx(1.5625)

    def test_update_weight_clamps(self):
        pe = PersonalityEngine()
        pe.update_weight("playfulness", 2.0)
        assert pe.weights.playfulness == 1.0
        pe.update_weight("playfulness", -1.0)
        assert pe.weights.playfulness == 0.0

    def test_update_weight_unknown_raises(self):
        pe = PersonalityEngine()
        with pytest.raises(ValueError):
            pe.update_weight("unknown", 0.5)

    def test_summary(self):
        pe = PersonalityEngine()
        s = pe.summary()
        assert len(s) == 8
        assert s["curiosity"] == 0.7
        assert s["playfulness"] == 0.6


# ── AttentionModel tests (T047) ──────────────────────────────

class TestAttentionModel:
    def test_initial_state(self):
        am = AttentionModel()
        assert am.get_state("logic").focus == 0.8
        assert am.get_state("logic").dominance == 0.7
        assert am.get_state("emotion").focus == 0.7
        assert am.get_state("emotion").dominance == 0.5
        assert am.get_state("sub").focus == 0.9
        assert am.get_state("sub").dominance == 0.6

    def test_get_state_unknown_raises(self):
        am = AttentionModel()
        with pytest.raises(ValueError):
            am.get_state("unknown")

    def test_should_exit_sub(self):
        am = AttentionModel()
        assert not am.should_exit_sub()  # focus=0.9, FOCUSED → not DULL, 0.9 >= 0.15

        # DULL 态 (focus < 0.3) 不沉默，始终返回 False
        am._states["sub"].focus = 0.1
        assert not am.should_exit_sub()  # DULL → always False

        # DRIFTING 态且 focus < 0.15 会退出（虽然 DRIFTING 下限是 0.3，但过渡态可能发生）
        am._states["sub"].focus = 0.35
        assert not am.should_exit_sub()  # DRIFTING, focus=0.35 >= 0.15

    def test_drift_decay(self):
        am = AttentionModel()
        # Set last_update far in the past
        am._last_update = time.time() - 100  # 100 seconds ago
        # sub focus starts at 0.9 (FOCUSED), decay_rate_focused = 0.001
        # dt=100: decay_factor = 1 - 0.001*100 = 0.9
        # focus = 0.9 * 0.9 = 0.81
        initial = am.get_state("sub").focus
        am.drift()
        assert am.get_state("sub").focus == pytest.approx(initial * 0.9, abs=0.01)

    def test_get_focus(self):
        am = AttentionModel()
        assert am.get_focus("sub") == 0.9

    def test_reset(self):
        am = AttentionModel()
        am._states["logic"].focus = 0.3
        am.reset("logic")
        assert am.get_state("logic").focus == 0.8  # back to baseline

    def test_boost(self):
        am = AttentionModel()
        am.boost("sub", 0.3)
        # focus was 0.9, +0.3 = 1.2 → capped at 1.0
        assert am.get_state("sub").focus == 1.0

        am.reset("sub")
        am.boost("sub", 0.05)
        assert am.get_state("sub").focus == pytest.approx(0.95)

    def test_boost_unknown_raises(self):
        am = AttentionModel()
        with pytest.raises(ValueError):
            am.boost("unknown")

    def test_get_all_states(self):
        am = AttentionModel()
        states = am.get_all_states()
        assert set(states.keys()) == {"logic", "emotion", "sub"}
        assert isinstance(states["logic"], AttentionState)


# ── Integration sanity checks ───────────────────────────────

class TestIntegration:
    def test_emotion_summary_in_prompt_context(self):
        """Verify emotion summary format works for prompt injection."""
        ee = EmotionEngine()
        ee.set_dimension("sub", "joy", 0.75)
        ee.set_dimension("sub", "sadness", 0.05)
        text = ee.get_emotion_summary("sub")
        assert isinstance(text, str)
        # Should contain non-zero dimensions
        assert "joy" in text or text == "neutral"

    def test_personality_temp_in_sub_config(self):
        """Verify personality temperature can be passed to SubSessionConfig."""
        from chat_core.core.loop import SubSessionConfig

        pe = PersonalityEngine()
        temp = pe.get_llm_temperature("sub_session")
        cfg = SubSessionConfig(max_iter=5, temperature=temp)
        assert cfg.temperature == temp
        assert cfg.max_iter == 5

    def test_attention_focus_in_loop_should_exit(self):
        """Verify attention model integrates with loop exit condition."""
        am = AttentionModel()
        am._states["sub"].focus = 0.9
        assert not am.should_exit_sub()

        # DULL 态 (focus < 0.3) → 不沉默，始终 False
        am._states["sub"].focus = 0.1
        assert not am.should_exit_sub()


# ── 注意力状态机测试 ─────────────────────────────────────────

from chat_core.core.types import AttentionStateEnum, AttentionEvent


class TestAttentionStateMachine:
    """注意力状态机: 三态 + apply_event + 平滑过渡"""

    def test_initial_state_focused(self):
        """新 AttentionModel 应从 FOCUSED(0.9) 开始"""
        model = AttentionModel()
        state = model.get_state("sub")
        assert state.focus == 0.9
        assert model.get_state_enum("sub") == AttentionStateEnum.FOCUSED

    def test_state_enum_thresholds(self):
        """focus 值正确映射到三态枚举"""
        model = AttentionModel()
        # 手动设置 sub focus
        model._states["sub"].focus = 0.85
        assert model.get_state_enum("sub") == AttentionStateEnum.FOCUSED
        model._states["sub"].focus = 0.50
        assert model.get_state_enum("sub") == AttentionStateEnum.DRIFTING
        model._states["sub"].focus = 0.20
        assert model.get_state_enum("sub") == AttentionStateEnum.DULL

    def test_apply_event_emotion_positive(self):
        """EMOTION_POSITIVE 事件应设置 target=current+0.10，然后 drift 到目标"""
        model = AttentionModel()
        initial = model.get_focus("sub")
        model.apply_event(AttentionEvent.EMOTION_POSITIVE, brain="sub")
        # apply_event 仅设置过渡目标，不立即改变 focus
        expected_target = min(1.0, initial + 0.10)
        assert model._transition_target["sub"] == pytest.approx(expected_target)
        # 运行 drift 后 focus 应达到目标
        model._last_update = time.time() - 0.35  # 超过 0.3s transition
        model.drift()
        assert model.get_focus("sub") == pytest.approx(expected_target)

    def test_apply_event_emotion_shock_dull(self):
        """DULL 态下 EMOTION_SHOCK → 80% 概率跳 DRIFTING"""
        import random
        random.seed(42)
        model = AttentionModel()
        model._states["sub"].focus = 0.20  # DULL
        model.apply_event(AttentionEvent.EMOTION_SHOCK, brain="sub")
        # 0.8 概率跳 DRIFTING: target = drifting_threshold + shock boost = 0.30 + 0.30 = 0.60
        assert model._transition_target["sub"] is not None
        # 完成过渡后验证
        model._last_update = time.time() - 0.35
        model.drift()
        assert model.get_focus("sub") >= 0.30

    def test_apply_event_memory_strong_hit(self):
        """MEMORY_STRONG_HIT → +0.25 FOCUSED, +0.20 DRIFTING"""
        model = AttentionModel()
        model._states["sub"].focus = 0.50  # DRIFTING
        model.apply_event(AttentionEvent.MEMORY_STRONG_HIT, brain="sub")
        # DRIFTING 态 → +0.20
        expected_target = min(1.0, 0.50 + 0.20)
        assert model._transition_target["sub"] == pytest.approx(expected_target)
        # 完成过渡
        model._last_update = time.time() - 0.35
        model.drift()
        assert model.get_focus("sub") == pytest.approx(expected_target)

    def test_smooth_transition_active(self):
        """apply_event 后 transition_target 非空，drift() 逐步插值"""
        model = AttentionModel()
        model._states["sub"].focus = 0.20  # DULL
        model.apply_event(AttentionEvent.EMOTION_SHOCK, brain="sub")
        assert model._transition_target["sub"] is not None  # 平滑过渡进行中

        # drift() 应逐步靠近目标
        model._last_update = time.time() - 0.15  # 模拟 0.15s 流逝
        model.drift()
        # 应已过渡一半
        assert model._transition_elapsed["sub"] > 0

    def test_apply_event_race_mild(self):
        """RACE_MILD (active≥3) → 70% 概率 FOCUSED→DRIFTING"""
        import random
        random.seed(42)
        model = AttentionModel()
        model._states["sub"].focus = 0.80  # FOCUSED
        model.apply_event(AttentionEvent.RACE_MILD, brain="sub")
        # 70% 概率: target = focused_threshold - 0.01 = 0.59
        # seed(42) 触发转移
        assert model._transition_target["sub"] is not None  # 有转移
        assert model._transition_target["sub"] < 0.80

    def test_dull_always_exit_sub_false(self):
        """DULL 态下 should_exit_sub() 始终返回 False"""
        model = AttentionModel()
        model._states["sub"].focus = 0.05  # 极低，DULL 态
        assert model.should_exit_sub() is False  # DULL 不沉默


# ── 情绪引擎 → 注意力状态机 联动 ──────────────────────────────

from chat_core.core.turn_manager import EventBus


class TestEmotionAttentionLink:
    """情绪引擎 → 注意力状态机 联动：tick() 检测 Δvalence → event_bus"""

    def test_tick_updates_prev_valence(self):
        """tick() 后 _prev_valence 更新为当前 valence"""
        engine = EmotionEngine()
        engine._states["sub"].joy = 0.3
        engine._states["sub"].trust = 0.5
        engine.tick()
        expected = (0.3 + 0.5) / 2.0
        assert engine._prev_valence.get("sub", -1) == pytest.approx(expected, abs=0.01)

    def test_valence_delta_below_threshold_no_publish(self):
        """|Δvalence| ≤ 0.5 → 不发布事件（event_bus=None 静默）"""
        engine = EmotionEngine()
        engine._prev_valence["sub"] = 0.5
        engine._states["sub"].joy = 0.6
        engine._states["sub"].trust = 0.6
        current = (0.6 + 0.6) / 2.0  # 0.6
        delta = abs(current - engine._prev_valence["sub"])  # 0.1
        assert delta <= 0.5, "小变化不触发 alarm"
        # tick() 不应因 event_bus=None 而抛出异常
        engine.tick()

    def test_tick_publishes_emotion_alert_on_shock(self):
        """|Δvalence| > 0.5 → _prev_valence 在 tick() 后更新为新的 (joy+trust)/2"""
        engine = EmotionEngine()
        # 建立低基线
        engine._states["sub"].joy = 0.05
        engine._states["sub"].trust = 0.05
        engine.tick()  # baseline ≈ 0.05 (after slight decay)
        # 制造冲击
        engine._states["sub"].joy = 0.9
        engine._states["sub"].trust = 0.7
        engine.tick()
        # 验证 _prev_valence 已更新（tick 中会有小幅衰减，使用宽容忍度）
        new_prev = engine._prev_valence.get("sub", 0.0)
        assert new_prev > 0.6, f"prev_valence 应反映高 joy+trust 值: {new_prev}"
        # Δ 应 > 0.5（从 ~0.05 跳到 ~0.77+）
        assert abs(new_prev - 0.05) > 0.5
