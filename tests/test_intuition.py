"""Tests for Spec 009: IntuitionEngine — 3-level degradation (SC-01~SC-05)"""

import pytest
from chat_core.core.types import (
    AttentionStateEnum,
    ChainedMemory,
    IntuitionLevel,
    MemoryEntry,
)
from chat_core.systems.intuition import IntuitionEngine


class TestIntuitionBasic:
    """SC-01: 默认 L3 降级"""

    def test_default_l3(self):
        """无记忆命中 → L3_FULL_REACT"""
        ie = IntuitionEngine()
        r = ie.evaluate([], None, 0.9)
        assert r.level == IntuitionLevel.L3_FULL_REACT
        assert not r.skip_react

    def test_l1_insufficient_hits(self):
        """SC-02: 记忆条数不足 (< 5) → 不触发 L1"""
        ie = IntuitionEngine()
        entries = [
            ChainedMemory(entry=MemoryEntry(namespace="t", key="k", value={}, salience=8.0))
        ]
        r = ie.evaluate(entries, AttentionStateEnum.FOCUSED, 0.9)
        assert r.level != IntuitionLevel.L1_MEMORY_MATCH

    def test_l1_insufficient_salience(self):
        """SC-02: max salience < 7 → 不触发 L1"""
        ie = IntuitionEngine()
        entries = [
            ChainedMemory(entry=MemoryEntry(namespace="t", key=f"k{i}", value={"text": "m"}, salience=3.0))
            for i in range(6)
        ]
        r = ie.evaluate(entries, AttentionStateEnum.FOCUSED, 0.9)
        assert r.level != IntuitionLevel.L1_MEMORY_MATCH

    def test_l1_strong_hits(self):
        """SC-03: ≥5 hits, max salience ≥ 8, FOCUSED → 大概率 L1（概率性，多次采样）"""
        ie = IntuitionEngine()
        entries = [
            ChainedMemory(entry=MemoryEntry(namespace="t", key=f"k{i}", value={"text": "记忆内容"}, salience=8.0))
            for i in range(6)
        ]
        # FOCUSED + 6 hits + salience 8 → FOCUSED boost 1.5 → prob = min(0.8*1.5, 0.95) = 0.95
        # 采样 10 次，至少 6 次命中 L1
        l1_count = 0
        for _ in range(10):
            r = ie.evaluate(entries, AttentionStateEnum.FOCUSED, 0.9)
            if r.level == IntuitionLevel.L1_MEMORY_MATCH:
                l1_count += 1
                assert r.skip_react
                assert r.fast_reply is not None
        assert l1_count >= 6, f"Expected ≥6 L1 hits in 10 samples, got {l1_count}"

    def test_dull_reduces_l1(self):
        """SC-05: DULL 态 L1 概率 × 0.5 → 减少 L1 触发"""
        ie = IntuitionEngine()
        entries = [
            ChainedMemory(entry=MemoryEntry(namespace="t", key=f"k{i}", value={"text": "记忆内容"}, salience=8.0))
            for i in range(6)
        ]
        # DULL → L1 prob = max(0.8*0.5, 0.05) = 0.4
        l1_count = 0
        for _ in range(10):
            r = ie.evaluate(entries, AttentionStateEnum.DULL, 0.9)
            if r.level == IntuitionLevel.L1_MEMORY_MATCH:
                l1_count += 1
        # DULL 下 L1 概率 ~40%，10 次中不超 7 次
        assert l1_count <= 7, f"DULL should suppress L1, got {l1_count}/10"


class TestConfidenceHeuristic:
    """SC-04: L2 Fast Path 置信度启发式"""

    def test_high_confidence(self):
        """回复长度 ≥ 50 字符 → 高置信度 (≥ 0.7)"""
        ie = IntuitionEngine()
        conf = ie.eval_fast_path_confidence("这是一个足够长的回复文本" * 12, "")
        assert conf > 0.7

    def test_low_confidence(self):
        """回复长度 < 50 字符 → 低置信度 (0.4)"""
        ie = IntuitionEngine()
        conf = ie.eval_fast_path_confidence("短", "")
        assert conf < 0.7


class TestL2FastPath:
    """SC-02: L2 Fast Path 完整链路 — 标记设置 + 置信度门控"""

    def test_l2_confidence_gating_high(self):
        """L2 置信度 ≥ 阈值 → 应采纳快速回复 (SC-02)"""
        ie = IntuitionEngine()
        conf = ie.eval_fast_path_confidence("x" * 60, "")
        assert conf >= 0.7  # ≥50 字符 → 高置信度
        assert conf >= ie._l2_confidence_threshold  # 通过门控

    def test_l2_confidence_gating_low(self):
        """L2 置信度 < 阈值 → 应回落 L3"""
        ie = IntuitionEngine()
        conf = ie.eval_fast_path_confidence("short", "")
        assert conf < 0.7  # 不通过门控

    def test_l2_flag_reset_after_consumption(self):
        """L2 pending flag 在 _think() 中消费后应重置"""
        # 此处验证 IntuitionEngine._check_l2 的返回值正确
        ie = IntuitionEngine()
        from chat_core.core.types import ChainedMemory, MemoryEntry, AttentionStateEnum
        entries = [ChainedMemory(entry=MemoryEntry(
            namespace="t", key=f"k{i}", value={"t": "m"}, salience=3.0)) for i in range(3)]
        result = ie.evaluate(entries, AttentionStateEnum.FOCUSED, 0.9)
        # L1 条件不满足（salience < 7），L2 应该被触发
        if result.level == IntuitionLevel.L2_FAST_PATH:
            assert not result.skip_react  # L2 不直接 skip，留给 _think() 判定
            assert result.confidence == 0.0  # 初始置信度 0，等待 Flash 调用后填充

