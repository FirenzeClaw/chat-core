"""内容安全过滤器 — 集中管理所有安全相关检查"""

from __future__ import annotations

import time
from typing import Any

from chat_core.config import get_config

# 阻断关键词列表
BLOCKED_KEYWORDS: list[str] = [
    "自杀", "自残", "割腕", "跳楼",
    "杀了我", "想死", "自伤",
]

# 额外敏感词（警告但不阻断）
WARNING_KEYWORDS: list[str] = [
    "抑郁", "焦虑", "崩溃",
]


class ContentFilter:
    """集中式内容安全过滤器"""

    @staticmethod
    def check_safety(text: str) -> bool:
        """检查文本是否包含阻断关键词。

        Returns:
            True 表示内容被阻断（不安全），False 表示安全。
        """
        return any(kw in text for kw in BLOCKED_KEYWORDS)

    @staticmethod
    def check_warning(text: str) -> bool:
        """检查文本是否包含警告关键词"""
        return any(kw in text for kw in WARNING_KEYWORDS)

    @staticmethod
    def get_blocked_keywords() -> list[str]:
        return list(BLOCKED_KEYWORDS)

    @staticmethod
    def get_warning_keywords() -> list[str]:
        return list(WARNING_KEYWORDS)
