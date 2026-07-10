"""QQ Bot WebSocket 协议模块

处理 QQ Bot API 的所有协议细节：连接、鉴权、心跳、Resume、消息接收。
通过 on_message 回调将业务消息传递给调用方。

从 chat-engine 的 qq_protocol.py 学习 WebSocket 调用模式，
适配 chat-core 的 Config 体系。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Awaitable

from chat_core.config import get_config

logger = logging.getLogger("chat_core.qq.protocol")

# Intents 位图
# PUBLIC_GUILD_MESSAGES (1<<30): 频道 @ (AT_MESSAGE_CREATE)
# GROUP_AND_C2C_EVENT  (1<<25): 群聊 @ + 单聊 (GROUP_AT_MESSAGE_CREATE, C2C_MESSAGE_CREATE)
# DIRECT_MESSAGE       (1<<12): 频道私信 (DIRECT_MESSAGE_CREATE)
INTENTS = (1 << 30) | (1 << 25) | (1 << 12)

# 消息去重 — 使用 deque 实现 LRU 逐出，避免全量 clear() 丢失去重状态
_seen_msg_ids: deque[str] = deque(maxlen=10000)
MAX_SEEN_IDS = 10000

# Token 缓存
_access_token: str = ""
_token_expires: float = 0


async def _get_access_token(appid: str, secret: str) -> str:
    """获取/刷新 QQ Bot access_token（7200s 有效期，提前 5 分钟刷新）

    失败时设 60s 短过期避免缓存空 token，最多重试 2 次。
    """
    import aiohttp

    global _access_token, _token_expires
    if _access_token and time.time() < _token_expires:
        return _access_token

    url = "https://bots.qq.com/app/getAppAccessToken"
    payload = {"appId": appid, "clientSecret": secret}

    last_error: str = ""
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        last_error = f"HTTP {resp.status}"
                        logger.warning(
                            "access_token 获取失败 (attempt %d/3): %s", attempt + 1, last_error
                        )
                        if attempt < 2:
                            await asyncio.sleep(1 * (attempt + 1))
                        continue

                    data = await resp.json()
                    token = data.get("access_token", "")
                    if not token:
                        last_error = "empty token in response"
                        logger.warning(
                            "access_token 响应无有效 token (attempt %d/3): %s",
                            attempt + 1, data.get("message", data),
                        )
                        if attempt < 2:
                            await asyncio.sleep(1 * (attempt + 1))
                        continue

                    _access_token = token
                    expires = int(data.get("expires_in", 7200))
                    _token_expires = time.time() + expires - 300
                    logger.info("access_token 已获取，有效期 %ds", expires)
                    return _access_token

        except Exception as e:
            last_error = str(e)
            logger.warning(
                "access_token 请求异常 (attempt %d/3): %s", attempt + 1, e
            )
            if attempt < 2:
                await asyncio.sleep(1 * (attempt + 1))

    # 全部重试失败：设短过期避免持续打 API，返回空字符串让调用方处理
    _token_expires = time.time() + 60
    logger.error("access_token 获取全部失败 (3 attempts): %s", last_error)
    return ""


def _is_duplicate(msg_id: str) -> bool:
    """检查消息是否重复（deque maxlen 自动逐出最旧条目，无需全量清空）"""
    if msg_id in _seen_msg_ids:
        return True
    _seen_msg_ids.append(msg_id)
    return False


# ── 类型化消息上下文 ──────────────────────────────────────

OnMessageCallback = Callable[["MessageContext"], Awaitable[None]]


@dataclass
class MessageContext:
    """类型化消息上下文 — QQ 协议层与业务层之间的缝线。

    替代 untyped dict，所有 QQ 特定字段在此集中定义。
    """

    user_id: str
    username: str
    content: str
    msg_type: str  # C2C_MESSAGE_CREATE, GROUP_AT_MESSAGE_CREATE, etc.
    msg_id: str = ""
    group_id: str = ""
    channel_id: str = ""
    guild_id: str = ""
    ref_msg_id: str = ""
    timestamp: float = 0.0
    image_urls: list[str] = field(default_factory=list)

    @property
    def is_group(self) -> bool:
        return "GROUP" in self.msg_type

    @property
    def is_at(self) -> bool:
        return "AT_MESSAGE" in self.msg_type

    @property
    def is_direct(self) -> bool:
        return "DIRECT" in self.msg_type or "C2C" in self.msg_type

    @property
    def session_key(self) -> str:
        """会话标识：私聊为 user_{uid}，群聊为 group_{gid}"""
        if self.is_group:
            return f"group_{self.group_id or self.user_id}"
        return f"user_{self.user_id}"

    @property
    def scene(self) -> str:
        """场景标识：c2c（私聊）或 group（群聊）"""
        return "group" if self.is_group else "c2c"


def _build_message_context(event_type: str, event_data: dict) -> MessageContext:
    """从 QQ 事件数据构造类型化消息上下文"""
    author = event_data.get("author", {})
    msg_id = event_data.get("id", "")
    attachments = event_data.get("attachments", [])
    image_urls = [
        att.get("url", "")
        for att in attachments
        if att.get("content_type", "").startswith("image/")
    ]
    return MessageContext(
        user_id=author.get("id", author.get("user_openid", "unknown")),
        username=author.get("username", ""),
        content=event_data.get("content", ""),
        msg_type=event_type,
        msg_id=msg_id,
        group_id=event_data.get("group_openid", ""),
        channel_id=event_data.get("channel_id", ""),
        guild_id=event_data.get("guild_id", ""),
        ref_msg_id=msg_id,
        timestamp=time.time(),
        image_urls=image_urls,
    )


# ── 消息发送 ────────────────────────────────────────────

# QQ API 错误码分类
_RETRYABLE_BACKOFF = {22009}       # 超频：退避重试
_RETRYABLE_ONCE = {304082, 304083}  # 富媒体失败：重试1次


async def fetch_user_nickname(openid: str, appid: str = "", secret: str = "") -> str:
    """通过 QQ API 获取用户昵称。

    QQ Bot API v2 无直接用户信息端点。尝试 /v2/users/{openid}，
    失败时返回空字符串（调用方使用 openid 作为匿名标识）。
    """
    import aiohttp

    if not appid or not secret:
        cfg = get_config()
        qq_cfg = cfg.qq_config()
        appid = appid or qq_cfg.get("appid", "")
        secret = secret or qq_cfg.get("secret", "")

    if not openid:
        return ""

    token = await _get_access_token(appid, secret)
    url = f"https://api.sgroup.qq.com/v2/users/{openid}"
    headers = {"Authorization": f"QQBot {token}"}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("username", data.get("nick", ""))
                # 404=endpoint不存在, 使用openid作为fallback
                if resp.status == 404:
                    logger.debug("用户昵称API不可用(404)，使用openid标识")
                else:
                    logger.debug("获取昵称失败: openid=%s status=%d", openid[:12], resp.status)
                return ""
    except Exception:
        logger.debug("获取昵称异常: openid=%s", openid[:12])
        return ""


async def _send_once(url: str, payload: dict, headers: dict) -> tuple[bool, int]:
    """单次发送请求，返回 (success, status_code)"""
    import aiohttp

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200:
                    return True, 200
                return False, resp.status
    except Exception:
        return False, 0


async def send_message(
    ctx: MessageContext,
    content: str,
    appid: str = "",
    secret: str = "",
) -> bool:
    """通过 QQ REST API 发送回复消息（含错误分类重试）。

    - 22009 (超频): backoff 退避 (1s/3s/9s)
    - 304082/304083 (富媒体失败): 重试 1 次
    - 其他错误: 记录日志，丢弃

    Returns:
        bool: 发送成功返回 True
    """
    if not appid or not secret:
        cfg = get_config()
        qq_cfg = cfg.qq_config()
        appid = appid or qq_cfg.get("appid", "")
        secret = secret or qq_cfg.get("secret", "")

    # 确定发送端点
    if ctx.msg_type == "AT_MESSAGE_CREATE":
        if not ctx.channel_id:
            logger.warning("AT_MESSAGE 缺少 channel_id")
            return False
        url = f"https://api.sgroup.qq.com/v2/channels/{ctx.channel_id}/messages"
    elif ctx.msg_type == "DIRECT_MESSAGE_CREATE":
        if not ctx.guild_id:
            logger.warning("DIRECT_MESSAGE 缺少 guild_id")
            return False
        url = f"https://api.sgroup.qq.com/v2/dms/{ctx.guild_id}/messages"
    elif ctx.is_group:
        if not ctx.group_id:
            logger.warning("群聊消息缺少 group_id")
            return False
        url = f"https://api.sgroup.qq.com/v2/groups/{ctx.group_id}/messages"
    else:
        url = f"https://api.sgroup.qq.com/v2/users/{ctx.user_id}/messages"

    msg_id = ctx.ref_msg_id if ctx.ref_msg_id else uuid.uuid4().hex
    seq = int(time.time() * 1000) % 100000
    payload = {"content": content, "msg_type": 0, "msg_id": msg_id, "msg_seq": seq}

    logger.info("发送 QQ 消息: type=%s content=%.60s...", ctx.msg_type, content)

    token = await _get_access_token(appid, secret)
    headers = {"Authorization": f"QQBot {token}", "Content-Type": "application/json"}

    # 首次发送
    success, status = await _send_once(url, payload, headers)
    if success:
        logger.info("回复已发送 → %s", ctx.user_id[:12])
        return True

    # 错误分类处理
    if status in _RETRYABLE_BACKOFF:
        for delay in [1, 3, 9]:
            logger.info("超频退避 %ds → retry", delay)
            await asyncio.sleep(delay)
            success, status = await _send_once(url, payload, headers)
            if success:
                logger.info("退避重试成功 → %s", ctx.user_id[:12])
                return True
        logger.warning("超频重试全部失败: user=%s", ctx.user_id[:12])
    elif status in _RETRYABLE_ONCE:
        logger.info("富媒体失败，重试 1 次")
        await asyncio.sleep(1)
        success, _ = await _send_once(url, payload, headers)
        if success:
            logger.info("富媒体重试成功 → %s", ctx.user_id[:12])
            return True
        logger.warning("富媒体重试失败: user=%s", ctx.user_id[:12])
    else:
        logger.warning("发送失败 status=%d: user=%s", status, ctx.user_id[:12])

    return False


# ── WebSocket 主循环 ─────────────────────────────────────

async def _heartbeat(ws: aiohttp.ClientWebSocketResponse, interval: float) -> None:
    """心跳协程：按 Hello 下发的 interval 发送心跳"""
    while True:
        await asyncio.sleep(interval)
        try:
            await ws.send_json({"op": 1, "d": None})
        except Exception:
            break


async def run_qq_loop(
    on_message: OnMessageCallback,
    appid: str = "",
    secret: str = "",
    ws_url: str = "",
) -> None:
    """QQ Bot WebSocket 主循环

    状态机: hello → identify/resume → running
    通过 on_message 回调将每条业务消息传递给调用方。

    Args:
        on_message: async callable(ctx) — 收到消息时的回调
        appid: QQ Bot AppID（为空时从 config 读取）
        secret: QQ Bot AppSecret
        ws_url: WebSocket URL
    """
    global _access_token, _token_expires

    # 从 config 获取凭证
    if not appid or not secret or not ws_url:
        cfg = get_config()
        qq_cfg = cfg.qq_config()
        appid = appid or qq_cfg.get("appid", "")
        secret = secret or qq_cfg.get("secret", "")
        ws_url = ws_url or qq_cfg.get("ws_url", "wss://api.sgroup.qq.com/websocket")

    if not appid or not secret:
        logger.error("QQ Bot AppID/Secret 未配置，无法启动")
        return

    import aiohttp

    _session_id: str = ""
    _latest_s: int | None = None

    session = aiohttp.ClientSession()

    while True:
        try:
            async with session.ws_connect(ws_url) as ws:
                logger.info("WebSocket 已连接: %s", ws_url)

                state = "hello"
                hello_interval = 45.0  # 默认 45s
                hb_task: asyncio.Task | None = None

                async for msg in ws:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        continue
                    data = json.loads(msg.data)
                    op = data.get("op", 0)

                    if "s" in data and data["s"] is not None:
                        _latest_s = data["s"]

                    # Phase 1: Hello
                    if state == "hello":
                        if op == 10:
                            hello_interval = (
                                data.get("d", {}).get("heartbeat_interval", 45000) / 1000
                            )
                            logger.info(
                                "Hello received, heartbeat_interval=%.2fs", hello_interval
                            )

                            token = await _get_access_token(appid, secret)
                            if _session_id and _latest_s is not None:
                                # 尝试 Resume
                                await ws.send_json({
                                    "op": 6,
                                    "d": {
                                        "token": f"QQBot {token}",
                                        "session_id": _session_id,
                                        "seq": _latest_s,
                                    },
                                })
                                logger.info(
                                    "发送 Resume (session=%s..., seq=%s)",
                                    _session_id[:8], _latest_s,
                                )
                                state = "resume"
                            else:
                                # 首次连接 → Identify
                                await ws.send_json({
                                    "op": 2,
                                    "d": {
                                        "token": f"QQBot {token}",
                                        "intents": INTENTS,
                                        "shard": [0, 1],
                                        "properties": {},
                                    },
                                })
                                logger.info("发送 Identify (intents=%d)", INTENTS)
                                state = "identify"

                    # Phase 2: Ready / Resumed
                    elif state in ("identify", "resume"):
                        if op == 0 and data.get("t") == "READY":
                            _session_id = data.get("d", {}).get("session_id", "")
                            logger.info("Ready, session_id=%s...", _session_id[:16])
                            state = "running"
                            hb_task = asyncio.create_task(
                                _heartbeat(ws, hello_interval)
                            )

                        elif op == 0 and data.get("t") == "RESUMED":
                            logger.info("Resumed，开始补发遗漏事件")
                            state = "running"
                            hb_task = asyncio.create_task(
                                _heartbeat(ws, hello_interval)
                            )

                        elif op == 9:
                            logger.warning("Invalid Session，重置状态后重连")
                            _session_id = ""
                            _latest_s = None
                            break

                        elif op == 7:
                            logger.info("服务端要求重连")
                            break

                    # Phase 3: Running
                    elif state == "running":
                        if op == 11:
                            # 心跳 ACK
                            pass
                        elif op == 7:
                            logger.info("服务端要求重连")
                            break
                        elif op == 9:
                            logger.warning("Session 失效，重置后重连")
                            _session_id = ""
                            _latest_s = None
                            break
                        elif op == 0:
                            event_type = data.get("t", "")
                            event_data = data.get("d", {})

                            # 处理关注的事件类型
                            if event_type in (
                                "C2C_MESSAGE_CREATE",
                                "GROUP_AT_MESSAGE_CREATE",
                                "GROUP_MESSAGE_CREATE",
                                "AT_MESSAGE_CREATE",
                                "DIRECT_MESSAGE_CREATE",
                            ):
                                msg_id = event_data.get("id", "")
                                if _is_duplicate(msg_id):
                                    logger.debug("重复消息已跳过: %s", msg_id[:16])
                                    continue

                                ctx = _build_message_context(event_type, event_data)
                                logger.info(
                                    "收到消息: type=%s user=%s content=%.50s",
                                    event_type, ctx.user_id[:12], ctx.content,
                                )
                                await on_message(ctx)

                # 清理心跳任务
                if hb_task:
                    hb_task.cancel()
                    try:
                        await hb_task
                    except asyncio.CancelledError:
                        pass

        except aiohttp.ClientError as e:
            logger.warning("WebSocket 连接错误: %s，5秒后重连", e)
        except Exception:
            logger.exception("WebSocket 异常，5秒后重连")

        await asyncio.sleep(5)
