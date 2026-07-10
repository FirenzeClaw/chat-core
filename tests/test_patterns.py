"""Tests for Spec 008: PatternDetector — greeting, timing, topic_cycle, inside_joke"""

import pytest
from chat_core.systems.patterns import PatternDetector


class TestPatternDetectorSync:
    """同步层测试（不依赖 MemoryStore 的纯逻辑）"""

    def test_initial_state(self):
        pd = PatternDetector()
        assert pd.enabled is True

    def test_get_pattern_injection_empty(self):
        pd = PatternDetector()
        hint = pd.get_pattern_injection("u1")
        assert hint is None


class TestPatternDetectorGreeting:
    """问候检测 (SC-10)"""

    @pytest.mark.asyncio
    async def test_greeting_repeat_detection(self):
        pd = PatternDetector()
        # 模拟 3 次重复 "早啊" — 最后一次返回新达标模式
        results = []
        for i in range(3):
            batch = await pd.detect("u1", "早啊", "")
            results.extend(batch)
        # 第 3 次应达标，返回包含 greeting 的结果
        found_greeting = any(p.pattern_type == "greeting" for p in results)
        assert found_greeting

    @pytest.mark.asyncio
    async def test_short_message_only(self):
        pd = PatternDetector()
        # 长消息不应触发 greeting
        results = await pd.detect("u1", "这是一条很长很长的消息不会被当作问候", "")
        greeting_count = sum(1 for p in results if p.pattern_type == "greeting")
        assert greeting_count == 0


class TestPatternDetectorTiming:
    """时间规律检测 (SC-11)"""

    @pytest.mark.asyncio
    async def test_timing_no_match_with_few_entries(self):
        pd = PatternDetector()
        # 少于 min_timing (5) 次，不达标
        results = []
        for i in range(4):
            batch = await pd.detect("u1", f"消息{i}", "")
            results.extend(batch)
        timing_count = sum(1 for p in results if p.pattern_type == "timing")
        assert timing_count == 0

    @pytest.mark.asyncio
    async def test_timing_dominant_hour_bucket(self):
        pd = PatternDetector()
        # 模拟同一时间段出现 > 60%
        # 注意：hour_bucket 根据当前真实时间生成，同一分钟内多次调用会落在同一 bucket
        results = []
        for i in range(6):
            batch = await pd.detect("u1", f"消息{i}", "")
            results.extend(batch)
        # 6 次 ≥ min_timing(5)，且全部在同一 bucket → dominant_ratio = 1.0 > 0.6
        found_timing = any(p.pattern_type == "timing" for p in results)
        assert found_timing


class TestPatternDetectorInsideJoke:
    """内部梗检测 (SC-12)"""

    @pytest.mark.asyncio
    async def test_inside_joke_detection(self):
        pd = PatternDetector()
        # inner_thoughts 含关键词 → 可能触发
        results = await pd.detect("u1", "抽风", "哈哈哈这个太好笑了")
        # 首次不会达标（需要 ≥ 2 次）
        joke_count = sum(1 for p in results if p.pattern_type == "inside_joke")
        assert joke_count == 0  # 第一次不达标

    @pytest.mark.asyncio
    async def test_no_joke_without_keyword(self):
        pd = PatternDetector()
        results = await pd.detect("u1", "ok", "收到了你的消息")
        joke_count = sum(1 for p in results if p.pattern_type == "inside_joke")
        assert joke_count == 0


class TestPatternInjection:
    """系统注入 (SC-13)"""

    def test_pattern_injection_format(self):
        pd = PatternDetector()
        # 手动设置 pattern
        from chat_core.core.types import InteractionPattern
        pd._patterns["u1"] = [
            InteractionPattern(
                pattern_type="greeting",
                template="早啊",
                count=5,
                last_seen="2026-07-10T09:00:00",
                time_distribution={"09:00-10:00": 5},
            )
        ]
        hint = pd.get_pattern_injection("u1")
        assert hint is not None
        assert "早啊" in hint
        assert "[社交模式]" in hint
