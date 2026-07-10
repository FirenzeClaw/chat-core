"""Tests for SubjectiveClock (Spec 007)."""
import pytest
from chat_core.systems.subjective_time import SubjectiveClock
from chat_core.core.types import AttentionStateEnum, EmotionState


class TestSubjectiveClock:
    """SubjectiveClock 单元测试 — 5 个测试用例。"""

    def test_focused_speeds_up_time(self):
        """FOCUSED 注意力 → speed_factor < 1（时间过得快）。"""
        clock = SubjectiveClock()
        sf = clock._compute_speed_factor(AttentionStateEnum.FOCUSED, None, 0)
        assert sf < 1.0

    def test_dull_slows_down_time(self):
        """DULL 注意力 → speed_factor > 1（时间过得慢）。"""
        clock = SubjectiveClock()
        sf = clock._compute_speed_factor(AttentionStateEnum.DULL, None, 0)
        assert sf > 1.0

    def test_joy_speeds_up(self):
        """joy > 0.5 → speed_factor 减小。"""
        clock = SubjectiveClock()
        emo = EmotionState(joy=0.6)
        sf = clock._compute_speed_factor(None, emo, 0)
        # base 1.0 * 0.7 (joy) = 0.7 < 1.0
        assert sf < 1.0

    def test_interest_speeds_up(self):
        """interest_match > 0.7 → speed_factor 减小。"""
        clock = SubjectiveClock()
        sf = clock._compute_speed_factor(None, None, 0.8)
        # base 1.0 * 0.6 (interest) = 0.6 < 1.0
        assert sf < 1.0

    def test_tick_accumulates_subjective_time(self):
        """tick() 累积主观时间：DULL 态 10s wall → 20s subjective。"""
        clock = SubjectiveClock()
        clock.tick(10, AttentionStateEnum.DULL, None, 0)
        # DULL factor = 2.0 → 10 * 2 = 20
        assert clock.accumulated > 15

    def test_get_perception_returns_correct_type(self):
        """get_perception 返回 SubjectiveTimePerception 对象。"""
        clock = SubjectiveClock()
        stp = clock.get_perception(0.9)
        assert stp.perception == "normal"
        assert stp.speed_factor == 1.0
        assert stp.fatigue_at_end == 0.9
