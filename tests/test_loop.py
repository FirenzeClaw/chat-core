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
        data = json.loads(result)
        assert data["sent"] is True
        assert loop._replies == ["Hello, world!"]

    @pytest.mark.asyncio
    async def test_send_reply_truncation(self):
        loop = ReActLoop(make_provider(), make_tool_registry(), "prompt")
        long_text = "x" * 600  # > 500 default max
        result = await _handle_send_reply({"text": long_text}, loop)
        data = json.loads(result)
        assert data["sent"] is True
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
