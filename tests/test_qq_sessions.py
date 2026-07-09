"""QQ 会话管理单元测试"""

from __future__ import annotations

import time

import pytest

from chat_core.qq.sessions import UserSession, SessionManager


class TestUserSession:
    """UserSession 基础功能测试"""

    def test_create_session(self):
        s = UserSession(user_id="user123", session_key="user_user123")
        assert s.user_id == "user123"
        assert s.session_key == "user_user123"
        assert s.turn_counter == 0
        assert s.sub_session is None

    def test_touch(self):
        s = UserSession(user_id="user123", session_key="user_user123")
        original = s.last_active
        time.sleep(0.01)
        s.touch()
        assert s.last_active > original

    def test_is_expired(self):
        s = UserSession(user_id="user123", session_key="user_user123")
        assert not s.is_expired(ttl=3600)

    def test_is_expired_short_ttl(self):
        s = UserSession(user_id="user123", session_key="user_user123")
        time.sleep(0.02)
        assert s.is_expired(ttl=0.01)

    def test_sub_session_binding(self):
        """子 Session 引用可设置"""
        s = UserSession(user_id="user123", session_key="user_user123")
        mock_sub = object()
        s.sub_session = mock_sub
        assert s.sub_session is mock_sub

    def test_no_conversation_history(self):
        """确认 conversation_history 已被移除"""
        s = UserSession(user_id="user123", session_key="user_user123")
        assert not hasattr(s, "conversation_history")
        assert not hasattr(s, "add_turn")


class TestSessionManager:
    """SessionManager CRUD 测试"""

    def test_get_or_create(self):
        sm = SessionManager(ttl=3600)
        s = sm.get_or_create("user123", "user_user123")
        assert s.user_id == "user123"
        assert sm.session_count == 1

    def test_get_or_create_returns_existing(self):
        sm = SessionManager(ttl=3600)
        s1 = sm.get_or_create("user123", "user_user123")
        s1.turn_counter = 5
        s2 = sm.get_or_create("user123", "user_user123")
        assert s2 is s1
        assert s2.turn_counter == 5

    def test_different_users(self):
        sm = SessionManager(ttl=3600)
        sm.get_or_create("user_a", "user_user_a")
        sm.get_or_create("user_b", "user_user_b")
        assert sm.session_count == 2

    def test_get_returns_none_for_unknown(self):
        sm = SessionManager(ttl=3600)
        assert sm.get("nonexistent") is None

    def test_remove(self):
        sm = SessionManager(ttl=3600)
        sm.get_or_create("user123", "user_user123")
        sm.remove("user_user123")
        assert sm.session_count == 0

    def test_cleanup_expired(self):
        sm = SessionManager(ttl=0.01)
        sm.get_or_create("user123", "user_user123")
        time.sleep(0.02)
        cleaned = sm.cleanup_expired()
        assert cleaned == 1
        assert sm.session_count == 0
