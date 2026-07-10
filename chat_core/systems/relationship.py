"""RelationshipEngine — 4 维关系向量 + 阶段判定 + 人格调制 (Spec 008)

纯计算引擎：零 LLM 调用，零 I/O。由 TurnManager/Adapter 在每 turn 后调用 update()。
"""

from __future__ import annotations

import time
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import (
    RelationshipModulation,
    RelationshipStage,
    RelationshipVector,
)


# ── 阶段判定阈值 ──────────────────────────────────────────

STAGE_RULES: list[tuple[RelationshipStage, dict[str, Any]]] = [
    (RelationshipStage.CLOSE_FRIEND,  {"trust_min": 0.5, "closeness_min": 0.4}),
    (RelationshipStage.FRIEND,        {"trust_min": 0.3, "closeness_min": 0.2}),
    (RelationshipStage.ACQUAINTANCE,  {"familiarity_min": 0.1}),
    # STRANGER is fallback
]


class RelationshipEngine:
    """关系引擎：per-user 4 维向量维护 + 阶段判定 + 人格调制系数输出。

    全局单例，TurnManager 持有。per-user 状态通过 dict[user_id, RelationshipVector] 管理。
    """

    def __init__(self) -> None:
        cfg = get_config()
        rc = cfg.relationship_config()
        self._enabled: bool = bool(rc.get("enabled", True))

        # 维度增长参数
        dims = rc.get("dimensions", {})
        tc = dims.get("trust", {})
        self._trust_recall_hit_boost: float = float(tc.get("recall_hit_boost", 0.03))
        self._trust_deep_threshold: float = float(tc.get("deep_conversation_threshold", 0.3))
        self._trust_deep_boost: float = float(tc.get("deep_conversation_boost", 0.05))
        self._trust_decay: float = float(tc.get("decay_rate", 0.001))

        cc = dims.get("closeness", {})
        self._close_per_turn: float = float(cc.get("per_turn", 0.01))
        self._close_resonance_threshold: float = float(cc.get("emotional_resonance_threshold", 0.6))
        self._close_resonance_boost: float = float(cc.get("emotional_resonance_boost", 0.03))
        self._close_disclosure_boost: float = float(cc.get("self_disclosure_boost", 0.02))
        self._close_disclosure_keywords: list[str] = list(cc.get("self_disclosure_keywords", []))
        self._close_decay: float = float(cc.get("decay_rate", 0.003))

        rc2 = dims.get("respect", {})
        self._respect_quality_boost: float = float(rc2.get("topic_quality_boost", 0.02))
        self._respect_quality_min_len: int = int(rc2.get("topic_quality_min_length", 20))
        self._respect_correction_boost: float = float(rc2.get("correction_accepted_boost", 0.05))
        self._respect_decay: float = float(rc2.get("decay_rate", 0.0))

        fc = dims.get("familiarity", {})
        self._fam_per_turn: float = float(fc.get("per_turn", 0.005))
        self._fam_per_memory: float = float(fc.get("per_memory_entry", 0.002))
        self._fam_decay: float = float(fc.get("decay_rate", 0.0))

        # 阶段阈值
        stages_cfg = rc.get("stages", {})
        self._stage_thresholds: dict[RelationshipStage, dict[str, float]] = {}
        for stage, defaults in STAGE_RULES:
            sc = stages_cfg.get(stage.value, {})
            thresholds: dict[str, float] = {}
            for k in defaults:
                thresholds[k] = float(sc.get(k, defaults[k]))
            self._stage_thresholds[stage] = thresholds

        # 人格调制系数
        mod_cfg = rc.get("personality_modulation", {})
        self._modulation: dict[RelationshipStage, RelationshipModulation] = {}
        default_mods = {
            RelationshipStage.STRANGER:     {"empathy_mult": 0.7, "self_disclosure_mult": 0.3, "defense_prob_mult": 1.5, "proactive_prob_mult": 0.0},
            RelationshipStage.ACQUAINTANCE: {"empathy_mult": 0.9, "self_disclosure_mult": 0.6, "defense_prob_mult": 1.1, "proactive_prob_mult": 0.3},
            RelationshipStage.FRIEND:       {"empathy_mult": 1.0, "self_disclosure_mult": 1.0, "defense_prob_mult": 0.8, "proactive_prob_mult": 1.0},
            RelationshipStage.CLOSE_FRIEND: {"empathy_mult": 1.2, "self_disclosure_mult": 1.5, "defense_prob_mult": 0.5, "proactive_prob_mult": 1.3},
        }
        for stage, defaults in default_mods.items():
            sm = mod_cfg.get(stage.value, {})
            self._modulation[stage] = RelationshipModulation(
                empathy_mult=float(sm.get("empathy", defaults["empathy_mult"])),
                self_disclosure_mult=float(sm.get("self_disclosure", defaults["self_disclosure_mult"])),
                defense_prob_mult=float(sm.get("defense_prob", defaults["defense_prob_mult"])),
                proactive_prob_mult=float(sm.get("proactive_prob", defaults["proactive_prob_mult"])),
            )

        # per-user 状态
        self._vectors: dict[str, RelationshipVector] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── Per-user 访问 ──────────────────────────────────────

    def get_vector(self, user_id: str) -> RelationshipVector:
        """获取或创建 per-user 关系向量"""
        if user_id not in self._vectors:
            self._vectors[user_id] = RelationshipVector(user_id=user_id)
        return self._vectors[user_id]

    def get_stage(self, user_id: str) -> RelationshipStage:
        """判定当前关系阶段"""
        return self._determine_stage(self.get_vector(user_id))

    def get_modulation(self, user_id: str) -> RelationshipModulation:
        """获取当前阶段的人格调制系数"""
        return self._modulation.get(self.get_stage(user_id), self._modulation[RelationshipStage.STRANGER])

    # ── 核心更新方法 ───────────────────────────────────────

    def update(
        self,
        user_id: str,
        recall_hit_count: int = 0,
        combined_review_weight: float = 1.0,
        inner_thoughts_text: str = "",
        user_message: str = "",
        correction_accepted: bool = False,
        user_emotion_valence: float = 0.0,
        ai_emotion_valence: float = 0.0,
        memory_entry_count: int = 0,
        is_turn: bool = True,
    ) -> RelationshipVector:
        """根据本轮上下文更新关系向量。

        Args:
            user_id: 用户标识
            recall_hit_count: recall 命中条数（≥3 → trust boost）
            combined_review_weight: 审查 combined_weight（< 0.3 → trust boost）
            inner_thoughts_text: 内心戏文本（检测 self-disclosure）
            user_message: 用户消息原文（topic quality check）
            correction_accepted: 纠正是否被接受
            user_emotion_valence: 用户情绪效价（来自 EmotionEngine）
            ai_emotion_valence: AI 情绪效价（来自 EmotionEngine）
            memory_entry_count: 此用户的记忆条目数
            is_turn: 是否是一次完整 turn（用于 per_turn 增长）

        Returns:
            更新后的 RelationshipVector
        """
        if not self._enabled:
            return self.get_vector(user_id)

        v = self.get_vector(user_id)
        now = time.time()

        # ① 衰减计算（基于时间间隔）
        self._apply_decay(v, now)

        if not is_turn:
            v.last_interaction = now
            return v

        # ② 基础 per-turn 增长
        v.familiarity += self._fam_per_turn
        v.closeness += self._close_per_turn

        # ③ trust: recall 命中
        if recall_hit_count >= 3:
            v.trust = min(1.0, v.trust + self._trust_recall_hit_boost)

        # ④ trust: 深度对话（低错误率 ≈ 聊得来）
        if combined_review_weight < self._trust_deep_threshold:
            v.trust = min(1.0, v.trust + self._trust_deep_boost)

        # ⑤ closeness: 情感共鸣 (|valence_diff| < threshold)
        valence_diff = abs(user_emotion_valence - ai_emotion_valence)
        if valence_diff < self._close_resonance_threshold:
            v.closeness = min(1.0, v.closeness + self._close_resonance_boost)

        # ⑥ closeness: 自我暴露检测
        if inner_thoughts_text and self._close_disclosure_keywords:
            if any(kw in inner_thoughts_text for kw in self._close_disclosure_keywords):
                v.closeness = min(1.0, v.closeness + self._close_disclosure_boost)

        # ⑦ respect: 话题质量
        if len(user_message) >= self._respect_quality_min_len:
            v.respect = min(1.0, v.respect + self._respect_quality_boost)

        # ⑧ respect: 纠正被接受
        if correction_accepted:
            v.respect = min(1.0, v.respect + self._respect_correction_boost)

        # ⑨ familiarity: 记忆条目
        v.familiarity = min(1.0, v.familiarity + memory_entry_count * self._fam_per_memory)

        v.last_interaction = now

        # Clamp
        v.trust = max(0.0, min(1.0, v.trust))
        v.closeness = max(0.0, min(1.0, v.closeness))
        v.respect = max(0.0, min(1.0, v.respect))
        v.familiarity = max(0.0, min(1.0, v.familiarity))

        return v

    # ── 内部 ────────────────────────────────────────────────

    def _apply_decay(self, v: RelationshipVector, now: float) -> None:
        """基于距上次交互的天数回溯计算衰减"""
        if v.last_interaction <= 0:
            return
        days = (now - v.last_interaction) / 86400.0
        if days <= 0:
            return
        v.trust = max(0.0, v.trust - self._trust_decay * days)
        v.closeness = max(0.0, v.closeness - self._close_decay * days)
        v.respect = max(0.0, v.respect - self._respect_decay * days)
        v.familiarity = max(0.0, v.familiarity - self._fam_decay * days)

    def _determine_stage(self, v: RelationshipVector) -> RelationshipStage:
        """按优先级判定关系阶段"""
        # close_friend: trust > 0.5 AND closeness > 0.4
        t = self._stage_thresholds.get(RelationshipStage.CLOSE_FRIEND, {})
        if v.trust > t.get("trust_min", 0.5) and v.closeness > t.get("closeness_min", 0.4):
            return RelationshipStage.CLOSE_FRIEND

        # friend: trust > 0.3 AND closeness > 0.2
        t = self._stage_thresholds.get(RelationshipStage.FRIEND, {})
        if v.trust > t.get("trust_min", 0.3) and v.closeness > t.get("closeness_min", 0.2):
            return RelationshipStage.FRIEND

        # acquaintance: familiarity >= 0.1
        t = self._stage_thresholds.get(RelationshipStage.ACQUAINTANCE, {})
        if v.familiarity >= t.get("familiarity_min", 0.1):
            return RelationshipStage.ACQUAINTANCE

        return RelationshipStage.STRANGER

    def get_stage_description(self, stage: RelationshipStage) -> str:
        """返回阶段的中文描述"""
        descriptions = {
            RelationshipStage.STRANGER: "陌生人",
            RelationshipStage.ACQUAINTANCE: "熟人",
            RelationshipStage.FRIEND: "朋友",
            RelationshipStage.CLOSE_FRIEND: "密友",
        }
        return descriptions.get(stage, "未知")

    # ── Spec 007 联动 ─────────────────────────────────────

    def get_adjusted_proactive_prob(
        self,
        user_id: str,
        base_proactive: float,
        energy: float,
        energy_low_threshold: float = 0.3,
    ) -> float:
        """低精力降主动 (Spec 007 → Spec 008 联动)。

        Args:
            user_id: 用户标识
            base_proactive: PersonalityEngine 的主动频率
            energy: 当前精力值 [0, 1]
            energy_low_threshold: 精力临界值

        Returns:
            调整后的主动概率
        """
        mod = self.get_modulation(user_id)
        adjusted = base_proactive * mod.proactive_prob_mult
        if energy < energy_low_threshold:
            adjusted *= 0.3  # 累了不想主动社交
        return adjusted
