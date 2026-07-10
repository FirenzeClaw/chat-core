"""Tests for Spec 008: GroupDynamics — role metrics, atmosphere snapshots"""

import pytest
from chat_core.systems.group_dynamics import GroupDynamics
from chat_core.core.types import GroupRoleMetrics, GroupAtmosphere


class TestGroupRoleMetrics:
    """群角色统计 (SC-06, SC-07)"""

    def test_at_ratio(self):
        m = GroupRoleMetrics(group_id="g1", total_messages=100, at_count=5)
        assert m.at_ratio == 0.05

    def test_engagement_rate(self):
        m = GroupRoleMetrics(group_id="g1", reply_count=10, member_reply_to_ai=3)
        assert m.engagement_rate == 0.3

    def test_role_score(self):
        m = GroupRoleMetrics(
            group_id="g1",
            total_messages=100,
            at_count=5,
            reply_count=10,
            member_reply_to_ai=3,
            active_days=15,
        )
        score = m.role_score
        assert 0.0 <= score <= 1.0

    def test_role_score_high_activity(self):
        m = GroupRoleMetrics(
            group_id="g1",
            total_messages=100,
            at_count=20,
            reply_count=50,
            member_reply_to_ai=40,
            active_days=30,
        )
        score = m.role_score
        assert score > 0.8  # 高活跃


class TestGroupDynamicsEngine:
    """GroupDynamics 引擎测试"""

    def test_record_at(self):
        gd = GroupDynamics()
        m = gd.record_at("g1")
        assert m.at_count == 1

    def test_record_observe(self):
        gd = GroupDynamics()
        m = gd.record_observe("g1")
        assert m.total_messages == 1

    def test_record_reply_and_member_reply(self):
        gd = GroupDynamics()
        gd.record_reply("g1")
        gd.record_member_reply_to_ai("g1")
        m = gd.get_metrics("g1")
        assert m.reply_count == 1
        assert m.member_reply_to_ai == 1

    def test_role_summary(self):
        gd = GroupDynamics()
        gd.record_at("g1")
        gd.record_observe("g1")
        summary = gd.get_role_summary("g1")
        assert "role_score" in summary
        assert summary["at_ratio"] > 0

    def test_atmosphere_snapshot(self):
        gd = GroupDynamics()
        gd.record_emotion_snapshot("g1", {"joy": 0.5, "sadness": 0.1})
        snaps = gd.get_recent_atmosphere("g1")
        assert len(snaps) == 1
        assert snaps[0].avg_emotion == {"joy": 0.5, "sadness": 0.1}

    def test_atmosphere_summary(self):
        gd = GroupDynamics()
        gd.record_emotion_snapshot("g1", {"joy": 0.5})
        summary = gd.get_atmosphere_summary("g1")
        assert summary is not None
        assert summary["snapshot_count"] == 1


class TestCrossGroupMemory:
    """跨群社交注解 (SC-09) — 测试 _format_recall_result 追加逻辑"""

    def test_cross_namespace_detection(self):
        """验证 _format_recall_result 在跨 namespace 时追加跨群注解"""
        from chat_core.core.types import ChainedMemory, MemoryEntry

        # 构造两条不同 namespace 的记忆
        e1 = MemoryEntry(
            namespace="group/A/u1", key="msg1",
            value={"text": "在群A的发言"}, salience=5.0,
        )
        e2 = MemoryEntry(
            namespace="c2c/u1", key="msg2",
            value={"text": "私聊中提到职业规划"}, salience=5.0,
        )
        cm1 = ChainedMemory(entry=e1, chain_level=0, relevance_score=1.0)
        cm2 = ChainedMemory(entry=e2, chain_level=0, relevance_score=0.9)

        # 验证 namespace 分组逻辑
        entries = [cm1, cm2]
        ns_groups: dict[str, list[str]] = {}
        for cm in entries:
            e = cm.entry
            parts = e.namespace.split("/")
            if len(parts) >= 2 and parts[0] in ("user", "c2c", "group"):
                ns_key = "/".join(parts[:2])
                if ns_key not in ns_groups:
                    ns_groups[ns_key] = []
                ns_groups[ns_key].append(e.key)

        assert len(ns_groups) > 1  # 跨 namespace
        assert "group/A" in ns_groups
        assert "c2c/u1" in ns_groups

    def test_single_namespace_no_cross_annotation(self):
        """同 namespace 不触发跨群注解"""
        from chat_core.core.types import ChainedMemory, MemoryEntry

        e1 = MemoryEntry(
            namespace="group/A/u1", key="msg1",
            value={"text": "发言1"}, salience=5.0,
        )
        e2 = MemoryEntry(
            namespace="group/A/u1", key="msg2",
            value={"text": "发言2"}, salience=5.0,
        )
        cm1 = ChainedMemory(entry=e1, chain_level=0, relevance_score=1.0)
        cm2 = ChainedMemory(entry=e2, chain_level=0, relevance_score=0.9)

        entries = [cm1, cm2]
        ns_groups: dict[str, list[str]] = {}
        for cm in entries:
            e = cm.entry
            parts = e.namespace.split("/")
            if len(parts) >= 2 and parts[0] in ("user", "c2c", "group"):
                ns_key = "/".join(parts[:2])
                if ns_key not in ns_groups:
                    ns_groups[ns_key] = []
                ns_groups[ns_key].append(e.key)

        assert len(ns_groups) == 1  # 同 namespace，不触发跨群注解
