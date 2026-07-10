"""AttentionModel — 三态注意力状态机 (FOCUSED/DRIFTING/DULL) + 平滑过渡"""

from __future__ import annotations

import random
import time

from chat_core.config import get_config
from chat_core.core.types import AttentionEvent, AttentionState, AttentionStateEnum

# 默认 baseline
DEFAULT_BASELINE: dict[str, AttentionState] = {
    "logic": AttentionState(focus=0.8, dominance=0.7),
    "emotion": AttentionState(focus=0.7, dominance=0.5),
    "sub": AttentionState(focus=0.9, dominance=0.6),
}


def _state_enum(focus: float, thresholds: dict[str, float]) -> AttentionStateEnum:
    """focus 值 → AttentionStateEnum"""
    if focus >= thresholds.get("focused", 0.6):
        return AttentionStateEnum.FOCUSED
    elif focus >= thresholds.get("drifting", 0.3):
        return AttentionStateEnum.DRIFTING
    else:
        return AttentionStateEnum.DULL


class AttentionModel:
    """三态注意力状态机。

    为每个大脑维护 focus（专注度）和 dominance（主导性），
    支持事件驱动的状态转移、平滑过渡、疲劳因子和状态感知衰减速率。
    """

    def __init__(self) -> None:
        cfg = get_config()
        ac = cfg.attention_config()
        sm = ac.get("state_machine", {})
        drift_cfg = ac.get("drift", {})
        fatigue_cfg = ac.get("fatigue", {})
        baseline_cfg = ac.get("baseline", {})

        # 状态机阈值
        self._thresholds: dict[str, float] = {
            "focused": float(sm.get("state_thresholds", {}).get("focused", 0.6)),
            "drifting": float(sm.get("state_thresholds", {}).get("drifting", 0.3)),
        }

        # boost/penalty 值
        boosts = sm.get("boosts", {})
        penalties = sm.get("penalties", {})
        probs = sm.get("transition_probabilities", {})
        self._boosts: dict[str, float] = {
            "user_message_dull": float(boosts.get("user_message_dull", 0.20)),
            "emotion_positive": float(boosts.get("emotion_positive", 0.10)),
            "emotion_shock": float(boosts.get("emotion_shock", 0.30)),
            "memory_strong_hit": float(boosts.get("memory_strong_hit", 0.25)),
            "topic_match_strong": float(boosts.get("topic_match_strong", 0.08)),
            "intent_detected": float(boosts.get("intent_detected", 0.05)),
        }
        self._penalties: dict[str, float] = {
            "emotion_negative": float(penalties.get("emotion_negative", 0.10)),
            "memory_miss": float(penalties.get("memory_miss", 0.03)),
            "short_reply_streak": float(penalties.get("short_reply_streak", 0.10)),
            "correction_triggered": float(penalties.get("correction_triggered", 0.05)),
            "per_segment_sent": float(penalties.get("per_segment_sent", 0.02)),
        }
        self._probs: dict[str, float] = {
            "drifting_to_focused_on_message": float(probs.get("drifting_to_focused_on_message", 0.7)),
            "dull_to_drifting_on_message": float(probs.get("dull_to_drifting_on_message", 0.5)),
            "dull_to_drifting_on_shock": float(probs.get("dull_to_drifting_on_shock", 0.8)),
            "focused_to_drifting_on_race3": float(probs.get("focused_to_drifting_on_race3", 0.7)),
            "focused_to_dull_on_race5": float(probs.get("focused_to_dull_on_race5", 0.6)),
            "drifting_to_dull_on_race5": float(probs.get("drifting_to_dull_on_race5", 0.8)),
        }

        # 衰减速率（状态感知）
        self._decay_rates: dict[str, float] = {
            "focused": float(drift_cfg.get("decay_rate_focused", 0.001)),
            "drifting": float(drift_cfg.get("decay_rate_drifting", 0.002)),
            "dull": float(drift_cfg.get("decay_rate_dull", 0.0005)),
        }
        # 向后兼容的 drift_decay_rate（旧配置）
        self._drift_decay_rate: float = float(ac.get("drift_decay_rate", 0.001))

        # 疲劳
        self._fatigue_max_turns: int = int(fatigue_cfg.get("max_turns", 50))
        self._fatigue_acceleration: float = float(fatigue_cfg.get("decay_acceleration", 0.5))
        self._total_turns: int = 0

        # baseline
        self._baseline: dict[str, AttentionState] = {}
        for name in ["logic", "emotion", "sub"]:
            bc = baseline_cfg.get(name, {})
            initial_focus = (
                float(sm.get("initial_focus", 0.9))
                if name == "sub"
                else float(bc.get("focus", DEFAULT_BASELINE[name].focus))
            )
            self._baseline[name] = AttentionState(
                focus=initial_focus,
                dominance=float(bc.get("dominance", DEFAULT_BASELINE[name].dominance)),
            )

        # 当前状态
        self._states: dict[str, AttentionState] = {
            name: AttentionState(
                focus=self._baseline[name].focus,
                dominance=self._baseline[name].dominance,
            )
            for name in self._baseline
        }

        self._last_update: float = time.time()

        # 平滑过渡
        self._transition_target: dict[str, float | None] = {name: None for name in self._baseline}
        self._transition_elapsed: dict[str, float] = {name: 0.0 for name in self._baseline}
        self._transition_start: dict[str, float] = {name: 0.0 for name in self._baseline}
        self._transition_duration: float = 0.3  # 0.3s

    # ── 状态枚举 ───────────────────────────────────────────────

    def get_state_enum(self, brain: str) -> AttentionStateEnum:
        """获取指定大脑的三态枚举值"""
        focus = self.get_focus(brain)
        return _state_enum(focus, self._thresholds)

    # ── 事件驱动 ───────────────────────────────────────────────

    def apply_event(self, event: AttentionEvent, brain: str = "sub") -> None:
        """应用注意力事件，触发状态转移（含概率性转移）。

        立即更新 focus 到目标值，并启动 0.3s 平滑过渡（用于视觉/日志呈现）。
        """
        state_enum = self.get_state_enum(brain)
        current = self._states[brain].focus
        target = current  # 默认不变

        if event == AttentionEvent.USER_MESSAGE:
            if state_enum == AttentionStateEnum.DULL:
                p = self._probs["dull_to_drifting_on_message"]
                if random.random() < p:
                    target = self._thresholds["drifting"] + self._boosts["user_message_dull"]
                else:
                    target = current + self._boosts["user_message_dull"]
            elif state_enum == AttentionStateEnum.DRIFTING:
                p = self._probs["drifting_to_focused_on_message"]
                if random.random() < p:
                    target = self._thresholds["focused"] + self._boosts["user_message_dull"]

        elif event == AttentionEvent.EMOTION_POSITIVE:
            target = min(1.0, current + self._boosts["emotion_positive"])

        elif event == AttentionEvent.EMOTION_NEGATIVE:
            target = max(0.0, current - self._penalties["emotion_negative"])

        elif event == AttentionEvent.EMOTION_SHOCK:
            if state_enum == AttentionStateEnum.DULL:
                p = self._probs["dull_to_drifting_on_shock"]
                if random.random() < p:
                    target = self._thresholds["drifting"] + self._boosts["emotion_shock"]
                else:
                    target = current + self._boosts["emotion_shock"]
            else:
                target = min(1.0, current + self._boosts["emotion_shock"])

        elif event == AttentionEvent.MEMORY_STRONG_HIT:
            if state_enum == AttentionStateEnum.DULL:
                if random.random() < 0.5:
                    target = self._thresholds["drifting"] + self._boosts["memory_strong_hit"]
                else:
                    target = current + self._boosts["memory_strong_hit"]
            elif state_enum == AttentionStateEnum.DRIFTING:
                target = min(1.0, current + 0.20)
            else:
                target = min(1.0, current + self._boosts["memory_strong_hit"])

        elif event == AttentionEvent.MEMORY_MISS:
            if state_enum == AttentionStateEnum.DRIFTING:
                target = max(0.0, current - 0.05)
            else:
                target = max(0.0, current - self._penalties["memory_miss"])

        elif event == AttentionEvent.TOPIC_MATCH:
            boost = self._boosts["topic_match_strong"] if state_enum != AttentionStateEnum.DULL else 0.05
            target = min(1.0, current + boost)

        elif event == AttentionEvent.RACE_MILD:
            if state_enum == AttentionStateEnum.FOCUSED:
                if random.random() < self._probs["focused_to_drifting_on_race3"]:
                    target = self._thresholds["focused"] - 0.01  # 刚跌破聚焦

        elif event == AttentionEvent.RACE_SEVERE:
            if state_enum == AttentionStateEnum.FOCUSED:
                if random.random() < self._probs["focused_to_dull_on_race5"]:
                    target = self._thresholds["drifting"] - 0.01
            elif state_enum == AttentionStateEnum.DRIFTING:
                if random.random() < self._probs["drifting_to_dull_on_race5"]:
                    target = self._thresholds["drifting"] - 0.01

        elif event == AttentionEvent.SHORT_REPLY_STREAK:
            penalty = self._penalties["short_reply_streak"]
            if state_enum == AttentionStateEnum.DRIFTING:
                target = max(0.0, current - penalty)
            else:
                target = max(0.0, current - penalty * 0.67)

        elif event == AttentionEvent.SILENCE_TICK:
            if state_enum == AttentionStateEnum.FOCUSED:
                target = max(0.0, current - 0.03)
            elif state_enum == AttentionStateEnum.DRIFTING:
                target = max(0.0, current - 0.05)
            # DULL: 保持 (不进一步降低)

        elif event == AttentionEvent.INTENT_DETECTED:
            target = min(1.0, current + self._boosts["intent_detected"])

        elif event == AttentionEvent.CORRECTION_TRIGGERED:
            penalty = self._penalties["correction_triggered"]
            if state_enum == AttentionStateEnum.DRIFTING:
                target = max(0.0, current - penalty)
            else:
                target = max(0.0, current - penalty * 0.5)

        # 启动平滑过渡 + 立即更新 focus
        if abs(target - current) > 0.001:
            self._transition_target[brain] = target
            self._transition_start[brain] = current
            self._transition_elapsed[brain] = 0.0
            # 立即应用 focus 变更（get_focus 立即可见提升后的值）
            self._states[brain].focus = target

    # ── drift ──────────────────────────────────────────────────

    def drift(self) -> None:
        """施加一次时间漂移衰减。状态感知衰减速率 + 平滑过渡插值 + 疲劳因子。"""
        now = time.time()
        dt = now - self._last_update
        if dt <= 0:
            return

        self._last_update = now

        for name in self._states:
            state = self._states[name]

            # 1. 平滑过渡（如果有进行中的过渡且尚未到达目标）
            if self._transition_target[name] is not None:
                self._transition_elapsed[name] += dt
                progress = min(1.0, self._transition_elapsed[name] / self._transition_duration)
                # 仅当过渡正在影响 focus 时才进行插值（若 focus 已被即时更新到目标，则不变）
                if abs(self._transition_target[name] - state.focus) > 0.001:
                    state.focus = self._transition_start[name] + (
                        self._transition_target[name] - self._transition_start[name]
                    ) * progress
                if progress >= 1.0:
                    self._transition_target[name] = None
            else:
                # 2. 状态感知衰减 + 疲劳因子
                state_enum_val = self.get_state_enum(name).value  # "focused" / "drifting" / "dull"
                base_rate = self._decay_rates.get(state_enum_val, self._drift_decay_rate)

                # 疲劳加速
                fatigue = min(1.0, self._total_turns / max(1, self._fatigue_max_turns))
                effective_rate = base_rate * (1.0 + fatigue * self._fatigue_acceleration)

                decay_factor = 1.0 - effective_rate * dt
                decay_factor = max(0.0, min(1.0, decay_factor))
                state.focus = max(0.0, state.focus * decay_factor)
                state.dominance = max(0.0, state.dominance * decay_factor)

    # ── 公共 API ───────────────────────────────────────────────

    def get_state(self, brain: str) -> AttentionState:
        """获取指定大脑的当前注意力状态（返回副本）"""
        if brain not in self._states:
            raise ValueError(f"Unknown brain: {brain}")
        state = self._states[brain]
        return AttentionState(focus=state.focus, dominance=state.dominance, fatigue=state.fatigue)

    def get_focus(self, brain: str) -> float:
        """获取指定大脑的当前 focus 值"""
        return self._states[brain].focus

    def reset(self, brain: str) -> None:
        """将指定大脑的注意力重置为其 baseline"""
        if brain not in self._baseline:
            raise ValueError(f"Unknown brain: {brain}")
        self._states[brain] = AttentionState(
            focus=self._baseline[brain].focus,
            dominance=self._baseline[brain].dominance,
        )

    def boost(self, brain: str, amount: float = 0.2) -> None:
        """临时提升指定大脑的 focus，上限 1.0。负值降低 focus。"""
        if brain not in self._states:
            raise ValueError(f"Unknown brain: {brain}")
        self._states[brain].focus = max(0.0, min(1.0, self._states[brain].focus + amount))

    def increment_turn(self) -> None:
        """增加总 turn 计数（用于疲劳计算）"""
        self._total_turns += 1

    def should_exit_sub(self) -> bool:
        """判断子Session 是否应因注意力过低而退出。

        注意力状态机: DULL 态不沉默，始终返回 False。
        """
        state_enum = self.get_state_enum("sub")
        if state_enum == AttentionStateEnum.DULL:
            return False
        return self._states["sub"].focus < 0.15

    def get_all_states(self) -> dict[str, AttentionState]:
        """获取全部大脑的当前注意力状态"""
        return {
            name: AttentionState(focus=s.focus, dominance=s.dominance)
            for name, s in self._states.items()
        }
