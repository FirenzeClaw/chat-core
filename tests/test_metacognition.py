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
