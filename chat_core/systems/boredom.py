"""BoredomDetector — 无聊检测器：对话结束后启动 ticker，指数衰减触发主动行为 (Phase 7, T053 + 注意力状态机 §5 联动)"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from chat_core.config import get_config


class BoredomDetector:
    """监控对话结束后的"无聊"水平，在合适时机触发主动行为。

    公式: boredom = 1 - eval_param × e^(-t / 600)
    - eval_param 来自 conversation_ended 事件（对话质量评估）
    - t 为对话结束后的经过时间（秒）
    - 随着时间推移无聊水平逐渐升高（参与感逐渐消退）

    触发阈值（注意力状态感知）:
    - 默认 (FOCUSED/DRIFTING): boredom > 0.70 → 主动发起
    - DULL 态: boredom > threshold_dull (0.40) → 更易触发
    - boredom > 0.90 AND impulsiveness > 0.5 → 结束当前主动对话

    Tick 间隔（注意力状态感知）:
    - FOCUSED: 30s / DRIFTING: 20s / DULL: 15s (加速 2×)
    """

    # 可配置参数
    DEFAULT_TICK_INTERVAL = 30       # 秒 (fallback)
    DEFAULT_DECAY_HALFLIFE = 600     # e 折半衰期（秒）
    TRIGGER_THRESHOLD = 0.70         # 默认无聊触发阈值
    END_CONVERSATION_THRESHOLD = 0.90  # 结束对话阈值

    def __init__(self, attention_model: Any = None, subjective_clock: Any = None,
                 energy_bar: Any = None, emotion_engine: Any = None,
                 interest_model: Any = None) -> None:
        self._eval_param: float = 0.0
        self._interest_weight: float = 0.0
        self._start_time: float = 0.0
        self._active: bool = False
        self._impulsiveness: float = 0.0

        # 注意力状态机联动（可选，向后兼容）
        self._attention_model = attention_model

        # Spec 007: 主观时钟 + 精力条（可选，向后兼容）
        self._subjective_clock = subjective_clock
        self._energy_bar = energy_bar
        self._emotion_engine = emotion_engine
        self._interest_model = interest_model

        # 后台 tick 控制
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None

        # 事件发布回调（由 TurnManager 注入）
        self._on_trigger: Any = None    # async callable()
        self._on_end_conversation: Any = None  # async callable()

    # ── 事件回调注入 ──────────────────────────────────────────

    def set_on_trigger(self, cb: Any) -> None:
        """注入 boredom_trigger 回调"""
        self._on_trigger = cb

    def set_on_end_conversation(self, cb: Any) -> None:
        """注入 end_conversation 回调"""
        self._on_end_conversation = cb

    # ── 生命周期 ──────────────────────────────────────────────

    def start(self, eval_param: float, interest_weight: float = 0.0,
              impulsiveness: float = 0.0) -> None:
        """开始追踪无聊水平。

        Args:
            eval_param: 对话质量评估值，来自 conversation_ended 事件
            interest_weight: 兴趣权重
            impulsiveness: 冲动性人格参数
        """
        self._eval_param = max(0.0, min(1.0, eval_param))
        self._interest_weight = interest_weight
        self._impulsiveness = impulsiveness
        self._start_time = time.time()
        self._active = True

        # 启动后台 ticker
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._tick_loop())

    def stop(self) -> None:
        """停止无聊追踪（新对话开始时调用）"""
        self._active = False
        if self._stop_event:
            self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
        self._task = None
        self._stop_event = None

    @property
    def is_active(self) -> bool:
        """是否正在追踪无聊水平"""
        return self._active

    # ── 无聊水平计算 ──────────────────────────────────────────

    def get_boredom(self) -> float:
        """计算当前无聊水平。

        boredom = 1 - eval_param × e^(-t / 600)
        其中 t 为对话结束后经过的秒数。
        无聊值随时间递增：t=0 时接近 1 - eval_param，t→∞ 趋近于 1。
        """
        if not self._active:
            return 0.0
        if self._subjective_clock and self._subjective_clock.accumulated > 0:
            elapsed = self._subjective_clock.accumulated
        else:
            elapsed = time.time() - self._start_time
        if elapsed <= 0:
            return 1.0 - self._eval_param
        decay = math.exp(-elapsed / self.DEFAULT_DECAY_HALFLIFE)
        return 1.0 - self._eval_param * decay

    # ── 注意力状态感知 ──────────────────────────────────────────

    def _get_tick_interval(self) -> float:
        """根据注意力状态返回 tick 间隔。

        FOCUSED: 30s / DRIFTING: 20s / DULL: 15s
        从 config.yaml 的 boredom_link 段读取。
        """
        cfg = get_config()
        bl = cfg.attention_config().get("boredom_link", {})
        if self._attention_model is not None:
            try:
                from chat_core.core.types import AttentionStateEnum
                state = self._attention_model.get_state_enum("sub")
                if state == AttentionStateEnum.FOCUSED:
                    return float(bl.get("tick_interval_focused", self.DEFAULT_TICK_INTERVAL))
                elif state == AttentionStateEnum.DRIFTING:
                    return float(bl.get("tick_interval_drifting", 20))
                else:  # DULL
                    return float(bl.get("tick_interval_dull", 15))
            except Exception:
                pass
        return float(bl.get("tick_interval_focused", self.DEFAULT_TICK_INTERVAL))

    def _get_trigger_threshold(self) -> float:
        """获取无聊触发阈值。

        DULL 态降低阈值 (0.40)，更容易触发。
        """
        if self._attention_model is not None:
            try:
                from chat_core.core.types import AttentionStateEnum
                if self._attention_model.get_state_enum("sub") == AttentionStateEnum.DULL:
                    bl = get_config().attention_config().get("boredom_link", {})
                    return float(bl.get("threshold_dull", 0.40))
            except Exception:
                pass
        return self.TRIGGER_THRESHOLD

    # ── 后台 tick 循环 ────────────────────────────────────────

    async def _tick_loop(self) -> None:
        """后台 tick 循环，每状态感知间隔检查一次"""
        while self._stop_event and not self._stop_event.is_set():
            self._check_thresholds()
            try:
                interval = self._get_tick_interval()
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=interval,
                )
            except asyncio.TimeoutError:
                pass  # 正常超时，继续检查
            # Spec 007: 主观时间采样 + 精力恢复
            if self._subjective_clock and self._attention_model:
                try:
                    attn_state = self._attention_model.get_state_enum("sub")
                    emotion_state = (
                        self._emotion_engine.get_state("sub")
                        if self._emotion_engine else None
                    )
                    interest_match = 0.0
                    self._subjective_clock.tick(
                        interval,
                        attention_state_enum=attn_state,
                        emotion_state=emotion_state,
                        interest_match=interest_match,
                    )
                except Exception:
                    pass  # 静默降级
            if self._energy_bar:
                self._energy_bar.recover(interval)

    def _check_thresholds(self) -> None:
        """检查无聊水平是否触发阈值"""
        if not self._active:
            return

        boredom = self.get_boredom()
        trigger_threshold = self._get_trigger_threshold()

        # 结束对话阈值：极度无聊 + 冲动 → 结束当前主动对话
        if boredom > self.END_CONVERSATION_THRESHOLD and self._impulsiveness > 0.5:
            if self._on_end_conversation:
                asyncio.create_task(self._on_end_conversation(boredom))

        # 触发阈值：无聊过高 → 需要主动发起
        elif boredom > trigger_threshold:
            if self._on_trigger:
                # 不能直接 await，用 create_task 调度
                asyncio.create_task(self._on_trigger(boredom))
            # 触发后停止，防止重复触发
            self._active = False

    # ── 辅助 ──────────────────────────────────────────────────

    def __repr__(self) -> str:
        status = "active" if self._active else "idle"
        return (f"BoredomDetector({status}, boredom={self.get_boredom():.4f}, "
                f"eval={self._eval_param:.2f})")
