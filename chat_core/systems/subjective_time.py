"""SubjectiveClock — 主观时间感知 (Spec 007)"""

from __future__ import annotations
import time
from chat_core.config import get_config
from chat_core.core.types import SubjectiveTimePerception


class SubjectiveClock:
    """独立于 wall clock 的主观时间感知器。

    factor > 1 = 时间过得慢（煎熬）, factor < 1 = 时间过得快（投入）。
    三维调制：注意力状态 + 情绪 + 兴趣匹配度。
    """

    def __init__(self) -> None:
        cfg = get_config()
        st = cfg.systems.get("subjective_time", {})
        self._enabled: bool = bool(st.get("enabled", True))
        sm = st.get("speed_modifiers", {})
        attn = sm.get("attention", {})
        self._attn_focused: float = float(attn.get("focused", 0.3))
        self._attn_drifting: float = float(attn.get("drifting", 0.8))
        self._attn_dull: float = float(attn.get("dull", 2.0))
        emo = sm.get("emotion", {})
        self._joy_threshold: float = float(emo.get("joy_threshold", 0.5))
        self._sadness_threshold: float = float(emo.get("sadness_threshold", 0.5))
        self._gratification_threshold: float = float(emo.get("gratification_threshold", 0.4))
        intr = sm.get("interest", {})
        self._interest_threshold: float = float(intr.get("match_threshold", 0.7))

        self._accumulated: float = 0.0
        self._last_tick_real: float = time.time()
        self._speed_factor: float = 1.0
        self._perception: str = "normal"

    def tick(self, wall_dt: float, attention_state_enum=None, emotion_state=None,
             interest_match: float = 0.0) -> float:
        """走过 wall_dt 秒墙钟时间，返回主观秒数。

        Args:
            wall_dt: 墙钟时间增量（秒）
            attention_state_enum: AttentionStateEnum 值（可选）
            emotion_state: EmotionState 对象（可选）
            interest_match: 兴趣匹配度 [0, 1]
        """
        if not self._enabled:
            self._accumulated += wall_dt
            return wall_dt
        sf = self._compute_speed_factor(attention_state_enum, emotion_state, interest_match)
        subjective_dt = wall_dt * sf
        self._accumulated += subjective_dt
        self._speed_factor = sf
        self._last_tick_real = time.time()
        self._update_perception(sf)
        return subjective_dt

    def _compute_speed_factor(self, attention_state_enum, emotion_state, interest_match) -> float:
        """计算当前 speed_factor。不依赖类状态，纯函数。"""
        base = 1.0
        # 注意力 (factor > 1 = 煎熬, < 1 = 投入)
        if attention_state_enum is not None:
            from chat_core.core.types import AttentionStateEnum
            if attention_state_enum == AttentionStateEnum.FOCUSED:
                base *= self._attn_focused
            elif attention_state_enum == AttentionStateEnum.DRIFTING:
                base *= self._attn_drifting
            else:
                base *= self._attn_dull
        # 情绪
        if emotion_state is not None:
            if getattr(emotion_state, 'joy', 0) > self._joy_threshold:
                base *= 0.7
            if getattr(emotion_state, 'sadness', 0) > self._sadness_threshold:
                base *= 1.3
            if getattr(emotion_state, 'gratification', 0) > self._gratification_threshold:
                base *= 0.8
        # 兴趣
        if interest_match > self._interest_threshold:
            base *= 0.6
        return base

    def _update_perception(self, sf: float) -> None:
        """根据 speed_factor 更新感知标签。"""
        if sf < 0.5:
            self._perception = "immersed"
        elif sf > 1.5:
            self._perception = "dragging"
        else:
            self._perception = "normal"

    def get_perception(self, fatigue: float) -> SubjectiveTimePerception:
        """返回当前主观时间感知快照。"""
        descriptions = {
            "immersed": "感觉聊得特别投入，时间像飞一样",
            "dragging": "时间过得特别慢，有点煎熬",
            "normal": "时间感正常",
        }
        return SubjectiveTimePerception(
            speed_factor=self._speed_factor,
            perception=self._perception,
            description=descriptions.get(self._perception, ""),
            fatigue_at_end=fatigue,
        )

    @property
    def accumulated(self) -> float:
        """累计主观时间（秒）。"""
        return self._accumulated

    @property
    def speed_factor(self) -> float:
        """当前 speed_factor。"""
        return self._speed_factor

    @property
    def perception(self) -> str:
        """当前感知标签。"""
        return self._perception
