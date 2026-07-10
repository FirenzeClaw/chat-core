"""TurnManager 适配器 — QQ 消息 ↔ 全局双主脑 + 多子 Session 管线

BotAdapter 是 QQ Bot 与 chat-core 核心引擎之间的桥梁：
- 全局双主脑 (LogicBrain + EmotionBrain) 共享
- Per-conversation 子 Session (ReActLoop) 复用
- 竞态追踪 + 潜意识注入调节
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from chat_core.config import get_config
from chat_core.core.brain import ActionBrainPool, EmotionBrain, LogicBrain
from chat_core.core.loop import ReActLoop, SubSessionConfig, register_sub_session_tools
from chat_core.core.provider import ModelProvider
from chat_core.core.prompt_engine import PromptEngine
from chat_core.core.tools import ToolRegistry
from chat_core.core.types import MemoryEntry, Message, RecallChainConfig, SUB_SESSION_CHAIN_CONFIG
from chat_core.systems.memory import MemoryStore
from chat_core.systems.emotion import EmotionEngine
from chat_core.systems.personality import PersonalityEngine
from chat_core.systems.attention import AttentionModel
from chat_core.systems.interest import InterestModel
from chat_core.systems.review import ReviewSystem
from chat_core.systems.boredom import BoredomDetector
from chat_core.systems.proactive import ProactiveSystem
from chat_core.qq.protocol import MessageContext, fetch_user_nickname, send_message
from chat_core.qq.sessions import SessionManager, UserSession
from chat_core.qq.race_tracker import RaceTracker
from chat_core.qq.subconscious import SubconsciousInjector

logger = logging.getLogger("chat_core.qq.adapter")


class BotAdapter:
    """QQ Bot 适配器：全局双主脑 + 每对话者子 Session + 竞态追踪。"""

    def __init__(
        self,
        provider: ModelProvider,
        memory_store: MemoryStore,
        prompt_engine: PromptEngine,
        personality_engine: PersonalityEngine | None = None,
        emotion_engine: EmotionEngine | None = None,
        logic_brain: LogicBrain | None = None,
        emotion_brain: EmotionBrain | None = None,
        race_tracker: RaceTracker | None = None,
        subconscious_injector: SubconsciousInjector | None = None,
        attention_model: AttentionModel | None = None,
        qq_appid: str = "",
        qq_secret: str = "",
    ):
        self._provider = provider
        self._memory = memory_store
        self._prompt_engine = prompt_engine
        self._personality_engine = personality_engine
        self._emotion_engine = emotion_engine
        self._logic_brain = logic_brain
        self._emotion_brain = emotion_brain
        self._race_tracker = race_tracker or RaceTracker()
        self._subconscious = subconscious_injector or SubconsciousInjector()
        self._attention_model = attention_model or AttentionModel()

        self._sub_sessions: dict[str, ReActLoop] = {}
        self._sessions = SessionManager()
        self._user_locks: dict[str, asyncio.Lock] = {}

        # QQ Bot 回复开关 (FR-22)
        qq_cfg = get_config().qq_config()
        self._c2c_reply_enabled: bool = qq_cfg.get("c2c_reply_enabled", True)
        self._group_reply_enabled: bool = qq_cfg.get("group_reply_enabled", True)
        self._passive_observe_enabled: bool = qq_cfg.get("passive_observe", True)

        # ── ProactiveSystem (FR-19, FR-20) ─────────────────
        self._qq_appid = qq_appid
        self._qq_secret = qq_secret

        # ActionBrainPool
        self._action_pool = ActionBrainPool(
            max_concurrent=get_config().brain_max_concurrent("action"),
        )
        self._action_pool.configure(provider, memory_store, prompt_engine)

        # InterestModel (话题追踪)
        ic = get_config().interest_config()
        self._interest_model = InterestModel(
            topic_trigger_threshold=int(ic.get("topic_trigger_threshold", 3)),
            topic_weight_increment=float(ic.get("topic_weight_increment", 0.1)),
            decay_per_hour=float(ic.get("decay_per_hour", 0.05)),
        )

        # ReviewSystem
        self._review_system = ReviewSystem(provider, memory_store)

        # BoredomDetector
        self._boredom_detector = BoredomDetector()

        # ProactiveSystem (FR-19)
        self._proactive = ProactiveSystem(
            provider=provider,
            memory=memory_store,
            prompt_engine=prompt_engine,
            action_pool=self._action_pool,
            interest_model=self._interest_model,
            review_system=self._review_system,
            emotion_engine=emotion_engine,
            personality_engine=personality_engine,
            attention_model=self._attention_model,
            reply_callback=self._send_proactive_message,
        )

        self._boredom_detector.set_on_trigger(self._proactive._on_boredom_trigger)
        self._boredom_detector.set_on_end_conversation(self._proactive._on_end_conversation_signal)

        # 主动发言频控状态 (FR-20)
        self._last_active_ctx: MessageContext | None = None
        self._last_proactive_send: float = 0.0
        self._proactive_min_interval: float = float(
            qq_cfg.get("proactive_min_interval", 60.0)
        )
        self._proactive_backoff: float = 1.0
        self._proactive_consecutive_failures: int = 0

    # ── 公共接口 ────────────────────────────────────────────

    async def process_message(
        self, ctx: MessageContext,
        send_fn: Any = None,
    ) -> list[str]:
        """处理一条 QQ 消息。send_fn 传入时，send_reply 逐段直接发送。"""
        # 私聊开关关闭 → 静默跳过
        if not ctx.is_group and not self._c2c_reply_enabled:
            return []
        # 群@开关关闭 → 静默跳过
        if ctx.is_group and ctx.is_at and not self._group_reply_enabled:
            return []
        # 群聊旁听 (非@普通消息)
        if ctx.is_group and not ctx.is_at and not ctx.is_direct:
            if self._passive_observe_enabled:
                await self._passive_observe(ctx)
            return []

        key = ctx.session_key
        if key not in self._user_locks:
            self._user_locks[key] = asyncio.Lock()
        async with self._user_locks[key]:
            self._race_tracker.enter()
            try:
                return await self._process(ctx, send_fn)
            finally:
                self._race_tracker.exit()

    async def _process(self, ctx: MessageContext, send_fn: Any = None) -> list[str]:
        user_session = self._sessions.get_or_create(ctx.user_id, ctx.session_key)

        if user_session.turn_counter == 0:
            await self._ensure_profile(ctx)

        # 追踪最近活跃用户（用于主动发言定向）
        self._last_active_ctx = ctx

        # 新对话开始 → 停止无聊检测，检查延迟意图 (FR-19)
        self._on_conversation_started()
        await self._proactive._check_deferred_actions()

        # 复用或创建子 Session
        loop = self._get_or_create_sub_session(ctx.session_key, ctx.user_id, ctx.scene)

        # 双主脑 recall + inject 异步启动（不阻塞子 Session）
        severity = self._race_tracker.severity
        inject_task = asyncio.create_task(
            self._inject_async(loop, ctx, severity)
        )

        # send_reply 回调 → 逐段直接发 QQ
        segments: list[str] = []

        async def _on_reply(text: str) -> None:
            segments.append(text)
            if send_fn:
                try:
                    await send_fn(text)
                except Exception:
                    logger.exception("send_fn 失败")

        loop.set_reply_callback(_on_reply)

        logger.info(
            "开始处理: user=%s session=%s turn=%d severity=%s",
            ctx.user_id[:12], ctx.session_key, user_session.turn_counter, severity,
        )

        try:
            await loop.run(ctx.content)
        except Exception:
            logger.exception("子 Session 异常: user=%s", ctx.user_id[:12])
            return ["[系统提示] 小深暂时无法回复，请稍后再试。"]

        # 等待双脑注入完成（如果还没完），保证归档时可取到
        await inject_task

        segs = list(loop.replies) if loop.replies else segments
        if not segs:
            return []

        await self._archive(ctx, "\n".join(segs), user_session, loop)
        user_session.turn_counter += 1
        user_session.touch()

        # 异步提取用户事实（不阻塞 turn 完成）
        asyncio.create_task(self._extract_facts(ctx, "\n".join(segs)))

        # 话题追踪: 从内心戏提取话题 (FR-19)
        if loop.inner_thoughts:
            self._proactive._record_topics_from_thoughts(loop.inner_thoughts)

        # 对话结束 → 启动无聊检测 (FR-19)
        self._on_conversation_ended()

        logger.info(
            "Turn 完成: user=%s turn=%d segments=%d",
            ctx.user_id[:12], user_session.turn_counter, len(segs),
        )
        return segs

    # ── 子 Session 复用 ─────────────────────────────────────

    def _get_or_create_sub_session(
        self, session_key: str, user_id: str = "", scene: str = "c2c",
    ) -> ReActLoop:
        """获取或创建子 Session。复用同一对话者的实例以保留上下文。
        
        Spec 003: 为子Session 构造 RecallChainConfig，限制 namespace 为 user/{uid}/* + short_term/*。
        """
        if session_key in self._sub_sessions:
            return self._sub_sessions[session_key]

        cfg = get_config()
        sub_config = SubSessionConfig(
            max_iter=cfg.brain_max_iter("sub_session"),
            temperature=self._personality_engine.get_llm_temperature("sub_session")
            if self._personality_engine else None,
        )
        system_prompt = self._prompt_engine.build_sub_session_prompt()
        tools = ToolRegistry()
        loop = ReActLoop(
            provider=self._provider,
            tool_registry=tools,
            system_prompt=system_prompt,
            config=sub_config,
            attention_model=AttentionModel(),
        )

        # Spec 003: 构造子Session 联锁配置
        chain_config = RecallChainConfig(
            top_n=SUB_SESSION_CHAIN_CONFIG.top_n,
            extensions=list(SUB_SESSION_CHAIN_CONFIG.extensions),
            max_per_level=SUB_SESSION_CHAIN_CONFIG.max_per_level,
            namespace_prefix=f"user/{user_id}" if user_id else None,
        )
        register_sub_session_tools(tools, loop, self._memory, chain_config=chain_config)

        from chat_core.systems.proactive import _enhance_recall
        _enhance_recall(tools, self._memory, self._personality_engine)

        self._sub_sessions[session_key] = loop
        return loop

    # ── 双主脑 recall ───────────────────────────────────────

    async def _dual_recall(self, user_message: str, ctx: MessageContext) -> str:
        """全局双主脑：think_pre (recall) + think_inject (tag + context)。"""
        if not self._logic_brain or not self._emotion_brain:
            return ""
        try:
            (logic_mem, logic_dir), (emotion_mem, emotion_dir) = await asyncio.gather(
                self._logic_brain.think_pre(user_message),
                self._emotion_brain.think_pre(user_message),
            )
            # Step 2: think_inject — EmotionBrain 在此阶段写入情感标签
            logic_inj, emotion_inj = await asyncio.gather(
                self._logic_brain.think_inject(user_message, logic_mem, logic_dir),
                self._emotion_brain.think_inject(user_message, emotion_mem, emotion_dir),
            )
            parts: list[str] = []
            for inj in [logic_inj, emotion_inj]:
                if isinstance(inj, dict) and inj.get("context"):
                    parts.append(str(inj["context"]))
            return " | ".join(parts) if parts else f"{logic_dir or ''} {emotion_dir or ''}".strip()
        except Exception:
            logger.exception("双主脑 recall 失败")
            return ""

    # ── 异步注入 ──────────────────────────────────────────

    async def _inject_async(
        self, loop: ReActLoop, ctx: MessageContext, severity: str,
    ) -> None:
        """后台执行双脑 recall + inject，结果注入子 Session 消息历史。"""
        try:
            context = await self._dual_recall(ctx.content, ctx)
            modulated = self._subconscious.inject(context, severity)
            if modulated:
                # 追加到消息历史末尾（子 Session 下次 _think() 可见）
                loop._messages.append(
                    Message(role="system", content=f"[主脑提示] {modulated}")
                )
        except Exception:
            logger.exception("异步注入失败: user=%s", ctx.user_id[:12])

    # ── 用户画像 ────────────────────────────────────────────

    async def _ensure_profile(self, ctx: MessageContext) -> None:
        try:
            nickname = await fetch_user_nickname(ctx.user_id)
            if nickname:
                await self._memory.save(MemoryEntry(
                    namespace=f"user/{ctx.user_id}/profile",
                    key="nickname",
                    value={"nickname": nickname, "source": "qq_api"},
                ))
                logger.info("用户画像已创建: user=%s", ctx.user_id[:12])
        except Exception:
            logger.exception("用户画像创建失败: user=%s", ctx.user_id[:12])

    # ── 归档 ────────────────────────────────────────────────

    async def _archive(
        self, ctx: MessageContext, reply: str,
        user_session: UserSession, loop: ReActLoop,
    ) -> None:
        scene = ctx.scene
        ns = (
            f"user/{ctx.user_id}/group/{ctx.group_id}/conversations"
            if scene == "group"
            else f"user/{ctx.user_id}/c2c/conversations"
        )
        summary = {
            "user_message": ctx.content,
            "reply": reply,
            "inner_thoughts": loop.inner_thoughts,
            "turn_id": f"{ctx.session_key}_turn_{user_session.turn_counter:03d}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self._memory.save(MemoryEntry(
                namespace=ns,
                key=f"turn_{user_session.turn_counter:03d}",
                value=summary,
            ))
        except Exception:
            logger.exception("归档失败: ns=%s", ns)

        if loop.inner_thoughts:
            try:
                await self._memory.save(MemoryEntry(
                    namespace="self/inner_thoughts",
                    key=f"{ctx.session_key}_turn_{user_session.turn_counter:03d}",
                    value={"raw": loop.inner_thoughts, "user_id": ctx.user_id},
                ))
            except Exception:
                pass

    # ── 事实提取 ────────────────────────────────────────────

    async def _extract_facts(self, ctx: MessageContext, reply: str) -> None:
        """异步从对话中提取用户事实，写入 user/{uid}/facts。"""
        if not self._logic_brain:
            return
        try:
            import json as _json
            prompt = (
                "从以下对话中提取关于用户的事实信息。只输出 JSON 数组，每个元素包含 fact（事实描述，一句话）和 category（分类：name/occupation/preference/habit/other）。"
                f"\n\n用户: {ctx.content}\nAI: {reply[:200]}"
            )
            response = await self._provider.chat(
                messages=[Message(role="user", content=prompt)],
                model="deepseek-v4-flash",
                max_tokens=256,
                temperature=0.1,
            )
            content = response.content
            facts = _json.loads(content) if content.strip().startswith("[") else []
            if isinstance(facts, list):
                for f in facts[:3]:
                    await self._memory.save(MemoryEntry(
                        namespace=f"user/{ctx.user_id}/facts",
                        key=f.get("category", "other"),
                        value={"fact": f.get("fact", ""), "source": "conversation"},
                    ))
                logger.info("事实提取: user=%s count=%d", ctx.user_id[:12], len(facts))
        except Exception:
            logger.debug("事实提取失败: user=%s", ctx.user_id[:12])

    # ── 群聊旁听 ────────────────────────────────────────────

    async def _passive_observe(self, ctx: MessageContext) -> None:
        try:
            summary = json.dumps({
                "date": datetime.now(timezone.utc).isoformat(),
                "summary": f"[群聊观察] {ctx.content[:100]}",
                "source": "group",
                "group_id": ctx.group_id,
            }, ensure_ascii=False)
            await self._memory.save(MemoryEntry(
                namespace=f"user/{ctx.user_id}/group/{ctx.group_id}/observations",
                key=datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
                value={"content": summary},
            ))
            logger.debug("旁听已记录: user=%s", ctx.user_id[:12])
        except Exception:
            pass

    # ── 主动发言 (FR-19, FR-20, FR-10) ──────────────────────

    async def _send_proactive_message(self, text: str) -> None:
        """ProactiveSystem 的 reply_callback：将主动发言发送给 QQ 用户。

        FR-20: 遵守 QQ 频控
        FR-10: 竞态 >= medium 时降级（延迟而非丢弃）
        """
        # T041: 竞态降级 — race_severity >= medium 时延迟
        severity = self._race_tracker.severity
        if severity != "low":
            logger.info(
                "主动发言因竞态降级 (severity=%s)，延迟发送",
                severity,
            )
            # 写入 nudge 以在竞态缓解后重试
            try:
                await self._memory.save(MemoryEntry(
                    namespace="subconscious/nudges",
                    key=f"deferred_proactive_{int(time.time())}",
                    value={
                        "source": "proactive_deferred",
                        "content": text[:500],
                        "severity": severity,
                        "direction": "竞态缓解后发送",
                    },
                ))
            except Exception:
                pass
            return

        # T040: QQ 频控检查
        now = time.time()
        elapsed = now - self._last_proactive_send
        if elapsed < self._proactive_min_interval:
            wait_time = self._proactive_min_interval - elapsed
            logger.debug("主动发言频控等待 %.1fs", wait_time)
            await asyncio.sleep(wait_time)

        # 发送给最近活跃的用户
        if self._last_active_ctx is None:
            logger.debug("无活跃用户，跳过主动发言")
            return

        ctx = self._last_active_ctx
        try:
            success = await send_message(
                ctx, text,
                appid=self._qq_appid,
                secret=self._qq_secret,
            )
            if success:
                self._proactive_consecutive_failures = 0
                self._proactive_backoff = 1.0
            else:
                self._proactive_consecutive_failures += 1
                self._proactive_backoff = min(
                    300.0,
                    self._proactive_backoff * 2.0,
                )
        except Exception:
            logger.exception("主动发言发送失败: user=%s", ctx.user_id[:12])
            self._proactive_consecutive_failures += 1

        self._last_proactive_send = time.time()
        logger.info("主动发言已发送: user=%s", ctx.user_id[:12])

        # 连续失败时延长间隔
        if self._proactive_consecutive_failures >= 3:
            self._proactive_min_interval = min(
                600.0,
                self._proactive_min_interval * 1.5,
            )
            logger.warning(
                "主动发言连续失败 %d 次，间隔延长至 %.0fs",
                self._proactive_consecutive_failures,
                self._proactive_min_interval,
            )

    def _on_conversation_ended(self) -> None:
        """对话 turn 结束后启动无聊检测。

        使用当前情绪和人格参数初始化 BoredomDetector。
        """
        if self._boredom_detector.is_active:
            return

        # 评估对话质量
        eval_param = 0.5  # 默认值
        if self._emotion_engine:
            sub_state = self._emotion_engine.get_state("sub")
            eval_param = (sub_state.joy + sub_state.trust) / 2

        # 兴趣权重
        interest_weight = 0.0
        top_interests = self._interest_model.get_top_interests(3)
        if top_interests:
            interest_weight = top_interests[0][1]

        # 冲动性
        impulsiveness = (
            self._personality_engine.weights.impulsiveness
            if self._personality_engine else 0.2
        )

        self._boredom_detector.start(eval_param, interest_weight, impulsiveness)
        logger.debug("无聊检测已启动: eval=%.2f interest=%.2f impulsiveness=%.2f",
                     eval_param, interest_weight, impulsiveness)

    def _on_conversation_started(self) -> None:
        """新对话开始 — 停止无聊检测，检查延迟意图。"""
        self._boredom_detector.stop()

    def cleanup_expired(self) -> int:
        cleaned = self._sessions.cleanup_expired()
        for key in list(self._sub_sessions):
            if not self._sessions.get(key):
                del self._sub_sessions[key]
                cleaned += 1
        return cleaned

    def status(self) -> dict:
        return {
            "active_sessions": self._sessions.session_count,
            "active_sub_sessions": len(self._sub_sessions),
            "race_severity": self._race_tracker.severity,
            "race_count": self._race_tracker.active_count,
        }
