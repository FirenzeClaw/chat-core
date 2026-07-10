"""Tests for Spec 009: HumorDetector — 预期违背 + 双关语 + 安全门 (SC-10~SC-11)

覆盖:
- 预期违背检测（反问句式）
- 双关语检测
- 陌生人安全门 → 不触发
- friend 安全门 → 通过
- build_injection 格式
"""

from __future__ import annotations

import pytest
from chat_core.core.types import HumorOpportunity, RelationshipStage
from chat_core.systems.humor import HumorDetector


class TestExpectationViolation:
    """SC-10: 预期违背检测 — 反问句式触发"""

    def test_reverse_question_triggers(self):
        """反问句 '难道不是吗' 应触发预期违背"""
        hd = HumorDetector()
        ops = hd.detect("难道不是吗", RelationshipStage.FRIEND)
        ev = next((o for o in ops if o.type == "expectation_violation"), None)
        assert ev is not None
        assert ev.hint != ""
        assert "难道" in ev.hint

    def test_dui_ba_triggers(self):
        """反问句 '对吧' 应触发预期违背"""
        hd = HumorDetector()
        ops = hd.detect("这个方案没问题，对吧？", RelationshipStage.FRIEND)
        ev = next((o for o in ops if o.type == "expectation_violation"), None)
        assert ev is not None
        assert "对吧" in ev.hint

    def test_no_question_pattern_no_violation(self):
        """无反问句 → 不应触发预期违背"""
        hd = HumorDetector()
        ops = hd.detect("今天天气还不错", RelationshipStage.FRIEND)
        ev = next((o for o in ops if o.type == "expectation_violation"), None)
        assert ev is None


class TestPunDetection:
    """SC-10: 双关语检测"""

    def test_ambiguous_word_triggers_pun(self):
        """歧义词 '意思' 应触发双关语"""
        hd = HumorDetector()
        ops = hd.detect("你这是什么意思", RelationshipStage.FRIEND)
        pun = next((o for o in ops if o.type == "pun"), None)
        assert pun is not None
        assert pun.word == "意思"
        assert pun.hint != ""

    def test_no_ambiguous_word_no_pun(self):
        """无歧义词 → 不触发双关语"""
        hd = HumorDetector()
        ops = hd.detect("吃饭了吗", RelationshipStage.FRIEND)
        pun = next((o for o in ops if o.type == "pun"), None)
        assert pun is None


class TestSafetyGate:
    """SC-11: 关系安全门"""

    def test_stranger_blocked(self):
        """陌生人 → 不触发"""
        hd = HumorDetector()
        ops = hd.detect("难道不是吗", RelationshipStage.STRANGER)
        assert ops == []

    def test_acquaintance_blocked(self):
        """熟人 → 不触发"""
        hd = HumorDetector()
        ops = hd.detect("你这是什么意思", RelationshipStage.ACQUAINTANCE)
        assert ops == []

    def test_friend_passes(self):
        """朋友 → 通过安全门"""
        hd = HumorDetector()
        ops = hd.detect("难道不是吗", RelationshipStage.FRIEND)
        assert len(ops) >= 1  # should have at least expectation_violation

    def test_close_friend_passes(self):
        """密友 → 通过安全门"""
        hd = HumorDetector()
        ops = hd.detect("难道不是吗", RelationshipStage.CLOSE_FRIEND)
        assert len(ops) >= 1


class TestBuildInjection:
    """SC-11: build_injection 格式"""

    def test_empty_returns_none(self):
        """空列表 → None"""
        hd = HumorDetector()
        result = hd.build_injection([])
        assert result is None

    def test_format_contains_header(self):
        """注入文本包含 [幽默机会] 头部"""
        hd = HumorDetector()
        ops = hd.detect("难道不是吗", RelationshipStage.FRIEND)
        injection = hd.build_injection(ops)
        assert injection is not None
        assert "[幽默机会]" in injection

    def test_format_contains_hint(self):
        """注入文本包含 hint 内容"""
        hd = HumorDetector()
        ops = hd.detect("你这是什么意思？难道不是吗？", RelationshipStage.FRIEND)
        injection = hd.build_injection(ops)
        assert injection is not None
        # Should contain hints from both opportunities
        assert "难道" in injection or "意思" in injection
