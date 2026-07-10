"""GroupDynamics — 群角色统计 + 群氛围感知 (Spec 008)

纯统计层：无 LLM 调用。定性判断由 Spec 006 元认知顺带处理。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import GroupAtmosphere, GroupRoleMetrics

logger = logging.getLogger(__name__)


class GroupDynamics:
    """群动力学引擎：per-group 角色统计 + 氛围快照持久化。

    TurnManager/Adapter 持有单例。per-group 状态通过 dict[group_id, ...] 管理。
    """

    def __init__(self) -> None:
        cfg = get_config()
        gdc = cfg.group_dynamics_config()
        self._enabled: bool = bool(gdc.get("enabled", True))
        self._atmosphere_interval: int = int(gdc.get("atmosphere_snapshot_interval", 10))
        self._role_metrics_window: int = int(gdc.get("role_metrics_window", 100))

        # per-group 统计
        self._role_metrics: dict[str, GroupRoleMetrics] = {}
        self._atmosphere_snapshots: dict[str, list[GroupAtmosphere]] = {}

        # MemoryStore 引用（由外部在初始化后设置）
        self._memory: Any = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_memory(self, memory: Any) -> None:
        """注入 MemoryStore 引用（用于氛围持久化）"""
        self._memory = memory

    # ── 群角色统计 ─────────────────────────────────────────

    def get_metrics(self, group_id: str) -> GroupRoleMetrics:
        """获取或创建 per-group 角色统计"""
        if group_id not in self._role_metrics:
            self._role_metrics[group_id] = GroupRoleMetrics(group_id=group_id)
        return self._role_metrics[group_id]

    def record_at(self, group_id: str, member_count: int = 0) -> GroupRoleMetrics:
        """记录一次被 @"""
        m = self.get_metrics(group_id)
        m.at_count += 1
        if member_count > 0:
            m.member_count = member_count
        return m

    def record_observe(self, group_id: str) -> GroupRoleMetrics:
        """记录一次旁听消息"""
        m = self.get_metrics(group_id)
        m.total_messages += 1
        return m

    def record_reply(self, group_id: str) -> GroupRoleMetrics:
        """记录 AI 在群内的回复"""
        m = self.get_metrics(group_id)
        m.reply_count += 1
        return m

    def record_member_reply_to_ai(self, group_id: str) -> GroupRoleMetrics:
        """记录群成员回复 AI 的消息"""
        m = self.get_metrics(group_id)
        m.member_reply_to_ai += 1
        return m

    def record_active_day(self, group_id: str, member_count: int = 0) -> GroupRoleMetrics:
        """记录活跃天数"""
        m = self.get_metrics(group_id)
        m.active_days += 1
        if member_count > 0:
            m.member_count = member_count
        return m

    def get_role_summary(self, group_id: str) -> dict[str, Any]:
        """返回群角色摘要，供 metacontext 注入"""
        m = self.get_metrics(group_id)
        return {
            "group_id": group_id,
            "at_ratio": round(m.at_ratio, 3),
            "engagement_rate": round(m.engagement_rate, 3),
            "role_score": round(m.role_score, 3),
            "total_messages": m.total_messages,
            "reply_count": m.reply_count,
        }

    # ── 群氛围 ──────────────────────────────────────────────

    def record_emotion_snapshot(
        self,
        group_id: str,
        emotion_state: dict[str, float],
        conflict: bool = False,
    ) -> None:
        """记录群氛围情绪快照（从 AI 的 inner_thoughts → user_read.mood 反推）"""
        if not self._enabled:
            return
        snap = GroupAtmosphere(
            group_id=group_id,
            avg_emotion=emotion_state,
            conflict_events=1 if conflict else 0,
        )
        if group_id not in self._atmosphere_snapshots:
            self._atmosphere_snapshots[group_id] = []
        self._atmosphere_snapshots[group_id].append(snap)

        # 限制窗口大小
        if len(self._atmosphere_snapshots[group_id]) > self._role_metrics_window:
            self._atmosphere_snapshots[group_id] = self._atmosphere_snapshots[group_id][-self._role_metrics_window:]

    def get_recent_atmosphere(self, group_id: str, n: int = 5) -> list[GroupAtmosphere]:
        """返回最近 N 条氛围快照"""
        snaps = self._atmosphere_snapshots.get(group_id, [])
        return snaps[-n:]

    def get_atmosphere_summary(self, group_id: str) -> dict[str, Any] | None:
        """返回群氛围摘要，供 metacontext 注入"""
        snaps = self._atmosphere_snapshots.get(group_id, [])
        if not snaps:
            return None
        return {
            "group_id": group_id,
            "snapshot_count": len(snaps),
            "total_conflict_events": sum(s.conflict_events for s in snaps),
            "latest_emotion": snaps[-1].avg_emotion if snaps[-1].avg_emotion else {},
        }

    async def persist_atmosphere(self, group_id: str) -> None:
        """将最新快照写入 MemoryStore global/group/{gid}/atmosphere"""
        if not self._enabled or self._memory is None:
            return
        snaps = self._atmosphere_snapshots.get(group_id, [])
        if not snaps:
            return
        latest = snaps[-1]
        from chat_core.core.types import MemoryEntry
        entry = MemoryEntry(
            namespace=f"global/group/{group_id}",
            key="atmosphere",
            value={
                "avg_emotion": latest.avg_emotion,
                "conflict_events": latest.conflict_events,
                "snapshot_at": time.time(),
            },
            entity_type="group_atmosphere",
            topic_tags=["群氛围", f"群{group_id}"],
            salience=3.0,
            ttl=86400 * 7,  # 7 天过期
        )
        try:
            await self._memory.save(entry)
        except Exception:
            logger.debug(f"Failed to persist atmosphere for group {group_id}", exc_info=True)
