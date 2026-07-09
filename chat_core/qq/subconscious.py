"""潜意识注入器 — 按竞态程度调节子 Session 的潜意识上下文质量"""

from __future__ import annotations

import logging

logger = logging.getLogger("chat_core.qq.subconscious")


class SubconsciousInjector:
    """根据竞态严重程度截断/降级潜意识注入上下文。

    注入策略：
    - low (1-2 active): 保留全部上下文
    - medium (3-4 active): 截断至 50%
    - high (5+ active): 仅保留前 2 句作为方向摘要
    """

    def inject(self, context: str, severity: str) -> str:
        """按竞态级别调节注入质量。

        Args:
            context: 完整的潜意识注入上下文
            severity: "low" / "medium" / "high"

        Returns:
            调节后的上下文
        """
        if not context:
            return ""

        if severity == "low":
            return context

        if severity == "medium":
            half = len(context) // 2
            return context[:half]

        if severity == "high":
            sentences = context.replace("！", "。").replace("？", "。").split("。")
            summary = "。".join(s.strip() for s in sentences[:2] if s.strip())
            if summary:
                summary += "。"
            logger.debug("SubconsciousInjector high: truncated %d→%d chars", len(context), len(summary))
            return summary

        return context

    def priority_sort(self, sessions: dict[str, float]) -> list[str]:
        """按最近活跃度排序——最活跃的优先获得高质量注入（顾此薄彼）。

        Args:
            sessions: {session_key: last_active_timestamp}

        Returns:
            按活跃度降序排列的 session_key 列表
        """
        return sorted(sessions, key=lambda k: sessions[k], reverse=True)
