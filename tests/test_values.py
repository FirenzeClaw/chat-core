"""Tests for Spec 010: ValueEngine — three-layer tree, dynamic adjust, modulation"""

import pytest
from chat_core.systems.values import ValueEngine, VIRTUE_CHILDREN


class TestValueEngineTree:
    """三层树加载测试"""

    def test_initial_virtue_weights(self):
        ve = ValueEngine()
        v = ve.values
        assert v.honesty == 0.7
        assert v.care == 0.6
        assert v.growth == 0.8

    def test_initial_child_weights(self):
        ve = ValueEngine()
        v = ve.values
        assert v.truthfulness == 0.8
        assert v.self_honesty == 0.7
        assert v.transparency == 0.5
        assert v.empathy_protection == 0.6
        assert v.loyalty == 0.5
        assert v.nurturing == 0.7
        assert v.curiosity_drive == 0.8
        assert v.self_improvement == 0.7
        assert v.openness == 0.6

    def test_virtue_children_structure(self):
        assert len(VIRTUE_CHILDREN) == 3
        total_children = sum(len(c) for c in VIRTUE_CHILDREN.values())
        assert total_children == 9


class TestValueEngineAdjust:
    """动态调权测试"""

    def test_metacognition_defense_adjust(self):
        ve = ValueEngine()
        original = ve.values.self_honesty
        ve.adjust("metacognition_defense")
        assert ve.values.self_honesty == min(1.0, original + 0.05)

    def test_positive_impact_adjust(self):
        ve = ValueEngine()
        original = ve.values.nurturing
        ve.adjust("positive_impact")
        assert ve.values.nurturing == min(1.0, original + 0.05)

    def test_adjust_clamped_to_1(self):
        ve = ValueEngine()
        ve.values.self_honesty = 0.98
        ve.adjust("metacognition_defense")
        assert ve.values.self_honesty == 1.0

    def test_vulnerability_does_not_adjust_weights(self):
        ve = ValueEngine()
        original_honesty = ve.values.honesty
        ve.adjust("vulnerability")
        assert ve.values.honesty == original_honesty


class TestValueEngineModulation:
    """决策调制测试"""

    def test_review_threshold_modulation(self):
        ve = ValueEngine()
        mod = ve.get_modulation("review_threshold")
        assert mod == 0.7

    def test_defense_prob_multiplier(self):
        ve = ValueEngine()
        mod = ve.get_modulation("defense_prob_multiplier")
        assert mod == pytest.approx(1.3)  # 2.0 - 0.7

    def test_moral_bias(self):
        ve = ValueEngine()
        bias = ve.get_modulation("moral_bias")
        expected = 0.8 / (0.8 + 0.6)
        assert bias == pytest.approx(expected)

    def test_disabled_engine_returns_default(self):
        """disabled 时返回不调制的默认值"""
        ve = ValueEngine()
        ve._enabled = False
        assert ve.get_modulation("review_threshold") == 1.0
        assert ve.get_modulation("defense_prob_multiplier") == 1.0
        assert ve.get_modulation("moral_bias") == 0.5
