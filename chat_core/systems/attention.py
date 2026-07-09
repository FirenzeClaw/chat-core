"""AttentionModel — 注意力模型：每脑 focus + dominance + drift 衰减"""

from __future__ import annotations

import time

from chat_core.config import get_config
from chat_core.core.types import AttentionState

# 默认 baseline
DEFAULT_BASELINE: dict[str, AttentionState] = {
    "logic": AttentionState(focus=0.8, dominance=0.7),
    "emotion": AttentionState(focus=0.7, dominance=0.5),
    "sub": AttentionState(focus=0.9, dominance=0.6),
}


class AttentionModel:
    """注意力模型。

    为每个大脑维护 focus（专注度）和 dominance（主导性）两个值。
    注意力会随时间漂移衰减（drift_decay_rate 每秒）。
    """

    def __init__(self) -> None:
        cfg = get_config()
        ac = cfg.attention_config()
        baseline_cfg = ac.get("baseline", {})
        self._drift_decay_rate: float = float(ac.get("drift_decay_rate", 0.01))

        # 从配置加载 baseline，缺失的用默认值
        self._baseline: dict[str, AttentionState] = {}
        for name in ["logic", "emotion", "sub"]:
            bc = baseline_cfg.get(name, {})
            self._baseline[name] = AttentionState(
                focus=float(bc.get("focus", DEFAULT_BASELINE[name].focus)),
                dominance=float(bc.get("dominance", DEFAULT_BASELINE[name].dominance)),
            )

        # 当前状态（初始 = baseline）
        self._states: dict[str, AttentionState] = {
            name: AttentionState(
                focus=self._baseline[name].focus,
                dominance=self._baseline[name].dominance,
            )
            for name in self._baseline
        }

        self._last_update: float = time.time()

    # ── drift ──────────────────────────────────────────────────

    def drift(self) -> None:
        """施加一次时间漂移衰减。按自上次调用的时间差计算。

        focus 和 dominance 向 0 衰减：V = V × (1 - drift_decay_rate × dt)
        """
        now = time.time()
        dt = now - self._last_update
        if dt <= 0:
            return

        self._last_update = now

        for name in self._states:
            state = self._states[name]
            decay_factor = 1.0 - self._drift_decay_rate * dt
            decay_factor = max(0.0, min(1.0, decay_factor))
            state.focus = max(0.0, state.focus * decay_factor)
            state.dominance = max(0.0, state.dominance * decay_factor)

    # ── 公共 API ───────────────────────────────────────────────

    def get_state(self, brain: str) -> AttentionState:
        """获取指定大脑的当前注意力状态（返回副本）"""
        if brain not in self._states:
            raise ValueError(f"Unknown brain: {brain}")
        state = self._states[brain]
        return AttentionState(focus=state.focus, dominance=state.dominance)

    def get_focus(self, brain: str) -> float:
        """获取指定大脑的当前 focus 值"""
        state = self.get_state(brain)
        return state.focus

    def reset(self, brain: str) -> None:
        """将指定大脑的注意力重置为其 baseline"""
        if brain not in self._baseline:
            raise ValueError(f"Unknown brain: {brain}")
        self._states[brain] = AttentionState(
            focus=self._baseline[brain].focus,
            dominance=self._baseline[brain].dominance,
        )

    def boost(self, brain: str, amount: float = 0.2) -> None:
        """临时提升指定大脑的 focus，上限 1.0"""
        if brain not in self._states:
            raise ValueError(f"Unknown brain: {brain}")
        self._states[brain].focus = min(1.0, self._states[brain].focus + amount)

    def should_exit_sub(self) -> bool:
        """判断子Session 是否应因注意力过低而退出。

        sub 脑的 focus < 0.15 → True
        """
        return self._states["sub"].focus < 0.15

    def get_all_states(self) -> dict[str, AttentionState]:
        """获取全部大脑的当前注意力状态"""
        return {
            name: AttentionState(focus=s.focus, dominance=s.dominance)
            for name, s in self._states.items()
        }
