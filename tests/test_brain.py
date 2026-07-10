"""Brain unit tests."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chat_core.core.brain import (
    ActionBrain,
    ActionBrainPool,
    EmotionBrain,
    LogicBrain,
    _RateLimiter,
)
from chat_core.core.provider import ModelProvider
from chat_core.core.prompt_engine import PromptEngine
from chat_core.core.types import ActionResult
from chat_core.systems.memory import MemoryStore


# ── Helpers ──────────────────────────────────────────────────

def make_provider() -> ModelProvider:
    provider = MagicMock(spec=ModelProvider)
    provider.chat = AsyncMock()
    return provider


def make_memory() -> MemoryStore:
    store = MagicMock(spec=MemoryStore)
    store.search = AsyncMock(return_value=[])
    store.save = AsyncMock()
    store.link = AsyncMock()
    store.tag = AsyncMock()
    store.get = AsyncMock(return_value=None)
    return store


def make_prompt_engine() -> PromptEngine:
    pe = MagicMock(spec=PromptEngine)
    pe.build_logic_brain_prompt.return_value = "logic prompt"
    pe.build_emotion_brain_prompt.return_value = "emotion prompt"
    pe.build_action_brain_prompt.return_value = "action prompt"
    pe.build_sub_session_prompt.return_value = "sub prompt"
    return pe


class TestLogicBrain:
    """LogicBrain creation and basic tests."""

    def test_logic_brain_creation(self):
        provider = make_provider()
        memory = make_memory()
        pe = make_prompt_engine()

        brain = LogicBrain(provider, memory, pe)

        assert brain._provider is provider
        assert brain._memory is memory
        assert brain._prompt_engine is pe
        assert len(brain._tools) == 4  # recall, memory_save, memory_link, inject_to_sub

    def test_logic_brain_tools_registered(self):
        brain = LogicBrain(make_provider(), make_memory(), make_prompt_engine())
        assert brain._tools.has("recall")
        assert brain._tools.has("memory_save")
        assert brain._tools.has("memory_link")
        assert brain._tools.has("inject_to_sub")


class TestEmotionBrain:
    """EmotionBrain creation and basic tests."""

    def test_emotion_brain_creation(self):
        provider = make_provider()
        memory = make_memory()
        pe = make_prompt_engine()

        brain = EmotionBrain(provider, memory, pe)

        assert brain._provider is provider
        assert brain._memory is memory
        assert brain._prompt_engine is pe
        assert len(brain._tools) == 3  # recall, memory_tag, inject_to_sub

    def test_emotion_brain_tools_registered(self):
        brain = EmotionBrain(make_provider(), make_memory(), make_prompt_engine())
        assert brain._tools.has("recall")
        assert brain._tools.has("memory_tag")
        assert brain._tools.has("inject_to_sub")


class TestActionBrain:
    """ActionBrain creation and basic tests."""

    def test_action_brain_creation(self):
        provider = make_provider()
        memory = make_memory()
        pe = make_prompt_engine()

        brain = ActionBrain(provider, memory, pe)

        assert brain._provider is provider
        assert brain._memory is memory
        assert brain._prompt_engine is pe
        assert len(brain._tools) == 3  # search, recall, web_fetch

    def test_action_brain_tools_registered(self):
        brain = ActionBrain(make_provider(), make_memory(), make_prompt_engine())
        assert brain._tools.has("search")
        assert brain._tools.has("recall")
        assert brain._tools.has("web_fetch")


class TestActionBrainPool:
    """ActionBrainPool concurrency tests."""

    def test_pool_not_configured_error(self):
        pool = ActionBrainPool(max_concurrent=1)

        async def _run():
            return await pool.submit("test task")

        result = asyncio.run(_run())
        assert result.success is False
        assert "not configured" in result.error

    @pytest.mark.asyncio
    async def test_pool_semaphore_creation(self):
        pool = ActionBrainPool(max_concurrent=1)
        assert pool._sem._value == 1  # Semaphore(1)

        pool2 = ActionBrainPool(max_concurrent=3)
        assert pool2._sem._value == 3

    @pytest.mark.asyncio
    async def test_pool_submit_with_mocks(self):
        """Submit a task and verify that the mock provider is called."""
        from chat_core.core.types import NonStreamResult, Usage

        provider = make_provider()
        provider.chat.return_value = NonStreamResult(
            content="search result",
            tool_calls=[],
            usage=Usage(),
        )
        memory = make_memory()
        pe = make_prompt_engine()

        pool = ActionBrainPool(max_concurrent=2)
        pool.configure(provider, memory, pe)

        result = await pool.submit("search for cats")
        assert result.success is True
        assert result.task == "search for cats"
        assert "search result" in result.output

    @pytest.mark.asyncio
    async def test_pool_concurrent_submission(self):
        """Verify that max_concurrent=1 serializes submissions."""
        from chat_core.core.types import NonStreamResult, Usage

        provider = make_provider()

        async def delayed_chat(*args, **kwargs):
            await asyncio.sleep(0.05)
            return NonStreamResult(content="ok", tool_calls=[], usage=Usage())

        provider.chat = delayed_chat
        memory = make_memory()
        pe = make_prompt_engine()

        pool = ActionBrainPool(max_concurrent=1)
        pool.configure(provider, memory, pe)

        start = time.monotonic()
        # Submit two tasks concurrently
        results = await asyncio.gather(
            pool.submit("task 1"),
            pool.submit("task 2"),
        )
        elapsed = time.monotonic() - start

        assert results[0].success
        assert results[1].success
        # With max_concurrent=1, two tasks should take ~0.1s (serialized)
        assert elapsed >= 0.09


class TestRateLimiter:
    """_RateLimiter token bucket tests."""

    @pytest.mark.asyncio
    async def test_rate_limiter_acquire_basic(self):
        limiter = _RateLimiter(
            max_per_interval=3,
            interval_seconds=0.5,
            cooldown_seconds=0.0,
        )
        # First 3 acquires should be fast
        start = time.monotonic()
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # all should be near-instant

    @pytest.mark.asyncio
    async def test_rate_limiter_throttle(self):
        limiter = _RateLimiter(
            max_per_interval=1,
            interval_seconds=0.5,
            cooldown_seconds=0.0,
        )
        # First acquire: fast
        await limiter.acquire()
        # Second: should wait for token
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        # Should have waited ~0.5s (interval) - 0 (cooldown) + 0.1 (buffer)
        assert elapsed >= 0.05  # at least some wait

    @pytest.mark.asyncio
    async def test_rate_limiter_cooldown(self):
        limiter = _RateLimiter(
            max_per_interval=5,
            interval_seconds=60.0,
            cooldown_seconds=0.3,
        )
        # First acquire fast
        await limiter.acquire()
        # Second: cooldown of 0.3s between tokens
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed >= 0.25  # should respect cooldown


# ── Spec 003: LogicBrain 联锁 recall 测试 (Phase 5, US3) ─────

class TestLogicRecallChained:
    """T035: LogicBrain._execute_recall 使用 search_chained() 联锁检索."""

    @pytest.mark.asyncio
    async def test_logic_recall_chained(self):
        """T035: mock search_chained, 验证返回 list[MemoryEntry]."""
        from chat_core.core.types import (
            ChainedMemory,
            LOGIC_BRAIN_CHAIN_CONFIG,
            MemoryEntry,
        )

        # Create mocked recalls
        mock_entry = MemoryEntry(
            namespace="user/facts", key="test_key",
            value={"content": "test value"},
        )
        chained_results = [
            ChainedMemory(entry=mock_entry, chain_level=0, chain_parent_key=None, relevance_score=1.0),
        ]

        provider = make_provider()
        memory = make_memory()
        memory.search_chained = AsyncMock(return_value=chained_results)
        pe = make_prompt_engine()

        brain = LogicBrain(provider, memory, pe)

        # Simulate a recall tool call
        from chat_core.core.types import ToolCall
        import json
        tc = ToolCall(
            id="call_1",
            function_name="recall",
            function_args=json.dumps({"query": "测试查询"}),
        )

        entries = await brain._execute_recall(tc)

        # Verify search_chained was called with correct config
        memory.search_chained.assert_called_once_with("测试查询", LOGIC_BRAIN_CHAIN_CONFIG)

        # Verify returns list[MemoryEntry]
        assert isinstance(entries, list)
        assert len(entries) == 1
        assert entries[0].key == "test_key"

        # Verify _last_chained_recall was stored
        assert len(brain._last_chained_recall) == 1

    @pytest.mark.asyncio
    async def test_logic_recall_chained_error_handling(self):
        """T035: search_chained 异常时返回空列表并清空缓存."""
        provider = make_provider()
        memory = make_memory()
        memory.search_chained = AsyncMock(side_effect=RuntimeError("DB error"))
        pe = make_prompt_engine()

        brain = LogicBrain(provider, memory, pe)
        from chat_core.core.types import ToolCall
        import json
        tc = ToolCall(
            id="call_1",
            function_name="recall",
            function_args=json.dumps({"query": "anything"}),
        )

        entries = await brain._execute_recall(tc)
        assert entries == []
        assert brain._last_chained_recall == []
