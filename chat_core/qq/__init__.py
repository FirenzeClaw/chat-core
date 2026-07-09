"""QQ Bot 集成包 — WebSocket 协议 + 会话管理 + TurnManager 适配器

使用延迟导入以支持部分模块独立使用。
"""

__all__ = [
    "MessageContext",
    "run_qq_loop",
    "send_message",
    "fetch_user_nickname",
    "UserSession",
    "SessionManager",
    "BotAdapter",
    "RaceTracker",
    "SubconsciousInjector",
]


def __getattr__(name: str):
    if name in ("MessageContext", "run_qq_loop", "send_message", "fetch_user_nickname"):
        from chat_core.qq.protocol import (
            MessageContext, run_qq_loop, send_message, fetch_user_nickname,
        )
        return locals()[name]
    if name in ("UserSession", "SessionManager"):
        from chat_core.qq.sessions import UserSession, SessionManager
        return locals()[name]
    if name == "BotAdapter":
        from chat_core.qq.adapter import BotAdapter
        return locals()[name]
    if name == "RaceTracker":
        from chat_core.qq.race_tracker import RaceTracker
        return locals()[name]
    if name == "SubconsciousInjector":
        from chat_core.qq.subconscious import SubconsciousInjector
        return locals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
