"""ReActLoop unit tests."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chat_core.core.loop import (
    ReActLoop,
    SubSessionConfig,
    _handle_recall,
    _handle_send_reply,
    _handle_wait,
    register_sub_session_tools,
)
from chat_core.core.provider import ModelProvider
from chat_core.core.tools import ToolContext, ToolRegistry
from chat_core.core.types import (
    Message,
    NonStreamResult,
    ToolCall,
    Usage,
)
from chat_core.systems.memory import MemoryStore


# ── Helpers ──────────────────────────────────────────────────

def make_provider() -> ModelProvider:
    """Create a mock ModelProvider that won't actually call APIs."""
    provider = MagicMock(spec=ModelProvider)
    provider.chat = AsyncMock()
    return provider


def make_tool_registry() -> ToolRegistry:
    return ToolRegistry()


def make_result(content: str = "", tool_calls: list[ToolCall] | None = None) -> NonStreamResult:
    return NonStreamResult(
        content=content,
        tool_calls=tool_calls or [],
        usage=Usage(),
    )


class TestReActLoop:
    """ReActLoop creation and internal state tests."""

    def test_init(self):
        provider = make_provider()
        tools = make_tool_registry()
        prompt = "You are a test assistant."

        loop = ReActLoop(provider=provider, tool_registry=tools, system_prompt=prompt)

        assert loop._provider is provider
        assert loop._tools is tools
        assert loop._system_prompt == prompt
        assert loop._config.max_iter == 5  # default
        assert loop._iteration == 0
        assert loop._done is False
        assert loop._cancelled is False

    def test_should_continue_false_when_done(self):
        loop = ReActLoop(make_provider(), make_tool_registry(), "prompt")
        loop._done = True
        assert loop._should_continue() is False

    def test_should_continue_false_when_max_iter(self):
        loop = ReActLoop(
            make_provider(), make_tool_registry(), "prompt",
            config=SubSessionConfig(max_iter=5),
        )
        loop._iteration = 5
        assert loop._should_continue() is False

    def test_cancel(self):
        loop = ReActLoop(make_provider(), make_tool_registry(), "prompt")
        loop.cancel()
        assert loop._cancelled is True
        assert loop._should_continue() is False

    def test_termination_sadness(self):
        loop = ReActLoop(make_provider(), make_tool_registry(), "prompt")
        loop._sadness = 0.9
        assert loop._should_continue() is False

    def test_termination_focus(self):
        loop = ReActLoop(make_provider(), make_tool_registry(), "prompt")
        loop._focus = 0.1
        assert loop._should_continue() is False

    def test_termination_boredom(self):
        loop = ReActLoop(make_provider(), make_tool_registry(), "prompt")
        loop._boredom = 0.8
        assert loop._should_continue() is False

    def test_estimate_tokens(self):
        loop = ReActLoop(make_provider(), make_tool_registry(), "short_prompt")
        loop._messages = [
            Message(role="system", content="a" * 80),
            Message(role="user", content="b" * 40),
        ]
        # total_chars = 80 + 40 + len("short_prompt")=13 = 133
        # 133 // 4 = 33
        tokens = loop.estimate_tokens()
        assert tokens == 33

    def test_compression_level_below_70(self):
        loop = ReActLoop(
            make_provider(), make_tool_registry(), "p",
            config=SubSessionConfig(max_context_tokens=32000),
        )
        # Empty messages + short prompt → very low compression level
        level = loop.compression_level()
        assert level < 0.70

    def test_compression_level_above_70(self):
        loop = ReActLoop(
            make_provider(), make_tool_registry(), "x" * 100000,
            config=SubSessionConfig(max_context_tokens=32000),
        )
        # 100000 chars → 25000 tokens → 25000/32000 ≈ 0.78 → above 70%
        level = loop.compression_level()
        assert level > 0.70
        assert level < 0.85

    def test_compression_level_above_85(self):
        loop = ReActLoop(
            make_provider(), make_tool_registry(), "x" * 120000,
            config=SubSessionConfig(max_context_tokens=32000),
        )
        # 120000 chars → 30000 tokens → 30000/32000 ≈ 0.9375 → above 85%
        level = loop.compression_level()
        assert level > 0.85


class TestReActLoopTools:
    """Tests for tool handlers registered in ReActLoop."""

    @pytest.mark.asyncio
    async def test_send_reply_appends(self):
        loop = ReActLoop(make_provider(), make_tool_registry(), "prompt")
        result = await _handle_send_reply({"text": "Hello, world!"}, loop)
        # 自然语言返回，非 JSON
        assert isinstance(result, str) and len(result) > 0
        assert loop._replies == ["Hello, world!"]

    @pytest.mark.asyncio
    async def test_send_reply_truncation(self):
        loop = ReActLoop(make_provider(), make_tool_registry(), "prompt")
        long_text = "x" * 600  # > 500 hard max
        # 第一次: 超过150软限制 → 提示不发送
        result1 = await _handle_send_reply({"text": long_text}, loop)
        assert "字太多了" in result1
        assert len(loop._replies) == 0
        # 第二次重试: 硬截断至500, 发送
        result2 = await _handle_send_reply({"text": long_text}, loop)
        assert len(loop._replies[0]) == 500
        assert loop._replies[0] == "x" * 500

    @pytest.mark.asyncio
    async def test_send_reply_blocked(self):
        loop = ReActLoop(make_provider(), make_tool_registry(), "prompt")
        result = await _handle_send_reply({"text": "我想自杀"}, loop)
        data = json.loads(result)
        assert data["sent"] is False
        assert "安全策略" in data["error"]
        assert len(loop._replies) == 0

    @pytest.mark.asyncio
    async def test_wait_handler(self):
        result = await _handle_wait({"seconds": 0.5})
        data = json.loads(result)
        assert data["waited"] == 0.5

    @pytest.mark.asyncio
    async def test_recall_no_store(self):
        result = await _handle_recall({"query": "test"}, memory_store=None)
        data = json.loads(result)
        assert data["results"] == []
        assert "记忆系统未初始化" in data["note"]

    @pytest.mark.asyncio
    async def test_inner_thoughts_recording(self):
        """Simulate _act with inner_thoughts + done to verify recording."""
        provider = make_provider()
        tools = make_tool_registry()
        loop = ReActLoop(provider, tools, "prompt")
        register_sub_session_tools(tools, loop)

        # Create tool calls: inner_thoughts then done
        result = make_result(tool_calls=[
            ToolCall(
                id="call_1", type="function",
                function_name="inner_thoughts",
                function_args=json.dumps({"text": "用户似乎有点难过"}),
            ),
            ToolCall(
                id="call_2", type="function",
                function_name="done",
                function_args="{}",
            ),
        ])

        loop._init_messages("hello")
        await loop._act(result)

        assert loop._inner_thoughts_raw == "用户似乎有点难过"
        assert loop._done is True

    @pytest.mark.asyncio
    async def test_protocol_enforcement_done_without_inner(self):
        """Simulate done without inner_thoughts → degraded mode."""
        provider = make_provider()
        tools = make_tool_registry()
        loop = ReActLoop(provider, tools, "prompt")
        register_sub_session_tools(tools, loop)

        result = make_result(tool_calls=[
            ToolCall(
                id="call_1", type="function",
                function_name="done",
                function_args="{}",
            ),
        ])

        loop._init_messages("hello")
        # _inner_thoughts_raw is None, done without inner_thoughts → set to ""
        await loop._act(result)

        assert loop._inner_thoughts_raw == ""
        assert loop._done is True

    @pytest.mark.asyncio
    async def test_act_pure_text(self):
        """Pure text → inner_thoughts retry → then done on second pure text."""
        provider = make_provider()
        tools = make_tool_registry()
        loop = ReActLoop(provider, tools, "prompt")

        result = make_result(content="你好！", tool_calls=[])

        loop._init_messages("hello")
        await loop._act(result)

        # 首次纯文本：_done=False，触发 inner_thoughts 补写
        assert loop._done is False
        assert loop._replies == ["你好！"]
        assert loop._inner_thoughts_retried is True

        # 第二次纯文本：已重试过，_done=True
        await loop._act(result)
        assert loop._done is True


# ── Spec 003: 子Session recall 隔离测试 (Phase 5, US3) ────────

class TestSubRecallIsolation:
    """T036-T037: 子Session recall 命名空间隔离 + 自然语言输出."""

    @pytest.mark.asyncio
    async def test_sub_recall_namespace_isolation(self):
        """T036: namespace_prefix="user/test_user" 过滤其他用户记忆."""
        from chat_core.core.types import (
            ChainedMemory,
            MemoryEntry,
            RecallChainConfig,
            SUB_SESSION_CHAIN_CONFIG,
        )
        import json

        # Use a real MemoryStore so _format_recall_result works properly
        real_store = MemoryStore(":memory:")
        await real_store.open()
        try:
            real_store.search_chained = AsyncMock(return_value=[
                ChainedMemory(
                    entry=MemoryEntry(
                        namespace="user/test_user/facts", key="k1",
                        value={"content": "test user memory"},
                    ),
                    chain_level=0, chain_parent_key=None, relevance_score=1.0,
                ),
            ])

            chain_config = RecallChainConfig(
                top_n=3, extensions=[2, 1, 0], max_per_level=2,
                namespace_prefix="user/test_user",
            )

            # Call _handle_recall with chained config
            result = await _handle_recall(
                {"query": "test"},
                memory_store=real_store,
                chain_config=chain_config,
            )

            # Verify search_chained was called with correct config
            real_store.search_chained.assert_called_once()
            call_args = real_store.search_chained.call_args
            assert call_args[0][0] == "test"
            # chain_config is passed as second positional argument
            assert call_args[0][1].namespace_prefix == "user/test_user"

            # Result should be a natural language string (not JSON array)
            assert isinstance(result, str)
            assert len(result) > 0
            # Should start with "【记忆回溯】" (or be a no-memory phrase)
            assert "【记忆回溯】" in result or any(
                phrase in result for phrase in [
                    "目前没什么特别的记忆浮现",
                    "脑子里暂时一片空白",
                    "一下子想不起相关的事",
                ]
            )
        finally:
            await real_store.close()

    @pytest.mark.asyncio
    async def test_sub_recall_natural_language(self):
        """T037: mock search_chained 返回自然语言文本, 验证非 JSON."""
        from chat_core.core.types import ChainedMemory, MemoryEntry, RecallChainConfig
        import json

        # Use a real MemoryStore so _format_recall_result works properly
        real_store = MemoryStore(":memory:")
        await real_store.open()
        try:
            # Mock search_chained to return controlled results
            real_store.search_chained = AsyncMock(return_value=[
                ChainedMemory(
                    entry=MemoryEntry(
                        namespace="user/alice/facts", key="mem1",
                        value={"content": "Alice 是设计师"},
                    ),
                    chain_level=0, chain_parent_key=None, relevance_score=1.0,
                ),
                ChainedMemory(
                    entry=MemoryEntry(
                        namespace="user/alice/facts", key="mem2",
                        value={"content": "Alice 喜欢画画"},
                    ),
                    chain_level=1, chain_parent_key="user/alice/facts/mem1",
                    relevance_score=0.8,
                ),
            ])

            chain_config = RecallChainConfig(
                top_n=3, extensions=[2, 1, 0], max_per_level=2,
                namespace_prefix="user/alice",
            )

            result = await _handle_recall(
                {"query": "Alice"},
                memory_store=real_store,
                chain_config=chain_config,
            )

            # Result should be natural language, not JSON
            assert isinstance(result, str)
            assert not result.startswith("[")
            assert not result.startswith("{")
            assert "我记得" in result

            # Without chain_config → old JSON format
            store_no_chain = MagicMock()
            store_no_chain.search = AsyncMock(return_value=[])
            result2 = await _handle_recall(
                {"query": "test"},
                memory_store=store_no_chain,
                chain_config=None,
            )
            data = json.loads(result2)
            assert "results" in data
            assert "count" in data
        finally:
            await real_store.close()


# ── 注意力注入 + recall→注意力回调 ──────────────────────────────

from chat_core.systems.attention import AttentionModel


class TestAttentionInjection:
    """子Session focus 注入 + recall→注意力回调"""

    def test_init_messages_injects_focus_prompt(self):
        """_init_messages 应在 system prompt 后注入注意力状态提示"""
        from chat_core.core.loop import ReActLoop, SubSessionConfig
        from chat_core.core.tools import ToolRegistry

        mock_provider = MagicMock()  # 不依赖真实 API
        tools = ToolRegistry()
        attn = AttentionModel()
        loop = ReActLoop(
            provider=mock_provider,
            tool_registry=tools,
            system_prompt="你是小深。",
            config=SubSessionConfig(max_iter=1),
            attention_model=attn,
        )
        loop._init_messages("你好")
        all_system = [m.content for m in loop._messages if m.role == "system"]
        attention_hints = [s for s in all_system if "[注意状态]" in s]
        assert len(attention_hints) >= 1, f"未找到 [注意状态] 提示: {all_system}"

    @pytest.mark.asyncio
    async def test_recall_hit_triggers_attention_boost(self):
        """_handle_recall 命中高 salience → apply_event(MEMORY_STRONG_HIT)"""
        from chat_core.core.loop import _handle_recall
        from chat_core.core.types import AttentionEvent, ChainedMemory, MemoryEntry

        attn = AttentionModel()
        attn._states["sub"].focus = 0.80

        mock_store = AsyncMock()
        mock_store.search_chained = AsyncMock(return_value=[
            ChainedMemory(
                entry=MemoryEntry(salience=8.0, key="test", namespace="test/ns"),
                chain_level=0, chain_parent_key=None, relevance_score=1.0,
            ),
        ])
        mock_store._format_recall_result = MagicMock(return_value="测试回溯")

        await _handle_recall(
            {"query": "测试"}, mock_store,
            chain_config=MagicMock(), attention_model=attn,
        )
        assert attn.get_focus("sub") > 0.80, \
            f"应提升 focus: 0.80 → {attn.get_focus('sub')}"

    @pytest.mark.asyncio
    async def test_recall_empty_triggers_attention_penalty(self):
        """_handle_recall 空结果 → apply_event(MEMORY_MISS)"""
        from chat_core.core.loop import _handle_recall

        attn = AttentionModel()
        attn._states["sub"].focus = 0.50

        mock_store = AsyncMock()
        mock_store.search = AsyncMock(return_value=[])

        await _handle_recall(
            {"query": "不存在"}, mock_store,
            chain_config=None, attention_model=attn,
        )
        assert attn.get_focus("sub") < 0.50, \
            f"空结果应降低 focus: 0.50 → {attn.get_focus('sub')}"
