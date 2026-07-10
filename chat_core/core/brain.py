"""四脑实现 — 逻辑/情感/子Session/行为脑"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

from chat_core.config import get_config
from chat_core.core.provider import ModelProvider
from chat_core.core.prompt_engine import PromptEngine
from chat_core.core.tools import ToolDefinition, ToolRegistry, ToolContext
from chat_core.core.types import (
    ActionResult,
    ChainedMemory,
    LOGIC_BRAIN_CHAIN_CONFIG,
    MemoryEntry,
    Message,
    MetacognitionReport,
    MetaParamOverrides,
    NonStreamResult,
    RelationType,
    ToolCall,
)
from chat_core.systems.memory import MemoryStore


# ── 逻辑主脑 ──────────────────────────────────────────────

class LogicBrain:
    """逻辑主脑：两阶段 recall → inject。单 pass（不循环）。不发言。
    
    主脑保持跨 turn 上下文，通过 message history 累积。达到 max_context_tokens
    阈值时自动压缩或退役（同子Session 三级策略）。
    """

    def __init__(
        self,
        provider: ModelProvider,
        memory: MemoryStore,
        prompt_engine: PromptEngine,
    ):
        self._provider = provider
        self._memory = memory
        self._prompt_engine = prompt_engine
        self._tools = ToolRegistry()
        self._register_tools()
        # 跨 turn 上下文
        self._history: list[Message] = []
        self._max_context_tokens = self._get_max_context()
        self._compression_applied = 0
        # Spec 003: 存储最近一次联锁 recall 结果，供 think_inject 格式化使用
        self._last_chained_recall: list[ChainedMemory] = []

    def _get_max_context(self) -> int:
        from chat_core.config import get_config
        return get_config().brain_config("logic").get("max_context_tokens", 700000)

    def _estimate_tokens(self) -> int:
        total = sum(len(m.content) for m in self._history)
        return max(1, total // 4)

    def _compress(self) -> None:
        """截断旧消息至 200 字符"""
        cutoff = len(self._history) // 2
        for i in range(cutoff):
            if len(self._history[i].content) > 200:
                self._history[i] = Message(
                    role=self._history[i].role,
                    content=self._history[i].content[:200] + "...",
                )
        self._compression_applied += 1

    def _should_compress(self) -> bool:
        ratio = self._estimate_tokens() / self._max_context_tokens
        return ratio > 0.7

    def _register_tools(self) -> None:
        """注册逻辑脑工具：recall, memory_save, memory_link, inject_to_sub"""
        self._tools.register(ToolDefinition(
            name="recall",
            description="深度记忆检索。搜索事实、用户画像、历史对话。",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string", "description": "检索查询"}},
                "required": ["query"],
            },
            fn=lambda args, ctx: self._do_recall(args),
            parallel_safe=True,
        ))
        self._tools.register(ToolDefinition(
            name="memory_save",
            description="写入结构化记忆。",
            parameters={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "命名空间，如 user/facts"},
                    "key": {"type": "string"},
                    "value": {"type": "object", "description": "JSON 值"},
                    "layer": {"type": "string", "enum": ["gist", "detail"], "default": "gist"},
                },
                "required": ["namespace", "key", "value"],
            },
            fn=lambda args, ctx: self._do_memory_save(args),
            parallel_safe=False,
        ))
        self._tools.register(ToolDefinition(
            name="memory_link",
            description="建立记忆之间的关联。",
            parameters={
                "type": "object",
                "properties": {
                    "from_key": {"type": "string"},
                    "to_key": {"type": "string"},
                    "relation": {"type": "string", "enum": ["extends", "contradicts", "related_to"]},
                },
                "required": ["from_key", "to_key", "relation"],
            },
            fn=lambda args, ctx: self._do_memory_link(args),
            parallel_safe=True,
        ))
        self._tools.register(ToolDefinition(
            name="inject_to_sub",
            description="向子Session注入上下文和方向指导。",
            parameters={
                "type": "object",
                "properties": {
                    "context": {"type": "string", "description": "对话上下文"},
                    "direction": {"type": "string", "description": "回复方向"},
                    "relevant_memories": {"type": "array", "items": {"type": "string"}},
                    "avoid_phrases": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["context", "direction"],
            },
            fn=lambda args, ctx: json.dumps({"injected": True}),
            parallel_safe=False,
        ))
        # Spec 006: 元认知报告工具
        self._tools.register(ToolDefinition(
            name="metacognition_report",
            description="提交元认知审查结论：文本洞察 + 可选参数调节",
            parameters={
                "type": "object",
                "properties": {
                    "insight_text": {
                        "type": "string",
                        "description": "自然语言自我洞察",
                    },
                    "param_overrides": {
                        "type": "object",
                        "properties": {
                            "review_threshold_offset": {
                                "type": "number",
                                "description": "审查阈值偏移，范围 ±0.15",
                            },
                            "defense_prob_multiplier": {
                                "type": "number",
                                "description": "防御概率乘数，范围 0.5~2.0",
                            },
                            "interest_modulations": {
                                "type": "object",
                                "description": "话题兴趣调制，{topic_name: ±0.3}",
                            },
                            "emotion_threshold_offset": {
                                "type": "number",
                                "description": "情绪交互阈值偏移，范围 ±0.1",
                            },
                            "inner_thoughts_mode": {
                                "type": "string",
                                "enum": ["full", "brief", "minimal"],
                                "description": "内心戏详细度",
                            },
                        },
                    },
                    "confidence": {
                        "type": "number",
                        "description": "确定度 0~1",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
                "required": ["insight_text", "confidence"],
            },
            fn=lambda args, ctx: json.dumps({"report_submitted": True}),
            parallel_safe=False,
        ))

    # ── Phase 1: recall + 初步判断 ─────────────────────────

    async def think_pre(self, user_message: str) -> tuple[list[MemoryEntry], str]:
        """Phase 1: 检索记忆 + 输出方向判断"""
        # 压缩检查
        if self._should_compress():
            self._compress()

        system_prompt = self._prompt_engine.build_logic_brain_prompt()

        # 首次初始化历史
        if not self._history:
            self._history = [Message(role="system", content=system_prompt)]

        # 追加用户消息到历史
        self._history.append(Message(role="user", content=f"用户说: {user_message}\n请先 recall 检索相关记忆，然后给出你的方向判断。"))

        cfg = get_config()
        api_cfg = cfg.brain_api_config("logic")

        result = await self._provider.chat(
            messages=self._history,
            model=api_cfg.get("model", "deepseek-v4-pro"),
            tools=self._tools.specs(),
            temperature=api_cfg.get("temperature", 0.3),
            max_tokens=api_cfg.get("max_tokens", 512),
            reasoning_effort=api_cfg.get("reasoning_effort", "max"),
            tool_choice="auto",
        )

        # 执行工具调用（recall + memory_link）
        memories: list[MemoryEntry] = []
        tool_results: list[Message] = []
        if result.tool_calls:
            for tc in result.tool_calls:
                if tc.function_name == "recall":
                    entries = await self._execute_recall(tc)
                    memories.extend(entries)
                    tool_results.append(Message(
                        role="tool",
                        content=json.dumps(
                            {"results": len(entries), "entries": [
                                {"namespace": e.namespace, "key": e.key}
                                for e in entries[:10]
                            ]},
                            ensure_ascii=False,
                        ),
                        tool_call_id=tc.id,
                    ))
                elif tc.function_name == "memory_link":
                    await self._execute_memory_link(tc)
                    tool_results.append(Message(
                        role="tool",
                        content=json.dumps({"linked": True}),
                        tool_call_id=tc.id,
                    ))

        # 追加 assistant 到历史
        self._history.append(Message(
            role="assistant",
            content=result.content,
            tool_calls=result.tool_calls if result.tool_calls else None,
            reasoning_content=result.reasoning_content,
        ))
        # 追加 tool 结果到历史（OpenAI API 要求）
        self._history.extend(tool_results)

        # LLM rerank: 去重后取 top 5 最相关
        if len(memories) > 5:
            memories = await self._rerank_memories(memories, user_message)

        return memories, result.content

    # ── Phase 2: inject ──────────────────────────────────────

    async def think_inject(
        self,
        user_message: str,
        memories: list[MemoryEntry],
        direction: str,
    ) -> dict[str, Any]:
        """Phase 2: 生成 inject_to_sub 调用。
        
        Spec 003: 使用 _format_recall_result 将联锁记忆转为自然语言回溯。
        """
        # Spec 003: 自然语言回溯
        if self._last_chained_recall:
            memory_text = self._memory._format_recall_result(self._last_chained_recall)
        else:
            # 降级：无联锁结果时使用原始格式
            memory_texts = [f"- {m.namespace}/{m.key}: {json.dumps(m.value, ensure_ascii=False)[:200]}" for m in memories[:5]]
            memory_text = "\n".join(memory_texts)

        self._history.append(Message(role="user", content=(
            f"用户说: {user_message}\n"
            f"方向判断: {direction}\n"
            f"相关记忆:\n{memory_text}"
            f"\n\n请调用 inject_to_sub 注入上下文和方向指导。"
        )))

        cfg = get_config()
        api_cfg = cfg.brain_api_config("logic")

        result = await self._provider.chat(
            messages=self._history,
            model=api_cfg.get("model", "deepseek-v4-pro"),
            tools=self._tools.specs(),
            temperature=api_cfg.get("temperature", 0.3),
            max_tokens=api_cfg.get("max_tokens", 512),
            reasoning_effort=api_cfg.get("reasoning_effort", "max"),
            tool_choice="auto",
        )

        injection: dict[str, Any] = {"context": "", "direction": ""}
        tool_inject_results: list[Message] = []
        if result.tool_calls:
            for tc in result.tool_calls:
                if tc.function_name == "inject_to_sub":
                    try:
                        injection = json.loads(tc.function_args)
                    except json.JSONDecodeError:
                        pass
                tool_inject_results.append(Message(
                    role="tool",
                    content=json.dumps({"injected": True}),
                    tool_call_id=tc.id,
                ))

        # 追加到历史
        self._history.append(Message(
            role="assistant",
            content=result.content,
            tool_calls=result.tool_calls if result.tool_calls else None,
            reasoning_content=result.reasoning_content,
        ))
        self._history.extend(tool_inject_results)

        return injection

    # ── 工具实现 ──────────────────────────────────────────────

    async def _do_recall(self, args: dict) -> str:
        query = str(args.get("query", ""))
        entries = await self._memory.search(query, top_n=10)
        results = [
            {
                "namespace": e.namespace,
                "key": e.key,
                "value": e.value,
                "salience": e.salience,
            }
            for e in entries
        ]
        return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False)

    async def _rerank_memories(
        self, memories: list[MemoryEntry], query: str
    ) -> list[MemoryEntry]:
        """LLM rerank: 从最多 20 条记忆中选出 top 5 最相关的。
        
        使用简短 prompt + max_tokens=64 + 1s timeout 保证低延迟。
        失败时返回原始列表的前 5 条（安全降级）。
        """
        if len(memories) <= 5:
            return memories

        # 构建候选列表
        candidates = []
        for i, m in enumerate(memories[:20]):
            val_str = json.dumps(m.value, ensure_ascii=False)[:200]
            candidates.append(f"{i+1}. [{m.namespace}/{m.key}] {val_str}")

        prompt = (
            f"用户问题是: {query}\n\n"
            f"候选记忆:\n" + "\n".join(candidates) + "\n\n"
            f"请选出与用户问题最相关的 5 条记忆，只返回编号，用逗号分隔。\n"
            f"示例输出: 3,7,1,12,5"
        )

        try:
            result = await asyncio.wait_for(
                self._provider.chat(
                    messages=[Message(role="user", content=prompt)],
                    model="deepseek-v4-flash",
                    max_tokens=64,
                    temperature=0.0,
                ),
                timeout=1.0,
            )
            # 解析编号
            nums = [int(n) - 1 for n in re.findall(r'\d+', result.content)]
            ranked = []
            seen_idx: set[int] = set()
            for idx in nums:
                if 0 <= idx < len(memories) and idx not in seen_idx:
                    seen_idx.add(idx)
                    ranked.append(memories[idx])
                if len(ranked) >= 5:
                    break
            if ranked:
                return ranked
        except Exception:
            pass  # 降级: 返回前 5 条

        return memories[:5]

    async def _execute_recall(self, tc: ToolCall) -> list[MemoryEntry]:
        """Spec 003: 使用 search_chained() 联锁检索，保持返回 list[MemoryEntry]"""
        try:
            args = json.loads(tc.function_args)
            query = str(args.get("query", ""))
            chained = await self._memory.search_chained(query, LOGIC_BRAIN_CHAIN_CONFIG)
            self._last_chained_recall = chained
            return [cm.entry for cm in chained]
        except Exception:
            self._last_chained_recall = []
            return []

    async def _do_memory_save(self, args: dict) -> str:
        entry = MemoryEntry(
            namespace=str(args.get("namespace", "")),
            key=str(args.get("key", "")),
            value=args.get("value", {}),
            layer=str(args.get("layer", "gist")),
        )
        await self._memory.save(entry)
        return json.dumps({"saved": f"{entry.namespace}/{entry.key}"})

    async def _do_memory_link(self, args: dict) -> str:
        from_parts = str(args["from_key"]).split("/", 1)
        to_parts = str(args["to_key"]).split("/", 1)
        if len(from_parts) != 2 or len(to_parts) != 2:
            return json.dumps({"error": "Invalid key format. Use namespace/key."})
        relation = RelationType(args.get("relation", "related_to"))
        await self._memory.link(from_parts[0], from_parts[1], to_parts[0], to_parts[1], relation)
        return json.dumps({"linked": True})

    async def _execute_memory_link(self, tc: ToolCall) -> None:
        try:
            args = json.loads(tc.function_args)
            await self._do_memory_link(args)
        except Exception:
            pass


    # ── Spec 006: 元认知 pass ───────────────────────────────

    async def metacognition_pass(self, context: str) -> "MetacognitionReport | None":
        """执行元认知审视：单次 LLM 调用，返回 MetacognitionReport。

        复用 LogicBrain 的 DeepSeek Pro，使用 metacognition_report 工具。
        失败时返回 None（静默降级，不阻塞 turn）。
        """
        prompt = (
            "[元认知审查] 请审视你最近的行为模式:\n\n"
            f"{context}\n\n"
            "请调用 metacognition_report 工具提交你的审查结论。"
        )

        messages = [Message(role="user", content=prompt)]

        cfg = get_config()
        api_cfg = cfg.brain_api_config("logic")

        try:
            result = await self._provider.chat(
                messages=messages,
                model=api_cfg.get("model", "deepseek-v4-pro"),
                tools=self._tools.specs(),
                temperature=api_cfg.get("temperature", 0.3),
                max_tokens=api_cfg.get("max_tokens", 1024),
                reasoning_effort=api_cfg.get("reasoning_effort", "max"),
            )

            # 解析 tool_calls → MetacognitionReport
            if result.tool_calls:
                for tc in result.tool_calls:
                    if tc.function_name == "metacognition_report":
                        try:
                            args = json.loads(tc.function_args)
                            insight_text = str(args.get("insight_text", ""))
                            confidence = float(args.get("confidence", 0.0))

                            overrides_raw = args.get("param_overrides", {}) or {}
                            param_overrides = MetaParamOverrides()
                            # 仅当 LLM 显式返回该字段时才设置值 + sentinel
                            if "review_threshold_offset" in overrides_raw:
                                param_overrides.review_threshold_offset = float(overrides_raw.get("review_threshold_offset", 0.0))
                                param_overrides._review_threshold_set = True
                            if "defense_prob_multiplier" in overrides_raw:
                                param_overrides.defense_prob_multiplier = float(overrides_raw.get("defense_prob_multiplier", 1.0))
                                param_overrides._defense_prob_set = True
                            if "interest_modulations" in overrides_raw:
                                param_overrides.interest_modulations = dict(overrides_raw.get("interest_modulations", {}))
                            if "emotion_threshold_offset" in overrides_raw:
                                param_overrides.emotion_threshold_offset = float(overrides_raw.get("emotion_threshold_offset", 0.0))
                                param_overrides._emotion_threshold_set = True
                            if "inner_thoughts_mode" in overrides_raw:
                                param_overrides.inner_thoughts_mode = str(overrides_raw.get("inner_thoughts_mode", "full"))
                                param_overrides._inner_thoughts_set = True

                            return MetacognitionReport(
                                insight_text=insight_text,
                                confidence=confidence,
                                param_overrides=param_overrides,
                            )
                        except (json.JSONDecodeError, ValueError, TypeError) as e:
                            logger.warning(f"Failed to parse metacognition_report: {e}")

            # 降级：从 content 中尝试提取
            if result.content:
                try:
                    content_json = json.loads(result.content)
                    if "insight_text" in content_json:
                        return MetacognitionReport(
                            insight_text=str(content_json.get("insight_text", "")),
                            confidence=float(content_json.get("confidence", 0.0)),
                        )
                except (json.JSONDecodeError, ValueError):
                    pass

            return None

        except Exception as e:
            logger.warning(f"Metacognition pass failed: {e}")
            return None

    # ── Spec 009: Pro/Con 道德评估 ─────────────────────────

    async def pro_con(self, conflict_context: str) -> tuple[float, str]:
        """Spec 009: 道德困境 Pro 评估 — 从逻辑/原则角度评估。

        Returns:
            (score, reasoning_text): score 在 [0, 1]，高分=倾向说真话
        """
        if not self._provider:
            return (0.5, "[LogicBrain不可用]")
        try:
            prompt = (
                "从逻辑和原则角度分析以下道德困境。给出一个分数 (0-1) 和简短推理。\n"
                "分数含义: 1.0 = 必须坚持真相/原则, 0.0 = 应该优先保护关系。\n\n"
                f"困境: {conflict_context}\n\n"
                "格式: SCORE:<0-1的数字>\nREASONING:<一句话推理>"
            )
            result = await self._provider.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=128,
            )
            text = result.content if hasattr(result, 'content') else str(result)
            score = 0.5
            reasoning = text
            for line in text.split("\n"):
                if line.upper().startswith("SCORE:"):
                    try:
                        score = float(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.upper().startswith("REASONING:"):
                    reasoning = line.split(":", 1)[1].strip()
            return (max(0.0, min(1.0, score)), reasoning)
        except Exception:
            return (0.5, "[LogicBrain评估失败]")

    # ── Spec 010: 自我叙事 pass ───────────────────────────

    async def narrative_pass(self, context: str) -> str | None:
        """生成/更新自我叙述：单次 LLM 调用，返回叙述文本。

        复用 LogicBrain 的 DeepSeek Pro，纯文本调用（无 tools）。
        失败返回 None（静默降级）。
        """
        prompt = (
            "[自我叙事更新] 请根据以下上下文，生成一段连贯的自我叙述。\n\n"
            f"{context}"
        )

        messages = [Message(role="user", content=prompt)]

        cfg = get_config()
        api_cfg = cfg.brain_api_config("logic")

        try:
            result = await self._provider.chat(
                messages=messages,
                model=api_cfg.get("model", "deepseek-v4-pro"),
                temperature=api_cfg.get("temperature", 0.3),
                max_tokens=512,
                reasoning_effort=api_cfg.get("reasoning_effort", "max"),
            )
            text = result.content.strip()
            return text if text else None
        except Exception as e:
            logger.warning(f"Narrative pass failed: {e}")
            return None


# ── 情感主脑 ──────────────────────────────────────────────

class EmotionBrain:
    """情感主脑：同两阶段结构，memory_tag 替代 memory_save。不发言。
    
    主脑保持跨 turn 上下文，通过 message history 累积。
    """

    def __init__(
        self,
        provider: ModelProvider,
        memory: MemoryStore,
        prompt_engine: PromptEngine,
    ):
        self._provider = provider
        self._memory = memory
        self._prompt_engine = prompt_engine
        self._tools = ToolRegistry()
        self._register_tools()
        self._history: list[Message] = []
        self._max_context_tokens = self._get_max_context()

    def _get_max_context(self) -> int:
        from chat_core.config import get_config
        return get_config().brain_config("emotion").get("max_context_tokens", 700000)

    def _estimate_tokens(self) -> int:
        total = sum(len(m.content) for m in self._history)
        return max(1, total // 4)

    def _compress(self) -> None:
        cutoff = len(self._history) // 2
        for i in range(cutoff):
            if len(self._history[i].content) > 200:
                self._history[i] = Message(
                    role=self._history[i].role,
                    content=self._history[i].content[:200] + "...",
                )

    def _should_compress(self) -> bool:
        ratio = self._estimate_tokens() / self._max_context_tokens
        return ratio > 0.7

    def _register_tools(self) -> None:
        self._tools.register(ToolDefinition(
            name="recall",
            description="搜索记忆，关注情感关联。",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            fn=lambda args, ctx: self._do_recall(args),
            parallel_safe=True,
        ))
        self._tools.register(ToolDefinition(
            name="memory_tag",
            description="给已有记忆追加情感标签，不创建新条目。",
            parameters={
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "key": {"type": "string"},
                    "tags": {"type": "object", "description": "情感标签键值对"},
                },
                "required": ["namespace", "key", "tags"],
            },
            fn=lambda args, ctx: self._do_memory_tag(args),
            parallel_safe=False,
        ))
        self._tools.register(ToolDefinition(
            name="inject_to_sub",
            description="向子Session注入情感方向指导。",
            parameters={
                "type": "object",
                "properties": {
                    "context": {"type": "string"},
                    "direction": {"type": "string"},
                    "relevant_memories": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["context", "direction"],
            },
            fn=lambda args, ctx: json.dumps({"injected": True}),
            parallel_safe=False,
        ))

    async def think_pre(self, user_message: str) -> tuple[list[MemoryEntry], str]:
        if self._should_compress():
            self._compress()

        system_prompt = self._prompt_engine.build_emotion_brain_prompt()
        if not self._history:
            self._history = [Message(role="system", content=system_prompt)]

        self._history.append(Message(role="user", content=f"用户说: {user_message}\n请先 recall 检索情感关联，然后给出情感方向判断。"))

        cfg = get_config()
        api_cfg = cfg.brain_api_config("emotion")

        result = await self._provider.chat(
            messages=self._history,
            model=api_cfg.get("model", "deepseek-v4-pro"),
            tools=self._tools.specs(),
            temperature=api_cfg.get("temperature", 0.3),
            max_tokens=api_cfg.get("max_tokens", 512),
            reasoning_effort=api_cfg.get("reasoning_effort", "max"),
            tool_choice="auto",
        )

        memories: list[MemoryEntry] = []
        tool_results: list[Message] = []
        if result.tool_calls:
            for tc in result.tool_calls:
                if tc.function_name == "recall":
                    try:
                        args = json.loads(tc.function_args)
                        entries = await self._memory.search(str(args.get("query", "")), top_n=10)
                        memories.extend(entries)
                        tool_results.append(Message(
                            role="tool",
                            content=json.dumps({"results": len(entries)}, ensure_ascii=False),
                            tool_call_id=tc.id,
                        ))
                    except Exception:
                        pass

        self._history.append(Message(
            role="assistant",
            content=result.content,
            tool_calls=result.tool_calls if result.tool_calls else None,
            reasoning_content=result.reasoning_content,
        ))
        self._history.extend(tool_results)

        return memories, result.content

    async def think_inject(
        self,
        user_message: str,
        memories: list[MemoryEntry],
        direction: str,
    ) -> dict[str, Any]:
        memory_texts = [f"- {m.namespace}/{m.key}" for m in memories[:5]]

        self._history.append(Message(role="user", content=(
            f"用户说: {user_message}\n"
            f"情感方向: {direction}\n"
            f"情感记忆:\n" + "\n".join(memory_texts) +
            f"\n\n请调用 inject_to_sub 注入情感方向。"
        )))

        cfg = get_config()
        api_cfg = cfg.brain_api_config("emotion")

        result = await self._provider.chat(
            messages=self._history,
            model=api_cfg.get("model", "deepseek-v4-pro"),
            tools=self._tools.specs(),
            temperature=api_cfg.get("temperature", 0.3),
            max_tokens=api_cfg.get("max_tokens", 512),
            reasoning_effort=api_cfg.get("reasoning_effort", "max"),
            tool_choice="auto",
        )

        injection: dict[str, Any] = {"context": "", "direction": ""}
        tool_inject_results: list[Message] = []
        if result.tool_calls:
            for tc in result.tool_calls:
                if tc.function_name == "inject_to_sub":
                    try:
                        injection = json.loads(tc.function_args)
                    except json.JSONDecodeError:
                        pass
                tool_inject_results.append(Message(
                    role="tool",
                    content=json.dumps({"injected": True}),
                    tool_call_id=tc.id,
                ))

        self._history.append(Message(
            role="assistant",
            content=result.content,
            tool_calls=result.tool_calls if result.tool_calls else None,
            reasoning_content=result.reasoning_content,
        ))
        self._history.extend(tool_inject_results)

        return injection

    # ── Spec 009: Pro/Con 道德评估 ─────────────────────────

    async def pro_con(self, conflict_context: str) -> tuple[float, str]:
        """Spec 009: 道德困境 Pro 评估 — 从情感/关系角度评估。

        Returns:
            (score, reasoning_text): score 在 [0, 1]，高分=倾向保护关系
        """
        if not self._provider:
            return (0.5, "[EmotionBrain不可用]")
        try:
            prompt = (
                "从情感和关系角度分析以下道德困境。给出一个分数 (0-1) 和简短推理。\n"
                "分数含义: 1.0 = 必须保护关系/感受, 0.0 = 应该说真话即使伤人。\n\n"
                f"困境: {conflict_context}\n\n"
                "格式: SCORE:<0-1的数字>\nREASONING:<一句话推理>"
            )
            result = await self._provider.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=128,
            )
            text = result.content if hasattr(result, 'content') else str(result)
            score = 0.5
            reasoning = text
            for line in text.split("\n"):
                if line.upper().startswith("SCORE:"):
                    try:
                        score = float(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.upper().startswith("REASONING:"):
                    reasoning = line.split(":", 1)[1].strip()
            return (max(0.0, min(1.0, score)), reasoning)
        except Exception:
            return (0.5, "[EmotionBrain评估失败]")

    async def _do_recall(self, args: dict) -> str:
        query = str(args.get("query", ""))
        entries = await self._memory.search(query, top_n=10)
        return json.dumps({"results": len(entries)}, ensure_ascii=False)

    async def _do_memory_tag(self, args: dict) -> str:
        ns = str(args.get("namespace", ""))
        key = str(args.get("key", ""))
        tags = args.get("tags", {})
        if isinstance(tags, dict):
            await self._memory.tag(ns, key, tags)
        return json.dumps({"tagged": f"{ns}/{key}"})


# ── 行为脑 ────────────────────────────────────────────────

class ActionBrain:
    """行为脑：临时创建，执行单一搜索/抓取任务，用完销毁。"""

    def __init__(
        self,
        provider: ModelProvider,
        memory: MemoryStore,
        prompt_engine: PromptEngine,
    ):
        self._provider = provider
        self._memory = memory
        self._prompt_engine = prompt_engine
        self._tools = ToolRegistry()
        self._register_tools()

    def _register_tools(self) -> None:
        self._tools.register(ToolDefinition(
            name="search",
            description="搜索互联网获取实时信息。",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            fn=lambda args, ctx: self._do_search(args),
            parallel_safe=True,
        ))
        self._tools.register(ToolDefinition(
            name="recall",
            description="从本地记忆中检索信息。只读。",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            fn=lambda args, ctx: self._do_recall(args),
            parallel_safe=True,
        ))
        self._tools.register(ToolDefinition(
            name="web_fetch",
            description="抓取网页内容。仅 http/https，100KB 限制，10s 超时。",
            parameters={
                "type": "object",
                "properties": {"url": {"type": "string", "format": "uri"}},
                "required": ["url"],
            },
            fn=lambda args, ctx: self._do_web_fetch(args),
            parallel_safe=True,
        ))

    async def run(self, task: str) -> ActionResult:
        """执行一次行为脑任务"""
        import time as _time
        start = _time.time()

        system_prompt = self._prompt_engine.build_action_brain_prompt(task)
        messages = [
            Message(role="system", content=system_prompt),
            Message(role="user", content=task),
        ]

        cfg = get_config()
        api_cfg = cfg.brain_api_config("action")

        try:
            result = await self._provider.chat(
                messages=messages,
                model=api_cfg.get("model", "deepseek-v4-flash"),
                tools=self._tools.specs(),
                temperature=api_cfg.get("temperature", 0.5),
                max_tokens=api_cfg.get("max_tokens", 256),
            )
        except Exception as e:
            return ActionResult(
                task=task,
                task_type="unknown",
                output="",
                success=False,
                error=str(e),
                elapsed_ms=int((_time.time() - start) * 1000),
            )

        # 执行工具调用
        output_parts: list[str] = []
        if result.tool_calls:
            for tc in result.tool_calls:
                tool = self._tools.get(tc.function_name)
                if tool:
                    try:
                        args = json.loads(tc.function_args)
                        r = tool.fn(args, ToolContext())
                        if asyncio.iscoroutine(r):
                            r = await r
                        output_parts.append(str(r))
                    except Exception:
                        pass

        if result.content:
            output_parts.append(result.content)

        return ActionResult(
            task=task,
            task_type="search",
            output="\n".join(output_parts),
            success=True,
            elapsed_ms=int((_time.time() - start) * 1000),
        )

    async def _do_search(self, args: dict) -> str:
        """DuckDuckGo 搜索 — 限速在 Pool 层处理"""
        query = str(args.get("query", ""))
        try:
            from duckduckgo_search import DDGS
            results = list(DDGS().text(query, max_results=5))
            return json.dumps(
                [{"title": r["title"], "url": r["href"], "snippet": r["body"]} for r in results],
                ensure_ascii=False,
            )
        except ImportError:
            return json.dumps({"error": "duckduckgo-search not installed"})
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def _do_recall(self, args: dict) -> str:
        query = str(args.get("query", ""))
        entries = await self._memory.search(query, top_n=5)
        return json.dumps(
            [{"key": f"{e.namespace}/{e.key}", "value": e.value} for e in entries],
            ensure_ascii=False,
        )

    async def _do_web_fetch(self, args: dict) -> str:
        url = str(args.get("url", ""))
        if not url.startswith(("http://", "https://")):
            return json.dumps({"error": "Only http/https URLs allowed"})
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "chat-core/0.1"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                content = resp.read(102400)  # max 100KB
                return content.decode("utf-8", errors="replace")[:5000]
        except Exception as e:
            return json.dumps({"error": str(e)})


# ── 行为脑并发池 ──────────────────────────────────────────

class ActionBrainPool:
    """行为脑并发池：Semaphore(max_concurrent) + Queue"""

    def __init__(self, max_concurrent: int = 2):
        cfg = get_config()
        safety = cfg.safety
        rate_limit = safety.get("search_rate_limit", [5, 60])
        cooldown = safety.get("search_cooldown", 2)
        self._sem = asyncio.Semaphore(max_concurrent)
        self._queue: asyncio.Queue[tuple[str, asyncio.Future]] = asyncio.Queue()
        self._provider: ModelProvider | None = None
        self._memory: MemoryStore | None = None
        self._prompt_engine: PromptEngine | None = None
        self._rate_limiter = _RateLimiter(
            max_per_interval=rate_limit[0] if isinstance(rate_limit, list) else 5,
            interval_seconds=rate_limit[1] if isinstance(rate_limit, list) and len(rate_limit) > 1 else 60,
            cooldown_seconds=cooldown,
        )

    def configure(self, provider: ModelProvider, memory: MemoryStore, prompt_engine: PromptEngine) -> None:
        self._provider = provider
        self._memory = memory
        self._prompt_engine = prompt_engine

    async def submit(self, task: str) -> ActionResult:
        """提交一个行为脑任务，受 Semaphore 控制并发"""
        if not self._provider or not self._memory or not self._prompt_engine:
            return ActionResult(task=task, task_type="unknown", output="", success=False, error="Pool not configured")

        await self._rate_limiter.acquire()

        async with self._sem:
            brain = ActionBrain(self._provider, self._memory, self._prompt_engine)
            return await brain.run(task)


# ── 限速器 ──────────────────────────────────────────────────

class _RateLimiter:
    """Token bucket 限速器"""

    def __init__(self, max_per_interval: int, interval_seconds: float, cooldown_seconds: float):
        self._max = max_per_interval
        self._interval = interval_seconds
        self._cooldown = cooldown_seconds
        self._tokens: list[float] = []

    async def acquire(self) -> None:
        now = asyncio.get_event_loop().time()
        # 清理过期 token
        self._tokens = [t for t in self._tokens if now - t < self._interval]

        if len(self._tokens) >= self._max:
            # 等待最早 token 过期
            wait_time = self._tokens[0] + self._interval - now + 0.1
            if wait_time > 0:
                await asyncio.sleep(wait_time)
            self._tokens = self._tokens[1:]

        # cooldown
        if self._tokens and now - self._tokens[-1] < self._cooldown:
            await asyncio.sleep(self._cooldown - (now - self._tokens[-1]))

        self._tokens.append(asyncio.get_event_loop().time())
