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

# Spec 005: 12 维复合情绪
COMPOUND_DIMS = [
    "bittersweet", "guilt", "anxiety", "contempt",
    "gratification", "disappointment", "envy", "pride",
    "resentment", "awe", "nostalgia", "bewilderment",
]

# Spec 005: 交互矩阵 — 维度 A × 维度 B → 复合情绪
# 当 A 和 B 均 ≥ interaction_threshold 时，每 tick 复合 += min(A,B) × coeff × tick_coeff
INTERACTION_MATRIX: dict[str, list[tuple[str, float, str | None]]] = {
    "joy": [
        ("sadness", 0.02, "bittersweet"),
        ("trust", 0.03, "gratification"),
        ("anticipation", 0.03, "pride"),
        ("interest", 0.02, None),            # joy × interest → 泛化愉悦（不单独成维度）
    ],
    "sadness": [
        ("joy", 0.02, "bittersweet"),
        ("fear", 0.03, "guilt"),
        ("anger", 0.02, "resentment"),
        ("surprise", 0.03, "disappointment"),
        ("interest", 0.02, "nostalgia"),
        ("anticipation", 0.01, "envy"),
    ],
    "anger": [
        ("disgust", 0.03, "contempt"),
        ("sadness", 0.02, "resentment"),
    ],
    "fear": [
        ("anticipation", 0.03, "anxiety"),
        ("sadness", 0.02, "guilt"),
        ("confusion", 0.03, "bewilderment"),
        ("surprise", 0.02, "awe"),
        ("trust", 0.01, "awe"),
    ],
    "anticipation": [
        ("fear", 0.03, "anxiety"),
        ("joy", 0.03, "pride"),
    ],
    "trust": [
        ("joy", 0.03, "gratification"),
        ("fear", 0.01, "awe"),
    ],
    "disgust": [
        ("anger", 0.03, "contempt"),
    ],
    "surprise": [
        ("sadness", 0.03, "disappointment"),
        ("fear", 0.02, "awe"),
    ],
    "confusion": [
        ("fear", 0.03, "bewilderment"),
    ],
    "interest": [
        ("joy", 0.02, "nostalgia"),
        ("sadness", 0.02, "nostalgia"),
    ],
}


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

    def __init__(self, event_bus: Any = None) -> None:
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

        # Spec 005: 复合情绪配置
        cc = ec.get("compound", {})
        self._compound_enabled: bool = bool(cc.get("enabled", True))
        self._interaction_threshold: float = float(cc.get("interaction_threshold", 0.3))
        self._interaction_tick_coeff: float = float(cc.get("interaction_tick_coeff", 1.0))
        self._decay_halflife_ratio: float = float(cc.get("decay_halflife_ratio", 0.5))

        # Spec 005: 脆弱感配置
        vc = ec.get("vulnerability", {})
        self._vulnerability_enabled: bool = bool(vc.get("enabled", True))
        self._vulnerability_thresholds: dict[str, float] = {}
        for k, v in vc.get("thresholds", {}).items():
            self._vulnerability_thresholds[str(k)] = float(v)
        self._vulnerability_cooldown: int = 0
        self._vulnerability_cooldown_max: int = int(vc.get("cooldown_turns", 5))
        self.is_vulnerable: bool = False

        # Spec 005: last_compound_delta (供 DefenseEngine 读取)
        self.last_compound_delta: float = 0.0

        # 初始化三个脑的情绪状态
        self._states: dict[str, EmotionState] = {
            name: EmotionState(brain=name) for name in BRAIN_NAMES
        }

        # 事件总线（用于注意力状态机集成，由 TurnManager 注入）
        self._event_bus = event_bus
        self._prev_valence: dict[str, float] = {}

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
        """执行一次情绪衰减 + 跨脑传染 + 复合情绪生成。

        ① 维度交互 → 生成 12 维复合情绪
        ② 复合情绪衰减
        ③ 基础维度衰减
        ④ 跨脑传染 (10 基础 + 12 复合)
        ⑤ compound_alert 检测
        ⑥ Δvalence → emotion_alert (注意力状态机集成)
        """
        now = time.time()

        # Spec 006: 元认知情绪阈值调制
        threshold = self._interaction_threshold
        if hasattr(self, "_meta_overrides") and self._meta_overrides is not None:
            threshold = self._interaction_threshold + self._meta_overrides.emotion_threshold_offset
            threshold = max(0.2, min(0.4, threshold))

        # ① 维度交互 → 生成复合情绪
        if self._compound_enabled:
            for brain_name in BRAIN_NAMES:
                state = self._states[brain_name]
                for dim_a, interactions in INTERACTION_MATRIX.items():
                    a_val = getattr(state, dim_a, 0.0)
                    if a_val < threshold:
                        continue
                    for dim_b, coeff, compound_name in interactions:
                        b_val = getattr(state, dim_b, 0.0)
                        if b_val < threshold:
                            continue
                        if compound_name:
                            contribution = min(a_val, b_val) * coeff * self._interaction_tick_coeff
                            current = getattr(state, compound_name, 0.0)
                            setattr(state, compound_name, min(1.0, current + contribution))

        # ② 复合情绪衰减
        if self._compound_enabled:
            comp_halves = self._compound_half_lives()
            for brain_name in BRAIN_NAMES:
                state = self._states[brain_name]
                dt = now - state.last_tick.timestamp()
                if dt <= 0:
                    continue
                for dim in COMPOUND_DIMS:
                    current = getattr(state, dim, 0.0)
                    hl = comp_halves.get(dim, 600)
                    if hl <= 0:
                        continue
                    decayed = current * (2 ** (-dt / hl))
                    setattr(state, dim, max(0.0, decayed))

        # ③ 基础维度衰减
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
                decayed = current * (2 ** (-dt / hl))
                setattr(state, dim, max(0.0, decayed))

            state.last_tick = datetime.fromtimestamp(now)

        # ④ 跨脑传染 (扩展: 10 基础 + 12 复合)
        all_dims = EMOTION_DIMS + (COMPOUND_DIMS if self._compound_enabled else [])
        for from_brain, to_brain in CONTAGION_FLOW:
            from_state = self._states[from_brain]
            to_state = self._states[to_brain]
            for dim in all_dims:
                from_val = getattr(from_state, dim, 0.0)
                to_val = getattr(to_state, dim, 0.0)
                delta = (from_val - to_val) * self._contagion_strength
                setattr(to_state, dim, _clamp(to_val + delta))

        # ⑤ compound_alert 检测
        if self._compound_enabled:
            if not hasattr(self, "_prev_compound"):
                self._prev_compound: dict[str, float] = {}
            for brain_name in BRAIN_NAMES:
                state = self._states[brain_name]
                max_delta = 0.0
                for dim in COMPOUND_DIMS:
                    current = getattr(state, dim, 0.0)
                    prev = self._prev_compound.get(f"{brain_name}_{dim}", current)
                    delta = abs(current - prev)
                    if delta > max_delta:
                        max_delta = delta
                self.last_compound_delta = max_delta
                if max_delta > 0.4 and self._event_bus:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.call_soon_threadsafe(
                            lambda d=max_delta, bn=brain_name: asyncio.ensure_future(
                                self._event_bus.publish("compound_alert", {
                                    "delta": d, "brain": bn,
                                })
                            )
                        )
                    except RuntimeError:
                        pass
            # 保存当前值为 prev
            for brain_name in BRAIN_NAMES:
                for dim in COMPOUND_DIMS:
                    self._prev_compound[f"{brain_name}_{dim}"] = getattr(
                        self._states[brain_name], dim, 0.0
                    )

        # ⑥ 检测 Δvalence → emotion_alert (注意力状态机集成)
        for brain_name in ["sub"]:
            state = self._states[brain_name]
            current_valence = (state.joy + state.trust) / 2.0
            prev = self._prev_valence.get(brain_name, current_valence)
            delta_valence = abs(current_valence - prev)
            if delta_valence > 0.5 and self._event_bus:
                try:
                    loop = asyncio.get_running_loop()
                    loop.call_soon_threadsafe(
                        lambda d=delta_valence, bn=brain_name: asyncio.ensure_future(
                            self._event_bus.publish("emotion_alert", {
                                "mood_shift": f"valence_delta={d:.2f}",
                                "intensity": min(1.0, d),
                                "brain": bn,
                            })
                        )
                    )
                except RuntimeError:
                    pass  # 无运行中的 loop
            self._prev_valence[brain_name] = current_valence

        # ⑦ 脆弱感检测 (Spec 005: Phase 4)
        if self._vulnerability_enabled:
            self._check_vulnerability()

    def _check_vulnerability(self) -> bool:
        """检测是否处于脆弱状态（任一复合情绪 ≥ 阈值）。
        
        仅检测 sub 脑（最接近人类"感受"的脑区）。
        触发后设置 cooldown 计数器，冷却期内不重复触发。
        """
        if not self._vulnerability_enabled:
            return False

        if self._vulnerability_cooldown > 0:
            self._vulnerability_cooldown -= 1
            self.is_vulnerable = False
            return False

        state = self._states["sub"]
        for dim, threshold in self._vulnerability_thresholds.items():
            if getattr(state, dim, 0.0) >= threshold:
                self.is_vulnerable = True
                self._vulnerability_cooldown = self._vulnerability_cooldown_max
                return True

        self.is_vulnerable = False
        return False

    def _compound_half_lives(self) -> dict[str, float]:
        """计算 12 复合维度的半衰期 = 构成维均值 × decay_halflife_ratio"""
        base_map: dict[str, list[str]] = {}
        for dim_a, interactions in INTERACTION_MATRIX.items():
            for dim_b, coeff, compound_name in interactions:
                if compound_name and compound_name not in base_map:
                    base_map[compound_name] = list(dict.fromkeys([dim_a, dim_b]))
        # 手动补充 awe (3 维) 和 nostalgia (3 维)
        base_map["awe"] = ["fear", "surprise", "trust"]
        base_map["nostalgia"] = ["joy", "sadness", "interest"]

        result: dict[str, float] = {}
        for comp, bases in base_map.items():
            avg = sum(self._half_lives.get(b, 600) for b in bases) / len(bases)
            result[comp] = avg * self._decay_halflife_ratio
        return result

    # ── 公共 API ───────────────────────────────────────────────

    def get_state(self, brain: str) -> EmotionState:
        """获取指定大脑的当前情绪状态（返回副本，含 12 复合维度）"""
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
            # Spec 005: 12 维复合情绪
            bittersweet=self._states[brain].bittersweet,
            guilt=self._states[brain].guilt,
            anxiety=self._states[brain].anxiety,
            contempt=self._states[brain].contempt,
            gratification=self._states[brain].gratification,
            disappointment=self._states[brain].disappointment,
            envy=self._states[brain].envy,
            pride=self._states[brain].pride,
            resentment=self._states[brain].resentment,
            awe=self._states[brain].awe,
            nostalgia=self._states[brain].nostalgia,
            bewilderment=self._states[brain].bewilderment,
        )

    def set_dimension(self, brain: str, dim: str, value: float) -> None:
        """设置指定大脑的指定维度值（自动钳制到 [0, 1]）。
        
        支持 10 基础维度 + 12 复合维度（防御机制需要操作复合情绪）。
        """
        if brain not in self._states:
            raise ValueError(f"Unknown brain: {brain}. Must be one of {BRAIN_NAMES}")
        if dim not in EMOTION_DIMS and dim not in COMPOUND_DIMS:
            raise ValueError(f"Unknown dimension: {dim}. Must be one of EMOTION_DIMS or COMPOUND_DIMS")
        setattr(self._states[brain], dim, _clamp(value))

    def accelerate(self, brain: str, dim: str, delta: float) -> None:
        """竞态驱动情感加速：在当前值上增加 delta（自动钳制到 [0, 1]）。
        
        用于多线对话竞态场景——同时活跃对话越多，"烦躁"维度增长越快。
        """
        current = getattr(self._states[brain], dim, 0.0)
        self.set_dimension(brain, dim, current + delta)

    def get_emotion_summary(self, brain: str) -> str:
        """获取指定大脑的情绪摘要文本，用于注入 sub_session prompt。

        示例输出: "joy=0.60, sadness=0.10, trust=0.70 | gratification=0.15, anxiety=0.05"
        """
        state = self._states.get(brain)
        if state is None:
            return ""
        parts = []
        for dim in EMOTION_DIMS:
            val = getattr(state, dim, 0.0)
            if val > 0.01:
                parts.append(f"{dim}={val:.2f}")
        # 复合情绪
        compound_parts = []
        for dim in COMPOUND_DIMS:
            val = getattr(state, dim, 0.0)
            if val > 0.01:
                compound_parts.append(f"{dim}={val:.2f}")
        base = ", ".join(parts) if parts else "neutral"
        if compound_parts:
            base += " | " + ", ".join(compound_parts)
        return base

    def get_all_states(self) -> dict[str, EmotionState]:
        """获取全部三个大脑的状态（返回副本的字典）"""
        return {
            name: self.get_state(name) for name in BRAIN_NAMES
        }

    # ── Spec 006: 复合情绪趋势 ───────────────────────────

    def get_compound_trend(self, brain: str = "sub") -> dict[str, list[float]]:
        """返回指定脑最近 N 个 tick 的复合情绪值历史。

        用于元认知上下文组装。保留最多 20 个值防止内存泄漏。
        """
        if not hasattr(self, "_compound_history"):
            self._compound_history: dict[str, list[float]] = {
                dim: [] for dim in COMPOUND_DIMS
            }
            return dict(self._compound_history)

        state = self._states.get(brain)
        if state is None:
            return {dim: [] for dim in COMPOUND_DIMS}

        # 追加当前值到历史
        for dim in COMPOUND_DIMS:
            val = getattr(state, dim, 0.0)
            history = self._compound_history.setdefault(dim, [])
            history.append(val)
            # 保留最多 20 个值
            if len(history) > 20:
                history.pop(0)

        return {dim: list(v) for dim, v in self._compound_history.items()}

    def set_meta_overrides(self, overrides: Any) -> None:
        """注入元认知参数覆盖（由 TurnManager 调用）"""
        self._meta_overrides = overrides
