"""pytest 共享 fixtures"""
import pytest


@pytest.fixture
def sample_messages():
    """生成示例消息列表"""
    from chat_core.core.types import Message
    return [
        Message(role="system", content="You are a helpful assistant."),
        Message(role="user", content="Hello!"),
    ]
