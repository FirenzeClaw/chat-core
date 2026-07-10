"""SilenceClassifier — 5 类沉默语义判定 (Spec 011)"""

from __future__ import annotations

from chat_core.config import get_config
from chat_core.core.types import (
    EmotionState, RelationshipStage, ReviewResult,
    SilenceRecord, SilenceType,
)


class SilenceClassifier:
    def __init__(self) -> None:
        cfg = get_config()
        sc = cfg.silence_semantics_config()
        self._enabled = bool(sc.get("enabled", True))
        types_cfg = sc.get("types", {})
        h = types_cfg.get("hesitant", {})
        self._hesitant_confusion = float(h.get("confusion_threshold", 0.4))
        self._hesitant_streak = int(h.get("streak_threshold", 3))
        h_inc = h.get("silence_increment", 1)
        t = types_cfg.get("tacit", {})
        self._tacit_min_stage = t.get("min_stage", "friend")
        self._tacit_max_severity = float(t.get("max_severity", 0.3))
        t_inc = t.get("silence_increment", 0)
        a = types_cfg.get("angry", {})
        self._angry_anger = float(a.get("anger_threshold", 0.5))
        self._angry_sadness_max = float(a.get("sadness_max", 0.3))
        a_inc = a.get("silence_increment", 1)
        s = types_cfg.get("strategic", {})
        s_inc = s.get("silence_increment", 1)
        o = types_cfg.get("overload", {})
        self._overload_energy = float(o.get("energy_threshold", 0.2))
        self._overload_min_turns = int(o.get("min_turns", 10))
        self._overload_recovery_boost = float(o.get("recovery_boost", 2.0))
        o_inc = o.get("silence_increment", 0)

        self._silence_increment = {
            SilenceType.HESITANT: h_inc, SilenceType.TACIT: t_inc,
            SilenceType.ANGRY: a_inc, SilenceType.STRATEGIC: s_inc,
            SilenceType.OVERLOAD: o_inc,
        }

    @property
    def enabled(self) -> bool: return self._enabled

    def classify(
        self, review: ReviewResult, emotion: EmotionState | None,
        energy: float, relationship_stage: RelationshipStage | None,
        silence_streak: int = 0, active_turns: int = 0,
    ) -> SilenceRecord:
        if not self._enabled:
            return SilenceRecord(silence_type=SilenceType.STRATEGIC)

        stage = relationship_stage.value if relationship_stage else "stranger"
        anger = emotion.anger if emotion else 0.0
        sadness = emotion.sadness if emotion else 0.0
        confusion = emotion.confusion if emotion else 0.0
        severity = review.combined_weight

        # OVERLOAD
        if energy < self._overload_energy and active_turns > self._overload_min_turns:
            return SilenceRecord(silence_type=SilenceType.OVERLOAD, reasoning="精力耗尽+轮次过多")

        # ANGRY
        if anger > self._angry_anger and sadness < self._angry_sadness_max:
            return SilenceRecord(silence_type=SilenceType.ANGRY, reasoning="生气但克制")

        # TACIT
        if stage in ("friend", "close_friend") and severity < self._tacit_max_severity:
            return SilenceRecord(silence_type=SilenceType.TACIT, reasoning="默契，不用多说")

        # HESITANT
        if silence_streak >= self._hesitant_streak and confusion > self._hesitant_confusion:
            return SilenceRecord(silence_type=SilenceType.HESITANT, reasoning="不确定该不该说")

        return SilenceRecord(silence_type=SilenceType.STRATEGIC, reasoning="选择不参与")

    def get_silence_increment(self, st: SilenceType) -> int:
        return self._silence_increment.get(st, 1)

    def get_recovery_boost(self) -> float:
        return self._overload_recovery_boost
