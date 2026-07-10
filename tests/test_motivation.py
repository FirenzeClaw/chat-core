"""Tests for MotivationEngine + LonelinessDetector (Spec 011 SC-06~SC-15)."""
import math
import pytest
from chat_core.core.types import DriveSignal, MotivationState, LonelinessState
from chat_core.systems.motivation import MotivationEngine
from chat_core.systems.loneliness import LonelinessDetector


# ═══════════════════════════════════════════════════════════════
# SC-06: Drive 驱动 — socialize (boredom > 0.5)
# ═══════════════════════════════════════════════════════════════

class TestSocializeDrive:
    """SC-06: socialize 驱动由无聊触发，boredom > 0.5 激活。"""

    def test_socialize_activated_when_boredom_above_threshold(self):
        """boredom=0.7 > 0.5 → socialize 驱动激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.7, energy=1.0)
        assert len(state.active_drives) == 1
        assert state.active_drives[0].name == "socialize"
        assert state.active_drives[0].strength == 0.7
        assert state.active_drives[0].source == "boredom"
        assert state.active_drives[0].layer == "drive"
        assert state.strongest == "socialize"

    def test_socialize_not_activated_when_boredom_below_threshold(self):
        """boredom=0.4 < 0.5 → socialize 驱动不激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.4, energy=1.0)
        drive_names = [d.name for d in state.active_drives]
        assert "socialize" not in drive_names

    def test_socialize_at_exact_threshold_not_activated(self):
        """boredom=0.5 不满足 strict > 0.5。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.5, energy=1.0)
        drive_names = [d.name for d in state.active_drives]
        assert "socialize" not in drive_names


# ═══════════════════════════════════════════════════════════════
# SC-07: Drive 驱动 — rest (energy < 0.2)
# ═══════════════════════════════════════════════════════════════

class TestRestDrive:
    """SC-07: rest 驱动由精力不足触发，raw = 1 - energy > threshold。"""

    def test_rest_activated_when_energy_low(self):
        """energy=0.15 → raw=0.85 > 0.2 → rest 激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=0.15)
        assert len(state.active_drives) == 1
        assert state.active_drives[0].name == "rest"
        assert state.active_drives[0].strength == pytest.approx(0.85)
        assert state.active_drives[0].source == "energy"
        assert state.active_drives[0].layer == "drive"
        assert state.strongest == "rest"

    def test_rest_not_activated_when_energy_high(self):
        """energy=1.0 → raw=0.0 < 0.2 → rest 不激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0)
        drive_names = [d.name for d in state.active_drives]
        assert "rest" not in drive_names

    def test_rest_activated_at_energy_075(self):
        """energy=0.75 → raw=0.25 > 0.2 → rest 激活（边界）。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=0.75)
        drive_names = [d.name for d in state.active_drives]
        assert "rest" in drive_names


# ═══════════════════════════════════════════════════════════════
# SC-08: Drive 驱动 — seek_close (loneliness > 0.6)
# ═══════════════════════════════════════════════════════════════

class TestSeekCloseDrive:
    """SC-08: seek_close 驱动由孤独触发，loneliness > 0.6 激活。"""

    def test_seek_close_activated_when_loneliness_above_threshold(self):
        """loneliness=0.8 > 0.6 → seek_close 激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, loneliness=0.8)
        assert len(state.active_drives) == 1
        assert state.active_drives[0].name == "seek_close"
        assert state.active_drives[0].strength == 0.8
        assert state.active_drives[0].source == "loneliness"
        assert state.active_drives[0].layer == "drive"
        assert state.strongest == "seek_close"

    def test_seek_close_not_activated_when_loneliness_below_threshold(self):
        """loneliness=0.3 < 0.6 → seek_close 不激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, loneliness=0.3)
        drive_names = [d.name for d in state.active_drives]
        assert "seek_close" not in drive_names

    def test_seek_close_at_exact_threshold_not_activated(self):
        """loneliness=0.6 不满足 strict > 0.6。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, loneliness=0.6)
        drive_names = [d.name for d in state.active_drives]
        assert "seek_close" not in drive_names


# ═══════════════════════════════════════════════════════════════
# SC-09: Value 追求 — explore (growth > 0.7)
# ═══════════════════════════════════════════════════════════════

class TestExploreValue:
    """SC-09: explore 价值追求由 growth 触发，growth > 0.7 激活。"""

    def test_explore_activated_when_growth_above_threshold(self):
        """growth=0.85 > 0.7 → explore 激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, value_weights={"growth": 0.85})
        assert len(state.active_values) == 1
        assert state.active_values[0].name == "explore"
        assert state.active_values[0].strength == 0.85
        assert state.active_values[0].source == "growth"
        assert state.active_values[0].layer == "value"
        assert state.strongest == "explore"

    def test_explore_not_activated_when_growth_below_threshold(self):
        """growth=0.5 < 0.7 → explore 不激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, value_weights={"growth": 0.5})
        value_names = [v.name for v in state.active_values]
        assert "explore" not in value_names


# ═══════════════════════════════════════════════════════════════
# SC-10: Value 追求 — check_on (care > 0.6)
# ═══════════════════════════════════════════════════════════════

class TestCheckOnValue:
    """SC-10: check_on 价值追求由 care 触发，care > 0.6 激活。"""

    def test_check_on_activated_when_care_above_threshold(self):
        """care=0.8 > 0.6 → check_on 激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, value_weights={"care": 0.8})
        assert len(state.active_values) == 1
        assert state.active_values[0].name == "check_on"
        assert state.active_values[0].strength == 0.8
        assert state.active_values[0].source == "care"
        assert state.active_values[0].layer == "value"
        assert state.strongest == "check_on"

    def test_check_on_at_exact_threshold_not_activated(self):
        """care=0.6 不满足 strict > 0.6。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, value_weights={"care": 0.6})
        value_names = [v.name for v in state.active_values]
        assert "check_on" not in value_names


# ═══════════════════════════════════════════════════════════════
# SC-11: 冲突解决 — 体力优先 rest > explore
# ═══════════════════════════════════════════════════════════════

class TestConflictResolution:
    """SC-11: 冲突解决 — 体力优先原则 rest > explore。"""

    def test_rest_overrides_explore_in_conflict(self):
        """energy 低 + growth 高 → rest 和 explore 同时激活，冲突标记 rest > explore。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.0, energy=0.15,
            value_weights={"growth": 0.85},
        )
        # 两个都应激活
        drive_names = [d.name for d in state.active_drives]
        value_names = [v.name for v in state.active_values]
        assert "rest" in drive_names
        assert "explore" in value_names
        # 冲突存在
        assert len(state.conflicts) > 0
        assert any("rest" in c and "explore" in c for c in state.conflicts)

    def test_socialize_and_check_on_merge_no_conflict(self):
        """socialize + check_on 合并，不产生冲突。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.7, energy=1.0,
            value_weights={"care": 0.8},
        )
        drive_names = [d.name for d in state.active_drives]
        value_names = [v.name for v in state.active_values]
        assert "socialize" in drive_names
        assert "check_on" in value_names
        # socialize + check_on 合并 (merge_compatible=true)，无冲突
        assert not any("socialize" in c and "check_on" in c for c in state.conflicts)

    def test_rest_overrides_reflect_in_conflict(self):
        """rest + reflect → 冲突标记 rest > reflect。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.0, energy=0.15,
            value_weights={"self_improvement": 0.85},
        )
        drive_names = [d.name for d in state.active_drives]
        value_names = [v.name for v in state.active_values]
        assert "rest" in drive_names
        assert "reflect" in value_names
        assert any("rest" in c and "reflect" in c for c in state.conflicts)

    def test_no_conflict_when_no_overlap(self):
        """无冲突组合不产生冲突记录。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.7, energy=0.9,
            value_weights={"growth": 0.85},
        )
        # socialize + explore — 无冲突（socialize 与 explore 不在冲突表中）
        assert state.conflicts == []


# ═══════════════════════════════════════════════════════════════
# SC-12: Loneliness 依赖亲近关系（无亲近 → level=0）
# ═══════════════════════════════════════════════════════════════

class TestLonelinessRequiresCloseRelationship:
    """SC-12: 无亲近关系时孤独水平始终为 0。"""

    def test_level_zero_without_close_relationship(self):
        """relationships 只有 stranger/acquaintance → level=0。"""
        detector = LonelinessDetector()
        level = detector.tick(60, [("u1", "stranger"), ("u2", "acquaintance")])
        assert level == 0.0

    def test_level_zero_with_empty_relationships(self):
        """空 relationships → level=0。"""
        detector = LonelinessDetector()
        level = detector.tick(60, [])
        assert level == 0.0

    def test_has_close_relationship_flag_false(self):
        """无亲近关系时 has_close_relationship=False。"""
        detector = LonelinessDetector()
        detector.tick(60, [("u1", "stranger")])
        assert detector._state.has_close_relationship is False

    def test_level_accumulates_with_friend_relationship(self):
        """有 friend 关系时 level > 0。"""
        detector = LonelinessDetector()
        level = detector.tick(120, [("u1", "friend")], subjective_speed=1.0)
        # 120s / halflife=1200 → decay = exp(-0.1) ≈ 0.9048
        # level = 1 - 0.9048 * (1 - 0) = 0.0952
        expected = 1.0 - math.exp(-120.0 / 1200.0)
        assert level == pytest.approx(expected, rel=1e-4)
        assert level > 0.0

    def test_level_accumulates_with_close_friend(self):
        """close_friend 也算亲近关系。"""
        detector = LonelinessDetector()
        level = detector.tick(120, [("u1", "close_friend")], subjective_speed=1.0)
        assert level > 0.0


# ═══════════════════════════════════════════════════════════════
# SC-13: Loneliness 主观时钟调制（speed_factor 加速）
# ═══════════════════════════════════════════════════════════════

class TestLonelinessSubjectiveTime:
    """SC-13: 主观时钟加速 → 孤独增长更快。"""

    def test_subjective_speed_accelerates_level_growth(self):
        """speed_factor=2.0 → 等效时间翻倍 → level 增长更快。"""
        detector1 = LonelinessDetector()
        detector2 = LonelinessDetector()

        level_normal = detector1.tick(120, [("u1", "friend")], subjective_speed=1.0)
        level_fast = detector2.tick(120, [("u1", "friend")], subjective_speed=2.0)

        # speed=2.0 时有效 dt=240s，level 应更高
        assert level_fast > level_normal

    def test_subjective_speed_slows_decay(self):
        """speed_factor=0.5 → 等效时间减半 → level 增长更慢。"""
        detector1 = LonelinessDetector()
        detector2 = LonelinessDetector()

        level_normal = detector1.tick(120, [("u1", "friend")], subjective_speed=1.0)
        level_slow = detector2.tick(120, [("u1", "friend")], subjective_speed=0.5)

        assert level_slow < level_normal

    def test_very_high_subjective_speed_near_one(self):
        """极高 speed_factor + 长时间 → level 接近 1.0。"""
        detector = LonelinessDetector()
        level = detector.tick(6000, [("u1", "close_friend")], subjective_speed=10.0)
        # 有效时间 = 60000s, halflife=1200 → 50 个半衰期 → level ≈ 1.0
        assert level == pytest.approx(1.0, abs=1e-6)


# ═══════════════════════════════════════════════════════════════
# SC-14: 动机注入 system prompt 格式
# ═══════════════════════════════════════════════════════════════

class TestMotivationInjection:
    """SC-14: build_injection 输出 system prompt 格式。"""

    def test_empty_state_returns_none(self):
        """无活跃驱动/价值时返回 None。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0)
        injection = engine.build_injection(state)
        assert injection is None

    def test_only_drives_format(self):
        """仅驱动激活时的注入格式。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.7, energy=1.0)
        injection = engine.build_injection(state)
        assert injection is not None
        assert "[内在驱动]" in injection
        assert "socialize(0.70)" in injection
        assert "当前需求:" in injection
        assert "正在追求:" not in injection  # 无 values

    def test_only_values_format(self):
        """仅价值激活时的注入格式。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, value_weights={"growth": 0.85})
        injection = engine.build_injection(state)
        assert injection is not None
        assert "[内在驱动]" in injection
        assert "explore(0.85)" in injection
        assert "正在追求:" in injection

    def test_drives_and_values_combined_format(self):
        """驱动+价值同时激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.7, energy=0.15,
            value_weights={"growth": 0.85, "care": 0.8},
        )
        injection = engine.build_injection(state)
        assert injection is not None
        assert "当前需求:" in injection
        assert "正在追求:" in injection

    def test_conflicts_in_injection(self):
        """冲突信息出现在注入中。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.0, energy=0.15,
            value_weights={"growth": 0.85},
        )
        injection = engine.build_injection(state)
        assert "内部冲突:" in injection
        assert "rest" in injection
        assert "explore" in injection

    def test_injection_truncates_to_top_3(self):
        """驱动/价值超过 3 个时只取前 3 个。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.7, energy=0.15, loneliness=0.8, confusion=0.7,
            value_weights={"growth": 0.85, "care": 0.8, "honesty": 0.9, "self_improvement": 0.75},
        )
        injection = engine.build_injection(state)
        assert injection is not None
        # 不应崩溃，最多 3 个驱动和 3 个价值
        lines = injection.split("\n")
        assert len(lines) <= 5  # header + drives line + values line + conflicts line 最多


# ═══════════════════════════════════════════════════════════════
# SC-15: MotivationEngine.evaluate 完整流程
# ═══════════════════════════════════════════════════════════════

class TestEvaluateFullFlow:
    """SC-15: evaluate 完整流程 — 多驱动+多价值+冲突+最强。"""

    def test_multiple_drives_sorted_by_strength(self):
        """多驱动按 strength 降序排列。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.9, energy=0.15, loneliness=0.8,
        )
        # rest(raw=0.85) > socialize(0.9) → 按 strength 排序: socialize(0.9) > rest(0.85) > seek_close(0.8)
        assert len(state.active_drives) == 3
        assert state.active_drives[0].name == "socialize"
        assert state.active_drives[1].name == "rest"
        assert state.active_drives[2].name == "seek_close"
        # strongest = socialize (最高 strength)
        assert state.strongest == "socialize"

    def test_multiple_values_sorted_by_strength(self):
        """多价值按 strength 降序排列。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.0, energy=1.0,
            value_weights={"growth": 0.85, "care": 0.9},
        )
        assert len(state.active_values) == 2
        assert state.active_values[0].name == "check_on"   # strength 0.9
        assert state.active_values[1].name == "explore"     # strength 0.85
        assert state.strongest == "check_on"

    def test_strongest_is_drive_when_drive_stronger(self):
        """驱动比价值更强时 strongest 为驱动。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.9, energy=1.0,
            value_weights={"growth": 0.85},
        )
        # socialize(0.9) > explore(0.85)
        assert state.strongest == "socialize"

    def test_strongest_is_value_when_value_stronger(self):
        """价值比驱动更强时 strongest 为价值。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.0, energy=1.0,
            value_weights={"growth": 0.95},
        )
        # explore(0.95)
        assert state.strongest == "explore"

    def test_all_empty_returns_default_state(self):
        """所有输入均为默认值 → 空 MotivationState。"""
        engine = MotivationEngine()
        state = engine.evaluate()
        assert state.active_drives == []
        assert state.active_values == []
        assert state.conflicts == []
        assert state.strongest == ""

    def test_disabled_engine_returns_empty(self):
        """disabled 引擎始终返回空状态。"""
        engine = MotivationEngine()
        engine._enabled = False
        state = engine.evaluate(boredom=0.9, energy=0.15, value_weights={"growth": 0.9})
        assert state.active_drives == []
        assert state.active_values == []
        assert state.strongest == ""

    def test_get_strongest_drive_name(self):
        """get_strongest_drive_name 返回正确值。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.7, energy=1.0)
        assert engine.get_strongest_drive_name(state) == "socialize"

    def test_clarify_drive_from_confusion(self):
        """confusion > 0.6 → clarify 驱动激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, confusion=0.75)
        assert len(state.active_drives) == 1
        assert state.active_drives[0].name == "clarify"
        assert state.active_drives[0].source == "confusion"

    def test_vent_drive_from_anger(self):
        """unexpressed_anger > 0.5 → vent 驱动激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, unexpressed_anger=0.7)
        assert len(state.active_drives) == 1
        assert state.active_drives[0].name == "vent"
        assert state.active_drives[0].source == "anger_unexpressed"

    def test_confront_value_from_honesty(self):
        """honesty > 0.7 → confront 价值激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(boredom=0.0, energy=1.0, value_weights={"honesty": 0.85})
        assert len(state.active_values) == 1
        assert state.active_values[0].name == "confront"

    def test_reflect_value_from_self_improvement(self):
        """self_improvement > 0.7 → reflect 价值激活。"""
        engine = MotivationEngine()
        state = engine.evaluate(
            boredom=0.0, energy=1.0,
            value_weights={"self_improvement": 0.85},
        )
        assert len(state.active_values) == 1
        assert state.active_values[0].name == "reflect"


# ═══════════════════════════════════════════════════════════════
# LonelinessDetector 补充测试
# ═══════════════════════════════════════════════════════════════

class TestLonelinessDetectorEdgeCases:
    """LonelinessDetector 边界场景。"""

    def test_disabled_returns_zero(self):
        """disabled 时 level 始终为 0。"""
        detector = LonelinessDetector()
        detector._enabled = False
        level = detector.tick(600, [("u1", "friend")])
        assert level == 0.0

    def test_level_property(self):
        """level property 返回当前水平。"""
        detector = LonelinessDetector()
        level = detector.tick(120, [("u1", "friend")])
        assert detector.level == pytest.approx(level)

    def test_last_tick_updated(self):
        """tick 后 last_tick 更新。"""
        detector = LonelinessDetector()
        before = detector._state.last_tick
        detector.tick(60, [("u1", "friend")])
        assert detector._state.last_tick > before

    def test_level_bounded_at_one(self):
        """level 不会超过 1.0。"""
        detector = LonelinessDetector()
        # 极长时间 → level 应收敛到 1.0
        level = detector.tick(100000, [("u1", "friend")], subjective_speed=10.0)
        assert level <= 1.0
        assert level == pytest.approx(1.0, abs=1e-6)

    def test_has_close_relationship_flag_true(self):
        """有 friend → has_close_relationship=True。"""
        detector = LonelinessDetector()
        detector.tick(60, [("u1", "friend")])
        assert detector._state.has_close_relationship is True

    def test_mixed_relationships_close_wins(self):
        """混合关系中有一个 friend 就算有亲近关系。"""
        detector = LonelinessDetector()
        level = detector.tick(120, [
            ("u1", "stranger"),
            ("u2", "acquaintance"),
            ("u3", "friend"),
        ])
        assert level > 0.0
        assert detector._state.has_close_relationship is True
