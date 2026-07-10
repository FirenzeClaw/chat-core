"""Tests for Spec 009: MoralConflictDetector + ProConAssessor (SC-12~SC-16)

覆盖:
- 三种冲突检测 (honesty_vs_protection, loyalty_conflict, self_vs_other)
- Pro/Con 评估路径判定 (honest / protective / deadlock)
- escalation 判定 (|diff| > 0.4 → escalation=True)
- deadlock 判定 (|diff| < 0.2 → deadlock=True)
"""

from __future__ import annotations

import pytest
from chat_core.core.types import (
    MoralConflictType,
    RelationshipStage,
)
from chat_core.systems.moral import MoralConflictDetector, ProConAssessor


class TestMoralConflictDetection:
    """SC-12~SC-14: 三种冲突类型检测"""

    # ── honesty_vs_protection ──

    def test_honesty_vs_protection_detected(self):
        """评价请求 + 负面内心判断 + friend 关系 → 触发"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="你觉得这个方案怎么样",
            inner_thoughts="但这个方案其实不太好",
            relationship_stage=RelationshipStage.FRIEND,
            energy=0.8,
        )
        assert conflict is not None
        assert conflict.conflict_type == MoralConflictType.HONESTY_VS_PROTECTION
        assert conflict.stakes == 0.5

    def test_honesty_vs_protection_close_friend_stakes(self):
        """密友 → stakes 更高 (0.8)"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="你觉得这个方案怎么样",
            inner_thoughts="但这个方案其实不太好",
            relationship_stage=RelationshipStage.CLOSE_FRIEND,
            energy=0.8,
        )
        assert conflict is not None
        assert conflict.conflict_type == MoralConflictType.HONESTY_VS_PROTECTION
        assert conflict.stakes == 0.8

    def test_honesty_vs_protection_no_negative_not_triggered(self):
        """无负面判断 → 不触发"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="你觉得这个方案怎么样",
            inner_thoughts="这个方案还不错",
            relationship_stage=RelationshipStage.FRIEND,
            energy=0.8,
        )
        assert conflict is None

    def test_honesty_vs_protection_no_evaluation_not_triggered(self):
        """无评价关键词 → 不触发"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="今天吃了什么",
            inner_thoughts="今天的饭不太好",
            relationship_stage=RelationshipStage.FRIEND,
            energy=0.8,
        )
        assert conflict is None

    def test_honesty_vs_protection_stranger_blocked(self):
        """陌生人评价请求 → 不触发"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="你觉得这个方案怎么样",
            inner_thoughts="但这个方案其实不太好",
            relationship_stage=RelationshipStage.STRANGER,
            energy=0.8,
        )
        assert conflict is None

    # ── loyalty_conflict ──

    def test_loyalty_conflict_detected(self):
        """用户对第三方抱怨 + friend → 触发"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="那个人真的很烦",
            inner_thoughts=None,
            relationship_stage=RelationshipStage.FRIEND,
            energy=0.8,
        )
        assert conflict is not None
        assert conflict.conflict_type == MoralConflictType.LOYALTY_CONFLICT
        assert conflict.stakes == 0.6

    def test_loyalty_conflict_stranger_blocked(self):
        """陌生人对第三方抱怨 → 不触发"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="那个人真的很烦",
            inner_thoughts=None,
            relationship_stage=RelationshipStage.STRANGER,
            energy=0.8,
        )
        assert conflict is None

    def test_loyalty_conflict_no_complaint_keyword(self):
        """无抱怨关键词 → 不触发"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="今天天气真好",
            inner_thoughts=None,
            relationship_stage=RelationshipStage.FRIEND,
            energy=0.8,
        )
        assert conflict is None

    # ── self_vs_other ──

    def test_self_vs_other_low_energy_triggered(self):
        """精力 < 0.2 → 触发"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="我们继续聊",
            inner_thoughts=None,
            relationship_stage=RelationshipStage.FRIEND,
            energy=0.1,
        )
        assert conflict is not None
        assert conflict.conflict_type == MoralConflictType.SELF_VS_OTHER
        assert conflict.stakes == 0.3

    def test_self_vs_other_normal_energy_not_triggered(self):
        """精力正常 → 不触发"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="我们继续聊",
            inner_thoughts=None,
            relationship_stage=RelationshipStage.FRIEND,
            energy=0.5,
        )
        # loyalty_conflict won't trigger (no complaint keyword)
        # honesty_vs_protection won't trigger (no evaluation)
        # self_vs_other won't trigger (energy >= 0.2)
        assert conflict is None

    def test_no_conflict_normal_message(self):
        """普通消息 + 正常状态 → 无冲突"""
        mcd = MoralConflictDetector()
        conflict = mcd.detect(
            user_message="你好",
            inner_thoughts=None,
            relationship_stage=RelationshipStage.FRIEND,
            energy=0.8,
        )
        assert conflict is None


class TestProConAssessment:
    """SC-15~SC-16: Pro/Con 评估 + 路径判定"""

    def test_honest_path(self):
        """logic > emotion → 'honest'"""
        pa = ProConAssessor()
        result = pa.assess(
            logic_score=0.8,
            logic_reasoning="从事实角度应该说实话",
            emotion_score=0.3,
            emotion_reasoning="但这可能伤害对方",
        )
        assert result.recommended_path == "honest"
        assert result.escalation is True   # |0.8 - 0.3| = 0.5 > 0.4
        assert result.deadlock is False

    def test_protective_path(self):
        """emotion > logic → 'protective'"""
        pa = ProConAssessor()
        result = pa.assess(
            logic_score=0.2,
            logic_reasoning="事实不重要",
            emotion_score=0.9,
            emotion_reasoning="关系最重要",
        )
        assert result.recommended_path == "protective"
        assert result.escalation is True   # |0.2 - 0.9| = 0.7 > 0.4
        assert result.deadlock is False

    def test_deadlock_path(self):
        """|diff| < 0.2 → 'deadlock'"""
        pa = ProConAssessor()
        result = pa.assess(
            logic_score=0.55,
            logic_reasoning="道理上也说得过去",
            emotion_score=0.45,
            emotion_reasoning="感情上也说得过去",
        )
        assert result.recommended_path == "deadlock"
        assert result.deadlock is True
        assert result.escalation is False  # |0.55 - 0.45| = 0.1 < 0.4

    def test_escalation_boundary(self):
        """|diff| 恰好 > 0.4 → escalation=True"""
        pa = ProConAssessor()
        result = pa.assess(
            logic_score=0.7,
            logic_reasoning="真相重要",
            emotion_score=0.29,
            emotion_reasoning="关系也要考虑",
        )
        # |0.7 - 0.29| = 0.41 > 0.4
        assert result.escalation is True

    def test_no_escalation_no_deadlock(self):
        """中间区域: 0.2 ≤ |diff| ≤ 0.4 → 不 deadlock 也不 escalation"""
        pa = ProConAssessor()
        result = pa.assess(
            logic_score=0.6,
            logic_reasoning="偏真相",
            emotion_score=0.3,
            emotion_reasoning="也偏关系",
        )
        # |0.6 - 0.3| = 0.3, 0.2 <= 0.3 <= 0.4
        assert result.deadlock is False
        assert result.escalation is False
        assert result.recommended_path == "honest"  # logic > emotion

    def test_exact_boundary_deadlock(self):
        """|diff| 恰好 == 0.21 → 不算 deadlock (< 0.2)"""
        pa = ProConAssessor()
        result = pa.assess(
            logic_score=0.61,
            logic_reasoning="偏真相",
            emotion_score=0.4,
            emotion_reasoning="偏关系",
        )
        # |0.61 - 0.4| = 0.21, not < 0.2
        assert result.deadlock is False
        assert result.escalation is False  # 0.21 not > 0.4

    def test_exact_boundary_escalation(self):
        """|diff| 恰好 == 0.4 → 不算 escalation (> 0.4)"""
        pa = ProConAssessor()
        result = pa.assess(
            logic_score=0.8,
            logic_reasoning="真相",
            emotion_score=0.4,
            emotion_reasoning="关系",
        )
        # |0.8 - 0.4| = 0.4, not > 0.4
        assert result.escalation is False

    def test_reasoning_preserved(self):
        """推理文本应原样保留"""
        pa = ProConAssessor()
        result = pa.assess(
            logic_score=0.7,
            logic_reasoning="逻辑推理",
            emotion_score=0.3,
            emotion_reasoning="情感推理",
        )
        assert result.logic_reasoning == "逻辑推理"
        assert result.emotion_reasoning == "情感推理"
