"""MoralConflict — 道德困境检测 + 双脑 Pro/Con 评估 (Spec 009)"""

from __future__ import annotations

from typing import Any

from chat_core.config import get_config
from chat_core.core.types import (
    MoralConflict,
    MoralConflictType,
    ProConAssessment,
    RelationshipStage,
)


# 诚实vs保护: 评价性关键词
EVALUATION_KEYWORDS = [
    "你觉得", "评价一下", "怎么样", "好不好",
    "是不是很差", "水平如何", "值得吗",
]

# 忠诚冲突: 告状/抱怨关键词
COMPLAINT_KEYWORDS = [
    "他/她", "那个人", "某某",
]


class MoralConflictDetector:
    """道德困境检测器。"""

    def __init__(self) -> None:
        cfg = get_config()
        mc = cfg.moral_conflict_config()
        self._enabled: bool = bool(mc.get("enabled", True))
        types_cfg = mc.get("types", [])
        self._active_types: set[str] = set(types_cfg)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def detect(
        self,
        user_message: str,
        inner_thoughts: str | None,
        relationship_stage: RelationshipStage | None,
        energy: float,
    ) -> MoralConflict | None:
        """检测本轮是否存在道德困境。"""
        if not self._enabled:
            return None

        inner = inner_thoughts or ""

        # 1. 诚实 vs 保护
        if "honesty_vs_protection" in self._active_types:
            conflict = self._check_honesty_vs_protection(user_message, inner, relationship_stage)
            if conflict:
                return conflict

        # 2. 忠诚冲突
        if "loyalty_conflict" in self._active_types:
            conflict = self._check_loyalty_conflict(user_message, relationship_stage)
            if conflict:
                return conflict

        # 3. 自我 vs 他人
        if "self_vs_other" in self._active_types:
            conflict = self._check_self_vs_other(energy)
            if conflict:
                return conflict

        return None

    def _check_honesty_vs_protection(
        self, message: str, inner_thoughts: str,
        stage: RelationshipStage | None,
    ) -> MoralConflict | None:
        """评价请求 + 内心负面判断 + 关系≥朋友 → 诚实vs保护"""
        has_evaluation = any(kw in message for kw in EVALUATION_KEYWORDS)
        if not has_evaluation:
            return None

        # 简易负面判断检测
        negative_cues = ["不太好", "不太行", "有问题", "差点意思"]
        has_negative = any(cue in inner_thoughts for cue in negative_cues)
        if not has_negative:
            return None

        if stage not in (RelationshipStage.FRIEND, RelationshipStage.CLOSE_FRIEND):
            return None

        stakes = 0.5 if stage == RelationshipStage.FRIEND else 0.8
        return MoralConflict(
            conflict_type=MoralConflictType.HONESTY_VS_PROTECTION,
            trigger_description=f"用户请求评价，AI内心有负面判断，关系={stage.value}",
            stakes=stakes,
        )

    def _check_loyalty_conflict(
        self, message: str, stage: RelationshipStage | None,
    ) -> MoralConflict | None:
        """用户对第三方抱怨 + 已有社交记忆 → 忠诚冲突"""
        has_complaint = any(kw in message for kw in COMPLAINT_KEYWORDS)
        if not has_complaint:
            return None
        if stage not in (RelationshipStage.FRIEND, RelationshipStage.CLOSE_FRIEND):
            return None
        return MoralConflict(
            conflict_type=MoralConflictType.LOYALTY_CONFLICT,
            trigger_description="用户对第三方表达不满",
            stakes=0.6,
        )

    def _check_self_vs_other(self, energy: float) -> MoralConflict | None:
        """精力耗尽 → 自我 vs 他人"""
        if energy < 0.2:
            return MoralConflict(
                conflict_type=MoralConflictType.SELF_VS_OTHER,
                trigger_description=f"精力耗尽 (energy={energy:.2f})，AI想退出但用户可能还想聊",
                stakes=0.3,
            )
        return None


class ProConAssessor:
    """双脑 Pro/Con 评估器。评估逻辑 + 路径判定。"""

    def __init__(self) -> None:
        cfg = get_config()
        pc = cfg.moral_conflict_config().get("pro_con", {})
        self._deadlock_threshold: float = float(pc.get("deadlock_threshold", 0.2))
        self._escalate_threshold: float = float(pc.get("escalate_to_metacognition", 0.4))

    def assess(
        self,
        logic_score: float,
        logic_reasoning: str,
        emotion_score: float,
        emotion_reasoning: str,
        moral_bias: float | None = None,
    ) -> ProConAssessment:
        """根据双脑结果判定路径。"""
        diff = abs(logic_score - emotion_score)
        # Spec 010: 价值观调制
        if moral_bias is not None:
            emotion_score += moral_bias * 0.1
            logic_score += (1.0 - moral_bias) * 0.1
            diff = abs(logic_score - emotion_score)
        deadlock = diff < self._deadlock_threshold
        escalation = diff > self._escalate_threshold

        if deadlock:
            path = "deadlock"
        elif logic_score > emotion_score:
            path = "honest"
        else:
            path = "protective"

        return ProConAssessment(
            logic_score=logic_score,
            logic_reasoning=logic_reasoning,
            emotion_score=emotion_score,
            emotion_reasoning=emotion_reasoning,
            deadlock=deadlock,
            escalation=escalation,
            recommended_path=path,
        )
