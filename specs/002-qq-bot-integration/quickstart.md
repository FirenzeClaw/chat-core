# Quickstart: QQ Bot

**Feature**: `002-qq-bot-integration`
**Created**: 2026-07-09

---

## 前置条件

1. 已注册 QQ Bot 并获取 AppID + AppSecret ([q.qq.com](https://q.qq.com))
2. 已配置 DeepSeek API Key
3. Python 3.12+，已安装依赖

## 快速启动

```bash
# 1. 配置凭证
cp chat_core/.env.example chat_core/.env
# 编辑 .env:
#   QQ_BOT_APPID=你的AppID
#   QQ_BOT_SECRET=你的AppSecret
#   DEEPSEEK_API_KEY=sk-你的Key

# 2. 安装依赖
pip install -e .

# 3. 启动 QQ Bot
python -m chat_core.qq_bot

# 或
chat-core-qq
```

## 验证

```bash
# 健康检查
curl http://localhost:18090/health
# → {"status": "ok", "service": "chat-core-qq-bot"}

# 日志应显示:
# [INFO] chat-core QQ Bot 启动中...
# [INFO] WebSocket 已连接: wss://api.sgroup.qq.com/websocket
# [INFO] Hello received, heartbeat_interval=45.00s
# [INFO] Ready, session_id=...
```

## 测试

1. QQ 私聊机器人发送 "你好" → 应在 2-5 秒内收到回复
2. QQ 群聊 @机器人 发送消息 → 应收到回复
3. 群聊发送普通消息（不 @）→ 不应收到回复
4. 连续对话 5 轮 → AI 应能引用第一轮的内容

## 停止

```bash
# Ctrl+C 优雅关闭
# 日志显示:
# [INFO] 收到信号 2，准备关闭...
# [INFO] 已关闭 — sessions=3 turns=15
```
