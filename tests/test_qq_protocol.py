"""QQ 协议模块单元测试"""

from __future__ import annotations

import pytest

from chat_core.qq.protocol import (
    MessageContext,
    _build_message_context,
    _is_duplicate,
)


class TestMessageContext:
    """MessageContext 属性测试"""

    def test_c2c_properties(self):
        ctx = MessageContext(
            user_id="user123", username="testuser",
            content="你好", msg_type="C2C_MESSAGE_CREATE",
        )
        assert ctx.is_group is False
        assert ctx.is_at is False
        assert ctx.is_direct is True
        assert ctx.session_key == "user_user123"
        assert ctx.scene == "c2c"

    def test_group_at_properties(self):
        ctx = MessageContext(
            user_id="user123", username="testuser",
            content="你好", msg_type="GROUP_AT_MESSAGE_CREATE",
            group_id="group456",
        )
        assert ctx.is_group is True
        assert ctx.is_at is True
        assert ctx.session_key == "group_group456"
        assert ctx.scene == "group"

    def test_scene_c2c(self):
        ctx = MessageContext(
            user_id="u1", username="", content="",
            msg_type="C2C_MESSAGE_CREATE",
        )
        assert ctx.scene == "c2c"

    def test_scene_group(self):
        ctx = MessageContext(
            user_id="u1", username="", content="",
            msg_type="GROUP_AT_MESSAGE_CREATE", group_id="g1",
        )
        assert ctx.scene == "group"


class TestBuildMessageContext:
    """_build_message_context 解析测试"""

    def test_c2c_event(self):
        event_data = {
            "id": "msg_001",
            "author": {"id": "user_abc", "username": "小明"},
            "content": "在吗",
        }
        ctx = _build_message_context("C2C_MESSAGE_CREATE", event_data)
        assert ctx.user_id == "user_abc"
        assert ctx.content == "在吗"
        assert ctx.msg_type == "C2C_MESSAGE_CREATE"

    def test_group_at_event(self):
        event_data = {
            "id": "msg_002",
            "author": {"id": "user_xyz"},
            "content": " 你好",
            "group_openid": "group_789",
        }
        ctx = _build_message_context("GROUP_AT_MESSAGE_CREATE", event_data)
        assert ctx.is_group is True
        assert ctx.group_id == "group_789"

    def test_attachments(self):
        event_data = {
            "id": "msg_003",
            "author": {"id": "user_img"},
            "content": "看这个",
            "attachments": [
                {"url": "https://example.com/img.jpg", "content_type": "image/jpeg"},
            ],
        }
        ctx = _build_message_context("C2C_MESSAGE_CREATE", event_data)
        assert ctx.image_urls == ["https://example.com/img.jpg"]

    def test_missing_author(self):
        event_data = {"id": "msg_005", "content": "hello"}
        ctx = _build_message_context("C2C_MESSAGE_CREATE", event_data)
        assert ctx.user_id == "unknown"


class TestDuplicateDetection:
    """消息去重测试"""

    def test_first_not_duplicate(self):
        from chat_core.qq import protocol
        protocol._seen_msg_ids.clear()
        assert not _is_duplicate("msg_new")

    def test_second_is_duplicate(self):
        assert _is_duplicate("msg_new")

    def test_different_not_duplicate(self):
        assert not _is_duplicate("msg_other")

    def test_clear_after_max(self):
        from chat_core.qq import protocol
        protocol._seen_msg_ids.clear()
        for i in range(protocol.MAX_SEEN_IDS + 5):
            _is_duplicate(f"msg_{i:06d}")
        assert len(protocol._seen_msg_ids) <= protocol.MAX_SEEN_IDS
