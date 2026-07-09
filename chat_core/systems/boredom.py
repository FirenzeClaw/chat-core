"""BoredomDetector — 无聊检测器：对话结束后启动 ticker，指数衰减触发主动行为 (Phase 7, T053)"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any


class BoredomDetector:
    """监控对话结束后的"无聊"水平，在合适时机触发主动行为。

    公式: boredom = 1 - eval_param × e^(-t / 600)
    - eval_param 来自 conversation_ended 事件（对话质量评估）
    - t 为对话结束后的经过时间（秒）
    - 随着时间推移无聊水平逐渐升高（参与感逐渐消退）

    触发阈值:
    - boredom > 0.70 → emit boredom_trigger: 无聊水平过高，需要主动发起
    - boredom > 0.90 AND impulsiveness > 0.5 → 结束当前主动对话
    """

    # 可配置参数
    DEFAULT_TICK_INTERVAL = 30       # 秒
    DEFAULT_DECAY_HALFLIFE = 600     # e 折半衰期（秒）
    TRIGGER_THRESHOLD = 0.70         # 无聊触发阈值
    END_CONVERSATION_THRESHOLD = 0.90  # 结束对话阈值

    def __init__(self) -> None:
        self._eval_param: float = 0.0
        self._interest_weight: float = 0.0
        self._start_time: float = 0.0
        self._active: bool = False
        self._impulsiveness: float = 0.0

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
        elapsed = time.time() - self._start_time
        if elapsed <= 0:
            return 1.0 - self._eval_param
        decay = math.exp(-elapsed / self.DEFAULT_DECAY_HALFLIFE)
        return 1.0 - self._eval_param * decay

    # ── 后台 tick 循环 ────────────────────────────────────────

    async def _tick_loop(self) -> None:
        """后台 tick 循环，每 tick_interval 秒检查一次"""
        while self._stop_event and not self._stop_event.is_set():
            self._check_thresholds()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.DEFAULT_TICK_INTERVAL,
                )
            except asyncio.TimeoutError:
                pass  # 正常超时，继续检查

    def _check_thresholds(self) -> None:
        """检查无聊水平是否触发阈值"""
        if not self._active:
            return

        boredom = self.get_boredom()

        # 结束对话阈值：极度无聊 + 冲动 → 结束当前主动对话
        if boredom > self.END_CONVERSATION_THRESHOLD and self._impulsiveness > 0.5:
            if self._on_end_conversation:
                asyncio.create_task(self._on_end_conversation(boredom))

        # 触发阈值：无聊过高 → 需要主动发起
        elif boredom > self.TRIGGER_THRESHOLD:
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
