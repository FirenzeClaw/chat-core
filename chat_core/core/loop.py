"""ReAct Loop 引擎 — 子Session think-act-observe 循环"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from chat_core.config import get_config
from chat_core.core.provider import ModelProvider
from chat_core.core.prompt_engine import PromptEngine
from chat_core.core.safety import ContentFilter
from chat_core.core.tools import ToolDefinition, ToolRegistry
from chat_core.core.types import (
    Message,
    NonStreamResult,
    StreamEventType,
    ToolCall,
    ToolContext,
)


class SubSessionConfig:
    """子Session 运行时配置"""

    def __init__(
        self,
        max_iter: int = 5,
        temperature: float | None = None,
        max_context_tokens: int | None = None,
    ):
        self.max_iter = max_iter
        self.temperature = temperature  # None = 使用 config 默认值
        # 优先读配置，fallback 500K
        if max_context_tokens is None:
            from chat_core.config import get_config
            cfg = get_config()
            bc = cfg.brain_config("sub_session")
            max_context_tokens = bc.get("max_context_tokens", 500000)
        self.max_context_tokens = max_context_tokens


class ReActLoop:
    """子Session 的 think → act → observe 循环"""

    def __init__(
        self,
        provider: ModelProvider,
        tool_registry: ToolRegistry,
        system_prompt: str,
        config: SubSessionConfig | None = None,
        attention_model: Any = None,
    ):
        self._provider = provider
        self._tools = tool_registry
        self._system_prompt = system_prompt
        self._config = config or SubSessionConfig()

        # 运行时状态
        self._messages: list[Message] = []
        self._iteration = 0
        self._inner_thoughts_raw: str | None = None
        self._inner_thoughts_retried = False  # 防止无限循环
        self._replies: list[str] = []
        self._done = False
        self._cancelled = False

        # 情绪模拟（简化版，Phase 6 替换为 EmotionEngine）
        self._sadness = 0.0
        self._boredom = 0.0
        self._focus = 0.9

        # 注意力模型集成（Phase 6）
        self._attention_model = attention_model

        # 流式回调
        self._on_reply: Any = None  # async callable(text: str)

        # 打字动画回调（T064）
        self._on_wait_start: Any = None  # async callable(seconds: float)
        self._on_wait_end: Any = None    # async callable()

        # 上下文退休回调 (Phase 7, T060)
        self._on_retirement: Any = None  # async callable(retirement_info: dict)

    # ── 公共接口 ────────────────────────────────────────────

    @property
    def replies(self) -> list[str]:
        return list(self._replies)

    @property
    def inner_thoughts(self) -> str | None:
        return self._inner_thoughts_raw

    def cancel(self) -> None:
        self._cancelled = True

    def set_reply_callback(self, cb: Any) -> None:
        self._on_reply = cb

    def set_wait_callbacks(self, on_start: Any, on_end: Any) -> None:
        """设置打字动画回调 (T064)"""
        self._on_wait_start = on_start
        self._on_wait_end = on_end

    def set_retirement_callback(self, cb: Any) -> None:
        """设置上下文退休回调 (Phase 7, T060)。

        当上下文使用率超过 85% 时触发，回调接收 retirement_info dict:
        {level: float, estimated_tokens: int, max_tokens: int, messages_count: int}
        """
        self._on_retirement = cb

    # ── Token 估算与压缩 (T069 / Phase 7, T060) ──────────────

    def estimate_tokens(self) -> int:
        """从消息内容总长度估算 token 数量（粗略: 总字符数 ÷ 4）"""
        total_chars = sum(len(m.content) for m in self._messages)
        # 加上 system prompt 的 token 数
        total_chars += len(self._system_prompt)
        return max(1, total_chars // 4)

    def compression_level(self) -> float:
        """返回当前上下文使用率（0.0 ~ 1.0），基于 max_context_tokens"""
        estimated = self.estimate_tokens()
        max_tokens = self._config.max_context_tokens
        if max_tokens <= 0:
            return 0.0
        return min(1.0, estimated / max_tokens)

    def apply_compression(self) -> bool:
        """Phase 7, T060: 根据压缩级别应用上下文压缩。

        阈值:
        - < 70%: 完整历史，不压缩
        - 70-85%: 压缩模式 — 旧 tool 结果截断至 100 字符
        - > 85%: 退休模式 — 触发退休信号，由 TurnManager 创建新子Session

        Returns:
            bool: True 如果触发了退休模式
        """
        level = self.compression_level()

        if level < 0.70:
            return False  # 完整历史

        if level < 0.85:
            # 压缩模式: 截断旧 tool 结果至 100 字符
            for i, msg in enumerate(self._messages):
                if msg.role == "tool" and len(msg.content) > 100:
                    self._messages[i] = Message(
                        role="tool",
                        content=msg.content[:100] + "...[truncated]",
                        tool_call_id=msg.tool_call_id,
                    )
            return False

        # > 85%: 退休模式
        retirement_info = {
            "level": level,
            "estimated_tokens": self.estimate_tokens(),
            "max_tokens": self._config.max_context_tokens,
            "messages_count": len(self._messages),
        }
        if self._on_retirement:
            asyncio.create_task(self._on_retirement(retirement_info))
        return True

    async def run(self, user_message: str) -> None:
        """执行一次完整的 ReAct 循环"""
        self._init_messages(user_message)

        while self._should_continue():
            self._iteration += 1

            # 上下文压缩检查 (Phase 7, T060)
            if self._iteration > 1:
                retired = self.apply_compression()
                if retired:
                    # 退休模式: 上下文已满，停止循环
                    self._done = True
                    break

            # Think
            result = await self._think()
            if result is None:
                break

            # Act
            await self._act(result)

    # ── 内部方法 ────────────────────────────────────────────

    def _init_messages(self, user_message: str) -> None:
        self._messages = [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=user_message),
        ]

    def _should_continue(self) -> bool:
        if self._cancelled:
            return False
        if self._done:
            return False
        if self._iteration >= self._config.max_iter:
            return False
        if self._sadness > 0.8:
            return False
        # 注意力检查：优先使用 AttentionModel，降级为内部 _focus
        if self._attention_model is not None:
            # 触发一次 drift 更新当前注意力值
            self._attention_model.drift()
            if self._attention_model.should_exit_sub():
                return False
        elif self._focus < 0.15:
            return False
        if self._boredom > 0.7:
            return False
        return True

    async def _emit_reply(self, text: str) -> None:
        """安全触发 _on_reply 回调（兼容同步和异步回调）"""
        if self._on_reply:
            result = self._on_reply(text)
            if asyncio.iscoroutine(result):
                await result

    async def _think(self) -> NonStreamResult | None:
        """调用 LLM，返回其决策"""
        cfg = get_config()
        brain_cfg = cfg.brain_api_config("sub_session")

        # 使用 SubSessionConfig 中的 temperature 覆盖（来自 PersonalityEngine）
        temperature = brain_cfg.get("temperature", 0.8)
        if self._config.temperature is not None:
            temperature = self._config.temperature

        try:
            result = await self._provider.chat(
                messages=self._messages,
                model=brain_cfg.get("model", "deepseek-v4-flash"),
                tools=self._tools.specs() if len(self._tools) > 0 else None,
                temperature=temperature,
                max_tokens=brain_cfg.get("max_tokens", 512),
                reasoning_effort=brain_cfg.get("reasoning_effort", "max"),
            )
        except Exception as e:
            # LLM 调用失败 → 优雅降级
            error_msg = f"[系统错误: {e}]"
            self._replies.append(error_msg)
            await self._emit_reply(error_msg)
            self._done = True
            return None

        # 记录 assistant 消息到上下文
        assistant_msg = Message(
            role="assistant",
            content=result.content,
            tool_calls=result.tool_calls if result.tool_calls else None,
        )
        self._messages.append(assistant_msg)

        return result

    async def _act(self, result: NonStreamResult) -> None:
        """执行 LLM 返回的工具调用"""
        # 没有 send_reply 工具时的显式文本才加入回复
        has_send_reply = any(tc.function_name == "send_reply" for tc in (result.tool_calls or []))
        if result.content.strip() and not has_send_reply:
            self._replies.append(result.content)
            await self._emit_reply(result.content)

        if not result.tool_calls:
            # 纯文本回复（无工具调用）
            # 如果还没写内心戏，追加一次 LLM 调用要求补 inner_thoughts
            if self._inner_thoughts_raw is None and not self._inner_thoughts_retried:
                self._inner_thoughts_retried = True
                self._messages.append(Message(
                    role="system",
                    content="你的发言已结束。请现在调用 inner_thoughts 写下你的内心想法，然后调用 done。"
                ))
            else:
                self._done = True
            return

        ctx = ToolContext(
            root_dir=".",
            session_id="sub_session",
        )

        # 检查是否包含 done
        has_done = any(tc.function_name == "done" for tc in result.tool_calls)
        has_inner = any(tc.function_name == "inner_thoughts" for tc in result.tool_calls)

        # 协议检查：done 前必须有 inner_thoughts
        if has_done and not has_inner and self._inner_thoughts_raw is None:
            self._inner_thoughts_raw = ""

        tool_results = await self._tools.execute_batch(result.tool_calls, ctx)

        # 处理各工具结果
        for tc, tr in zip(result.tool_calls, tool_results):
            self._messages.append(
                Message(role="tool", content=tr, tool_call_id=tc.id)
            )

            if tc.function_name == "inner_thoughts":
                try:
                    args = json.loads(tc.function_args)
                    self._inner_thoughts_raw = args.get("text", "")
                except json.JSONDecodeError:
                    self._inner_thoughts_raw = ""
                # inner_thoughts = 结束信号，自动 done
                self._done = True
            elif tc.function_name == "done":
                self._done = True


# ── 子Session 工具注册 ────────────────────────────────────

def register_sub_session_tools(registry: ToolRegistry, loop: ReActLoop, memory_store: Any = None) -> None:
    """向 ToolRegistry 注册子Session 的五项基础工具"""

    registry.register(ToolDefinition(
        name="send_reply",
        description="发送一条消息到聊天窗口。每次调用后收到 {'sent': True}。可多次调用分段表达。",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "消息内容，自然口语，≤500字"}
            },
            "required": ["text"],
        },
        fn=lambda args, ctx: _handle_send_reply(args, loop),
        parallel_safe=False,
    ))

    registry.register(ToolDefinition(
        name="wait",
        description="自然停顿。模拟思考和打字的间隙。",
        parameters={
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "0.5-5.0秒", "minimum": 0.5, "maximum": 5.0}
            },
            "required": ["seconds"],
        },
        fn=lambda args, ctx: _handle_wait(args, loop),
        parallel_safe=False,
    ))

    registry.register(ToolDefinition(
        name="recall",
        description="从记忆中检索相关信息。只读。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词或问题"}
            },
            "required": ["query"],
        },
        fn=lambda args, ctx: _handle_recall(args, memory_store),
        parallel_safe=True,
    ))

    registry.register(ToolDefinition(
        name="inner_thoughts",
        description="发言结束后的内心戏。仅在全部发言完成后调用一次。纯文本，不展示给用户。",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "你的内心想法、感受、反思，及'我是否想要做什么'"}
            },
            "required": ["text"],
        },
        fn=lambda args, ctx: json.dumps({"recorded": True}),
        parallel_safe=False,
    ))

    registry.register(ToolDefinition(
        name="done",
        description="确认本轮发言结束。必须在 inner_thoughts 之后调用。",
        parameters={"type": "object", "properties": {}},
        fn=lambda args, ctx: json.dumps({"turn_ended": True}),
        parallel_safe=False,
    ))


# ── 工具处理函数 ──────────────────────────────────────────

async def _handle_send_reply(args: dict, loop: ReActLoop) -> str:
    text = str(args.get("text", ""))
    # 安全过滤：使用集中式 ContentFilter (T068)
    blocked = ContentFilter.check_safety(text)
    if blocked:
        return json.dumps({"sent": False, "error": "内容被安全策略拦截"})

    # 截断过长内容
    cfg = get_config()
    max_len = cfg.safety.get("send_reply_max_length", 500)
    if len(text) > max_len:
        text = text[:max_len]

    loop._replies.append(text)

    # 流式回调
    if loop._on_reply:
        await loop._emit_reply(text)

    return json.dumps({"sent": True})


async def _handle_wait(args: dict, loop: ReActLoop | None = None) -> str:
    seconds = float(args.get("seconds", 1.0))
    seconds = max(0.5, min(5.0, seconds))
    # 打字动画回调 (T064)
    if loop and loop._on_wait_start:
        await loop._on_wait_start(seconds)
    await asyncio.sleep(seconds)
    if loop and loop._on_wait_end:
        await loop._on_wait_end()
    return json.dumps({"waited": seconds})


async def _handle_recall(args: dict, memory_store: Any = None) -> str:
    """从记忆中检索信息。如果 memory_store 可用则执行真实检索，否则返回空。"""
    query = str(args.get("query", ""))
    if memory_store is None:
        return json.dumps({"results": [], "query": query, "note": "记忆系统未初始化"})
    try:
        entries = await memory_store.search(query, top_n=5)
        results = [
            {"key": f"{e.namespace}/{e.key}", "value": e.value}
            for e in entries
        ]
        return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"results": [], "query": query, "error": str(e)})


