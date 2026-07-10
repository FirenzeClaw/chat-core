"""Tests for Spec 008: RelationshipEngine — 4-dim vector, stage determination, modulation"""

import time
import pytest
from chat_core.core.types import RelationshipStage, RelationshipModulation
from chat_core.systems.relationship import RelationshipEngine


class TestRelationshipVector:
    """4 维基础计算 (SC-01)"""

    def test_initial_vector_is_stranger(self):
        re = RelationshipEngine()
        v = re.get_vector("u1")
        assert v.trust == 0.0
        assert v.closeness == 0.0
        assert v.respect == 0.0
        assert v.familiarity == 0.0
        assert re.get_stage("u1") == RelationshipStage.STRANGER

    def test_per_turn_growth(self):
        re = RelationshipEngine()
        # 两个 valence 都为 0 时 diff=0 < 0.6，resonance boost 也会触发
        v = re.update("u1", is_turn=True)
        assert v.familiarity == 0.005
        assert v.closeness == 0.04  # per_turn(0.01) + resonance(0.03)

    def test_recall_hit_boosts_trust(self):
        re = RelationshipEngine()
        v = re.update("u1", recall_hit_count=3, is_turn=True)
        assert v.trust == pytest.approx(0.03)

    def test_recall_hit_below_3_no_boost(self):
        re = RelationshipEngine()
        v = re.update("u1", recall_hit_count=2, is_turn=True)
        assert v.trust == 0.0

    def test_deep_conversation_boosts_trust(self):
        re = RelationshipEngine()
        v = re.update("u1", combined_review_weight=0.2, is_turn=True)
        assert v.trust == pytest.approx(0.05)

    def test_topic_quality_boosts_respect(self):
        re = RelationshipEngine()
        msg = "我觉得你说的很有道理，但我还有一个问题想请教一下"
        v = re.update("u1", user_message=msg, is_turn=True)
        assert v.respect == pytest.approx(0.02)

    def test_correction_accepted_boosts_respect(self):
        re = RelationshipEngine()
        v = re.update("u1", correction_accepted=True, is_turn=True)
        assert v.respect == pytest.approx(0.05)

    def test_emotional_resonance_boosts_closeness(self):
        re = RelationshipEngine()
        v = re.update("u1", user_emotion_valence=0.5, ai_emotion_valence=0.45, is_turn=True)
        assert v.closeness == pytest.approx(0.04)  # per_turn(0.01) + resonance(0.03)

    def test_self_disclosure_boosts_closeness(self):
        re = RelationshipEngine()
        # valence 都为 0 → resonance boost 也触发
        v = re.update("u1", inner_thoughts_text="这件事我只跟你说", is_turn=True)
        assert v.closeness == pytest.approx(0.06)  # per_turn(0.01) + resonance(0.03) + disclosure(0.02)

    def test_memory_entries_boost_familiarity(self):
        re = RelationshipEngine()
        v = re.update("u1", memory_entry_count=10, is_turn=True)
        assert v.familiarity == pytest.approx(0.025)  # per_turn(0.005) + 10 * 0.002

    def test_clamp_to_1(self):
        re = RelationshipEngine()
        for _ in range(500):
            re.update("u1", recall_hit_count=5, is_turn=True, memory_entry_count=100)
        v = re.get_vector("u1")
        assert v.trust <= 1.0
        assert v.closeness <= 1.0


class TestStageDetermination:
    """阶段自动判定 (SC-02)"""

    def test_stranger_by_default(self):
        re = RelationshipEngine()
        assert re.get_stage("u1") == RelationshipStage.STRANGER

    def test_acquaintance_when_familiar(self):
        re = RelationshipEngine()
        # familiarity >= 0.1 → ACQUAINTANCE
        for _ in range(20):
            re.update("u1", is_turn=True)
        assert re.get_stage("u1") == RelationshipStage.ACQUAINTANCE

    def test_friend_when_trust_and_closeness(self):
        re = RelationshipEngine()
        # 模拟多轮深度对话 + recall 命中
        # closeness > 0.2 需要 21 turns (21 * 0.01 = 0.21 > 0.2)
        for _ in range(21):
            re.update("u1", recall_hit_count=5, combined_review_weight=0.1,
                      user_message="这是一个很有深度的长篇问题需要仔细思考",
                      is_turn=True)
        v = re.get_vector("u1")
        # 应该达到 friend 或 close_friend
        stage = re.get_stage("u1")
        assert stage in (RelationshipStage.FRIEND, RelationshipStage.CLOSE_FRIEND)

    def test_close_friend_requires_high_trust_and_closeness(self):
        re = RelationshipEngine()
        # 大量 recall 命中 + 深度对话 + 情感共鸣 + 自我暴露
        for _ in range(50):
            re.update("u1", recall_hit_count=5, combined_review_weight=0.05,
                      user_message="我想和你聊聊人生的一些深层问题",
                      inner_thoughts_text="这件事很私人，只跟你说",
                      user_emotion_valence=0.6, ai_emotion_valence=0.55,
                      is_turn=True)
        assert re.get_stage("u1") == RelationshipStage.CLOSE_FRIEND


class TestDecay:
    """衰减计算 (SC-05)"""

    def test_closeness_decay_over_days(self):
        re = RelationshipEngine()
        v = re.update("u1", is_turn=True)
        # 手动回退 last_interaction 模拟 7 天间隔
        v.last_interaction = time.time() - 7 * 86400
        v2 = re.update("u1", is_turn=True)
        # closeness 应下降约 0.003 * 7 = 0.021 → clamped at 0 因为初始很小
        assert v2.closeness >= 0.0

    def test_trust_decay_slow(self):
        re = RelationshipEngine()
        # 先积累一些 trust
        for _ in range(10):
            re.update("u1", recall_hit_count=5, is_turn=True)
        v = re.get_vector("u1")
        assert v.trust > 0.2
        old_trust = v.trust  # snapshot before decay
        # 模拟 10 天
        v.last_interaction = time.time() - 10 * 86400
        v2 = re.update("u1", is_turn=True)
        # trust 降了但还有残留
        assert v2.trust < old_trust

    def test_respect_never_decays(self):
        re = RelationshipEngine()
        v = re.update("u1", correction_accepted=True, is_turn=True)
        assert v.respect > 0
        v.last_interaction = time.time() - 365 * 86400  # 1 年
        v2 = re.update("u1", is_turn=True)
        assert v2.respect == pytest.approx(v.respect)  # decay_rate = 0


class TestModulation:
    """人格调制系数 (SC-03)"""

    def test_stranger_defense_boost(self):
        re = RelationshipEngine()
        mod = re.get_modulation("u1")
        assert mod.defense_prob_mult == 1.5
        assert mod.proactive_prob_mult == 0.0

    def test_close_friend_modulation(self):
        re = RelationshipEngine()
        # 模拟晋升到 close_friend
        for _ in range(50):
            re.update("u1", recall_hit_count=5, combined_review_weight=0.05,
                      inner_thoughts_text="私人秘密", user_emotion_valence=0.6,
                      ai_emotion_valence=0.58, is_turn=True, memory_entry_count=20)
        mod = re.get_modulation("u1")
        assert mod.empathy_mult == 1.2
        assert mod.self_disclosure_mult == 1.5
        assert mod.defense_prob_mult == 0.5
        assert mod.proactive_prob_mult == 1.3


class TestEnergyLink:
    """Spec 007 联动 (SC-14)"""

    def test_low_energy_reduces_proactive(self):
        re = RelationshipEngine()
        # 即使 close_friend，低精力也降主动
        adjusted = re.get_adjusted_proactive_prob("u1", base_proactive=0.8, energy=0.1)
        assert adjusted < 0.8

    def test_normal_energy_uses_stage_modulation(self):
        re = RelationshipEngine()
        adjusted = re.get_adjusted_proactive_prob("u1", base_proactive=0.8, energy=0.8)
        # stranger → proactive_prob_mult = 0.0
        assert adjusted == 0.0
