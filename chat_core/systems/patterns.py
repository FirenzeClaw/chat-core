"""PatternDetector — 仪式感/习惯检测 (Spec 008)

检测问候重复、时间规律、话题循环、内部梗四种模式。
中间态跨 session 持久化（_pending → patterns），达标后迁移。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import InteractionPattern

logger = logging.getLogger(__name__)


class PatternDetector:
    """模式检测器：识别重复交互模式。

    MemoryStore 引用由外部注入（用于 _pending 持久化）。
    """

    def __init__(self) -> None:
        cfg = get_config()
        pc = cfg.patterns_config()
        self._enabled: bool = bool(pc.get("enabled", True))

        min_rep = pc.get("min_repetitions", {})
        self._min_greeting: int = int(min_rep.get("greeting", 3))
        self._min_timing: int = int(min_rep.get("timing", 5))
        self._min_topic_cycle: int = int(min_rep.get("topic_cycle", 3))
        self._min_inside_joke: int = int(min_rep.get("inside_joke", 2))

        self._joke_keywords: list[str] = list(pc.get("inside_joke_keywords", [
            "好笑", "有趣", "笑了", "哈哈哈", "笑死",
        ]))

        # MemoryStore 引用（由外部设置）
        self._memory: Any = None

        # 内存缓存：避免每轮都查询 MemoryStore
        self._pending: dict[str, dict[str, Any]] = {}  # key = f"{user_id}:{pattern_type}:{hash}"
        self._patterns: dict[str, list[InteractionPattern]] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_memory(self, memory: Any) -> None:
        self._memory = memory

    # ── 检测入口 ───────────────────────────────────────────

    async def detect(
        self,
        user_id: str,
        user_message: str,
        inner_thoughts_text: str = "",
    ) -> list[InteractionPattern]:
        """检测本轮消息中的模式。

        Returns:
            本轮新达标的模式列表（供注入 system prompt）
        """
        if not self._enabled:
            return []

        new_patterns: list[InteractionPattern] = []
        now = datetime.now()
        now_iso = now.isoformat()
        hour_bucket = f"{now.hour:02d}:00-{now.hour + 1:02d}:00" if now.hour < 23 else "23:00-00:00"

        # 1. greeting 检测
        greeting = await self._detect_greeting(user_id, user_message, now_iso)
        if greeting:
            new_patterns.append(greeting)

        # 2. timing 检测
        timing = await self._detect_timing(user_id, hour_bucket, now_iso)
        if timing:
            new_patterns.append(timing)

        # 3. topic_cycle 检测
        topic = await self._detect_topic_cycle(user_id, user_message, now_iso)
        if topic:
            new_patterns.append(topic)

        # 4. inside_joke 检测
        joke = await self._detect_inside_joke(user_id, user_message, inner_thoughts_text, now_iso)
        if joke:
            new_patterns.append(joke)

        return new_patterns

    # ── 各模式检测 ─────────────────────────────────────────

    async def _detect_greeting(
        self, user_id: str, message: str, now_iso: str
    ) -> InteractionPattern | None:
        """检测问候模式：相同问候文本 ≥ min_greeting 次"""
        # 只检测短消息（≤10 字）
        clean = message.strip()
        if len(clean) > 10:
            return None

        # key: greeting/{template_hash}，避免特殊字符问题
        import hashlib
        template_hash = hashlib.md5(clean.encode()).hexdigest()[:8]
        pending_ns = f"user/{user_id}/patterns/_pending"
        pending_k = f"greeting/{template_hash}"
        current = await self._get_pending(pending_ns, pending_k)
        current["current_streak"] = current.get("current_streak", 0) + 1
        current["last_seen"] = now_iso
        # 保存原始模板文本
        current["_template"] = clean

        if current["current_streak"] >= self._min_greeting:
            # 达标：迁移至正式 patterns
            pattern = InteractionPattern(
                pattern_type="greeting",
                template=clean,
                count=current["current_streak"],
                last_seen=now_iso,
                time_distribution=current.get("time_distribution", {}),
            )
            await self._promote_pattern(user_id, pattern)
            await self._delete_pending(pending_ns, pending_k)
            return pattern

        await self._set_pending(pending_ns, pending_k, current)
        return None

    async def _detect_timing(
        self, user_id: str, hour_bucket: str, now_iso: str
    ) -> InteractionPattern | None:
        """检测时间规律：某时间段占比 > 60% 且总次数 ≥ min_timing"""
        pending_ns = f"user/{user_id}/patterns/_pending"
        pending_k = "timing/global"
        current = await self._get_pending(pending_ns, pending_k)
        current["current_streak"] = current.get("current_streak", 0) + 1
        current["last_seen"] = now_iso

        td = current.get("time_distribution", {})
        td[hour_bucket] = td.get(hour_bucket, 0) + 1
        current["time_distribution"] = td

        total = current["current_streak"]
        if total >= self._min_timing:
            # 找 dominant 时间段
            dominant_bucket = max(td, key=td.get)
            dominant_ratio = td[dominant_bucket] / total
            if dominant_ratio > 0.6:
                pattern = InteractionPattern(
                    pattern_type="timing",
                    template=dominant_bucket,
                    count=total,
                    last_seen=now_iso,
                    time_distribution=td,
                )
                await self._promote_pattern(user_id, pattern)
                await self._delete_pending(pending_ns, pending_k)
                return pattern

        await self._set_pending(pending_ns, pending_k, current)
        return None

    async def _detect_topic_cycle(
        self, user_id: str, message: str, now_iso: str
    ) -> InteractionPattern | None:
        """检测话题循环：相同关键词被提及 ≥ min_topic_cycle 次"""
        # 用前 20 字作为 topic 标识
        topic_key = message[:20].strip()
        if len(topic_key) < 2:
            return None

        import hashlib
        topic_hash = hashlib.md5(topic_key.encode()).hexdigest()[:8]
        pending_ns = f"user/{user_id}/patterns/_pending"
        pending_k = f"topic_cycle/{topic_hash}"
        current = await self._get_pending(pending_ns, pending_k)
        current["current_streak"] = current.get("current_streak", 0) + 1
        current["last_seen"] = now_iso
        current["_template"] = topic_key

        if current["current_streak"] >= self._min_topic_cycle:
            pattern = InteractionPattern(
                pattern_type="topic_cycle",
                template=topic_key,
                count=current["current_streak"],
                last_seen=now_iso,
            )
            await self._promote_pattern(user_id, pattern)
            await self._delete_pending(pending_ns, pending_k)
            return pattern

        await self._set_pending(pending_ns, pending_k, current)
        return None

    async def _detect_inside_joke(
        self, user_id: str, message: str, inner_thoughts: str, now_iso: str
    ) -> InteractionPattern | None:
        """检测内部梗：inner_thoughts 含关键词 + 同话题 ≥ min_inside_joke 次"""
        if not inner_thoughts:
            return None
        # 检查是否有笑点
        has_humor = any(kw in inner_thoughts for kw in self._joke_keywords)
        if not has_humor:
            return None

        joke_key = message[:20].strip()
        if len(joke_key) < 2:
            return None

        import hashlib
        joke_hash = hashlib.md5(joke_key.encode()).hexdigest()[:8]
        pending_ns = f"user/{user_id}/patterns/_pending"
        pending_k = f"inside_joke/{joke_hash}"
        current = await self._get_pending(pending_ns, pending_k)
        current["current_streak"] = current.get("current_streak", 0) + 1
        current["last_seen"] = now_iso
        current["_template"] = joke_key

        if current["current_streak"] >= self._min_inside_joke:
            pattern = InteractionPattern(
                pattern_type="inside_joke",
                template=joke_key,
                count=current["current_streak"],
                last_seen=now_iso,
            )
            await self._promote_pattern(user_id, pattern)
            await self._delete_pending(pending_ns, pending_k)
            return pattern

        await self._set_pending(pending_ns, pending_k, current)
        return None

    # ── MemoryStore 操作 ────────────────────────────────────

    async def _get_pending(self, namespace: str, key: str) -> dict[str, Any]:
        """从 MemoryStore 读取中间态计数，带内存缓存。

        Args:
            namespace: e.g. "user/u1/patterns/_pending"
            key: e.g. "greeting/abc12345"
        """
        cache_key = f"{namespace}/{key}"
        if cache_key in self._pending:
            return self._pending[cache_key]
        if self._memory is None:
            return {}

        try:
            entry = await self._memory.get(namespace, key)
            if entry and isinstance(entry.value, dict):
                self._pending[cache_key] = entry.value
                return entry.value
        except Exception:
            pass
        return {}

    async def _set_pending(self, namespace: str, key: str, data: dict[str, Any]) -> None:
        """写入 MemoryStore 中间态计数"""
        cache_key = f"{namespace}/{key}"
        self._pending[cache_key] = data
        if self._memory is None:
            return
        try:
            from chat_core.core.types import MemoryEntry
            entry = MemoryEntry(
                namespace=namespace, key=key, value=data,
                entity_type="pattern_pending", salience=1.0,
            )
            await self._memory.save(entry)
        except Exception:
            logger.debug(f"Failed to persist pending pattern: {namespace}/{key}", exc_info=True)

    async def _delete_pending(self, namespace: str, key: str) -> None:
        """达标后删除中间态"""
        cache_key = f"{namespace}/{key}"
        self._pending.pop(cache_key, None)
        if self._memory is None:
            return
        try:
            await self._memory.delete(namespace, key)
        except Exception:
            pass

    async def _promote_pattern(self, user_id: str, pattern: InteractionPattern) -> None:
        """达标后写入正式 patterns 命名空间。

        key 使用 {pattern_type}/{template_hash} 避免同类型多模式冲突。
        例如同一用户的两个不同问候 "早啊" 和 "你好" 不会互相覆盖。
        """
        if user_id not in self._patterns:
            self._patterns[user_id] = []
        self._patterns[user_id].append(pattern)

        if self._memory is None:
            return
        try:
            import hashlib
            template_hash = hashlib.md5(pattern.template.encode()).hexdigest()[:8]
            from chat_core.core.types import MemoryEntry
            entry = MemoryEntry(
                namespace=f"user/{user_id}/patterns",
                key=f"{pattern.pattern_type}/{template_hash}",
                value={
                    "pattern_type": pattern.pattern_type,
                    "template": pattern.template,
                    "count": pattern.count,
                    "last_seen": pattern.last_seen,
                    "time_distribution": pattern.time_distribution,
                },
                entity_type="interaction_pattern",
                topic_tags=[pattern.pattern_type, "社交模式"],
                salience=5.0,
            )
            await self._memory.save(entry)
        except Exception:
            logger.debug(f"Failed to promote pattern for user {user_id}", exc_info=True)

    # ── 消费：生成 system prompt 注入文本 ──────────────────

    def get_pattern_injection(self, user_id: str) -> str | None:
        """生成社交模式 system prompt 注入文本"""
        patterns = self._patterns.get(user_id, [])
        if not patterns:
            return None

        lines: list[str] = ["[社交模式]"]
        for p in patterns[-3:]:  # 最近 3 个
            if p.pattern_type == "greeting":
                lines.append("  这个用户通常在跟你说\u201c" + p.template + "\u201d。")
            elif p.pattern_type == "timing":
                lines.append("  这个用户通常在 " + p.template + " 时间段找你聊天。")
            elif p.pattern_type == "topic_cycle":
                lines.append("  你们经常聊到\u201c" + p.template + "\u201d相关话题。")
            elif p.pattern_type == "inside_joke":
                lines.append("  你们之间有个内部梗关于\u201c" + p.template + "\u201d——可以在适当的时候自然提起。")

        return "\n".join(lines) if len(lines) > 1 else None
