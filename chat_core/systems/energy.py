"""EnergyBar — 精力管理 (Spec 007)"""

from __future__ import annotations
import time
from chat_core.config import get_config
from chat_core.core.types import EnergyState


class EnergyBar:
    """精力条：事件驱动消耗 + idle 恢复 + 防御联动 (Spec 007)."""

    def __init__(self) -> None:
        cfg = get_config()
        ec = cfg.systems.get("energy", {})
        self._enabled: bool = bool(ec.get("enabled", True))
        self._initial: float = float(ec.get("initial", 0.9))
        cons = ec.get("consumption", {})
        self._cost_normal: float = float(cons.get("normal_turn", 0.03))
        self._cost_long: float = float(cons.get("long_reply", 0.06))
        self._cost_emotion_shock: float = float(cons.get("emotion_shock", 0.10))
        self._cost_correction: float = float(cons.get("correction", 0.05))
        self._cost_defense: float = float(cons.get("defense", 0.04))
        self._long_threshold: int = int(cons.get("long_reply_threshold", 3))
        rec = ec.get("recovery", {})
        self._recovery_interval: int = int(rec.get("interval", 60))
        self._rate_high: float = float(rec.get("rate_high", 0.02))
        self._rate_mid: float = float(rec.get("rate_mid", 0.01))
        self._rate_low: float = float(rec.get("rate_low", 0.005))
        self._exit_threshold: float = float(ec.get("exit_threshold", 0.15))
        di = ec.get("defense_interaction", {})
        self._project_relief: float = float(di.get("project_relief", 0.02))
        self._denial_drain: float = float(di.get("denial_drain", 0.02))

        self._state = EnergyState(energy=self._initial, last_update=time.time())

    def consume(self, reply_count: int = 1, has_correction: bool = False,
                has_defense_denial: bool = False, has_defense_project: bool = False,
                compound_delta: float = 0.0) -> float:
        """事件驱动消耗。返回消耗后的 energy 值。

        Args:
            reply_count: 发言段数 (>3 触发长回复消耗)
            has_correction: 是否触发纠正
            has_defense_denial: 是否触发 DENIAL 防御
            has_defense_project: 是否触发 PROJECT 防御
            compound_delta: 情绪变化量 (|Δ|>0.4 触发情绪冲击)
        """
        if not self._enabled:
            return self._state.energy
        cost = self._cost_normal
        if reply_count > self._long_threshold:
            cost = self._cost_long
        if abs(compound_delta) > 0.4:
            cost += self._cost_emotion_shock
        if has_correction:
            cost += self._cost_correction
        if has_defense_denial:
            cost += self._cost_defense + self._denial_drain
        if has_defense_project:
            self._state.energy = min(1.0, self._state.energy + self._project_relief)
        self._state.energy = max(0.0, self._state.energy - cost)
        self._state.total_turns_today += 1
        self._state.last_update = time.time()
        return self._state.energy

    def recover(self, wall_dt: float) -> float:
        """Idle 恢复。wall_dt 为墙钟秒数。

        高分位 (>0.6): 快速恢复；中分位 (0.3~0.6): 正常恢复；低分位 (<0.3): 慢速恢复。
        """
        if not self._enabled or wall_dt <= 0:
            return self._state.energy
        if self._state.energy > 0.6:
            rate = self._rate_high
        elif self._state.energy > 0.3:
            rate = self._rate_mid
        else:
            rate = self._rate_low
        self._state.energy = min(1.0, self._state.energy + rate * (wall_dt / self._recovery_interval))
        self._state.last_update = time.time()
        return self._state.energy

    def boost_recovery(self, multiplier: float = 2.0) -> None:
        """Spec 011: OVERLOAD 沉默 → 加速恢复。
        直接给 energy 加一跳，受 multiplier 放大。
        """
        if not self._enabled:
            return
        boost = self._rate_high * multiplier
        self._state.energy = min(1.0, self._state.energy + boost)

    def should_exit(self) -> bool:
        """精力是否已耗尽（低于 exit 阈值）。"""
        return self._enabled and self._state.energy < self._exit_threshold

    def get_state(self) -> EnergyState:
        """返回当前精力状态快照。"""
        return EnergyState(
            energy=self._state.energy,
            last_update=self._state.last_update,
            total_turns_today=self._state.total_turns_today,
        )
