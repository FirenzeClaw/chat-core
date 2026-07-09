"""EmotionEngine — 情绪模拟引擎：10 维度 × 3 大脑，指数衰减 + 跨脑传染"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import EmotionState

# 默认半衰期（秒），config.yaml 中 systems.emotion.decay 可覆盖
DEFAULT_HALF_LIVES: dict[str, float] = {
    "surprise": 30,
    "confusion": 120,
    "fear": 300,
    "anger": 600,
    "disgust": 600,
    "joy": 600,
    "sadness": 900,
    "interest": 1200,
    "anticipation": 1800,
    "trust": 3600,
}

EMOTION_DIMS = [
    "surprise", "confusion", "fear", "anger", "disgust",
    "joy", "sadness", "interest", "anticipation", "trust",
]

BRAIN_NAMES = ["logic", "emotion", "sub"]

# 传染方向：logic→emotion, emotion→sub, sub→logic
CONTAGION_FLOW: list[tuple[str, str]] = [
    ("logic", "emotion"),
    ("emotion", "sub"),
    ("sub", "logic"),
]


def _clamp(value: float) -> float:
    """将值钳制到 [0.0, 1.0]"""
    return max(0.0, min(1.0, value))


class EmotionEngine:
    """情绪模拟引擎。

    维护 3 个大脑（logic / emotion / sub）各自的 10 维情绪向量。
    每个维度按指数衰减：V(t) = V₀ × 2^(-Δt / half_life)。
    跨脑传染：每个 tick，相邻大脑之间按 contagion_strength 传播。

    通过 asyncio 后台任务以 tick_interval 秒为周期运行。
    在活跃对话期间暂停，在 "conversation_ended" 事件后恢复。
    """

    def __init__(self) -> None:
        cfg = get_config()
        ec = cfg.emotion_config()
        decay_cfg = ec.get("decay", {})
        self._tick_interval: float = float(ec.get("tick_interval", 10))
        self._contagion_strength: float = float(ec.get("contagion_strength", 0.1))
        self._introspect_threshold: float = float(ec.get("introspect_threshold", 0.3))

        # 解析半衰期配置
        self._half_lives: dict[str, float] = {}
        for dim in EMOTION_DIMS:
            self._half_lives[dim] = float(decay_cfg.get(dim, DEFAULT_HALF_LIVES[dim]))

        # 初始化三个脑的情绪状态
        self._states: dict[str, EmotionState] = {
            name: EmotionState(brain=name) for name in BRAIN_NAMES
        }

        # 后台 tick 控制
        self._task: asyncio.Task[None] | None = None
        self._paused: bool = False
        self._stop_event: asyncio.Event | None = None

    # ── 生命周期 ──────────────────────────────────────────────

    async def start(self) -> None:
        """启动后台 tick 任务"""
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._paused = False
        self._task = asyncio.create_task(self._tick_loop())

    async def stop(self) -> None:
        """停止后台 tick 任务"""
        if self._stop_event:
            self._stop_event.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._stop_event = None

    def pause(self) -> None:
        """暂停 tick（活跃对话期间）"""
        self._paused = True

    def resume(self) -> None:
        """恢复 tick（对话结束后）"""
        self._paused = False

    # ── 后台循环 ──────────────────────────────────────────────

    async def _tick_loop(self) -> None:
        """后台 tick 循环，每 tick_interval 秒执行一次"""
        while self._stop_event and not self._stop_event.is_set():
            if not self._paused:
                self.tick()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._tick_interval,
                )
            except asyncio.TimeoutError:
                pass  # 正常超时，继续循环

    # ── Tick 逻辑 ──────────────────────────────────────────────

    def tick(self) -> None:
        """执行一次情绪衰减 + 跨脑传染。

        1. 对每个大脑的每个维度施加指数衰减。
        2. 按 contagion_flow 方向传播情绪值。
        """
        now = time.time()

        # 1. 指数衰减
        for brain_name in BRAIN_NAMES:
            state = self._states[brain_name]
            dt = now - state.last_tick.timestamp()
            if dt <= 0:
                continue

            for dim in EMOTION_DIMS:
                current = getattr(state, dim, 0.0)
                hl = self._half_lives[dim]
                if hl <= 0:
                    continue
                # V(t) = V₀ × 2^(-Δt / half_life)
                decayed = current * (2 ** (-dt / hl))
                # 不让衰减低于 0
                setattr(state, dim, max(0.0, decayed))

            state.last_tick = datetime.fromtimestamp(now)

        # 2. 跨脑传染
        for from_brain, to_brain in CONTAGION_FLOW:
            from_state = self._states[from_brain]
            to_state = self._states[to_brain]
            for dim in EMOTION_DIMS:
                from_val = getattr(from_state, dim, 0.0)
                to_val = getattr(to_state, dim, 0.0)
                # 差值 × 传染强度
                delta = (from_val - to_val) * self._contagion_strength
                setattr(to_state, dim, _clamp(to_val + delta))

    # ── 公共 API ───────────────────────────────────────────────

    def get_state(self, brain: str) -> EmotionState:
        """获取指定大脑的当前情绪状态（返回副本）"""
        if brain not in self._states:
            raise ValueError(f"Unknown brain: {brain}. Must be one of {BRAIN_NAMES}")
        return EmotionState(
            brain=self._states[brain].brain,
            surprise=self._states[brain].surprise,
            confusion=self._states[brain].confusion,
            fear=self._states[brain].fear,
            anger=self._states[brain].anger,
            disgust=self._states[brain].disgust,
            joy=self._states[brain].joy,
            sadness=self._states[brain].sadness,
            interest=self._states[brain].interest,
            anticipation=self._states[brain].anticipation,
            trust=self._states[brain].trust,
            last_tick=self._states[brain].last_tick,
        )

    def set_dimension(self, brain: str, dim: str, value: float) -> None:
        """设置指定大脑的指定维度值（自动钳制到 [0, 1]）"""
        if brain not in self._states:
            raise ValueError(f"Unknown brain: {brain}. Must be one of {BRAIN_NAMES}")
        if dim not in EMOTION_DIMS:
            raise ValueError(f"Unknown dimension: {dim}. Must be one of {EMOTION_DIMS}")
        setattr(self._states[brain], dim, _clamp(value))

    def get_emotion_summary(self, brain: str) -> str:
        """获取指定大脑的情绪摘要文本，用于注入 sub_session prompt。

        示例输出: "joy=0.6, sadness=0.1, interest=0.5, trust=0.7"
        """
        state = self._states.get(brain)
        if state is None:
            return ""
        parts = []
        for dim in EMOTION_DIMS:
            val = getattr(state, dim, 0.0)
            if val > 0.01:  # 只展示非零维度
                parts.append(f"{dim}={val:.2f}")
        return ", ".join(parts) if parts else "neutral"

    def get_all_states(self) -> dict[str, EmotionState]:
        """获取全部三个大脑的状态（返回副本的字典）"""
        return {
            name: self.get_state(name) for name in BRAIN_NAMES
        }
