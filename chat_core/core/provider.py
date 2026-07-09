"""LLM Provider — OpenAI-compatible API 客户端 (AsyncOpenAI)"""

from __future__ import annotations

import json
from typing import AsyncGenerator

from openai import AsyncOpenAI

from chat_core.config import get_config
from chat_core.core.types import (
    Message,
    NonStreamResult,
    StreamEvent,
    StreamEventType,
    ToolCall,
    ToolSpec,
    Usage,
)


class ModelProvider:
    """封装 AsyncOpenAI，提供流式和非流式聊天完成"""

    def __init__(self, api_config: dict | None = None):
        if api_config is None:
            cfg = get_config()
            api_config = cfg.brain_api_config("sub_session")
        self._client = AsyncOpenAI(
            api_key=api_config.get("api_key", ""),
            base_url=api_config.get("base_url", "https://api.deepseek.com/v1"),
        )
        self._default_model = api_config.get("model", "deepseek-v4-flash")

    @property
    def client(self) -> AsyncOpenAI:
        return self._client

    # ── 非流式 ──────────────────────────────────────────────

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        tool_choice: str = "auto",
    ) -> NonStreamResult:
        kwargs: dict = {
            "model": model or self._default_model,
            "messages": self._serialize_messages(messages),
            "stream": False,
        }
        if tools:
            kwargs["tools"] = [self._serialize_tool(t) for t in tools]
            kwargs["tool_choice"] = tool_choice
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        resp = await self._client.chat.completions.create(**kwargs)
        choice = resp.choices[0] if resp.choices else None
        msg = choice.message if choice else None

        usage = Usage.zero()
        if resp.usage:
            usage.prompt_tokens = resp.usage.prompt_tokens or 0
            usage.completion_tokens = resp.usage.completion_tokens or 0
            usage.total_tokens = resp.usage.total_tokens or 0

        tool_calls: list[ToolCall] = []
        if msg and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        type="function",
                        function_name=tc.function.name,
                        function_args=tc.function.arguments,
                    )
                )

        return NonStreamResult(
            content=msg.content if msg else "",
            tool_calls=tool_calls,
            usage=usage,
        )

    # ── 流式 ────────────────────────────────────────────────

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        tool_choice: str = "auto",
    ) -> AsyncGenerator[StreamEvent, None]:
        kwargs: dict = {
            "model": model or self._default_model,
            "messages": self._serialize_messages(messages),
            "stream": True,
        }
        if tools:
            kwargs["tools"] = [self._serialize_tool(t) for t in tools]
            kwargs["tool_choice"] = tool_choice
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            kwargs["reasoning_effort"] = reasoning_effort

        try:
            stream = await self._client.chat.completions.create(**kwargs)
        except Exception as e:
            yield StreamEvent(type=StreamEventType.ERROR, error=str(e))
            return

        current_tool_id = ""
        current_tool_name = ""
        current_tool_args = ""
        accumulated_reasoning = ""
        usage: Usage | None = None

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            delta = choice.delta if choice else None

            if delta and delta.content:
                yield StreamEvent(type=StreamEventType.CONTENT_DELTA, content=delta.content)

            # DeepSeek reasoning_effort: 捕获推理链内容
            if delta and getattr(delta, "reasoning_content", None):
                accumulated_reasoning += delta.reasoning_content

            if delta and delta.tool_calls:
                for tc in delta.tool_calls:
                    if tc.id:
                        if current_tool_id:
                            yield StreamEvent(
                                type=StreamEventType.TOOL_CALL_END,
                                tool_call_id=current_tool_id,
                                tool_call_name=current_tool_name,
                                tool_call_args=current_tool_args,
                            )
                        current_tool_id = tc.id
                        current_tool_name = tc.function.name if tc.function else ""
                        current_tool_args = ""
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_START,
                            tool_call_id=current_tool_id,
                            tool_call_name=current_tool_name,
                        )
                    if tc.function and tc.function.arguments:
                        current_tool_args += tc.function.arguments
                        yield StreamEvent(
                            type=StreamEventType.TOOL_CALL_DELTA,
                            tool_call_id=current_tool_id,
                            tool_call_args=tc.function.arguments,
                        )

            if chunk.usage:
                usage = Usage(
                    prompt_tokens=chunk.usage.prompt_tokens or 0,
                    completion_tokens=chunk.usage.completion_tokens or 0,
                    total_tokens=chunk.usage.total_tokens or 0,
                )

        # Flush final tool call
        if current_tool_id:
            yield StreamEvent(
                type=StreamEventType.TOOL_CALL_END,
                tool_call_id=current_tool_id,
                tool_call_name=current_tool_name,
                tool_call_args=current_tool_args,
            )

        yield StreamEvent(
            type=StreamEventType.DONE,
            usage=usage,
            reasoning_content=accumulated_reasoning if accumulated_reasoning else None,
        )

    # ── 序列化辅助 ──────────────────────────────────────────

    def _serialize_messages(self, messages: list[Message]) -> list[dict]:
        result: list[dict] = []
        for m in messages:
            d: dict = {"role": m.role, "content": m.content}
            if m.tool_call_id:
                d["tool_call_id"] = m.tool_call_id
            if m.tool_calls:
                d["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function_name,
                            "arguments": tc.function_args,
                        },
                    }
                    for tc in m.tool_calls
                ]
            if m.name:
                d["name"] = m.name
            if m.reasoning_content:
                d["reasoning_content"] = m.reasoning_content
            result.append(d)
        return result

    def _serialize_tool(self, tool: ToolSpec) -> dict:
        return {"type": tool.type, "function": tool.function}
