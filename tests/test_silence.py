"""Tests for Spec 011: SilenceClassifier — 5 类沉默语义判定 (SC-01~SC-05)"""

import pytest
from chat_core.core.types import (
    EmotionState,
    EnergyState,
    RelationshipStage,
    ReviewResult,
    SilenceRecord,
    SilenceType,
)
from chat_core.systems.silence import SilenceClassifier
from chat_core.systems.energy import EnergyBar


# ── SC-01: 5 类沉默判定 ──────────────────────────────────────

class TestFiveTypeClassification:
    """5 类判定：OVERLOAD → ANGRY → TACIT → HESITANT → STRATEGIC"""

    # ── OVERLOAD ──

    def test_overload_low_energy_and_many_turns(self):
        """OVERLOAD: energy < 0.2 + active_turns > 10"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=None,
            energy=0.1,
            relationship_stage=None,
            active_turns=15,
        )
        assert result.silence_type == SilenceType.OVERLOAD
        assert "精力耗尽" in result.reasoning

    def test_overload_not_triggered_by_low_energy_alone(self):
        """仅低能量但轮次不足 → 不触发 OVERLOAD"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=None,
            energy=0.1,
            relationship_stage=None,
            active_turns=5,
        )
        assert result.silence_type != SilenceType.OVERLOAD

    def test_overload_not_triggered_by_many_turns_alone(self):
        """仅多轮但能量充足 → 不触发 OVERLOAD"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=None,
            energy=0.5,
            relationship_stage=None,
            active_turns=20,
        )
        assert result.silence_type != SilenceType.OVERLOAD

    def test_overload_boundary_energy_at_threshold(self):
        """OVERLOAD 边界：energy = 0.2 刚好不触发"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=None,
            energy=0.2,
            relationship_stage=None,
            active_turns=15,
        )
        assert result.silence_type != SilenceType.OVERLOAD

    def test_overload_boundary_turns_at_threshold(self):
        """OVERLOAD 边界：active_turns = 10 刚好不触发"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=None,
            energy=0.05,
            relationship_stage=None,
            active_turns=10,
        )
        assert result.silence_type != SilenceType.OVERLOAD

    # ── ANGRY ──

    def test_angry_high_anger_low_sadness(self):
        """ANGRY: anger > 0.5 + sadness < 0.3"""
        sc = SilenceClassifier()
        emotion = EmotionState(anger=0.7, sadness=0.1)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.6),
            emotion=emotion,
            energy=0.5,
            relationship_stage=None,
        )
        assert result.silence_type == SilenceType.ANGRY
        assert "生气" in result.reasoning

    def test_angry_not_triggered_high_sadness(self):
        """sadness ≥ 0.3 → ANGRY 不触发"""
        sc = SilenceClassifier()
        emotion = EmotionState(anger=0.7, sadness=0.5)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.6),
            emotion=emotion,
            energy=0.5,
            relationship_stage=None,
        )
        assert result.silence_type != SilenceType.ANGRY

    def test_angry_not_triggered_low_anger(self):
        """anger ≤ 0.5 → ANGRY 不触发"""
        sc = SilenceClassifier()
        emotion = EmotionState(anger=0.3, sadness=0.1)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.6),
            emotion=emotion,
            energy=0.5,
            relationship_stage=None,
        )
        assert result.silence_type != SilenceType.ANGRY

    def test_angry_boundary_anger_just_above(self):
        """ANGRY 边界：anger=0.51 刚好触发"""
        sc = SilenceClassifier()
        emotion = EmotionState(anger=0.51, sadness=0.0)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=emotion,
            energy=0.8,
            relationship_stage=None,
        )
        assert result.silence_type == SilenceType.ANGRY

    def test_angry_boundary_sadness_just_below(self):
        """ANGRY 边界：sadness=0.29 刚好不阻止"""
        sc = SilenceClassifier()
        emotion = EmotionState(anger=0.6, sadness=0.29)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=emotion,
            energy=0.8,
            relationship_stage=None,
        )
        assert result.silence_type == SilenceType.ANGRY

    # ── TACIT ──

    def test_tacit_friend_low_severity(self):
        """TACIT: friend + combined_weight < 0.3"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.2),
            emotion=None,
            energy=0.8,
            relationship_stage=RelationshipStage.FRIEND,
        )
        assert result.silence_type == SilenceType.TACIT
        assert "默契" in result.reasoning

    def test_tacit_close_friend(self):
        """TACIT: close_friend 阶段"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.1),
            emotion=None,
            energy=0.8,
            relationship_stage=RelationshipStage.CLOSE_FRIEND,
        )
        assert result.silence_type == SilenceType.TACIT

    def test_tacit_not_triggered_stranger(self):
        """stranger → TACIT 不触发"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.1),
            emotion=None,
            energy=0.8,
            relationship_stage=RelationshipStage.STRANGER,
        )
        assert result.silence_type != SilenceType.TACIT

    def test_tacit_not_triggered_acquaintance(self):
        """acquaintance → TACIT 不触发"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.1),
            emotion=None,
            energy=0.8,
            relationship_stage=RelationshipStage.ACQUAINTANCE,
        )
        assert result.silence_type != SilenceType.TACIT

    def test_tacit_not_triggered_high_severity(self):
        """combined_weight ≥ 0.3 → TACIT 不触发"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.8),
            emotion=None,
            energy=0.8,
            relationship_stage=RelationshipStage.FRIEND,
        )
        assert result.silence_type != SilenceType.TACIT

    def test_tacit_boundary_severity_at_max(self):
        """TACIT 边界：combined_weight=0.3 刚好不触发"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.3),
            emotion=None,
            energy=0.8,
            relationship_stage=RelationshipStage.FRIEND,
        )
        assert result.silence_type != SilenceType.TACIT

    # ── HESITANT ──

    def test_hesitant_streak_and_confusion(self):
        """HESITANT: silence_streak ≥ 3 + confusion > 0.4"""
        sc = SilenceClassifier()
        emotion = EmotionState(confusion=0.6)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=emotion,
            energy=0.8,
            relationship_stage=RelationshipStage.STRANGER,
            silence_streak=3,
        )
        assert result.silence_type == SilenceType.HESITANT
        assert "不确定" in result.reasoning

    def test_hesitant_not_triggered_low_streak(self):
        """silence_streak < 3 → HESITANT 不触发"""
        sc = SilenceClassifier()
        emotion = EmotionState(confusion=0.6)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=emotion,
            energy=0.8,
            relationship_stage=RelationshipStage.STRANGER,
            silence_streak=2,
        )
        assert result.silence_type != SilenceType.HESITANT

    def test_hesitant_not_triggered_low_confusion(self):
        """confusion ≤ 0.4 → HESITANT 不触发"""
        sc = SilenceClassifier()
        emotion = EmotionState(confusion=0.3)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=emotion,
            energy=0.8,
            relationship_stage=RelationshipStage.STRANGER,
            silence_streak=5,
        )
        assert result.silence_type != SilenceType.HESITANT

    def test_hesitant_boundary_confusion_just_above(self):
        """HESITANT 边界：confusion=0.41 刚好触发"""
        sc = SilenceClassifier()
        emotion = EmotionState(confusion=0.41)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=emotion,
            energy=0.8,
            relationship_stage=RelationshipStage.STRANGER,
            silence_streak=3,
        )
        assert result.silence_type == SilenceType.HESITANT

    # ── STRATEGIC ──

    def test_strategic_default_fallback(self):
        """STRATEGIC: 不满足其他条件时的默认兜底"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(combined_weight=0.6),
            emotion=EmotionState(anger=0.2, sadness=0.2, confusion=0.2),
            energy=0.8,
            relationship_stage=RelationshipStage.STRANGER,
            silence_streak=0,
            active_turns=5,
        )
        assert result.silence_type == SilenceType.STRATEGIC
        assert "选择不参与" in result.reasoning

    def test_strategic_with_none_emotion(self):
        """无情绪数据时也正常兜底为 STRATEGIC"""
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(),
            emotion=None,
            energy=0.8,
            relationship_stage=None,
        )
        assert result.silence_type == SilenceType.STRATEGIC


# ── 优先级排序 ─────────────────────────────────────────────────

class TestSilenceClassifierPriority:
    """判定优先级: OVERLOAD → ANGRY → TACIT → HESITANT → STRATEGIC"""

    def test_overload_beats_angry(self):
        """同时满足 OVERLOAD 和 ANGRY → OVERLOAD 优先"""
        sc = SilenceClassifier()
        emotion = EmotionState(anger=0.7, sadness=0.1)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=emotion,
            energy=0.1,
            relationship_stage=RelationshipStage.FRIEND,
            active_turns=15,
        )
        assert result.silence_type == SilenceType.OVERLOAD

    def test_angry_beats_tacit(self):
        """同时满足 ANGRY 和 TACIT → ANGRY 优先"""
        sc = SilenceClassifier()
        emotion = EmotionState(anger=0.7, sadness=0.1)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.1),
            emotion=emotion,
            energy=0.8,
            relationship_stage=RelationshipStage.FRIEND,
        )
        assert result.silence_type == SilenceType.ANGRY

    def test_tacit_beats_hesitant(self):
        """同时满足 TACIT 和 HESITANT → TACIT 优先"""
        sc = SilenceClassifier()
        emotion = EmotionState(confusion=0.6)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.1),
            emotion=emotion,
            energy=0.8,
            relationship_stage=RelationshipStage.FRIEND,
            silence_streak=5,
        )
        assert result.silence_type == SilenceType.TACIT


# ── SC-02: TACIT 不递增 silence_counter ───────────────────────

class TestTacitSilenceCounter:
    """TACIT 的 silence_increment = 0"""

    def test_tacit_silence_increment_is_zero(self):
        sc = SilenceClassifier()
        assert sc.get_silence_increment(SilenceType.TACIT) == 0

    def test_non_tacit_types_have_positive_increment(self):
        sc = SilenceClassifier()
        assert sc.get_silence_increment(SilenceType.HESITANT) == 1
        assert sc.get_silence_increment(SilenceType.ANGRY) == 1
        assert sc.get_silence_increment(SilenceType.STRATEGIC) == 1

    def test_overload_silence_increment_is_zero(self):
        """OVERLOAD 也不递增 silence_counter（等同于 TACIT）"""
        sc = SilenceClassifier()
        assert sc.get_silence_increment(SilenceType.OVERLOAD) == 0


# ── SC-03: ANGRY 情绪影响 ─────────────────────────────────────

class TestAngryEmotionEffect:
    """ANGRY 沉默的 resentment 方向情绪"""

    def test_angry_silence_preserves_anger_context(self):
        """ANGRY 判定保留 anger 情绪特征"""
        sc = SilenceClassifier()
        emotion = EmotionState(anger=0.8, sadness=0.1)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.7),
            emotion=emotion,
            energy=0.6,
            relationship_stage=None,
        )
        assert result.silence_type == SilenceType.ANGRY

    def test_angry_with_minimal_sadness(self):
        """anger 高 + sadness=0 → 纯 resentment 方向"""
        sc = SilenceClassifier()
        emotion = EmotionState(anger=0.9, sadness=0.0)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=emotion,
            energy=0.8,
            relationship_stage=None,
        )
        assert result.silence_type == SilenceType.ANGRY

    def test_angry_not_triggered_when_sadness_blocks(self):
        """sadness ≥ 0.3 阻止 ANGRY (转向其他类型或 STRATEGIC)"""
        sc = SilenceClassifier()
        emotion = EmotionState(anger=0.9, sadness=0.4)
        result = sc.classify(
            review=ReviewResult(combined_weight=0.5),
            emotion=emotion,
            energy=0.8,
            relationship_stage=None,
        )
        # sadness 压过 anger → 不判定为 ANGRY
        assert result.silence_type != SilenceType.ANGRY

    def test_angry_silence_increment_counts(self):
        """ANGRY silence 计入计数器 (increment ≥ 1)"""
        sc = SilenceClassifier()
        assert sc.get_silence_increment(SilenceType.ANGRY) >= 1


# ── SC-04: OVERLOAD 恢复加速 ──────────────────────────────────

class TestOverloadRecovery:
    """OVERLOAD 触发后 EnergyBar.boost_recovery() 加速恢复"""

    def test_overload_has_recovery_boost(self):
        sc = SilenceClassifier()
        boost = sc.get_recovery_boost()
        assert boost > 1.0
        assert boost == 2.0

    def test_energy_bar_boost_recovery_increases_energy(self):
        eb = EnergyBar()
        eb._state = EnergyState(energy=0.1)
        initial = eb._state.energy
        eb.boost_recovery(multiplier=2.0)
        assert eb._state.energy > initial
        assert eb._state.energy <= 1.0

    def test_energy_bar_boost_recovery_respects_cap(self):
        eb = EnergyBar()
        eb._state = EnergyState(energy=0.99)
        eb.boost_recovery(multiplier=5.0)
        assert eb._state.energy == 1.0

    def test_energy_bar_boost_recovery_multiple_calls(self):
        """多次 boost 叠加但不超过 1.0"""
        eb = EnergyBar()
        eb._state = EnergyState(energy=0.05)
        eb.boost_recovery(multiplier=2.0)
        first = eb._state.energy
        eb.boost_recovery(multiplier=2.0)
        second = eb._state.energy
        assert second >= first
        assert second <= 1.0

    def test_overload_increment_is_zero(self):
        """OVERLOAD 不递增 silence_counter"""
        sc = SilenceClassifier()
        assert sc.get_silence_increment(SilenceType.OVERLOAD) == 0


# ── SC-05: SilenceRecord 字段 ─────────────────────────────────

class TestSilenceRecordFields:
    """SilenceRecord 包含 turn_id + reasoning"""

    def test_silence_record_has_turn_id_field(self):
        record = SilenceRecord(
            silence_type=SilenceType.STRATEGIC,
            turn_id="turn_42",
            reasoning="测试推理",
        )
        assert record.turn_id == "turn_42"
        assert "turn_id" in SilenceRecord.__dataclass_fields__

    def test_silence_record_has_reasoning_field(self):
        record = SilenceRecord(
            silence_type=SilenceType.ANGRY,
            reasoning="生气但克制",
        )
        assert "生气" in record.reasoning
        assert "reasoning" in SilenceRecord.__dataclass_fields__

    def test_classify_returns_record_with_reasoning(self):
        sc = SilenceClassifier()
        result = sc.classify(
            review=ReviewResult(),
            emotion=None,
            energy=0.8,
            relationship_stage=None,
        )
        assert isinstance(result, SilenceRecord)
        assert result.reasoning != ""

    def test_all_five_types_have_nonempty_reasoning(self):
        """所有 5 类的 reasoning 字段非空"""
        sc = SilenceClassifier()

        r_overload = sc.classify(ReviewResult(), None, 0.1, None, active_turns=15)
        assert r_overload.reasoning != ""

        r_angry = sc.classify(ReviewResult(), EmotionState(anger=0.7, sadness=0.1), 0.8, None)
        assert r_angry.reasoning != ""

        r_tacit = sc.classify(ReviewResult(combined_weight=0.1), None, 0.8, RelationshipStage.FRIEND)
        assert r_tacit.reasoning != ""

        r_hesitant = sc.classify(ReviewResult(), EmotionState(confusion=0.6), 0.8, None, silence_streak=3)
        assert r_hesitant.reasoning != ""

        r_strategic = sc.classify(ReviewResult(), None, 0.8, None)
        assert r_strategic.reasoning != ""

    def test_silence_record_emotion_snapshot_field(self):
        """SilenceRecord 包含 emotion_snapshot 字段"""
        assert "emotion_snapshot" in SilenceRecord.__dataclass_fields__

    def test_silence_record_trigger_field(self):
        """SilenceRecord 包含 trigger 字段"""
        record = SilenceRecord(trigger="test_trigger")
        assert record.trigger == "test_trigger"
        assert "trigger" in SilenceRecord.__dataclass_fields__

    def test_silence_record_defaults(self):
        """默认构造的 SilenceRecord 有合理默认值"""
        record = SilenceRecord()
        assert record.silence_type == SilenceType.STRATEGIC
        assert record.turn_id == ""
        assert record.trigger == ""
        assert record.emotion_snapshot is None
        assert record.reasoning == ""
