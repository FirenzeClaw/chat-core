"""用户会话管理 — per-user 状态隔离

为 QQ Bot 多用户场景提供用户级别的会话状态管理。
UserSession 只保存元数据，不持有对话内容。
对话内容由子 Session (ReActLoop) 自身和 MemoryStore 管理。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class UserSession:
    """单个 QQ 用户的会话元数据。

    不持有对话历史——子 Session 自身的 message history 和
    MemoryStore 归档负责跨 turn 上下文。
    """

    user_id: str
    session_key: str  # user_{uid} 或 group_{gid}

    # turn 计数
    turn_counter: int = 0

    # 时间戳
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)

    # 关联的子 Session 实例（类型 Any 延迟绑定，避免循环导入）
    sub_session: Any = None

    def is_expired(self, ttl: float = 3600) -> bool:
        """检查会话是否过期（默认 1 小时无活动）"""
        return time.time() - self.last_active > ttl

    def touch(self) -> None:
        """更新最后活跃时间"""
        self.last_active = time.time()


class SessionManager:
    """用户会话管理器

    内存中维护 session_key → UserSession 映射，
    支持 TTL 过期清理。
    """

    def __init__(self, ttl: float = 3600):
        self._sessions: dict[str, UserSession] = {}
        self._ttl = ttl

    def get_or_create(self, user_id: str, session_key: str = "") -> UserSession:
        """获取或创建用户会话"""
        key = session_key or f"user_{user_id}"
        if key in self._sessions:
            session = self._sessions[key]
            if not session.is_expired(self._ttl):
                session.touch()
                return session
        session = UserSession(user_id=user_id, session_key=key)
        self._sessions[key] = session
        return session

    def get(self, session_key: str) -> UserSession | None:
        """获取会话，不存在或已过期返回 None"""
        session = self._sessions.get(session_key)
        if session is None:
            return None
        if session.is_expired(self._ttl):
            del self._sessions[session_key]
            return None
        return session

    def remove(self, session_key: str) -> None:
        """移除会话"""
        self._sessions.pop(session_key, None)

    def cleanup_expired(self) -> int:
        """清理所有过期会话，返回清理数量"""
        expired_keys = [
            k for k, s in self._sessions.items()
            if s.is_expired(self._ttl)
        ]
        for k in expired_keys:
            del self._sessions[k]
        return len(expired_keys)

    @property
    def session_count(self) -> int:
        return len(self._sessions)
