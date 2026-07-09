"""TurnManager — 编排一个完整 Turn 的流程 (Phase 7: proactive + intent)"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from chat_core.config import get_config
from chat_core.core.brain import ActionBrainPool, EmotionBrain, LogicBrain
from chat_core.core.loop import ReActLoop, SubSessionConfig, register_sub_session_tools
from chat_core.core.provider import ModelProvider
from chat_core.core.prompt_engine import PromptEngine
from chat_core.core.tools import ToolDefinition, ToolRegistry
from chat_core.core.types import (
    ConversationTurn,
    CorrectionCmd,
    DecisionType,
    InnerThought,
    IntentType,
    MemoryEntry,
    ReplySegment,
    ReviewResult,
    TurnStatus,
)
from chat_core.systems.memory import MemoryStore
from chat_core.systems.emotion import EmotionEngine
from chat_core.systems.personality import PersonalityEngine
from chat_core.systems.attention import AttentionModel
from chat_core.systems.review import ReviewSystem, extract_intent
from chat_core.systems.interest import InterestModel, SilenceAccumulator
from chat_core.systems.boredom import BoredomDetector
from chat_core.systems.proactive import ProactiveSystem, _enhance_recall


class EventBus:
    """内部消息总线 — 三窗口交换"""

    def __init__(self):
        self._queues: dict[str, asyncio.Queue] = {}

    def subscribe(self, event: str) -> asyncio.Queue:
        if event not in self._queues:
            self._queues[event] = asyncio.Queue()
        return self._queues[event]

    async def publish(self, event: str, data: Any) -> None:
        if event in self._queues:
            await self._queues[event].put(data)


class TurnManager:
    """协调完整 turn 流程：双脑 recall → inject → 子Session → review → 决策"""

    def __init__(
        self,
        logic_brain: LogicBrain,
        emotion_brain: EmotionBrain,
        provider: ModelProvider,
        memory: MemoryStore,
        prompt_engine: PromptEngine,
        action_pool: ActionBrainPool,
        emotion_engine: EmotionEngine | None = None,
        personality_engine: PersonalityEngine | None = None,
        attention_model: AttentionModel | None = None,
    ):
        self.logic = logic_brain
        self.emotion = emotion_brain
        self._provider = provider
        self._memory = memory
        self._prompt_engine = prompt_engine
        self._action_pool = action_pool
        self._event_bus = EventBus()

        self._emotion_engine = emotion_engine
        self._personality_engine = personality_engine
        self._attention_model = attention_model

        self._turn_counter = 0
        self._current_turn: ConversationTurn | None = None

        # 沉默累积器
        self._silence_counters: dict[str, int] = {}

        # Phase 5: Review system and silence accumulator
        self._review_system = ReviewSystem(provider, memory)
        self._silence_accumulator = SilenceAccumulator()
        self._in_correction = False  # anti-recursion guard (T036)

        # Phase 7: BoredomDetector, InterestModel, proactive system
        cfg = get_config()
        bc = cfg.boredom_config()
        ic = cfg.interest_config()

        self._boredom_detector = BoredomDetector()
        self._interest_model = InterestModel(
            topic_trigger_threshold=int(ic.get("topic_trigger_threshold", 3)),
            topic_weight_increment=float(ic.get("topic_weight_increment", 0.1)),
            decay_per_hour=float(ic.get("decay_per_hour", 0.05)),
        )

        self._proactive = ProactiveSystem(
            provider=provider,
            memory=memory,
            prompt_engine=prompt_engine,
            action_pool=action_pool,
            interest_model=self._interest_model,
            review_system=self._review_system,
            emotion_engine=emotion_engine,
            personality_engine=personality_engine,
            attention_model=attention_model,
            correct_fn=self._issue_correction,
        )

        self._boredom_detector.set_on_trigger(self._proactive._on_boredom_trigger)
        self._boredom_detector.set_on_end_conversation(self._proactive._on_end_conversation_signal)

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    # ── 主流程 ──────────────────────────────────────────────

    async def process_turn(self, user_message: str) -> ConversationTurn:
        """执行一个完整的 turn"""
        # Phase 7: 对话开始 → 停止无聊追踪，兴趣衰减，检查延迟意图
        self._boredom_detector.stop()
        self._interest_model.decay_all()
        await self._proactive._check_deferred_actions()

        # 对话开始时暂停情绪衰减
        if self._emotion_engine:
            self._emotion_engine.pause()

        self._turn_counter += 1
        turn = ConversationTurn(
            turn_id=f"turn_{self._turn_counter:03d}",
            user_message=user_message,
        )
        self._current_turn = turn

        try:
            # 1. 双脑并行 recall
            turn.status = TurnStatus.DUAL_RECALL
            logic_memories, logic_direction, emotion_memories, emotion_direction = await self._dual_recall(user_message)

            # 交换窗口 1：双脑交换检索结果
            await self._event_bus.publish("logic_recall", {"facts": logic_memories, "direction": logic_direction})
            await self._event_bus.publish("emotion_recall", {"feelings": emotion_memories, "direction": emotion_direction})

            # 2. 双脑各自 inject
            turn.status = TurnStatus.INJECTING
            logic_injection, emotion_injection = await asyncio.gather(
                self.logic.think_inject(user_message, logic_memories, logic_direction),
                self.emotion.think_inject(user_message, emotion_memories, emotion_direction),
            )
            turn.logic_injection = logic_injection
            turn.emotion_injection = emotion_injection

            # 合并注入
            merged_context = f"{logic_injection.get('context', '')} {emotion_injection.get('context', '')}"
            merged_direction = f"{logic_injection.get('direction', '')} {emotion_injection.get('direction', '')}"

            # 3. 子Session ReAct
            turn.status = TurnStatus.SUB_SESSION
            replies, inner_thoughts = await self._run_sub_session(
                user_message, merged_context, merged_direction, logic_memories + emotion_memories
            )
            turn.reply_segments = [ReplySegment(text=r) for r in replies]
            turn.inner_thoughts_raw = inner_thoughts

            # Phase 7: 提取话题 → InterestModel，提取意图 → 执行
            if inner_thoughts:
                self._proactive._record_topics_from_thoughts(inner_thoughts)
                intent = extract_intent(inner_thoughts, self._provider)
                if intent.action != IntentType.NONE:
                    turn.inner_thoughts_parsed = InnerThought(
                        raw=inner_thoughts,
                        intent=intent,
                    )
                    await self._proactive._execute_intent(intent)

            # 4. 双脑审查
            turn.status = TurnStatus.REVIEWING
            review = await self._review(replies, inner_thoughts, logic_memories, user_message)
            turn.review = review

            # 交换窗口 2：双脑交换审查结论
            await self._event_bus.publish("logic_review", {"errors": review.logic_errors, "weight": review.logic_weight})
            await self._event_bus.publish("emotion_review", {"tone_issues": review.emotion_issues, "weight": review.emotion_weight})

            # 5. 权重决策
            turn.status = TurnStatus.DECIDING
            if review.decision == DecisionType.CORRECT:
                turn.status = TurnStatus.CORRECTING
                correction = await self._issue_correction(review, replies)
                turn.correction = correction
            elif review.decision == DecisionType.TWISTED:
                turn.status = TurnStatus.CORRECTING
                correction = await self._issue_correction(review, replies)
                turn.correction = correction
                # T038: 拧巴记录 — logic overrides emotion
                await self._memory.save(MemoryEntry(
                    namespace="self/feelings",
                    key=f"twisted_{turn.turn_id}",
                    value={
                        "context": f"逻辑权重={review.logic_weight:.2f}，情感权重={review.emotion_weight:.2f}",
                        "logic_decision": "纠正（逻辑脑主导）",
                        "logic_weight": review.logic_weight,
                        "emotion_dissent": f"情感脑认为问题轻微（weight={review.emotion_weight:.2f}）",
                        "emotion_weight": review.emotion_weight,
                        "resolution": "按逻辑执行纠正",
                        "logic_errors": [e.description for e in review.logic_errors],
                        "emotion_aftermath": "轻微不适",
                    },
                ))

            # 6. 归档
            turn.status = TurnStatus.ARCHIVING
            await self._archive_turn(turn)

            # 对话结束事件 → 启动无聊追踪
            turn.status = TurnStatus.DONE
            eval_data = {
                "turn_id": turn.turn_id,
                "logic_eval": {"depth": 0.6, "satisfaction": 0.7},
                "emotion_weight": review.emotion_weight if review else 0.5,
                "timestamp": time.time(),
            }
            await self._event_bus.publish("conversation_ended", eval_data)

            # Phase 7: 启动无聊检测器
            eval_param = (eval_data["logic_eval"]["satisfaction"] + eval_data["logic_eval"]["depth"]) / 2
            impulsiveness = (
                self._personality_engine.weights.impulsiveness
                if self._personality_engine else 0.2
            )
            # 获取当前最佳兴趣权重
            top_interests = self._interest_model.get_top_interests(3)
            interest_weight = top_interests[0][1] if top_interests else 0.0
            self._boredom_detector.start(eval_param, interest_weight, impulsiveness)

        finally:
            # 对话结束后恢复情绪衰减
            if self._emotion_engine:
                self._emotion_engine.resume()

        return turn

    # ── 双脑并行 recall ────────────────────────────────────

    async def _dual_recall(self, user_message: str) -> tuple[
        list[MemoryEntry], str,
        list[MemoryEntry], str,
    ]:
        (logic_mem, logic_dir), (emotion_mem, emotion_dir) = await asyncio.gather(
            self.logic.think_pre(user_message),
            self.emotion.think_pre(user_message),
        )
        return logic_mem, logic_dir, emotion_mem, emotion_dir

    # ── 子Session 运行 ─────────────────────────────────────

    async def _run_sub_session(
        self,
        user_message: str,
        context: str,
        direction: str,
        memories: list[MemoryEntry],
    ) -> tuple[list[str], str | None]:
        """创建并运行子Session"""
        cfg = get_config()

        # 构建运行时状态（注入情绪 & 注意力）
        runtime_state: dict[str, Any] = {
            "context": context,
            "direction": direction,
            "relevant_memories": [
                json.dumps(m.value, ensure_ascii=False)[:200] for m in memories[:3]
            ],
        }

        # 注入情绪状态
        if self._emotion_engine:
            emotion_text = self._emotion_engine.get_emotion_summary("sub")
            if emotion_text:
                runtime_state["emotion"] = f"[情绪状态] {emotion_text}"

        # 注入注意力状态
        if self._attention_model:
            attn = self._attention_model.get_state("sub")
            runtime_state["attention"] = {"focus": attn.focus}

        # 注入共情模式
        if self._personality_engine:
            mode = self._personality_engine.get_response_mode()
            if mode == "empathetic":
                runtime_state["direction"] = (
                    f"{direction} 【共情模式：注意用户情绪，表达理解和关心】"
                )

        system_prompt = self._prompt_engine.build_sub_session_prompt(runtime_state)

        tools = ToolRegistry()

        # 应用人格温度到子Session 配置
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

        # 增强 recall 工具 — 连接真实记忆存储
        _enhance_recall(tools, self._memory, self._personality_engine)

        await loop.run(user_message)
        return loop.replies, loop.inner_thoughts

    # ── 审查 ──────────────────────────────────────────────────

    async def _review(
        self,
        replies: list[str],
        inner_thoughts: str | None,
        memories: list[MemoryEntry],
        user_message: str,
    ) -> ReviewResult:
        """双脑审查子Session 产出 — delegates to ReviewSystem (Phase 5)."""
        return await self._review_system.review(
            replies=replies,
            inner_thoughts=inner_thoughts,
            memories=memories,
            user_message=user_message,
        )

    # ── 纠正 ──────────────────────────────────────────────────

    async def _issue_correction(
        self, review: ReviewResult, replies: list[str]
    ) -> CorrectionCmd | None:
        """Issue correction or silent archive based on combined weight (T036-T038).

        - combined > 0.5: write correction directive → run correction sub-session
        - combined ≤ 0.5: silent archive (T037)
        - Anti-recursion guard: correction sessions do NOT trigger new cycles
        """
        # Anti-recursion guard (T036)
        if self._in_correction:
            return None

        combined = review.combined_weight

        if combined <= 0.5:
            # T037: Silent archive — increment silence accumulator
            await self._silent_archive(review, replies)
            return None

        # combined > 0.5: active correction
        self._in_correction = True
        try:
            turn_id = self._current_turn.turn_id if self._current_turn else "unknown"
            correction_key = f"correction_{turn_id}"

            # Write correction directive to subconscious/corrections
            error_descriptions = [e.description for e in review.logic_errors]
            issue_descriptions = [i.description for i in review.emotion_issues]

            await self._memory.save(MemoryEntry(
                namespace="subconscious/corrections",
                key=correction_key,
                value={
                    "logic_errors": error_descriptions,
                    "tone_issues": issue_descriptions,
                    "combined_weight": combined,
                    "logic_weight": review.logic_weight,
                    "emotion_weight": review.emotion_weight,
                    "decision": review.decision.value,
                    "original_replies": replies,
                },
            ))

            # Run correction sub-session (single-pass, max_iter=2)
            correction_text = await self._run_correction_sub_session(review, replies)

            is_twisted = review.decision == DecisionType.TWISTED
            source = (
                "logic" if review.logic_weight >= review.emotion_weight else "emotion"
            )

            return CorrectionCmd(
                source=source,
                message=correction_text or "Correction applied",
                written_to=f"subconscious/corrections/{correction_key}",
                is_twisted=is_twisted,
            )
        finally:
            self._in_correction = False

    # ── 纠正子Session (T036) ─────────────────────────────────

    async def _run_correction_sub_session(
        self,
        review: ReviewResult,
        original_replies: list[str],
    ) -> str:
        """Run a correction sub-session: single-pass, max_iter=2, no inner_thoughts.

        Tools: recall (subconscious only), send_reply (max 1), done.
        The sub-session receives the correction context and generates
        a corrected version of the reply.
        """
        original_text = " ".join(original_replies)

        # Build correction system prompt
        error_summary_parts: list[str] = []
        for e in review.logic_errors:
            error_summary_parts.append(
                f"- [{e.error_type.value}] {e.description} (weight={e.weight:.2f})"
            )
        for i in review.emotion_issues:
            error_summary_parts.append(
                f"- [tone:{i.issue_type.value}] {i.description} (weight={i.weight:.2f})"
            )
        error_summary = "\n".join(error_summary_parts) if error_summary_parts else "无"

        system_prompt = (
            "你是小深的纠正脑。你的职责是根据审查结果修正发言。\n\n"
            f"【原始发言】\n{original_text}\n\n"
            f"【发现的错误】\n{error_summary}\n\n"
            "【要求】\n"
            "1. 修正所有事实错误和语气问题\n"
            "2. 保持自然的口语风格\n"
            "3. 用 send_reply 发送修正后的发言（只能调用1次）\n"
            "4. 完成后调用 done\n"
            "5. 不要调用 inner_thoughts"
        )

        # Build a limited tool registry for correction
        correction_tools = ToolRegistry()

        # recall — restricted to subconscious namespace
        correction_tools.register(
            self._build_correction_recall_tool()
        )

        # send_reply — enforce max 1 call
        send_count = [0]  # mutable counter

        def _send_reply_once(args: dict, ctx: Any) -> str:
            if send_count[0] >= 1:
                return json.dumps({"sent": False, "error": "send_reply limit reached (max 1 for correction)"})
            send_count[0] += 1
            text = str(args.get("text", ""))
            if len(text) > 500:
                text = text[:500]
            return json.dumps({"sent": True, "text": text})

        correction_tools.register(
            ToolDefinition(
                name="send_reply",
                description="发送修正后的回复。只能调用1次。",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "修正后的回复内容，≤500字"}
                    },
                    "required": ["text"],
                },
                fn=_send_reply_once,
                parallel_safe=False,
            )
        )

        # done
        correction_tools.register(
            ToolDefinition(
                name="done",
                description="确认修正完成。",
                parameters={"type": "object", "properties": {}},
                fn=lambda args, ctx: json.dumps({"correction_complete": True}),
                parallel_safe=False,
            )
        )

        # Run mini ReAct loop (max_iter=2)
        sub_config = SubSessionConfig(max_iter=2)
        loop = ReActLoop(
            provider=self._provider,
            tool_registry=correction_tools,
            system_prompt=system_prompt,
            config=sub_config,
        )
        correction_task = "请根据上述错误审查结果，修正原始发言。"
        await loop.run(correction_task)

        return " ".join(loop.replies) if loop.replies else ""

    def _build_correction_recall_tool(self) -> ToolDefinition:
        """Build a recall tool that only reads from subconscious namespace."""

        async def _recall_subconscious(args: dict, ctx: Any) -> str:
            query = str(args.get("query", ""))
            entries = await self._memory.query("subconscious", limit=10)
            # Filter: only return corrections
            corrections = [
                {"key": f"{e.namespace}/{e.key}", "value": e.value}
                for e in entries
            ]
            return json.dumps({"results": corrections, "count": len(corrections)}, ensure_ascii=False)

        return ToolDefinition(
            name="recall",
            description="从潜意识记忆中检索纠正历史。只读，仅访问 subconscious 命名空间。",
            parameters={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            fn=_recall_subconscious,
            parallel_safe=True,
        )

    # ── 沉默归档 (T037) ─────────────────────────────────────

    async def _silent_archive(
        self,
        review: ReviewResult,
        replies: list[str],
    ) -> None:
        """Silent archive: combined ≤ 0.5 → write to self/noticed, increment accumulator."""
        turn_id = self._current_turn.turn_id if self._current_turn else "unknown"
        reply_text = " ".join(replies)

        # Collect all error/issue types for silence accumulation
        for e in review.logic_errors:
            error_type_key = e.error_type.value
            self._silence_accumulator.increment(error_type_key)
            # Update legacy counter for backward compat
            self._silence_counters[error_type_key] = (
                self._silence_counters.get(error_type_key, 0) + 1
            )

        for i in review.emotion_issues:
            tone_key = i.issue_type.value
            self._silence_accumulator.increment(tone_key)
            self._silence_counters[tone_key] = (
                self._silence_counters.get(tone_key, 0) + 1
            )

        # Write observation to self/noticed
        await self._memory.save(MemoryEntry(
            namespace="self/noticed",
            key=f"noticed_{turn_id}",
            value={
                "turn_id": turn_id,
                "logic_weight": review.logic_weight,
                "emotion_weight": review.emotion_weight,
                "combined_weight": review.combined_weight,
                "decision": review.decision.value,
                "logic_errors": [e.description for e in review.logic_errors],
                "tone_issues": [i.description for i in review.emotion_issues],
                "reply_excerpt": reply_text[:300],
                "silence_bases": self._silence_accumulator.all_bases,
            },
        ))

    # ── 归档 ──────────────────────────────────────────────────

    async def _archive_turn(self, turn: ConversationTurn) -> None:
        """归档对话 turn 到记忆"""
        summary = {
            "user_message": turn.user_message,
            "reply": " ".join([s.text for s in turn.reply_segments]),
            "inner_thoughts": turn.inner_thoughts_raw,
            "turn_id": turn.turn_id,
        }
        await self._memory.save(MemoryEntry(
            namespace="user/default/conversations",
            key=turn.turn_id,
            value=summary,
            topic_tags=[],
        ))

        # 如果有内心戏，归档到 self/
        if turn.inner_thoughts_raw:
            await self._memory.save(MemoryEntry(
                namespace="self/inner_thoughts",
                key=turn.turn_id,
                value={"raw": turn.inner_thoughts_raw},
            ))


