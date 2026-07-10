"""Tests for Spec 009: CreativityEngine — 双路径概念发散 (SC-06~SC-09)"""

import pytest
from chat_core.core.types import (
    ChainedMemory,
    CreativityContext,
    MemoryEntry,
    RecallChainConfig,
)
from chat_core.systems.creativity import CreativityEngine


class TestCreativityTrigger:
    """SC-06: 触发判定"""

    def test_playfulness_low_no_trigger(self):
        """playfulness ≤ 0.5 且非开放性问题 → 不触发"""
        ce = CreativityEngine()
        assert not ce.should_trigger(0.3, "今天天气怎么样")
        assert not ce.should_trigger(0.5, "你好")

    def test_playfulness_high_triggers(self):
        """playfulness > 0.5 → 触发"""
        ce = CreativityEngine()
        assert ce.should_trigger(0.7, "今天天气怎么样")

    def test_open_ended_triggers(self):
        """SC-06: 开放性问题关键词触发"""
        ce = CreativityEngine()
        assert ce.should_trigger(0.1, "你觉得为什么天空是蓝色的")
        assert ce.should_trigger(0.1, "假如人类能飞会怎样")
        assert ce.should_trigger(0.1, "想象一下没有电的世界")
        assert ce.should_trigger(0.1, "换个角度看这个问题")


class TestPathAPrompt:
    """SC-07: Path A prompt 生成格式"""

    def test_build_path_a_prompt_format(self):
        """生成 Path A prompt 包含关键词和格式说明"""
        ce = CreativityEngine()
        prompt = ce.build_path_a_prompt("如果时间旅行可能，世界会怎样")
        assert "远距离概念联想" in prompt
        assert "跨领域映射" in prompt
        assert "→" in prompt
        assert "概念联系" in prompt

    def test_parse_path_a_result(self):
        """解析 Flash 返回的概念映射"""
        ce = CreativityEngine()
        result = "物理学 → 时空弯曲与因果链\n生物学 → 进化树的分支结构\n哲学 → 自由意志与决定论"
        mappings = ce.parse_path_a_result(result)
        assert len(mappings) == 3
        assert "物理学 → 时空弯曲与因果链" in mappings
        assert all("→" in m for m in mappings)


class TestPathBFilter:
    """SC-08: Path B 联锁记忆过滤"""

    def test_filter_chain_level(self):
        """仅保留 chain_level ≥ 3 的记忆"""
        ce = CreativityEngine()
        entries = [
            ChainedMemory(
                entry=MemoryEntry(namespace="t", key="k0", value={"text": "直接匹配"}, salience=7.0),
                chain_level=0,
            ),
            ChainedMemory(
                entry=MemoryEntry(namespace="t", key="k1", value={"text": "一级关联"}, salience=5.0),
                chain_level=1,
            ),
            ChainedMemory(
                entry=MemoryEntry(namespace="t", key="k2", value={"text": "意外关联记忆A"}, salience=6.0),
                chain_level=3,
            ),
            ChainedMemory(
                entry=MemoryEntry(namespace="t", key="k3", value={"text": "深度意外关联B"}, salience=4.0),
                chain_level=4,
            ),
        ]
        summaries = ce.filter_path_b_memories(entries)
        assert len(summaries) == 2
        assert "意外关联记忆A" in summaries
        assert "深度意外关联B" in summaries

    def test_filter_chain_level_empty(self):
        """所有记忆 chain_level < 3 → 空列表"""
        ce = CreativityEngine()
        entries = [
            ChainedMemory(
                entry=MemoryEntry(namespace="t", key="k0", value={"text": "直接匹配"}, salience=7.0),
                chain_level=0,
            ),
            ChainedMemory(
                entry=MemoryEntry(namespace="t", key="k1", value={"text": "一级关联"}, salience=5.0),
                chain_level=2,
            ),
        ]
        summaries = ce.filter_path_b_memories(entries)
        assert len(summaries) == 0


class TestBuildInjection:
    """SC-09: 合并注入格式"""

    def test_full_injection_format(self):
        """Path A 映射 + Path B 记忆 → 完整注入文本"""
        ce = CreativityEngine()
        path_a = ["物理学 → 时空弯曲与因果链", "生物学 → 进化树的分支结构"]
        path_b = ["意外关联记忆A", "深度意外关联B"]
        injection = ce.build_injection(path_a, path_b)
        assert "[创造力增强]" in injection
        assert "概念发散" in injection
        assert "意外关联记忆" in injection
        assert "物理学 → 时空弯曲与因果链" in injection
        assert "意外关联记忆A" in injection

    def test_path_a_only(self):
        """仅 Path A 映射 → 不含 Path B 标题"""
        ce = CreativityEngine()
        path_a = ["物理学 → 时空弯曲"]
        injection = ce.build_injection(path_a, [])
        assert "[创造力增强]" in injection
        assert "概念发散" in injection
        assert "意外关联记忆" not in injection

    def test_path_b_only(self):
        """仅 Path B 记忆 → 不含 Path A 标题"""
        ce = CreativityEngine()
        path_b = ["意外关联记忆A"]
        injection = ce.build_injection([], path_b)
        assert "[创造力增强]" in injection
        assert "概念发散" not in injection
        assert "意外关联记忆" in injection

    def test_empty_injection(self):
        """Path A + Path B 均为空 → 返回空字符串"""
        ce = CreativityEngine()
        injection = ce.build_injection([], [])
        assert injection == ""

    def test_extended_chain_config(self):
        """get_extended_chain_config 返回扩大的 RecallChainConfig"""
        ce = CreativityEngine()
        config = ce.get_extended_chain_config()
        assert isinstance(config, RecallChainConfig)
        assert config.top_n == 5
        assert config.extensions == [5, 5, 5, 5, 5]
        assert config.max_per_level == 5
