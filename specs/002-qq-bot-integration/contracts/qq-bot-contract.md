# QQ Bot Behavior Contract

**Feature**: `002-qq-bot-integration`
**Created**: 2026-07-09

---

## Entry Point

```bash
python -m chat_core.qq_bot
# or after pip install:
chat-core-qq
```

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `QQ_BOT_APPID` | Yes | QQ Bot AppID from [q.qq.com](https://q.qq.com) |
| `QQ_BOT_SECRET` | Yes | QQ Bot AppSecret |
| `DEEPSEEK_API_KEY` | Yes | LLM API key (or `STEPFUN_API_KEY` etc.) |

Can be set via environment or `chat_core/.env` file.

## Configuration (`config.yaml` qq_bot section)

```yaml
qq_bot:
  appid: ${QQ_BOT_APPID}
  secret: ${QQ_BOT_SECRET}
  ws_url: wss://api.sgroup.qq.com/websocket
  group_reply_enabled: true       # 群聊 @ 回复开关
  c2c_reply_enabled: true         # 私聊回复开关
  passive_observe: true           # 群聊旁听记忆开关
  session_ttl: 3600               # 用户会话过期秒数 (默认 1h)
  health_port: 18090              # 健康检查端口
```

## Health Check

```
GET http://localhost:18090/health
→ {"status": "ok", "service": "chat-core-qq-bot"}
```

## Message Behavior Contract

### C2C (私聊)

| Condition | Behavior |
|-----------|----------|
| User sends message | AI replies with personality |
| First-ever message from user | Fetch nickname from QQ API → store in profile |
| Consecutive turns | Emotion, boredom, interest accumulate across turns |
| User idle > TTL | TurnManager cleaned up, memory retained |
| User returns after TTL | New TurnManager created, past memories recalled |

### Group Chat (群聊)

| Condition | Behavior |
|-----------|----------|
| @robot message | AI replies |
| @robot from user with C2C history | Cross-namespace recall: group + C2C memories |
| Non-@ message | Silent observe → write to `user/{uid}/group/{gid}/observations` |
| Bot mentioned without @ | No reply (passive observe only) |

### Proactive Speech (主动发言)

| Condition | Behavior |
|-----------|----------|
| Boredom > threshold | AI initiates conversation |
| QQ rate limit hit | Backoff retry, skip if still limited |
| Group proactive | Must comply with QQ platform rate limits |

### Error Handling

| Error Code | Behavior |
|------------|----------|
| 22009 (rate limit) | Backoff retry: 1s → 3s → 9s |
| 304082/304083 (media fail) | Retry once |
| 4001-4015 (protocol) | Reconnect WebSocket |
| Other | Log and discard |

## Memory Isolation Contract

| Scenario | Write Namespace | Recall Namespace |
|----------|----------------|------------------|
| C2C chat | `user/{uid}/c2c/conversations` | `user/{uid}/c2c/*` + `user/{uid}/profile` |
| Group @ chat | `user/{uid}/group/{gid}/conversations` | `user/{uid}/group/{gid}/*` + `user/{uid}/c2c/*` (if known) + `user/{uid}/profile` |
| Group observe | `user/{uid}/group/{gid}/observations` | N/A (no reply generated) |
| Profile (nickname) | `user/{uid}/profile` | All scenarios |
| Profile (facts) | `user/{uid}/facts` | All scenarios |

## Shutdown Behavior

- SIGINT (Ctrl+C) → graceful shutdown
- SIGTERM → graceful shutdown
- Shutdown sequence: stop QQ WS → cancel pending tasks → stop EmotionEngine → close MemoryStore
