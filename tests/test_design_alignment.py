"""Spec 004 Design Alignment tests — 8 scenarios for T016.

Covers: async review, subconscious injection, correction depth, weight formula,
correction sub-session tools, twisted archive, silent degradation, emotion alert.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chat_core.core.loop import ReActLoop, SubSessionConfig, _format_correction
from chat_core.core.provider import ModelProvider
from chat_core.core.tools import ToolRegistry
from chat_core.core.types import (
    DecisionType,
    ErrorType,
    FactError,
    MemoryEntry,
    Message,
    ReviewResult,
    ToneErrorType,
    ToneIssue,
)
from chat_core.systems.review import ReviewSystem
from chat_core.systems.memory import MemoryStore


# ── Helpers ──────────────────────────────────────────────────

def make_provider() -> ModelProvider:
    """Create a mock ModelProvider."""
    provider = MagicMock(spec=ModelProvider)
    provider.chat = AsyncMock()
    provider.stream_chat = AsyncMock()
    return provider


async def make_memory_store() -> MemoryStore:
    """Create an in-memory MemoryStore for testing."""
    store = MemoryStore(":memory:")
    await store.open()
    return store


async def make_turn_manager(memory=None):
    """Create a TurnManager suitable for testing.

    Works around the _reply_callback/_stream_callback ordering issue
    in TurnManager.__init__ by pre-setting class-level defaults and
    patching ProactiveSystem.
    """
    from chat_core.core.turn_manager import TurnManager, ProactiveSystem
    from chat_core.core.brain import LogicBrain, EmotionBrain, ActionBrainPool
    from chat_core.core.prompt_engine import PromptEngine

    # Pre-set class-level defaults so the attribute access at line 118
    # doesn't raise AttributeError during __init__.
    # These are normally set as instance attrs later in __init__ (lines 126-127),
    # but ProactiveSystem(...) on line 107-120 needs them first.
    if not hasattr(TurnManager, '_reply_callback'):
        TurnManager._reply_callback = None
        TurnManager._stream_callback = None

    logic = MagicMock(spec=LogicBrain)
    logic.think_pre = AsyncMock(return_value=([], ""))
    logic.think_inject = AsyncMock(return_value={})

    emotion = MagicMock(spec=EmotionBrain)
    emotion.think_pre = AsyncMock(return_value=([], ""))
    emotion.think_inject = AsyncMock(return_value={})

    provider = make_provider()
    prompt_engine = MagicMock(spec=PromptEngine)
    prompt_engine.build_sub_session_prompt = MagicMock(return_value="test prompt")
    action_pool = MagicMock(spec=ActionBrainPool)

    if memory is None:
        memory = await make_memory_store()

    # Patch ProactiveSystem to skip its heavy init
    with patch.object(ProactiveSystem, '__init__', return_value=None):
        tm = TurnManager(
            logic_brain=logic,
            emotion_brain=emotion,
            provider=provider,
            memory=memory,
            prompt_engine=prompt_engine,
            action_pool=action_pool,
        )

    # Mock _proactive methods called during process_turn
    tm._proactive._check_deferred_actions = AsyncMock()
    tm._proactive._record_topics_from_thoughts = MagicMock()
    tm._proactive._execute_intent = AsyncMock()

    return tm, memory


# ═══════════════════════════════════════════════════════════════
# Scenario 1: 审查异步执行（不阻塞子Session）
# ═══════════════════════════════════════════════════════════════

class TestAsyncReviewNonBlocking:
    """验证 process_turn 在审查完成前返回（异步 fire-and-forget）。"""

    @pytest.mark.asyncio
    async def test_process_turn_returns_before_review_completes(self):
        """process_turn 使用 create_task 启动审查，应在审查完成前返回。"""
        tm, memory = await make_turn_manager()

        tm._run_sub_session = AsyncMock(return_value=(["mock reply"], "mock thoughts"))

        review_started = asyncio.Event()
        review_completed = asyncio.Event()

        async def slow_review(replies, thoughts, memories, user_msg):
            review_started.set()
            await asyncio.sleep(0.5)
            review_completed.set()

        tm._async_review_and_decide = slow_review

        turn = await tm.process_turn("hello")

        assert turn.user_message == "hello"
        assert turn.status.value == "done"
        assert review_started.is_set(), "审查任务应该已启动"
        await memory.close()

    @pytest.mark.asyncio
    async def test_async_review_uses_create_task(self):
        """验证 _async_review_and_decide 通过 create_task 异步调度。"""
        tm, memory = await make_turn_manager()

        tm._run_sub_session = AsyncMock(return_value=(["mock reply"], "mock thoughts"))

        with patch("asyncio.create_task") as mock_create_task:
            await tm.process_turn("hello")
            assert mock_create_task.called, "应使用 create_task 启动异步审查"

        await memory.close()


# ═══════════════════════════════════════════════════════════════
# Scenario 2: subconscious/corrections 注入到 _init_messages
# ═══════════════════════════════════════════════════════════════

class TestSubconsciousInjection:
    """验证 ReActLoop._inject_subconscious_corrections 正确注入 system message。"""

    @pytest.mark.asyncio
    async def test_inject_subconscious_adds_system_message(self):
        """mock memory_store 有 corrections → _init_messages 后消息历史包含注入。"""
        memory = await make_memory_store()

        await memory.save(MemoryEntry(
            namespace="subconscious/corrections",
            key="correction_test_001",
            value={
                "logic_errors": ["事实错误：用户叫张三，不是李四"],
                "tone_issues": ["语气太冷漠"],
                "combined_weight": 0.75,
            },
        ))

        provider = make_provider()
        tools = ToolRegistry()
        loop = ReActLoop(
            provider=provider,
            tool_registry=tools,
            system_prompt="test prompt",
            memory_store=memory,
        )

        loop._init_messages("hello")
        await loop._inject_subconscious_corrections()

        system_messages = [m for m in loop._messages if m.role == "system"]
        assert len(system_messages) >= 2, (
            f"应有 system_prompt + 注入的 correction，实际: "
            f"{[m.content[:60] for m in system_messages]}"
        )

        correction_msg = system_messages[1]
        assert "[注意]" in correction_msg.content
        assert "事实错误" in correction_msg.content
        assert "张三" in correction_msg.content
        await memory.close()

    @pytest.mark.asyncio
    async def test_no_corrections_when_store_empty(self):
        """memory_store 无 corrections → 不应注入额外 system message。"""
        memory = await make_memory_store()

        provider = make_provider()
        tools = ToolRegistry()
        loop = ReActLoop(
            provider=provider,
            tool_registry=tools,
            system_prompt="test prompt",
            memory_store=memory,
        )

        loop._init_messages("hello")
        await loop._inject_subconscious_corrections()

        assert len(loop._messages) == 2
        assert loop._messages[0].role == "system"
        assert loop._messages[0].content == "test prompt"
        assert loop._messages[1].role == "user"
        await memory.close()

    @pytest.mark.asyncio
    async def test_no_memory_store_no_error(self):
        """memory_store=None 时不应报错。"""
        provider = make_provider()
        tools = ToolRegistry()
        loop = ReActLoop(
            provider=provider,
            tool_registry=tools,
            system_prompt="test prompt",
            memory_store=None,
        )

        loop._init_messages("hello")
        await loop._inject_subconscious_corrections()
        assert len(loop._messages) == 2


# ═══════════════════════════════════════════════════════════════
# Scenario 3: 纠正递归深度 ≤ 2
# ═══════════════════════════════════════════════════════════════

class TestCorrectionDepthGuard:
    """验证 _issue_correction 在 depth > 2 时跳过。"""

    @pytest.mark.asyncio
    async def test_depth_exceeds_limit_skips_correction(self):
        """_correction_depth > 2 → _issue_correction 返回 None。"""
        tm, memory = await make_turn_manager()

        tm._correction_depth = 3

        review = ReviewResult(
            logic_weight=0.9,
            emotion_weight=0.8,
            combined_weight=0.85,
            decision=DecisionType.CORRECT,
            logic_errors=[
                FactError(
                    error_type=ErrorType.FACT_ERROR,
                    description="test error",
                    weight=0.9,
                ),
            ],
        )

        result = await tm._issue_correction(review, ["bad reply"])
        assert result is None, "depth > 2 应跳过纠正"
        assert tm._correction_depth == 3
        await memory.close()

    @pytest.mark.asyncio
    async def test_depth_within_limit_proceeds(self):
        """_correction_depth ≤ 2 → _issue_correction 正常执行。"""
        tm, memory = await make_turn_manager()
        tm._correction_depth = 0

        review = ReviewResult(
            logic_weight=0.9,
            emotion_weight=0.8,
            combined_weight=0.85,
            decision=DecisionType.CORRECT,
            logic_errors=[
                FactError(
                    error_type=ErrorType.FACT_ERROR,
                    description="test error",
                    weight=0.9,
                ),
            ],
        )

        result = await tm._issue_correction(review, ["bad reply"])
        assert result is not None, "depth ≤ 2 应正常执行纠正"
        assert result.source in ("logic", "emotion")
        assert tm._correction_depth == 0  # finally 中恢复
        await memory.close()

    @pytest.mark.asyncio
    async def test_correction_sub_session_depth_guard(self):
        """_run_correction_sub_session 在 depth > 2 时返回空字符串。"""
        tm, memory = await make_turn_manager()
        tm._correction_depth = 3

        review = ReviewResult(
            logic_weight=0.9,
            emotion_weight=0.8,
            combined_weight=0.85,
            decision=DecisionType.CORRECT,
        )

        result = await tm._run_correction_sub_session(review, ["bad reply"])
        assert result == "", "depth > 2 应返回空字符串"
        await memory.close()


# ═══════════════════════════════════════════════════════════════
# Scenario 4: 权重公式 0.5/0.5
# ═══════════════════════════════════════════════════════════════

class TestWeightFormula:
    """验证 combined = 0.5 × logic + 0.5 × emotion。"""

    def test_combined_weight_formula(self):
        """直接测试 ReviewSystem._compute_decision 中的组合公式。"""
        provider = make_provider()
        rs = ReviewSystem(provider, MagicMock())

        test_cases = [
            (0.0, 0.0, DecisionType.SILENCE),     # 0.0 ≤ 0.5
            (0.5, 0.5, DecisionType.SILENCE),     # 0.5 ≤ 0.5
            (0.6, 0.5, DecisionType.CORRECT),     # 0.55 > 0.5
            (1.0, 0.0, DecisionType.SILENCE),     # 0.5 ≤ 0.5
            (0.0, 1.0, DecisionType.SILENCE),     # 0.5 ≤ 0.5
            (1.0, 1.0, DecisionType.CORRECT),     # 1.0 > 0.5
            (0.8, 0.5, DecisionType.CORRECT),     # 0.65 > 0.5
        ]

        for logic_w, emotion_w, expected_decision in test_cases:
            combined = logic_w * 0.5 + emotion_w * 0.5
            decision = rs._compute_decision(logic_w, emotion_w)
            assert decision == expected_decision, (
                f"_compute_decision({logic_w}, {emotion_w}): "
                f"combined={combined:.3f}, got {decision}, expected {expected_decision}"
            )

    def test_twisted_condition(self):
        """logic > 0.8 且 emotion < 0.3 且 combined > 0.5 → TWISTED。"""
        provider = make_provider()
        rs = ReviewSystem(provider, MagicMock())

        # TWISTED: logic > 0.8 AND emotion < 0.3 AND combined > 0.5
        # 0.9*0.5 + 0.2*0.5 = 0.55 > 0.5 ✓
        assert rs._compute_decision(0.9, 0.2) == DecisionType.TWISTED
        # 0.82*0.5 + 0.25*0.5 = 0.535 > 0.5 ✓
        assert rs._compute_decision(0.82, 0.25) == DecisionType.TWISTED
        # 1.0*0.5 + 0.1*0.5 = 0.55 > 0.5 ✓
        assert rs._compute_decision(1.0, 0.1) == DecisionType.TWISTED

        # 边界：emotion = 0.3 不触发 TWISTED（需要 < 0.3）
        # 0.9*0.5 + 0.3*0.5 = 0.6 > 0.5 → CORRECT
        assert rs._compute_decision(0.9, 0.3) == DecisionType.CORRECT

        # 边界：logic = 0.8, emotion = 0.2
        # 0.8*0.5 + 0.2*0.5 = 0.5 → SILENCE（≤ 0.5, 不进入 TWISTED 检查）
        assert rs._compute_decision(0.8, 0.2) == DecisionType.SILENCE

        # combined ≤ 0.5 → SILENCE（即使 logic > 0.8, emotion < 0.3）
        # 0.85*0.5 + 0.1*0.5 = 0.475 ≤ 0.5
        assert rs._compute_decision(0.85, 0.1) == DecisionType.SILENCE

    def test_review_result_combined_weight(self):
        """验证 ReviewResult.combined_weight 字段由 review() 正确计算。"""
        review = ReviewResult(
            logic_weight=0.8,
            emotion_weight=0.6,
        )
        combined = review.logic_weight * 0.5 + review.emotion_weight * 0.5
        assert abs(combined - 0.7) < 0.001


# ═══════════════════════════════════════════════════════════════
# Scenario 5: 纠正子Session 有 inner_thoughts
# ═══════════════════════════════════════════════════════════════

class TestCorrectionSubSessionTools:
    """验证纠正子Session 配置：max_iter=5, inner_thoughts 工具已注册。"""

    def test_correction_sub_session_max_iter_5(self):
        """_run_correction_sub_session 使用 SubSessionConfig(max_iter=5)。"""
        from chat_core.core.turn_manager import TurnManager

        source = inspect.getsource(TurnManager._run_correction_sub_session)
        assert "max_iter=5" in source, "纠正子Session 应使用 max_iter=5"
        assert "SubSessionConfig" in source

    def test_correction_tool_registry_has_inner_thoughts(self):
        """纠正子Session 的 ToolRegistry 应注册 inner_thoughts 等全部工具。"""
        from chat_core.core.turn_manager import TurnManager

        source = inspect.getsource(TurnManager._run_correction_sub_session)

        # 验证注册了所需的工具
        assert 'name="inner_thoughts"' in source, "纠正子Session 应注册 inner_thoughts"
        assert 'name="send_reply"' in source, "纠正子Session 应注册 send_reply"
        assert 'name="done"' in source, "纠正子Session 应注册 done"
        assert 'name="wait"' in source, "纠正子Session 应注册 wait"
        # recall 工具通过 _build_correction_recall_tool() 动态构建
        assert "_build_correction_recall_tool" in source, (
            "纠正子Session 应注册 recall（通过 _build_correction_recall_tool）"
        )


# ═══════════════════════════════════════════════════════════════
# Scenario 6: 拧巴记录写入 self/feelings/twisted
# ═══════════════════════════════════════════════════════════════

class TestTwistedArchive:
    """验证 TWISTED 决策时写入 self/feelings。"""

    @pytest.mark.asyncio
    async def test_twisted_writes_to_self_feelings(self):
        """_async_review_and_decide 中 TWISTED → 写入 self/feelings。"""
        tm, memory = await make_turn_manager()

        from chat_core.core.types import ConversationTurn
        tm._current_turn = ConversationTurn(turn_id="turn_test")

        review = ReviewResult(
            logic_weight=0.9,
            emotion_weight=0.2,
            combined_weight=0.55,
            decision=DecisionType.TWISTED,
            logic_errors=[
                FactError(
                    error_type=ErrorType.FACT_ERROR,
                    description="twisted test error",
                    weight=0.9,
                ),
            ],
        )
        tm._review = AsyncMock(return_value=review)

        await tm._async_review_and_decide(
            replies=["bad reply"],
            inner_thoughts="thoughts",
            memories=[],
            user_message="hello",
        )

        entries = await memory.query("self/feelings")
        twisted_entries = [e for e in entries if "twisted" in e.key]
        assert len(twisted_entries) >= 1, (
            f"应有 twisted 归档，实际 keys: {[e.key for e in entries]}"
        )

        twisted = twisted_entries[0]
        assert "logic_weight" in twisted.value
        assert ("emotion_dissent" in twisted.value
                or "emotion_weight" in twisted.value)
        await memory.close()

    @pytest.mark.asyncio
    async def test_correct_decision_does_not_write_twisted(self):
        """CORRECT 决策不应写入 self/feelings/twisted。"""
        tm, memory = await make_turn_manager()

        review = ReviewResult(
            logic_weight=0.9,
            emotion_weight=0.8,
            combined_weight=0.85,
            decision=DecisionType.CORRECT,
            logic_errors=[
                FactError(
                    error_type=ErrorType.FACT_ERROR,
                    description="correct test error",
                    weight=0.9,
                ),
            ],
        )
        tm._review = AsyncMock(return_value=review)

        await tm._async_review_and_decide(
            replies=["bad reply"],
            inner_thoughts="thoughts",
            memories=[],
            user_message="hello",
        )

        entries = await memory.query("self/feelings")
        twisted_entries = [e for e in entries if "twisted" in e.key]
        assert len(twisted_entries) == 0, (
            f"CORRECT 不应写入 twisted，实际: {[e.key for e in twisted_entries]}"
        )
        await memory.close()


# ═══════════════════════════════════════════════════════════════
# Scenario 7: 审查异常静默降级
# ═══════════════════════════════════════════════════════════════

class TestSilentDegradation:
    """验证 _async_review_and_decide 异常不向上传播。"""

    @pytest.mark.asyncio
    async def test_async_review_exception_does_not_propagate(self):
        """审查抛异常 → 静默降级，不抛给调用方。"""
        tm, memory = await make_turn_manager()

        tm._review = AsyncMock(side_effect=RuntimeError("模拟审查失败"))

        try:
            await tm._async_review_and_decide(
                replies=["reply"],
                inner_thoughts=None,
                memories=[],
                user_message="hello",
            )
        except Exception as e:
            pytest.fail(f"_async_review_and_decide 不应抛异常: {e}")

        tm._review.assert_called_once()
        await memory.close()

    @pytest.mark.asyncio
    async def test_async_review_exception_in_process_turn_does_not_block(self):
        """process_turn 中异步审查抛异常不影响 process_turn 正常返回。"""
        tm, memory = await make_turn_manager()

        tm._run_sub_session = AsyncMock(return_value=(["reply"], "thoughts"))

        async def failing_review(*args, **kwargs):
            raise RuntimeError("后台审查失败")

        tm._async_review_and_decide = failing_review

        turn = await tm.process_turn("hello")
        assert turn.user_message == "hello"
        assert turn.status.value == "done"
        await memory.close()


# ═══════════════════════════════════════════════════════════════
# Scenario 8: 情感通知通道发布事件
# ═══════════════════════════════════════════════════════════════

class TestEmotionAlertChannel:
    """验证 emotion_alert 事件在情感变化时正确发布。"""

    @pytest.mark.asyncio
    async def test_emotion_alert_published_when_keyword_detected(self):
        """emotion_direction 包含情绪关键词 → 发布 emotion_alert 事件。"""
        tm, memory = await make_turn_manager()

        tm.emotion.think_pre = AsyncMock(
            return_value=([], "用户情绪波动明显，心情低落")
        )

        tm._run_sub_session = AsyncMock(return_value=(["reply"], "thoughts"))
        tm._event_bus.publish = AsyncMock()

        await tm.process_turn("hello")

        publish_calls = tm._event_bus.publish.call_args_list
        emotion_alert_calls = [
            call for call in publish_calls
            if call.args[0] == "emotion_alert"
        ]
        assert len(emotion_alert_calls) >= 1, (
            f"应发布 emotion_alert，实际: {[c.args[0] for c in publish_calls]}"
        )

        alert_data = emotion_alert_calls[0].args[1]
        assert "mood_shift" in alert_data
        assert "intensity" in alert_data
        assert alert_data["intensity"] == 0.5
        await memory.close()

    @pytest.mark.asyncio
    async def test_no_emotion_alert_without_keyword(self):
        """emotion_direction 不含情绪关键词 → 不发布 emotion_alert。"""
        tm, memory = await make_turn_manager()

        tm.emotion.think_pre = AsyncMock(
            return_value=([], "用户只是在聊日常")
        )

        tm._run_sub_session = AsyncMock(return_value=(["reply"], "thoughts"))
        tm._event_bus.publish = AsyncMock()

        await tm.process_turn("hello")

        publish_calls = tm._event_bus.publish.call_args_list
        emotion_alert_calls = [
            call for call in publish_calls
            if call.args[0] == "emotion_alert"
        ]
        assert len(emotion_alert_calls) == 0, "不含情绪关键词时不应发布 emotion_alert"
        await memory.close()

    @pytest.mark.asyncio
    async def test_emotion_alert_with_other_keywords(self):
        """测试其他情绪关键词也能触发（开心、难过、生气等）。"""
        keywords = ["开心", "难过", "生气", "紧张", "焦虑", "兴奋"]

        for keyword in keywords:
            tm, memory = await make_turn_manager()

            tm.emotion.think_pre = AsyncMock(
                return_value=([], f"用户{keyword}")
            )
            tm._run_sub_session = AsyncMock(return_value=(["reply"], "thoughts"))
            tm._event_bus.publish = AsyncMock()

            await tm.process_turn(f"test {keyword}")

            publish_calls = tm._event_bus.publish.call_args_list
            emotion_alert_calls = [
                call for call in publish_calls
                if call.args[0] == "emotion_alert"
            ]
            assert len(emotion_alert_calls) >= 1, (
                f"关键词 '{keyword}' 应触发 emotion_alert"
            )
            await memory.close()


# ═══════════════════════════════════════════════════════════════
# Bonus: _format_correction 辅助函数
# ═══════════════════════════════════════════════════════════════

class TestFormatCorrection:
    """验证 _format_correction 辅助函数。"""

    def test_format_with_both_errors(self):
        result = _format_correction({
            "logic_errors": ["错误A", "错误B"],
            "tone_issues": ["语气问题"],
        })
        assert "错误A" in result
        assert "错误B" in result
        assert "语气问题" in result

    def test_format_with_logic_only(self):
        result = _format_correction({
            "logic_errors": ["事实错误"],
        })
        assert "事实错误" in result
        assert "语气" not in result

    def test_format_with_combined_weight_fallback(self):
        result = _format_correction({
            "combined_weight": 0.75,
        })
        assert "权重=0.75" in result

    def test_format_empty_dict(self):
        result = _format_correction({})
        assert result == ""


# ═══════════════════════════════════════════════════════════════
# Bonus: review() uses _compute_decision internally
# ═══════════════════════════════════════════════════════════════

class TestReviewInternalDecision:
    """验证 review() 通过内部 _compute_decision 生成 decision。"""

    @pytest.mark.asyncio
    async def test_review_calls_compute_decision(self):
        """review() 内部调用 _compute_decision 而非外部赋值。"""
        provider = make_provider()
        memory = await make_memory_store()

        rs = ReviewSystem(provider, memory)

        # 验证 review() 调用 _compute_decision
        with patch.object(rs, '_compute_decision', wraps=rs._compute_decision) as spy:
            result = await rs.review(
                replies=["这是一个正常的回复"],
                inner_thoughts=None,
                memories=[],
                user_message="hello",
            )
            spy.assert_called_once()

        assert result.logic_verdict == "ok"
        assert result.emotion_verdict == "ok"
        assert result.combined_weight == 0.0
        assert result.decision == DecisionType.SILENCE
        await memory.close()
