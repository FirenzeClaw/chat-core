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
    StreamEvent,
    StreamEventType,
    ToolCall,
    ToolContext,
    Usage,
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
        memory_store: Any = None,
        energy_bar: Any = None,
        meta_overrides: Any = None,
        narrative_engine: Any = None,  # Spec 010
    ):
        self._provider = provider
        self._tools = tool_registry
        self._system_prompt = system_prompt
        self._config = config or SubSessionConfig()
        self._memory_store = memory_store

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

        # Spec 007: 精力条（可选，向后兼容）
        self._energy_bar = energy_bar

        # Spec 006: 元认知参数覆盖
        self._meta_overrides = meta_overrides

        # Spec 010: 自我叙事引擎
        self._narrative_engine = narrative_engine

        # 流式回调
        self._on_reply: Any = None  # async callable(text: str)

        # 流式 LLM 输出回调（think 阶段）
        self._on_stream_event: Any = None  # async callable(event: StreamEvent)

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

    def set_stream_callback(self, cb: Any) -> None:
        """设置流式 LLM 输出回调（think 阶段）"""
        self._on_stream_event = cb

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
        self._replies.clear()
        self._init_messages(user_message)

        # 注入潜意识纠正（Spec 004 T002）
        await self._inject_subconscious_corrections()

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
        # 首次启动: 全新初始化
        if not self._messages:
            self._messages = [
                Message(role="system", content=self._system_prompt),
                Message(role="user", content=user_message),
            ]
            self._inject_attention_hint()
            self._inject_meta_mode_hint()
            self._inject_narrative()  # Spec 010
            return
        # 复用 Session: 追加用户消息，保留跨 turn 上下文
        self._messages.append(Message(role="user", content=user_message))
        self._inject_attention_hint()
        self._inject_meta_mode_hint()
        self._inject_narrative()  # Spec 010

    def _inject_attention_hint(self) -> None:
        """向消息历史注入注意力状态提示（在 system prompt 之后、用户消息之前）"""
        if self._attention_model is None:
            return
        try:
            from chat_core.core.types import AttentionStateEnum
            state_enum = self._attention_model.get_state_enum("sub")
            focus = self._attention_model.get_focus("sub")
            hints = {
                AttentionStateEnum.FOCUSED: f"[注意状态] 你感到专注、投入，对对话充满兴趣。（focus={focus:.2f}）",
                AttentionStateEnum.DRIFTING: f"[注意状态] 你有点走神，注意力不太集中，回复会偏短。（focus={focus:.2f}）",
                AttentionStateEnum.DULL: f"[注意状态] 你很难集中注意力，只想简单回应，但请一定回复对方。（focus={focus:.2f}）",
            }
            hint = hints.get(state_enum, "")
            if hint:
                # 插入到用户消息之前（system prompt 之后）
                self._messages.insert(-1, Message(role="system", content=hint))
        except Exception:
            pass

    def _inject_meta_mode_hint(self) -> None:
        """Spec 006: 根据 inner_thoughts_mode 注入内心戏详细度提示。"""
        if self._meta_overrides is None:
            return
        try:
            mode = getattr(self._meta_overrides, "inner_thoughts_mode", "full")
            mode_hints = {
                "full": "",  # 默认不追加额外提示
                "brief": "[内心戏模式] 请保持内心戏简洁，不超过50字，只记录关键感受即可。",
                "minimal": "[内心戏模式] 极简模式——内心戏仅记录最重要的一个感受词（如'开心''困惑''疲惫'），不超过10字。",
            }
            hint = mode_hints.get(mode, "")
            if hint:
                self._messages.insert(-1, Message(role="system", content=hint))
        except Exception:
            pass

    def _inject_narrative(self) -> None:
        """Spec 010: 注入自我叙述到 system prompt。"""
        if self._narrative_engine is None:
            return
        try:
            injection = self._narrative_engine.get_system_injection()
            if injection:
                self._messages.insert(-1, Message(role="system", content=injection))
        except Exception:
            pass

    async def _inject_subconscious_corrections(self) -> None:
        """查询 subconscious/corrections namespace，注入为 system message。

        在 _init_messages() 之后、首次 _think() 之前调用。
        读取后更新 last_access 标记已处理。
        """
        if self._memory_store is None:
            return
        try:
            import json
            import time

            entries = await self._memory_store.query("subconscious/corrections")
            for entry in entries:
                value = entry.value
                if isinstance(value, dict):
                    correction_text = _format_correction(value)
                elif isinstance(value, str):
                    try:
                        correction_text = _format_correction(json.loads(value))
                    except json.JSONDecodeError:
                        correction_text = value
                else:
                    continue

                if correction_text:
                    # 注入到 user message 之前（system prompt 之后）
                    self._messages.insert(
                        -1,
                        Message(role="system", content=f"[注意] {correction_text}"),
                    )

            # Spec 005: 读取 defense_awareness
            awareness_entries = await self._memory_store.query("subconscious/defense_awareness")
            for entry in awareness_entries:
                value = entry.value
                text = value.get("text", "") if isinstance(value, dict) else str(value)
                if text:
                    self._messages.insert(
                        -1,
                        Message(role="system", content=text),
                    )

            # 更新 last_access（标记为已处理）
            now = time.time()
            for entry in entries:
                try:
                    entry.last_access = str(now)
                except Exception:
                    pass  # 更新失败不影响主流程

            # Spec 006: 读取 self/metacognition 洞察
            try:
                metacog_entries = await self._memory_store.query("self/metacognition", limit=5)
                for entry in metacog_entries:
                    value = entry.value
                    if isinstance(value, dict):
                        text = value.get("insight_text", "")
                        if text:
                            self._messages.insert(
                                -1,
                                Message(role="system", content=f"[自我洞察] {text}"),
                            )
            except Exception:
                pass
        except Exception:
            pass  # 静默降级

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
        # Spec 007: 精力耗尽 → exit
        if self._energy_bar and self._energy_bar.should_exit():
            return False
        return True

    async def _emit_reply(self, text: str) -> None:
        """安全触发 _on_reply 回调（兼容同步和异步回调）"""
        if self._on_reply:
            result = self._on_reply(text)
            if asyncio.iscoroutine(result):
                await result

    async def _emit_stream_event(self, event: StreamEvent) -> None:
        """安全触发 _on_stream_event 回调"""
        if self._on_stream_event:
            result = self._on_stream_event(event)
            if asyncio.iscoroutine(result):
                await result

    async def _think(self) -> NonStreamResult | None:
        """调用 LLM（流式），返回其决策"""
        cfg = get_config()
        brain_cfg = cfg.brain_api_config("sub_session")

        # 使用 SubSessionConfig 中的 temperature 覆盖（来自 PersonalityEngine）
        temperature = brain_cfg.get("temperature", 0.8)
        if self._config.temperature is not None:
            temperature = self._config.temperature

        tools_spec = self._tools.specs() if len(self._tools) > 0 else None
        model = brain_cfg.get("model", "deepseek-v4-flash")
        max_tokens = brain_cfg.get("max_tokens", 512)
        reasoning_effort = brain_cfg.get("reasoning_effort", "max")

        accumulated_content = ""
        accumulated_tool_calls: dict[str, ToolCall] = {}
        final_usage = Usage.zero()
        final_reasoning: str | None = None

        try:
            async for event in self._provider.stream_chat(
                messages=self._messages,
                model=model,
                tools=tools_spec,
                temperature=temperature,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
            ):
                # 触发流式回调 → CLI 实时渲染
                await self._emit_stream_event(event)

                if event.type == StreamEventType.CONTENT_DELTA:
                    accumulated_content += (event.content or "")
                elif event.type == StreamEventType.TOOL_CALL_END:
                    tc = ToolCall(
                        id=event.tool_call_id or "",
                        function_name=event.tool_call_name or "",
                        function_args=event.tool_call_args or "",
                    )
                    accumulated_tool_calls[tc.id] = tc
                elif event.type == StreamEventType.DONE:
                    if event.usage:
                        final_usage = event.usage
                    if event.reasoning_content:
                        final_reasoning = event.reasoning_content
                elif event.type == StreamEventType.ERROR:
                    raise RuntimeError(event.error or "Stream error")
        except Exception as e:
            # LLM 调用失败 → 优雅降级
            error_msg = f"[系统错误: {e}]"
            self._replies.append(error_msg)
            await self._emit_reply(error_msg)
            self._done = True
            return None

        tool_calls_list = list(accumulated_tool_calls.values())
        result = NonStreamResult(
            content=accumulated_content,
            tool_calls=tool_calls_list,
            usage=final_usage,
        )

        # 记录 assistant 消息到上下文
        assistant_msg = Message(
            role="assistant",
            content=result.content,
            tool_calls=result.tool_calls if result.tool_calls else None,
            reasoning_content=final_reasoning,
        )
        self._messages.append(assistant_msg)

        return result

    async def _act(self, result: NonStreamResult) -> None:
        """执行 LLM 返回的工具调用。
        
        如果 LLM 输出了纯文本（无 tool_calls），自动合成为 send_reply 工具调用，
        保持 ReAct 协议的 tool_call → tool_result 结构完整性。
        """
        if not result.tool_calls:
            # 纯文本 → 合成为 send_reply 工具调用
            text = result.content.strip()
            if text:
                self._replies.append(text)
                await self._emit_reply(text)
                # 合成 tool_call + tool_result 注入历史，保持 ReAct 协议
                fake_tc = ToolCall(
                    id=f"synth_{self._iteration}",
                    function_name="send_reply",
                    function_args=json.dumps({"text": text}, ensure_ascii=False),
                )
                self._messages[-1] = Message(
                    role="assistant",
                    content=result.content,
                    tool_calls=[fake_tc],
                    reasoning_content=self._messages[-1].reasoning_content,
                )
                self._messages.append(Message(
                    role="tool",
                    content=json.dumps({"sent": True}),
                    tool_call_id=fake_tc.id,
                ))
            
            # 如果还没写内心戏，追加一次 LLM 调用要求补 inner_thoughts
            if self._inner_thoughts_raw is None and not self._inner_thoughts_retried:
                self._inner_thoughts_retried = True
                self._messages.append(Message(
                    role="system",
                    content="你的发言已结束。请现在调用 inner_thoughts 写下你的内心想法。"
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

def register_sub_session_tools(
    registry: ToolRegistry,
    loop: ReActLoop,
    memory_store: Any = None,
    chain_config: Any = None,  # Spec 003: RecallChainConfig | None
    attention_model: Any = None,  # 注意力状态机集成
) -> None:
    """向 ToolRegistry 注册子Session 的五项基础工具。
    
    Spec 003: chain_config 为 RecallChainConfig 时，recall 工具使用联锁检索。
    注意力状态机: attention_model 用于 recall 结果后的注意力回调。
    """

    registry.register(ToolDefinition(
        name="send_reply",
        description="发送一句话到聊天窗口（1-3句话）。发送后继续发下一条，直到说完所有话。必须分段，不要一次全说完。",
        parameters={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "消息内容，自然口语，1-3句话，≤500字"}
            },
            "required": ["text"],
        },
        fn=lambda args, ctx: _handle_send_reply(args, loop),
        parallel_safe=False,
    ))

    registry.register(ToolDefinition(
        name="wait",
        description="额外停顿，模拟思考和打字的间隙。0.5-5.0秒。",
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
        description="从记忆中检索信息。自动追溯关联记忆。只读。先思考再查：不要直接复制用户原话，而是从用户消息和上下文里提炼出人名、话题、事件等搜索线索作为 query。",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "提炼后的搜索线索（人名、话题、关键概念），不是用户原话"}
            },
            "required": ["query"],
        },
        fn=lambda args, ctx: _handle_recall(args, memory_store, chain_config, attention_model),
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
    
    # 安全过滤
    blocked = ContentFilter.check_safety(text)
    if blocked:
        return json.dumps({"sent": False, "error": "内容被安全策略拦截"})

    # 硬截断 (500字上限)
    cfg = get_config()
    max_len = cfg.safety.get("send_reply_max_length", 500)
    if len(text) > max_len:
        text = text[:max_len]

    # ── 字数软判定 (150字) ──
    SOFT_LIMIT = 25
    if len(text) > SOFT_LIMIT:
        # 首次超字数 → 提示，不发送
        last = getattr(loop, '_last_long_text', None)
        if last is None or last != text[:200]:
            loop._last_long_text = text[:200]
            return "字太多了会不会不好？当然，真的很有必要这么说的话，再发一次我就发出去。"
        # 第二次还是超字数 → 发送（用户认为有必要）
        loop._last_long_text = None

    # ── 发送 ──
    loop._replies.append(text)
    if loop._on_reply:
        await loop._emit_reply(text)

    # ── 渐进式鼓励 (基于已发送条数) ──
    count = len(loop._replies)
    if count == 1:
        return "【QQ消息发送成功】<潜意识>{下一句？}"
    elif count == 2:
        return "【QQ消息发送成功】<潜意识>{还有吗？}"
    elif count == 3:
        return "【QQ消息发送成功】<潜意识>{还想说还是够了？（不发了记得内心戏）}"
    else:
        return "【QQ消息发送成功】"


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


async def _handle_recall(args: dict, memory_store: Any = None, chain_config: Any = None, attention_model: Any = None) -> str:
    """从记忆中检索信息。
    
    Spec 003: 若提供 chain_config，使用 search_chained 联锁检索 + 自然语言回溯。
    否则使用旧 search() API 返回 JSON。
    注意力状态机集成: salience≥7 → MEMORY_STRONG_HIT，空结果 → MEMORY_MISS。
    """
    query = str(args.get("query", ""))
    if memory_store is None:
        return json.dumps({"results": [], "query": query, "note": "记忆系统未初始化"})
    try:
        # Spec 003: 联锁检索
        if chain_config is not None:
            chained = await memory_store.search_chained(query, chain_config)
            result_text = memory_store._format_recall_result(chained)
            # 注意力回调：检查 salience
            if attention_model is not None and chained:
                from chat_core.core.types import AttentionEvent
                max_sal = max(
                    (getattr(cm.entry, 'salience', 0) for cm in chained if hasattr(cm, 'entry')),
                    default=0,
                )
                if max_sal >= 7:
                    attention_model.apply_event(AttentionEvent.MEMORY_STRONG_HIT, brain="sub")
            return result_text
        # 旧 API 降级
        entries = await memory_store.search(query, top_n=5)
        results = [
            {"key": f"{e.namespace}/{e.key}", "value": e.value}
            for e in entries
        ]
        # 注意力回调：空结果 → penalty
        if attention_model is not None and not entries:
            from chat_core.core.types import AttentionEvent
            attention_model.apply_event(AttentionEvent.MEMORY_MISS, brain="sub")
        return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"results": [], "query": query, "error": str(e)})


# ── Subconscious Correction Helpers ──────────────────────────


def _format_correction(correction: dict) -> str:
    """将 correction dict 格式化为人类可读的注入文本。"""
    parts: list[str] = []
    logic_errors = correction.get("logic_errors", [])
    tone_issues = correction.get("tone_issues", [])

    if logic_errors:
        parts.append(f"上一轮回复存在事实错误：{'；'.join(str(e) for e in logic_errors)}")
    if tone_issues:
        parts.append(f"上一轮回复存在语气问题：{'；'.join(str(i) for i in tone_issues)}")

    if not parts:
        # 通用格式
        combined = correction.get("combined_weight", 0)
        if combined > 0:
            parts.append(f"上一轮回复需要修正（权重={combined:.2f}）")

    return " ".join(parts) if parts else ""

