"""ProactiveSystem — Phase 7 主动行为与意图执行系统"""

from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import datetime
from typing import Any, Callable

from chat_core.config import get_config
from chat_core.core.brain import ActionBrainPool
from chat_core.core.loop import ReActLoop, SubSessionConfig, register_sub_session_tools
from chat_core.core.provider import ModelProvider
from chat_core.core.prompt_engine import PromptEngine
from chat_core.core.tools import ToolDefinition, ToolRegistry
from chat_core.core.types import (
    AttentionStateEnum,
    CorrectionCmd,
    Intent,
    IntentStatus,
    IntentType,
    MemoryEntry,
    ReviewResult,
)
from chat_core.systems.memory import MemoryStore
from chat_core.systems.emotion import EmotionEngine
from chat_core.systems.personality import PersonalityEngine
from chat_core.systems.attention import AttentionModel
from chat_core.systems.review import ReviewSystem, extract_intent
from chat_core.systems.interest import InterestModel


class ProactiveSystem:
    """Phase 7 主动行为系统：话题追踪、无聊触发、意图提取与执行"""

    def __init__(
        self,
        provider: ModelProvider,
        memory: MemoryStore,
        prompt_engine: PromptEngine,
        action_pool: ActionBrainPool,
        interest_model: InterestModel,
        review_system: ReviewSystem,
        emotion_engine: EmotionEngine | None = None,
        personality_engine: PersonalityEngine | None = None,
        attention_model: AttentionModel | None = None,
        correct_fn: Callable[[ReviewResult, list[str]], Any] | None = None,
        reply_callback: Any = None,
        stream_callback: Any = None,
    ):
        self._provider = provider
        self._memory = memory
        self._prompt_engine = prompt_engine
        self._action_pool = action_pool
        self._interest_model = interest_model
        self._review_system = review_system
        self._emotion_engine = emotion_engine
        self._personality_engine = personality_engine
        self._attention_model = attention_model
        self._correct_fn = correct_fn

        # 流式回调（由 CLI 注入，供主动发言子Session 使用）
        self._reply_callback = reply_callback
        self._stream_callback = stream_callback

        # 主动子Session 状态
        self._proactive_loop: ReActLoop | None = None
        self._proactive_active: bool = False

        # 延迟意图队列
        self._deferred_intents: list[Intent] = []

    @property
    def proactive_active(self) -> bool:
        return self._proactive_active

    @property
    def proactive_loop(self) -> ReActLoop | None:
        return self._proactive_loop

    # ── Phase 7: 话题提取 ───────────────────────────────────

    def _record_topics_from_thoughts(self, inner_thoughts: str) -> None:
        """从内心戏中提取关键话题并记录到 InterestModel。

        使用简单的关键词分割：按常见分隔符和停用词提取 2-4 字的片段。
        """
        import re

        # 提取中文词片段（2-4 字）
        cleaned = re.sub(r'[^\u4e00-\u9fff]', ' ', inner_thoughts)
        tokens = cleaned.split()
        topics_seen: set[str] = set()
        for token in tokens:
            if 2 <= len(token) <= 4 and token not in topics_seen:
                topics_seen.add(token)
                self._interest_model.record_topic(token)

    # ── Phase 7: 无聊触发 → 主动行为 (T055-T056) ────────────

    def _should_initiate(self) -> bool:
        """检查是否应主动发起对话（注意力状态感知）。

        - FOCUSED:  允许主动发起（正常概率）
        - DRIFTING: 允许但概率 ×0.3
        - DULL:     禁止主动发起（概率 0.0）

        Returns:
            True 表示可以主动发起
        """
        if self._attention_model is not None:
            try:
                state = self._attention_model.get_state_enum("sub")
                if state == AttentionStateEnum.DULL:
                    return False
                elif state == AttentionStateEnum.DRIFTING:
                    if random.random() > 0.3:
                        return False
            except Exception:
                pass
        return True  # FOCUSED 或无注意力模型时正常概率

    async def _on_boredom_trigger(self, boredom: float) -> None:
        """无聊触发回调：检查兴趣权重，决定是否主动发起对话。

        如果兴趣权重 > 0.5：创建 ActionBrain 搜索兴趣话题，
        双脑评估结果，有意义的写入潜意识/nudges，然后触发主动发言。
        如果兴趣权重 ≤ 0.5：直接写 nudge 到潜意识，触发主动发言。

        DULL 态特殊处理：不发起对话，仅写入 subconscious/nudges。
        """
        # DULL 态 + 无聊 → 不发起对话，写 nudges 等恢复后处理
        if self._attention_model is not None:
            try:
                if self._attention_model.get_state_enum("sub") == AttentionStateEnum.DULL:
                    await self._memory.save(MemoryEntry(
                        namespace="subconscious/nudges",
                        key=f"dull_boredom_{int(time.time())}",
                        value={
                            "source": "dull_boredom",
                            "boredom_level": boredom,
                            "note": "注意力昏沉，等恢复后再主动发起",
                        },
                    ))
                    return
            except Exception:
                pass

        top_interests = self._interest_model.get_top_interests(3)
        interest_weight = top_interests[0][1] if top_interests else 0.0

        if interest_weight > 0.5:
            # 用 ActionBrain 搜索兴趣话题
            topics = [t for t, _ in top_interests if _ > 0.3]
            topic_str = "、".join(topics[:3]) if topics else "近期动态"
            search_task = f"搜索关于 {topic_str} 的最新消息"

            try:
                result = await self._action_pool.submit(search_task)
                if result.success and result.output:
                    # 双脑评估搜索结果
                    relevance = await self._assess_relevance(result.output, topic_str)
                    if relevance > 0.4:
                        # 写入潜意识 nudges
                        await self._memory.save(MemoryEntry(
                            namespace="subconscious/nudges",
                            key=f"nudge_boredom_{int(time.time())}",
                            value={
                                "source": "boredom_detector",
                                "boredom_level": boredom,
                                "topics": topics,
                                "search_result": result.output[:500],
                                "relevance": relevance,
                                "direction": "用关心的语气开聊",
                            },
                        ))
            except Exception:
                pass  # ActionBrain 失败静默处理
        else:
            # 兴趣不足，直接写入 nudge
            await self._memory.save(MemoryEntry(
                namespace="subconscious/nudges",
                key=f"nudge_boredom_{int(time.time())}",
                value={
                    "source": "boredom_detector",
                    "boredom_level": boredom,
                    "interest_weight": interest_weight,
                    "direction": "用关心的语气开聊",
                    "note": "兴趣权重低，使用通用开场",
                },
            ))

        # 触发主动发言
        await self._on_proactive_trigger()

    async def _on_end_conversation_signal(self, boredom: float) -> None:
        """结束对话信号回调：无聊过高 + 冲动 → 结束当前主动对话。"""
        if self._proactive_loop and self._proactive_active:
            self._proactive_loop.cancel()
            self._proactive_active = False
            self._proactive_loop = None

    async def _on_proactive_trigger(self) -> None:
        """主动发起对话：检查/创建子Session，注入主动上下文，运行 ReActLoop。

        Flow:
        1. 获取潜意识 nudges
        2. 构建主动 system prompt
        3. 创建或复用子Session
        4. 运行 ReActLoop → send_reply → review
        """
        # 查询最近的 nudge
        nudges = await self._memory.query("subconscious/nudges", limit=5)
        nudge_text = ""
        for n in nudges:
            nudge_text += json.dumps(n.value, ensure_ascii=False)[:200] + "\n"

        # 获取当前兴趣话题
        top_interests = self._interest_model.get_top_interests(3)
        interest_text = "、".join([f"{t}({w:.2f})" for t, w in top_interests]) if top_interests else "无"

        # 构建主动发言的用户消息（模拟"自己想到"的提示）
        init_message = (
            f"【主动发起】你注意到自己有点空闲，想主动和用户聊聊。\n"
            f"当前你感兴趣的话题: {interest_text}\n"
            f"潜意识提示: {nudge_text if nudge_text else '无特殊提示'}\n"
            f"请自然地开启对话，不要太突兀。"
        )

        # 注入情绪和人格状态
        runtime_state: dict[str, Any] = {
            "context": "主动发起对话",
            "direction": "用关心的语气开聊，自然地发起一个话题",
            "relevant_memories": [],
            "initiative": "主动发起",
        }

        if self._emotion_engine:
            emotion_text = self._emotion_engine.get_emotion_summary("sub")
            if emotion_text:
                runtime_state["emotion"] = f"[情绪状态] {emotion_text}"

        if self._attention_model:
            attn = self._attention_model.get_state("sub")
            runtime_state["attention"] = {"focus": attn.focus}

        # 注入共情模式
        if self._personality_engine:
            mode = self._personality_engine.get_response_mode()
            if mode == "empathetic":
                runtime_state["direction"] = (
                    f"{runtime_state['direction']} 【共情模式：注意用户情绪，表达理解和关心】"
                )

        system_prompt = self._prompt_engine.build_sub_session_prompt(runtime_state)

        cfg = get_config()
        tools = ToolRegistry()

        sub_max_iter = cfg.brain_max_iter("sub_session")
        if self._personality_engine:
            personality_temp = self._personality_engine.get_llm_temperature("sub_session")
            sub_config = SubSessionConfig(
                max_iter=sub_max_iter,
                temperature=personality_temp,
            )
        else:
            sub_config = SubSessionConfig(max_iter=sub_max_iter)

        loop = ReActLoop(
            provider=self._provider,
            tool_registry=tools,
            system_prompt=system_prompt,
            config=sub_config,
            attention_model=self._attention_model,
        )
        register_sub_session_tools(tools, loop)
        _enhance_recall(tools, self._memory, self._personality_engine)

        # 注入 CLI 回调（流式输出 + 回复显示）
        if self._reply_callback:
            loop.set_reply_callback(self._reply_callback)
        if self._stream_callback:
            loop.set_stream_callback(self._stream_callback)

        self._proactive_loop = loop
        self._proactive_active = True

        try:
            await loop.run(init_message)

            # 审查产出
            if loop.replies:
                review = await self._review_system.review(
                    loop.replies,
                    loop.inner_thoughts,
                    [],
                    init_message,
                )
                if review.combined_weight > 0.5:
                    # 有问题需要纠正
                    correction = None
                    if self._correct_fn:
                        correction = await self._correct_fn(review, loop.replies)
                    if correction:
                        # 归档为主动 turn
                        await self._memory.save(MemoryEntry(
                            namespace="self/proactive",
                            key=f"proactive_{int(time.time())}",
                            value={
                                "init_message": init_message,
                                "replies": loop.replies,
                                "inner_thoughts": loop.inner_thoughts,
                                "review_weight": review.combined_weight,
                                "correction": correction.message,
                            },
                        ))
                else:
                    # 正常归档
                    await self._memory.save(MemoryEntry(
                        namespace="self/proactive",
                        key=f"proactive_{int(time.time())}",
                        value={
                            "init_message": init_message,
                            "replies": loop.replies,
                            "inner_thoughts": loop.inner_thoughts,
                            "review_weight": review.combined_weight,
                        },
                    ))
        finally:
            self._proactive_active = False

    # ── Phase 7: 意图执行 (T058) ─────────────────────────────

    async def _execute_intent(self, intent: Intent) -> None:
        """执行从内心戏中提取的意图。

        双脑评估：
        - 逻辑脑：评估 relevance + value + timing
        - 情感脑：评估 naturalness + atmosphere
        - combined > 0.5 → 通过 ActionBrain 或直接执行
        - combined ≤ 0.5 → 写入 subconscious/deferred_actions
        """
        # 双脑并行评估
        logic_score, emotion_score = await asyncio.gather(
            self._assess_logic_intent(intent),
            self._assess_emotion_intent(intent),
        )
        combined = logic_score * 0.5 + emotion_score * 0.5
        intent.assessed_weight = combined

        if combined > 0.5:
            # 执行意图
            intent.status = IntentStatus.EXECUTING
            result = await self._dispatch_intent(intent)
            if result:
                intent.status = IntentStatus.RESOLVED
                # 归档执行结果
                await self._memory.save(MemoryEntry(
                    namespace="self/actions",
                    key=f"intent_{int(time.time())}",
                    value={
                        "intent_type": intent.action.value,
                        "detail": intent.detail,
                        "result": result[:500],
                        "combined_weight": combined,
                    },
                ))
            else:
                intent.status = IntentStatus.PENDING
        else:
            # 延迟执行
            intent.status = IntentStatus.DEFERRED
            intent.revisit_condition = "next_turn"
            self._deferred_intents.append(intent)

            await self._memory.save(MemoryEntry(
                namespace="subconscious/deferred_actions",
                key=f"deferred_{int(time.time())}",
                value={
                    "intent_type": intent.action.value,
                    "detail": intent.detail,
                    "confidence": intent.confidence,
                    "assessed_weight": combined,
                    "logic_score": logic_score,
                    "emotion_score": emotion_score,
                    "status": "deferred",
                    "created_at": datetime.now().isoformat(),
                },
                ttl=86400,  # 24h auto-expire
            ))

    async def _assess_logic_intent(self, intent: Intent) -> float:
        """逻辑脑评估意图的相关性、价值和时机。"""
        # 快速关键词评估（无需 LLM）
        score = 0.5  # 默认中性

        # SEARCH: 相关性强
        if intent.action == IntentType.SEARCH:
            score += 0.2
        # SPEAK: 有价值
        elif intent.action == IntentType.SPEAK:
            score += 0.15
        # REMEMBER: 低优先级
        elif intent.action == IntentType.REMEMBER:
            score -= 0.1

        # 细节长度体现价值
        if len(intent.detail) > 20:
            score += 0.1

        return max(0.0, min(1.0, score))

    async def _assess_emotion_intent(self, intent: Intent) -> float:
        """情感脑评估意图的自然度和氛围。"""
        score = 0.5

        # SPEAK: 自然
        if intent.action == IntentType.SPEAK:
            score += 0.15
        # SEARCH: 偏中性
        elif intent.action == IntentType.SEARCH:
            score += 0.05
        # REMEMBER: 对维系关系有益
        elif intent.action == IntentType.REMEMBER:
            score += 0.1

        # 考虑当前情绪状态
        if self._emotion_engine:
            sub_state = self._emotion_engine.get_state("sub")
            if sub_state.joy > 0.5:
                score += 0.1

        return max(0.0, min(1.0, score))

    async def _dispatch_intent(self, intent: Intent) -> str | None:
        """根据意图类型分派执行。

        Returns:
            执行结果文本，失败返回 None
        """
        if intent.action == IntentType.SEARCH:
            result = await self._action_pool.submit(intent.detail)
            return result.output if result.success else None

        elif intent.action == IntentType.REMEMBER:
            # 直接写入记忆
            await self._memory.save(MemoryEntry(
                namespace="user/default/remembered",
                key=f"intent_remember_{int(time.time())}",
                value={
                    "content": intent.detail,
                    "source": "intent_extraction",
                    "timestamp": datetime.now().isoformat(),
                },
            ))
            return f"已记住: {intent.detail[:100]}"

        elif intent.action == IntentType.SPEAK:
            # 记录为待发送消息（下一次主动发言时使用）
            await self._memory.save(MemoryEntry(
                namespace="subconscious/nudges",
                key=f"intent_speak_{int(time.time())}",
                value={
                    "source": "intent_extraction",
                    "content": intent.detail,
                    "direction": "用户希望你说这个",
                },
            ))
            return f"已记录待发言: {intent.detail[:100]}"

        return None

    # ── Phase 7: 延迟意图检查 (T059) ────────────────────────

    async def _check_deferred_actions(self) -> None:
        """每次新 turn 开始时检查延迟意图。

        - 查询 subconscious/deferred_actions
        - 重新评估权重
        - > 0.5 → 执行 → 标记 resolved
        - 自动过期：24h（由 TTL 处理）
        """
        deferred = await self._memory.query("subconscious/deferred_actions", limit=10)
        now = datetime.now()

        for entry in deferred:
            value = entry.value
            if isinstance(value, dict) and value.get("status") == "deferred":
                # 检查 TTL 过期
                created_str = value.get("created_at", "")
                try:
                    created = datetime.fromisoformat(created_str)
                    if (now - created).total_seconds() > 86400:
                        # 过期，标记
                        await self._memory.save(MemoryEntry(
                            namespace="subconscious/deferred_actions",
                            key=entry.key,
                            value={**value, "status": "expired", "expired_at": now.isoformat()},
                        ))
                        continue
                except (ValueError, TypeError):
                    pass

                # 重新评估
                detail = value.get("detail", "")
                intent_type_str = value.get("intent_type", "")
                try:
                    intent_type = IntentType(intent_type_str)
                except ValueError:
                    intent_type = IntentType.NONE

                intent = Intent(
                    action=intent_type,
                    detail=detail,
                    confidence=0.5,
                )
                logic_score = await self._assess_logic_intent(intent)
                emotion_score = await self._assess_emotion_intent(intent)
                combined = logic_score * 0.5 + emotion_score * 0.5

                if combined > 0.5:
                    # 执行
                    intent.status = IntentStatus.EXECUTING
                    result = await self._dispatch_intent(intent)
                    # 标记为 resolved
                    await self._memory.save(MemoryEntry(
                        namespace="subconscious/deferred_actions",
                        key=entry.key,
                        value={
                            **value,
                            "status": "resolved",
                            "reassessed_weight": combined,
                            "result": result[:200] if result else "executed",
                            "resolved_at": now.isoformat(),
                        },
                    ))

    # ── Phase 7: 辅助方法 ────────────────────────────────────

    async def _assess_relevance(self, search_result: str, topic: str) -> float:
        """快速评估搜索结果与话题的相关性（基于关键词匹配）。"""
        if not search_result or not topic:
            return 0.0
        # 简单关键词匹配
        topic_chars = set(topic)
        result_chars = set(search_result[:500])
        overlap = len(topic_chars & result_chars)
        return min(1.0, overlap / max(len(topic_chars), 1) * 0.8)


def _enhance_recall(
    tools: ToolRegistry,
    memory: MemoryStore,
    personality_engine: PersonalityEngine | None = None,
) -> None:
    """替换子Session 的 recall 为真实记忆检索"""
    # 先移除已有的 recall（register_sub_session_tools 可能已注册）
    tools.unregister("recall")
    tools.register(ToolDefinition(
        name="recall",
        description="从记忆中检索相关信息。只读。",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        fn=lambda args, ctx: _recall_with_memory(args, memory, personality_engine),
        parallel_safe=True,
    ))


async def _recall_with_memory(
    args: dict,
    memory: MemoryStore,
    personality_engine: PersonalityEngine | None = None,
) -> str:
    query = str(args.get("query", ""))
    # 子Session 只能读 user/{uid}/* 和 short_term/*
    # loyalty 提升检索相关性：top_n 乘以 boost 因子
    base_top_n = 5
    if personality_engine:
        boost = personality_engine.get_memory_boost()
        base_top_n = max(5, int(base_top_n * boost))
    user_entries = await memory.search(query, namespace_prefix="user/default", top_n=base_top_n)
    short_entries = await memory.query("short_term", limit=3)
    results = user_entries + short_entries
    return json.dumps(
        [{"key": f"{e.namespace}/{e.key}", "value": e.value} for e in results],
        ensure_ascii=False,
    )
