"""MotivationEngine — 双层动机 + 冲突解决 (Spec 011)"""

from __future__ import annotations

from chat_core.config import get_config
from chat_core.core.types import DriveSignal, MotivationState


class MotivationEngine:
    def __init__(self) -> None:
        cfg = get_config()
        mc = cfg.motivations_config()
        self._enabled = bool(mc.get("enabled", True))
        drives_cfg = mc.get("drives", {})
        self._drive_cfg = drives_cfg
        values_cfg = mc.get("values_pursuit", {})
        self._value_cfg = values_cfg
        cr = mc.get("conflict_resolution", {})
        self._drive_over_value = bool(cr.get("drive_over_value", True))
        self._merge_compatible = bool(cr.get("merge_compatible", True))

    @property
    def enabled(self) -> bool: return self._enabled

    def evaluate(
        self, boredom: float = 0.0, energy: float = 1.0,
        loneliness: float = 0.0, confusion: float = 0.0,
        unexpressed_anger: float = 0.0,
        value_weights: dict[str, float] | None = None,
    ) -> MotivationState:
        if not self._enabled:
            return MotivationState()

        drives = self._eval_drives(boredom, energy, loneliness, confusion, unexpressed_anger)
        values = self._eval_values(value_weights or {})
        conflicts = self._resolve(drives, values)
        all_active = drives + values
        strongest = max(all_active, key=lambda d: d.strength).name if all_active else ""

        return MotivationState(
            active_drives=drives, active_values=values,
            conflicts=conflicts, strongest=strongest,
        )

    def _eval_drives(self, boredom, energy, loneliness, confusion, anger_unexp):
        drives = []
        for name, cfg in self._drive_cfg.items():
            threshold = float(cfg.get("threshold", 1.0))
            source = cfg.get("source", "")
            value_map = {"boredom": boredom, "energy": 1.0 - energy, "loneliness": loneliness,
                         "confusion": confusion, "anger_unexpressed": anger_unexp}
            raw = value_map.get(source, 0.0)
            if raw > threshold:
                drives.append(DriveSignal(name=name, strength=raw, source=source, layer="drive"))
        return sorted(drives, key=lambda d: d.strength, reverse=True)

    def _eval_values(self, weights: dict[str, float]):
        values = []
        value_source_map = {"explore": "growth", "check_on": "care",
                            "confront": "honesty", "reflect": "self_improvement"}
        for name, cfg in self._value_cfg.items():
            threshold = float(cfg.get("threshold", 1.0))
            source = value_source_map.get(name, cfg.get("source", ""))
            w = weights.get(source, 0.0)
            if w > threshold:
                values.append(DriveSignal(name=name, strength=w, source=source, layer="value"))
        return sorted(values, key=lambda d: d.strength, reverse=True)

    def _resolve(self, drives, values):
        conflicts = []
        if not self._drive_over_value:
            return conflicts
        for d in drives:
            for v in values:
                if d.name == "rest" and v.name in ("explore", "reflect"):
                    conflicts.append(f"体力优先: [{d.name}] > [{v.name}]")
                elif d.name == "socialize" and v.name == "check_on" and self._merge_compatible:
                    pass  # 合并，不冲突
        return conflicts

    def get_strongest_drive_name(self, state: MotivationState) -> str:
        return state.strongest

    def build_injection(self, state: MotivationState) -> str | None:
        if not state.active_drives and not state.active_values:
            return None
        lines = ["[内在驱动]"]
        if state.active_drives:
            drive_strs = [f"{d.name}({d.strength:.2f})" for d in state.active_drives[:3]]
            lines.append(f"  当前需求: {', '.join(drive_strs)}")
        if state.active_values:
            value_strs = [f"{v.name}({v.strength:.2f})" for v in state.active_values[:3]]
            lines.append(f"  正在追求: {', '.join(value_strs)}")
        if state.conflicts:
            lines.append(f"  内部冲突: {'; '.join(state.conflicts)}")
        return "\n".join(lines) if len(lines) > 1 else None
