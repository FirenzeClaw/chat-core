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
    AttentionEvent,
    AttentionStateEnum,
    ConversationTurn,
    CorrectionCmd,
    DecisionType,
    DefenseResult,
    DefenseType,
    InnerThought,
    IntentType,
    MemoryEntry,
    MetaParamOverrides,
    ReplySegment,
    ReviewResult,
    SilenceType,
    TurnStatus,
)
from chat_core.systems.memory import MemoryStore
from chat_core.systems.emotion import COMPOUND_DIMS, EmotionEngine
from chat_core.systems.personality import PersonalityEngine
from chat_core.systems.attention import AttentionModel
from chat_core.systems.defense import DefenseEngine
from chat_core.systems.review import ReviewSystem, extract_intent
from chat_core.systems.interest import InterestModel, SilenceAccumulator
from chat_core.systems.boredom import BoredomDetector
from chat_core.systems.energy import EnergyBar
from chat_core.systems.metacognition import MetacognitionEngine
from chat_core.systems.values import ValueEngine  # Spec 010
from chat_core.systems.narrative import NarrativeEngine  # Spec 010
from chat_core.systems.subjective_time import SubjectiveClock
from chat_core.systems.proactive import ProactiveSystem, _enhance_recall
from chat_core.systems.relationship import RelationshipEngine  # Spec 008
from chat_core.systems.group_dynamics import GroupDynamics  # Spec 008
from chat_core.systems.patterns import PatternDetector  # Spec 008
from chat_core.systems.creativity import CreativityEngine  # Spec 009
from chat_core.systems.humor import HumorDetector  # Spec 009
from chat_core.systems.moral import MoralConflictDetector, ProConAssessor  # Spec 009
from chat_core.systems.silence import SilenceClassifier  # Spec 011
from chat_core.systems.motivation import MotivationEngine  # Spec 011
from chat_core.systems.loneliness import LonelinessDetector  # Spec 011

import logging

logger = logging.getLogger(__name__)


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
        self._correction_depth = 0  # anti-recursion counter: max 2 levels (T003)

        # Spec 005: 防御机制
        self._defense_engine = DefenseEngine()
        self._defense_history: list[str] = []
        self._error_history: dict[str, int] = {}

        # Spec 007: 具身感知 — 精力条 + 主观时钟
        self._energy_bar = EnergyBar()
        self._subjective_clock = SubjectiveClock()

        # Phase 7: BoredomDetector, InterestModel, proactive system
        cfg = get_config()
        bc = cfg.boredom_config()
        ic = cfg.interest_config()

        # Spec 006: 元认知深度
        mc_cfg = cfg.metacognition_config()
        self._metacognition = MetacognitionEngine() if mc_cfg.get("enabled", True) else None
        self._meta_overrides = MetaParamOverrides()  # 容器始终存在（禁用时保持默认值）
        self._last_inner_thoughts: str | None = None
        self._had_defense_this_turn: bool = False

        # Spec 010: 价值体系 + 自我叙事
        vc_cfg = cfg.value_config()
        nc_cfg = cfg.narrative_config()
        self._value_engine = ValueEngine() if vc_cfg.get("enabled", True) else None
        self._narrative_engine = NarrativeEngine() if nc_cfg.get("enabled", True) else None

        # Spec 008: 社交与关系
        self._relationship_engine = RelationshipEngine()
        self._group_dynamics = GroupDynamics()
        self._group_dynamics.set_memory(memory)  # 注入 MemoryStore 供氛围持久化
        self._pattern_detector = PatternDetector()
        self._pattern_detector.set_memory(memory)  # 注入 MemoryStore 供中间态持久化
        self._current_user_id: str = "default"  # CLI 默认，QQ 模式由 adapter 覆写

        # Spec 009: 认知增强
        self._creativity_engine = CreativityEngine()
        self._humor_detector = HumorDetector()
        self._moral_conflict_detector = MoralConflictDetector()
        self._pro_con_assessor = ProConAssessor()

        # Spec 011: 沉默语义 + 动机系统
        self._silence_classifier = SilenceClassifier()
        self._motivation_engine = MotivationEngine()
        self._loneliness_detector = LonelinessDetector()

        self._interest_model = InterestModel(
            topic_trigger_threshold=int(ic.get("topic_trigger_threshold", 3)),
            topic_weight_increment=float(ic.get("topic_weight_increment", 0.1)),
            decay_per_hour=float(ic.get("decay_per_hour", 0.05)),
        )

        self._boredom_detector = BoredomDetector(
            attention_model=attention_model,
            subjective_clock=self._subjective_clock,
            energy_bar=self._energy_bar,
            emotion_engine=self._emotion_engine,
            interest_model=self._interest_model,
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
            reply_callback=self._reply_callback,
            stream_callback=self._stream_callback,
        )

        self._boredom_detector.set_on_trigger(self._proactive._on_boredom_trigger)
        self._boredom_detector.set_on_end_conversation(self._proactive._on_end_conversation_signal)

        # Spec 011: 注入动机引擎到主动系统
        self._proactive.set_motivation_engine(self._motivation_engine)

        # 流式回调（由 CLI 注入）
        self._stream_callback: Any = None
        self._reply_callback: Any = None

        # 注意力事件监听器延迟启动标记
        self._listeners_started: bool = False

    @property
    def event_bus(self) -> EventBus:
        return self._event_bus

    def set_stream_callback(self, cb: Any) -> None:
        """设置流式 LLM 输出回调（传递给子Session）"""
        self._stream_callback = cb

    def set_reply_callback(self, cb: Any) -> None:
        """设置 send_reply 回调（传递给子Session）"""
        self._reply_callback = cb

    def set_current_user_id(self, user_id: str) -> None:
        """设置当前 turn 的用户 ID（CLI 调用前设置 "default"，QQ adapter 设置 openid）"""
        self._current_user_id = user_id

    # ── 注意力事件监听（延迟启动）────────────────────────────

    def _ensure_listeners(self) -> None:
        """延迟启动 event_bus 监听器（首次 process_turn 调用时触发）"""
        if self._listeners_started:
            return
        self._listeners_started = True
        if self._attention_model:
            asyncio.create_task(self._listen_emotion_alerts())
            asyncio.create_task(self._listen_compound_alerts())

    async def _listen_emotion_alerts(self) -> None:
        """监听 emotion_alert + logic_conflict → AttentionModel.apply_event()"""
        alert_q = self._event_bus.subscribe("emotion_alert")
        conflict_q = self._event_bus.subscribe("logic_conflict")

        async def _handle_alert():
            while True:
                data = await alert_q.get()
                if self._attention_model:
                    self._attention_model.apply_event(
                        AttentionEvent.EMOTION_SHOCK, brain="sub"
                    )

        async def _handle_conflict():
            while True:
                data = await conflict_q.get()
                if self._attention_model:
                    # logic_conflict → 困惑/犹豫，focus -0.05
                    self._attention_model.boost("sub", -0.05)

        # 并行监听两个事件
        await asyncio.gather(_handle_alert(), _handle_conflict())

    async def _listen_compound_alerts(self) -> None:
        """监听 compound_alert → AttentionModel.apply_event()"""
        compound_q = self._event_bus.subscribe("compound_alert")
        while True:
            data = await compound_q.get()
            if self._attention_model:
                self._attention_model.apply_event(
                    "sub", AttentionEvent.EMOTION_SHOCK, boost=0.30,
                )

    async def _on_compound_alert(self, event: dict[str, Any]) -> None:
        if self._attention_model:
            self._attention_model.apply_event(
                "sub", AttentionEvent.EMOTION_SHOCK, boost=0.30,
            )

    # ── 主流程 ──────────────────────────────────────────────

    async def process_turn(self, user_message: str) -> ConversationTurn:
        """执行一个完整的 turn"""
        # 首次调用时启动事件监听器
        self._ensure_listeners()

        # Phase 7: 对话开始 → 停止无聊追踪，兴趣衰减，检查延迟意图
        self._boredom_detector.stop()
        self._interest_model.decay_all()
        await self._proactive._check_deferred_actions()

        # 对话开始时暂停情绪衰减
        if self._emotion_engine:
            self._emotion_engine.pause()

        self._turn_counter += 1

        # 注意力疲劳递增
        if self._attention_model:
            self._attention_model.increment_turn()
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
            # Spec 008: 应用关系阶段人格调制
            if self._personality_engine and self._relationship_engine:
                stage = self._relationship_engine.get_stage(self._current_user_id)
                modulation = self._relationship_engine.get_modulation(self._current_user_id)
                mod_params = self._personality_engine.apply_relationship_modulation(
                    stage=stage, modulation=modulation
                )
                self.runtime_state["relationship_modulation"] = mod_params
            replies, inner_thoughts = await self._run_sub_session(
                user_message, merged_context, merged_direction, logic_memories + emotion_memories
            )
            turn.reply_segments = [ReplySegment(text=r) for r in replies]
            turn.inner_thoughts_raw = inner_thoughts

            # 发言完成 → 注意力衰减 (FOCUSED -0.02/段, DRIFTING -0.03/段, DULL -0.01/段)
            if self._attention_model and replies:
                for _ in replies:
                    state_enum = self._attention_model.get_state_enum("sub")
                    if state_enum == AttentionStateEnum.DRIFTING:
                        self._attention_model.boost("sub", -0.03)
                    elif state_enum == AttentionStateEnum.DULL:
                        self._attention_model.boost("sub", -0.01)
                    else:
                        self._attention_model.boost("sub", -0.02)

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

            # T008: emotion_alert — 情感主脑检测到情绪变化时发布事件
            _mood_keywords = [
                "情绪", "心情", "情感", "mood", "波动", "变化",
                "开心", "难过", "生气", "紧张", "焦虑", "兴奋",
            ]
            if emotion_direction and any(
                kw in emotion_direction for kw in _mood_keywords
            ):
                await self._event_bus.publish("emotion_alert", {
                    "mood_shift": emotion_direction[:200],
                    "intensity": 0.5,
                })

            # T004: 审查异步化 — 不阻塞用户下一轮消息
            asyncio.create_task(
                self._async_review_and_decide(
                    replies, inner_thoughts,
                    logic_memories + emotion_memories,
                    user_message,
                )
            )

            # 同步归档对话 turn
            turn.status = TurnStatus.ARCHIVING
            await self._archive_turn(turn)

            # 对话结束事件 → 启动无聊追踪
            turn.status = TurnStatus.DONE
            eval_data = {
                "turn_id": turn.turn_id,
                "logic_eval": {"depth": 0.6, "satisfaction": 0.7},
                "emotion_weight": 0.5,  # 审查异步，使用默认值
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

            # Spec 007: 基础精力消耗（防御联动在 _apply_defense 内部异步触发）
            if self._energy_bar and replies:
                compound_delta = self._emotion_engine.last_compound_delta if self._emotion_engine else 0.0
                self._energy_bar.consume(
                    reply_count=len(replies),
                    compound_delta=compound_delta,
                )

            # Spec 005: 脆弱后效应 — 写入 aftermath 记忆，重置脆弱标志，恢复人格
            if self._emotion_engine and self._emotion_engine.is_vulnerable:
                # 找出触发脆弱的复合情绪
                sub_state = self._emotion_engine.get_state("sub")
                vuln_emotion = ""
                for dim in COMPOUND_DIMS:
                    if getattr(sub_state, dim, 0.0) >= 0.7:
                        vuln_emotion = dim
                        break
                # 写入脆弱回忆到 subconscious/defense_awareness（下一轮 _init_messages 读取）
                await self._memory.save(MemoryEntry(
                    namespace="subconscious/defense_awareness",
                    key=f"vulnerability_aftermath_{turn.turn_id}",
                    value={
                        "text": f"[脆弱回忆] 上一轮你在这段关系中暴露了脆弱（{vuln_emotion}）。你现在可能感到更亲近，也可能有点尴尬——取决于对方的反应。",
                        "type": "vulnerability_aftermath",
                    },
                ))
                self._emotion_engine.is_vulnerable = False  # 重置脆弱标志
                # Spec 010: 脆弱暴露 → 叙事章节
                if self._narrative_engine:
                    self._narrative_engine.append_chapter(
                        event_type="vulnerability",
                        text=f"我在对话中暴露了脆弱（{vuln_emotion}）。这对我来说不容易。",
                        turn=self._turn_counter,
                    )
                # 恢复人格权重（self_disclosure 调制清除）
                if self._personality_engine and hasattr(self, "_vuln_orig_empathy"):
                    self._personality_engine.weights.empathy = self._vuln_orig_empathy
                    self._personality_engine.weights.playfulness = self._vuln_orig_playfulness

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

        # Spec 005: 脆弱时刻 — 极端情绪触发行为调制
        if self._emotion_engine and self._emotion_engine.is_vulnerable:
            # 找出触发脆弱的复合情绪
            sub_state = self._emotion_engine.get_state("sub")
            vuln_emotion = ""
            for dim in COMPOUND_DIMS:
                if getattr(sub_state, dim, 0.0) >= 0.7:
                    vuln_emotion = dim
                    break
            runtime_state["vulnerability"] = (
                f"[脆弱时刻] 你现在情绪很强烈（{vuln_emotion}达到高水平）。"
                "你比平时更愿意分享真实感受。可以诚实表达你的不安、恐惧、或遗憾。"
                "但不要说教或过度分析——这是感受的时刻，不是分析的时刻。"
            )
            # self_disclosure ×2.0: 临时提升 empathy 和 playfulness
            if self._personality_engine:
                self._vuln_orig_empathy = self._personality_engine.weights.empathy
                self._vuln_orig_playfulness = self._personality_engine.weights.playfulness
                self._personality_engine.weights.empathy = min(1.0, self._personality_engine.weights.empathy * 2.0)
                self._personality_engine.weights.playfulness = min(1.0, self._personality_engine.weights.playfulness * 2.0)

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
            memory_store=self._memory,
            energy_bar=self._energy_bar,
            meta_overrides=self._meta_overrides,  # Spec 006
            narrative_engine=self._narrative_engine,  # Spec 010
        )
        register_sub_session_tools(tools, loop, attention_model=self._attention_model)

        # 传递流式回调
        if self._stream_callback:
            loop.set_stream_callback(self._stream_callback)
        if self._reply_callback:
            loop.set_reply_callback(self._reply_callback)

        # 增强 recall 工具 — 连接真实记忆存储
        _enhance_recall(tools, self._memory, self._personality_engine)

        # Spec 008: 注入关系阶段 + 社交模式
        user_id = self._current_user_id
        stage = self._relationship_engine.get_stage(user_id)
        description = self._relationship_engine.get_stage_description(stage)
        loop.set_relationship_context(user_id, stage.value, description)

        patterns_hint = self._pattern_detector.get_pattern_injection(user_id)
        if patterns_hint:
            loop.set_social_patterns(patterns_hint)

        # Spec 009: 创造力发散 + 幽默检测
        if self._creativity_engine.should_trigger(
            playfulness=self._personality_engine.weights.playfulness if self._personality_engine else 0.3,
            user_message=user_message,
        ):
            loop.set_creativity_engine(self._creativity_engine)
            # Path A: Flash LLM 概念发散
            path_a_mappings: list[str] = []
            path_b_summaries: list[str] = []
            try:
                pa_prompt = self._creativity_engine.build_path_a_prompt(user_message)
                pa_result = await self._provider.chat(
                    messages=[{"role": "user", "content": pa_prompt}],
                    temperature=0.8, max_tokens=256,
                )
                path_a_mappings = self._creativity_engine.parse_path_a_result(
                    pa_result.content if hasattr(pa_result, 'content') else str(pa_result)
                )
            except Exception:
                pass
            # Path B: 联锁放大
            try:
                extended_config = self._creativity_engine.get_extended_chain_config()
                pb_results = await self._memory.search_chained(user_message, extended_config)
                path_b_summaries = self._creativity_engine.filter_path_b_memories(pb_results)
            except Exception:
                pass
            injection = self._creativity_engine.build_injection(path_a_mappings, path_b_summaries)
            if injection:
                loop.set_creativity_context(injection)

        humor_ops = self._humor_detector.detect(user_message, stage)
        if humor_ops:
            hint = self._humor_detector.build_injection(humor_ops)
            if hint:
                loop.set_humor_hint(hint)

        # Spec 011: 动机注入 — 从 subconscious/motivations 读取当前动机
        try:
            m_entries = await self._memory.query("subconscious/motivations")
            for m_entry in m_entries:
                if m_entry.key == "current" and isinstance(m_entry.value, dict):
                    hint_text = m_entry.value.get("hint")
                    if hint_text:
                        loop.set_motivation_hint(hint_text)
                    break
        except Exception:
            pass  # 静默降级，动机注入失败不影响子Session

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
            meta_overrides=self._meta_overrides,  # Spec 006
            turn_counter=self._turn_counter,
            value_engine=self._value_engine,  # Spec 010
        )

    # ── 异步审查 + 决策 (T005) ───────────────────────────────

    async def _async_review_and_decide(
        self,
        replies: list[str],
        inner_thoughts: str | None,
        memories: list[MemoryEntry],
        user_message: str,
    ) -> None:
        """后台异步审查 + 权重协商 + 写 subconscious (T005).

        异常静默降级，不向上抛出。
        """
        try:
            review = await self._review(replies, inner_thoughts, memories, user_message)

            # 交换窗口 2：双脑交换审查结论
            await self._event_bus.publish(
                "logic_review",
                {"errors": review.logic_errors, "weight": review.logic_weight},
            )
            await self._event_bus.publish(
                "emotion_review",
                {"tone_issues": review.emotion_issues, "weight": review.emotion_weight},
            )

            # ── Spec 005: 防御判定 (仅 CORRECT 决策) ──
            if review.decision == DecisionType.CORRECT:
                impulsiveness = (
                    self._personality_engine.weights.impulsiveness
                    if self._personality_engine else 0.2
                )
                # 更新 error_history
                for e in review.logic_errors:
                    error_type_str = e.error_type.value if hasattr(e.error_type, 'value') else str(e.error_type)
                    self._error_history[error_type_str] = self._error_history.get(error_type_str, 0) + 1

                compound_delta = (
                    self._emotion_engine.last_compound_delta
                    if self._emotion_engine else 0.0
                )
                defense = self._defense_engine.evaluate(
                    review, self._error_history,
                    impulsiveness=impulsiveness,
                    last_compound_delta=compound_delta,
                    is_vulnerable=(
                        self._emotion_engine.is_vulnerable
                        if self._emotion_engine else False
                    ),
                    meta_overrides=self._meta_overrides,  # Spec 006
                    turn_counter=self._turn_counter,
                    value_engine=self._value_engine,  # Spec 010
                    relationship_modulation=(
                        self._relationship_engine.get_modulation(self._current_user_id)
                        if self._relationship_engine else None
                    ),
                )
                if defense.defense_type != DefenseType.DIRECT:
                    await self._apply_defense(defense, review, replies)
                    return  # 防御路径短路正常纠正流

            if review.decision == DecisionType.CORRECT:
                await self._issue_correction(review, replies)
            elif review.decision == DecisionType.TWISTED:
                await self._issue_correction(review, replies)
            elif review.decision == DecisionType.SILENCE:
                await self._silent_archive(review, replies)
            if review.decision == DecisionType.TWISTED:
                # T006: 拧巴记录 — logic overrides emotion
                turn_id = (
                    self._current_turn.turn_id
                    if self._current_turn else "unknown"
                )
                await self._memory.save(MemoryEntry(
                    namespace="self/feelings",
                    key=f"twisted_{turn_id}",
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
            # ── Spec 006: 元认知审视 ──
            # 在 _async_review_and_decide 结束后、异常处理前运行
            had_defense = getattr(self, "_had_defense_this_turn", False)
            self._had_defense_this_turn = False  # 每轮重置
            compound_delta = abs(self._emotion_engine.last_compound_delta) if self._emotion_engine else 0.0

            if self._metacognition is not None and self._metacognition.check_triggers(
                turn_counter=self._turn_counter,
                review_decision=review.decision,
                had_defense=had_defense,
                inner_thoughts_text=inner_thoughts,
                compound_delta=compound_delta,
            ):
                # 组装上下文
                turn_summaries = await self._build_turn_summaries()
                compound_trends = (
                    self._emotion_engine.get_compound_trend()
                    if self._emotion_engine else {}
                )
                defense_summary = self._build_defense_summary()
                memory_state = await self._build_memory_state()
                attention_label = (
                    self._attention_model.get_state_enum("sub").value
                    if self._attention_model else "unknown"
                )
                energy_state = self._energy_bar.get_state() if self._energy_bar else None
                energy_dict = {"energy": energy_state.energy} if energy_state else None
                stp = (
                    self._subjective_clock.get_perception(
                        energy_state.energy if energy_state else 0.9
                    )
                    if self._subjective_clock else None
                )
                stp_dict = {
                    "speed_factor": stp.speed_factor,
                    "perception": stp.perception,
                } if stp else None
                # Spec 005 §9: 脆弱历史
                vuln_history = None
                if self._emotion_engine:
                    vuln_history = {
                        "is_vulnerable": self._emotion_engine.is_vulnerable,
                        "cooldown_remaining": getattr(self._emotion_engine, "_vulnerability_cooldown", 0),
                    }

                # ── Spec 008/009/010/011: 扩展上下文 ──
                if self._relationship_engine:
                    stage_enum = self._relationship_engine.get_stage(self._current_user_id)
                    rel_stage = stage_enum.value if stage_enum else None
                else:
                    rel_stage = None

                if self._group_dynamics:
                    metrics = self._group_dynamics.get_metrics(self._current_user_id)
                    group_summary = vars(metrics) if metrics else None
                else:
                    group_summary = None

                moral_ctx = None
                if getattr(self._metacognition, "moral_escalation_pending", False):
                    moral_ctx = "道德冲突已升级到元认知审查"

                silence_pattern = None
                silence_total = sum(self._silence_counters.values())
                if silence_total > 0:
                    silence_pattern = f"连续沉默{silence_total}次"

                active_motivations = None
                if self._motivation_engine:
                    ms = self._motivation_engine.evaluate(
                        boredom=self._boredom_detector.get_boredom(),
                        energy=self._energy_bar._state.energy,
                        loneliness=(
                            self._loneliness_detector.current
                            if self._loneliness_detector else 0.0
                        ),
                        value_weights=(
                            vars(self._value_engine.values)
                            if self._value_engine else None
                        ),
                    )
                    active_motivations = self._motivation_engine.build_injection(ms)

                value_state = (
                    vars(self._value_engine.values)
                    if self._value_engine else None
                )
                narrative_text = (
                    self._narrative_engine.state.latest
                    if self._narrative_engine else None
                )

                context = self._metacognition.build_context(
                    turn_summaries=turn_summaries,
                    compound_trends=compound_trends,
                    defense_mode_summary=defense_summary,
                    memory_system_state=memory_state,
                    attention_state=attention_label,
                    energy_state=energy_dict,
                    subjective_time=stp_dict,
                    vulnerability_history=vuln_history,
                    value_state=value_state,
                    narrative_text=narrative_text,
                    relationship_stage=rel_stage,
                    group_role_summary=group_summary,
                    moral_conflict_context=moral_ctx,
                    silence_pattern=silence_pattern,
                    active_motivations=active_motivations,
                )

                report = await self.logic.metacognition_pass(context)
                if report:
                    import time as _time
                    timestamp = _time.strftime("%Y%m%d_%H%M%S")
                    await self._memory.save(MemoryEntry(
                        namespace="self/metacognition",
                        key=f"insight_{self._turn_counter}_{timestamp}",
                        value={
                            "insight_text": report.insight_text,
                            "confidence": report.confidence,
                            "turn": self._turn_counter,
                        },
                    ))
                    # Spec 006: 元认知洞察同步写入 subconscious
                    if report.insight_text:
                        await self._memory.save(MemoryEntry(
                            namespace="subconscious/metacognition_insight",
                            key="latest",
                            value={"insight_text": report.insight_text, "confidence": report.confidence},
                        ))
                    self._meta_overrides.apply(report, self._turn_counter)
                    if self._emotion_engine:
                        self._emotion_engine.set_meta_overrides(self._meta_overrides)
                    # Spec 010: 元认知发现防御 → self_honesty↑ (仅当本轮确有防御)
                    if self._value_engine and had_defense:
                        self._value_engine.adjust("metacognition_defense")

            # ── Spec 010: 定期自我叙事生成 ──
            if self._narrative_engine and self._turn_counter % self._narrative_engine._periodic_interval == 0:
                ctx = self._narrative_engine.build_narrative_context(
                    value_engine=self._value_engine
                )
                text = await self.logic.narrative_pass(ctx)
                if text:
                    self._narrative_engine.update_latest(text)
                    await self._memory.save(MemoryEntry(
                        namespace="self/narrative",
                        key="latest",
                        value={"narrative": text, "turn": self._turn_counter},
                    ))

            # ── Spec 009: 道德困境检测 ──
            moral_conflict = self._moral_conflict_detector.detect(
                user_message=user_message,
                inner_thoughts=inner_thoughts,
                relationship_stage=stage,
                energy=self._energy_bar._state.energy,
            )
            if moral_conflict:
                # 双脑 Pro/Con
                conflict_context = f"困境: {moral_conflict.trigger_description}\n用户消息: {user_message}"
                logic_score, logic_reason = await self.logic.pro_con(conflict_context)
                emotion_score, emotion_reason = await self.emotion.pro_con(conflict_context)
                moral_bias = (
                    self._value_engine.get_modulation("moral_bias")
                    if self._value_engine else None
                )
                assessment = self._pro_con_assessor.assess(
                    logic_score, logic_reason, emotion_score, emotion_reason,
                    moral_bias=moral_bias,
                )
                # 归档
                await self._memory.save(MemoryEntry(
                    namespace=f"self/moral/{self._turn_counter}",
                    key="assessment",
                    value={
                        "conflict_type": moral_conflict.conflict_type.value,
                        "logic_score": assessment.logic_score,
                        "emotion_score": assessment.emotion_score,
                        "recommended_path": assessment.recommended_path,
                        "deadlock": assessment.deadlock,
                        "escalation": assessment.escalation,
                    },
                ))
                # 升级到元认知
                if assessment.escalation and self._metacognition:
                    self._metacognition.moral_escalation_pending = True
                # 两难 → 写入 subconscious
                if assessment.deadlock:
                    await self._memory.save(MemoryEntry(
                        namespace="subconscious/moral_conflict",
                        key=str(self._turn_counter),
                        value={
                            "path": "deadlock",
                            "logic": assessment.logic_reasoning,
                            "emotion": assessment.emotion_reasoning,
                        },
                    ))

            # ── Spec 008: 更新关系 + 群氛围 + 检测模式 ──
            user_id = self._current_user_id

            if self._relationship_engine.enabled:
                recall_hit_count = len(memories)
                ai_valence = 0.0
                if self._emotion_engine:
                    ai_state = self._emotion_engine.get_state("sub")
                    ai_valence = getattr(ai_state, "valence", 0.0)

                self._relationship_engine.update(
                    user_id=user_id,
                    recall_hit_count=recall_hit_count,
                    combined_review_weight=review.combined_weight if review else 1.0,
                    inner_thoughts_text=self._last_inner_thoughts or "",
                    user_message=user_message,
                    correction_accepted=(review.decision == DecisionType.CORRECT),
                    memory_entry_count=len(memories),
                    user_emotion_valence=0.0,
                    ai_emotion_valence=ai_valence,
                )

            if self._emotion_engine and self._relationship_engine:
                stage_enum = self._relationship_engine.get_stage(user_id)
                self._emotion_engine.set_relationship_stage(
                    stage_enum.value if stage_enum else None
                )

            # Spec 008: 群氛围情绪聚合（从 inner_thoughts → user_read.mood 反推）
            if self._group_dynamics.enabled and self._current_turn:
                parsed = self._current_turn.inner_thoughts_parsed
                if parsed and parsed.user_read and parsed.user_read.mood:
                    self._group_dynamics.record_emotion_snapshot(
                        group_id=user_id,  # 群聊时为 group_id，私聊时退化为 user_id
                        emotion_state={"mood": parsed.user_read.mood},
                    )

            if self._pattern_detector.enabled:
                await self._pattern_detector.detect(
                    user_id=user_id,
                    user_message=user_message,
                    inner_thoughts_text=self._last_inner_thoughts or "",
                )

            # ── Spec 011: 动机评估 ──
            # 孤独检测（主观时钟驱动）
            loneliness = self._loneliness_detector.tick(
                wall_dt=60,  # 每 turn 约 60s
                relationships=[(self._current_user_id, self._relationship_engine.get_stage(self._current_user_id).value)]
                if self._relationship_engine else [],
                subjective_speed=self._subjective_clock.speed_factor if self._subjective_clock else 1.0,
            )
            # 子Session 困惑度（从情绪引擎读取）
            sub_confusion = self._emotion_engine.get_state("sub").confusion if self._emotion_engine else 0.0
            sub_anger = self._emotion_engine.get_state("sub").anger if self._emotion_engine else 0.0
            # 动机评估
            motivation_state = self._motivation_engine.evaluate(
                boredom=self._boredom_detector.get_boredom() if self._boredom_detector else 0,
                energy=self._energy_bar._state.energy,
                loneliness=loneliness,
                confusion=sub_confusion,
                unexpressed_anger=sub_anger,
                value_weights={
                    "growth": self._value_engine.values.growth,
                    "care": self._value_engine.values.care,
                    "honesty": self._value_engine.values.honesty,
                    "self_improvement": self._value_engine.values.self_improvement,
                } if self._value_engine else None,
            )
            hint = self._motivation_engine.build_injection(motivation_state)
            await self._memory.save(MemoryEntry(
                namespace="subconscious/motivations",
                key="current",
                value={"state": motivation_state.__dict__, "hint": hint},
            ))

        except Exception:
            logger.exception("Async review failed, silently degraded")

    # ── 纠正 ──────────────────────────────────────────────────

    async def _issue_correction(
        self, review: ReviewResult, replies: list[str]
    ) -> CorrectionCmd | None:
        """Issue correction or silent archive based on combined weight (T006).

        - combined > 0.5: write correction directive to subconscious (deferred to next turn)
        - combined ≤ 0.5: silent archive (T014)
        - Anti-recursion guard: max 2 levels (T003)
        """
        # Anti-recursion guard: max 2 levels (T003)
        if self._correction_depth > 2:
            logger.warning(
                f"Correction depth {self._correction_depth} exceeds limit (2), skipping correction"
            )
            return None

        combined = review.combined_weight

        if combined <= 0.5:
            # T014: Silent archive — write to self/noticed, increment accumulator
            await self._silent_archive(review, replies)
            return None

        # combined > 0.5: write correction to subconscious (deferred to next turn)
        # T006: 不再立即运行纠正子Session，纠正延迟到下一轮自动触发
        self._correction_depth += 1
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

            is_twisted = review.decision == DecisionType.TWISTED
            source = (
                "logic" if review.logic_weight >= review.emotion_weight else "emotion"
            )

            return CorrectionCmd(
                source=source,
                message="Correction written to subconscious (deferred to next turn)",
                written_to=f"subconscious/corrections/{correction_key}",
                is_twisted=is_twisted,
            )
        finally:
            self._correction_depth -= 1

    # ── 纠正子Session (T036) ─────────────────────────────────

    async def _run_correction_sub_session(
        self,
        review: ReviewResult,
        original_replies: list[str],
    ) -> str:
        """Run a full-capability correction sub-session (T007).

        max_iter=5. Tools: recall(subconscious), send_reply, wait, inner_thoughts, done.
        Archives inner_thoughts to self/inner_thoughts/ and send_reply to conversations.
        Integrates _correction_depth check (>2 skip).
        """
        # T007: Anti-recursion guard
        if self._correction_depth > 2:
            logger.warning(
                f"Correction sub-session skipped: depth {self._correction_depth} > 2"
            )
            return ""

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
            "3. 用 send_reply 发送修正后的回复（可以多段）\n"
            "4. 可以调用 wait 制造自然的停顿节奏\n"
            "5. 完成后先调用 inner_thoughts 记录纠正内心戏，再调用 done\n"
        )

        # Full tool registry for correction
        correction_tools = ToolRegistry()

        # recall — restricted to subconscious namespace
        correction_tools.register(
            self._build_correction_recall_tool()
        )

        # send_reply — multi-segment (no limit)
        correction_replies: list[str] = []

        def _send_reply_correction(args: dict, ctx: Any) -> str:
            text = str(args.get("text", ""))
            if len(text) > 500:
                text = text[:500]
            correction_replies.append(text)
            return json.dumps({"sent": True, "text": text})

        correction_tools.register(
            ToolDefinition(
                name="send_reply",
                description="发送修正后的回复。可以调用多次，模拟自然的多段发言。",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "修正后的回复内容，≤500字"}
                    },
                    "required": ["text"],
                },
                fn=_send_reply_correction,
                parallel_safe=False,
            )
        )

        # wait tool (T007: restored)
        def _wait_correction(args: dict, ctx: Any) -> str:
            seconds = float(args.get("seconds", 1.0))
            if seconds > 10:
                seconds = 10.0
            return json.dumps({"waited": seconds})

        correction_tools.register(
            ToolDefinition(
                name="wait",
                description="在回复之间等待指定秒数，制造自然的停顿节奏。≤10秒。",
                parameters={
                    "type": "object",
                    "properties": {
                        "seconds": {"type": "number", "description": "等待秒数, ≤10"}
                    },
                    "required": ["seconds"],
                },
                fn=_wait_correction,
                parallel_safe=False,
            )
        )

        # inner_thoughts tool (T007: restored)
        correction_inner_thoughts: list[str] = []

        def _inner_thoughts_correction(args: dict, ctx: Any) -> str:
            text = str(args.get("text", ""))
            correction_inner_thoughts.append(text)
            return json.dumps({"recorded": True, "text": text})

        correction_tools.register(
            ToolDefinition(
                name="inner_thoughts",
                description="记录纠正过程的内心戏（纯文本）。调用后不代表结束。",
                parameters={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "内心戏文本"}
                    },
                    "required": ["text"],
                },
                fn=_inner_thoughts_correction,
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

        # Run full ReAct loop (max_iter=5, T007)
        sub_config = SubSessionConfig(max_iter=5)
        loop = ReActLoop(
            provider=self._provider,
            tool_registry=correction_tools,
            system_prompt=system_prompt,
            config=sub_config,
            energy_bar=self._energy_bar,
        )
        if self._stream_callback:
            loop.set_stream_callback(self._stream_callback)
        correction_task = "请根据上述错误审查结果，修正原始发言。"
        await loop.run(correction_task)

        # T007: Archive correction content
        turn_id = self._current_turn.turn_id if self._current_turn else "unknown"

        if correction_inner_thoughts:
            await self._memory.save(MemoryEntry(
                namespace="self/inner_thoughts",
                key=f"correction_{turn_id}",
                value={"raw": " ".join(correction_inner_thoughts)},
            ))

        if correction_replies:
            await self._memory.save(MemoryEntry(
                namespace="user/default/conversations",
                key=f"correction_{turn_id}",
                value={
                    "user_message": f"[纠正发言 - turn {turn_id}]",
                    "reply": " ".join(correction_replies),
                    "turn_id": f"correction_{turn_id}",
                },
            ))

        return (
            " ".join(correction_replies)
            if correction_replies
            else (" ".join(loop.replies) if loop.replies else "")
        )

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

    # ── 防御执行 (Spec 005) ──────────────────────────────────

    async def _apply_defense(
        self, defense: DefenseResult, review: ReviewResult, replies: list[str]
    ) -> None:
        """执行防御裁定。

        三种防御路径的核心行为：
        - DENIAL: 不写 correction, silence_increment=1
        - RATIONALIZE: 写 correction 含辩护文本, silence_increment=0
        - PROJECT: 写偏向 correction, 情绪偏移
        所有路径：归档 self/defenses + subconscious/defense_awareness
        """
        turn_id = self._current_turn.turn_id if self._current_turn else "unknown"

        # 归档 defense 记录到 self/defenses
        await self._memory.save(MemoryEntry(
            namespace="self/defenses",
            key=f"defense_{turn_id}",
            value={
                "defense_type": defense.defense_type.value,
                "reflection": defense.inner_reflection,
                "original_errors": [e.description for e in review.logic_errors],
            },
        ))

        # 写入 defense_awareness（下一轮 _init_messages 读取）
        if defense.defense_awareness:
            await self._memory.save(MemoryEntry(
                namespace="subconscious/defense_awareness",
                key=f"awareness_{turn_id}",
                value={
                    "text": defense.defense_awareness,
                    "defense_type": defense.defense_type.value,
                },
            ))

        # DENIAL: 不写 correction
        # RATIONALIZE/PROJECT: 写 correction
        if defense.correction_text:
            await self._memory.save(MemoryEntry(
                namespace="subconscious/corrections",
                key=f"correction_{turn_id}",
                value={
                    "logic_errors": [e.description for e in review.logic_errors],
                    "combined_weight": review.combined_weight,
                    "defense_note": defense.correction_text,
                },
            ))

        # 情绪调整 (支持复合维度 — set_dimension 已在 Spec 005 扩展)
        if defense.emotion_delta and self._emotion_engine:
            for dim, delta in defense.emotion_delta.items():
                try:
                    self._emotion_engine.accelerate("sub", dim, delta)
                except ValueError:
                    pass  # 未知维度静默跳过

        # Spec 007: 防御联动精力消耗
        if self._energy_bar:
            if defense.defense_type == DefenseType.DENIAL:
                self._energy_bar.consume(has_defense_denial=True)
            elif defense.defense_type == DefenseType.PROJECT:
                self._energy_bar.consume(has_defense_project=True)

        # 沉默累积器
        if defense.silence_increment > 0:
            self._silence_accumulator.increment("defense_denial")

        # Spec 006: 标记本轮有防御（供元认知触发判定）
        self._defense_history.append(defense.defense_type.value)
        self._had_defense_this_turn = True

    # ── 沉默归档 (T037) ─────────────────────────────────────

    async def _silent_archive(
        self,
        review: ReviewResult,
        replies: list[str],
    ) -> None:
        """Silent archive: combined ≤ 0.5 → write to self/noticed, increment accumulator."""
        turn_id = self._current_turn.turn_id if self._current_turn else "unknown"
        reply_text = " ".join(replies)

        # Spec 011: SilenceClassifier 语义化判定
        silence_record = self._silence_classifier.classify(
            review=review,
            emotion=self._emotion_engine.get_state("sub") if self._emotion_engine else None,
            energy=self._energy_bar._state.energy,
            relationship_stage=self._relationship_engine.get_stage(self._current_user_id) if self._relationship_engine else None,
            silence_streak=sum(self._silence_counters.values()),
            active_turns=self._turn_counter,
        )
        increment = self._silence_classifier.get_silence_increment(silence_record.silence_type)
        if increment > 0:
            self._silence_accumulator.increment(silence_record.silence_type.value)
        if silence_record.silence_type == SilenceType.OVERLOAD:
            self._energy_bar.boost_recovery(self._silence_classifier.get_recovery_boost())
        if (silence_record.silence_type == SilenceType.ANGRY
                and self._emotion_engine):
            self._emotion_engine.accelerate("sub", "resentment", 0.05)
        await self._memory.save(MemoryEntry(
            namespace="self/silences",
            key=str(self._turn_counter),
            value={"type": silence_record.silence_type.value, "reasoning": silence_record.reasoning},
            entity_type="silence_record",
            salience=5.0,
        ))
        # Spec 011: silence streak → 叙事事件
        silence_streak_count = sum(self._silence_counters.values())
        if silence_streak_count >= 3 and self._narrative_engine:
            self._narrative_engine.append_chapter(
                "silence_streak",
                f"连续{silence_streak_count}次沉默，最近一次原因: {silence_record.reasoning}",
                self._turn_counter,
            )

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

        # Spec 007: 附加主观时间感知
        if self._subjective_clock:
            fatigue = self._energy_bar.get_state().energy
            stp = self._subjective_clock.get_perception(fatigue)
            summary["subjective_time_perception"] = {
                "speed_factor": stp.speed_factor,
                "perception": stp.perception,
                "description": stp.description,
                "fatigue_at_end": stp.fatigue_at_end,
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

    # ── Spec 006: 元认知辅助 ──────────────────────────────

    async def _build_turn_summaries(self) -> list[dict[str, Any]]:
        """从 memory 查询最近 N 轮摘要（self/inner_thoughts + user/default/conversations）。"""
        summaries: list[dict[str, Any]] = []
        try:
            interval = self._metacognition._periodic_interval if self._metacognition else 5
            thoughts = await self._memory.query("self/inner_thoughts", limit=interval)
            conversations = await self._memory.query("user/default/conversations", limit=interval)
            for i, t in enumerate(thoughts):
                conv = conversations[i] if i < len(conversations) else None
                summaries.append({
                    "turn": t.key,
                    "inner_thoughts_excerpt": str(t.value.get("raw", ""))[:200] if isinstance(t.value, dict) else "",
                    "reply_excerpt": str(conv.value.get("reply", ""))[:200] if conv and isinstance(conv.value, dict) else "",
                })
        except Exception:
            pass
        return summaries

    def _build_defense_summary(self) -> dict[str, Any]:
        try:
            recent = self._defense_history[-10:] if self._defense_history else []
            unique = list(dict.fromkeys(recent))
            return {
                "activation_rate": round(len(self._defense_history) / max(1, self._turn_counter), 2),
                "main_types": ", ".join(unique[-3:]) if unique else "无",
                "error_counts": dict(self._error_history),
            }
        except Exception:
            return {"activation_rate": 0.0, "main_types": "无", "awareness_entries": []}

    async def _build_memory_state(self) -> dict[str, Any]:
        try:
            deep_count = 0
            decay_warnings = 0
            entries = await self._memory.query("self/inner_thoughts", limit=50)
            for e in entries:
                curve = getattr(e, "decay_curve", "standard")
                sal = getattr(e, "salience", 10.0)
                if curve == "deep":
                    deep_count += 1
                if sal < 3.0:
                    decay_warnings += 1
            return {
                "total_entries": len(entries),
                "deep_memory_count": deep_count,
                "decay_warning_count": decay_warnings,
            }
        except Exception:
            return {"total_entries": 0, "deep_memory_count": 0, "decay_warning_count": 0}


