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
        assert not am.should_exit_sub()  # focus=0.9 > 0.15

        # Force sub focus below threshold
        am._states["sub"].focus = 0.1
        assert am.should_exit_sub()

    def test_drift_decay(self):
        am = AttentionModel()
        # Set last_update far in the past
        am._last_update = time.time() - 100  # 100 seconds ago
        am.drift()
        # After 100s: decay_factor = 1 - 0.01*100 = 0.0 (capped at 0)
        # Actually: decay_factor = max(0, 1 - 0.01*100) = 0.0
        # focus = 0.9 * max(0, 0.0) = 0.0
        assert am.get_state("sub").focus == 0.0

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

        am._states["sub"].focus = 0.1
        assert am.should_exit_sub()
