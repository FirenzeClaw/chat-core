# Data Model: QQ Bot 集成

**Feature**: `002-qq-bot-integration`
**Created**: 2026-07-09

---

## Entities

### MessageContext (existing, enhanced)

QQ 消息事件的类型化表示。从 WebSocket JSON 解析后传入 adapter。

| Field | Type | Source | Description |
|-------|------|--------|-------------|
| user_id | str | event.author.id | QQ openid |
| username | str | event.author.username or API fetch | 昵称（首次对话时通过 API 获取） |
| content | str | event.content | 消息文本 |
| msg_type | str | event.t (事件类型) | `C2C_MESSAGE_CREATE` / `GROUP_AT_MESSAGE_CREATE` / `GROUP_MESSAGE_CREATE` / `DIRECT_MESSAGE_CREATE` / `AT_MESSAGE_CREATE` |
| msg_id | str | event.id | 平台消息 ID，用于去重和被动回复 |
| group_id | str | event.group_openid | 群 openid（群聊时有值） |
| channel_id | str | event.channel_id | 频道 ID |
| guild_id | str | event.guild_id | 频道服务器 ID |
| ref_msg_id | str | = msg_id | 用于被动回复关联 |
| timestamp | float | time.time() | 接收时间戳 |
| image_urls | list[str] | event.attachments | 图片 URL 列表 |

**Computed properties**:
- `is_group: bool` — `"GROUP" in msg_type`
- `is_at: bool` — `"AT_MESSAGE" in msg_type`
- `is_direct: bool` — `"DIRECT" in msg_type or "C2C" in msg_type`
- `session_key: str` — `"user_{user_id}"` or `"group_{group_id}"`
- `scene: str` — `"c2c"` if `is_direct` else `"group"`

---

### UserSession (rewritten)

单个 QQ 用户的会话元数据。纯数据对象，不持有对话内容。

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| user_id | str | (required) | QQ openid |
| session_key | str | (required) | `user_{uid}` or `group_{gid}` |
| turn_counter | int | 0 | 该用户累计 turn 数 |
| last_active | float | time.time() | 最后活跃时间戳 |

**Methods**:
- `is_expired(ttl: float = 3600) -> bool` — 超过 TTL 无活动
- `touch() -> None` — 更新 last_active

**Lifecycle**:
```
[首次消息] → get_or_create() → UserSession(turn_counter=0)
[后续消息] → touch() → turn_counter += 1
[TTL 过期] → cleanup_expired() → 删除 session + 关联 TurnManager
```

**Dropped fields** (from original draft):
- ~~conversation_history~~ — TurnManager 自身的 message history + MemoryStore 归档替代
- ~~silence_counters~~ — TurnManager._silence_accumulator 替代
- ~~add_turn()~~ — turn_counter 递增即可

---

### TurnManager (per-user, unmodified)

|| Existing class — no changes required. Per-user instance stored in `BotAdapter._turn_managers[session_key]`.

**Constructor deps** (injected by adapter):
- `LogicBrain` — per-TurnManager instance
- `EmotionBrain` — per-TurnManager instance
- `ModelProvider` — global singleton
- `MemoryStore` — global singleton
- `PromptEngine` — global singleton
- `ActionBrainPool` — per-TurnManager instance
- `EmotionEngine` — **global singleton** (FR-12)
- `PersonalityEngine` — global singleton
- `AttentionModel` — per-TurnManager instance

**Internal state** (preserved across turns):
- `_turn_counter` — turn 序号
- `_silence_accumulator` — 沉默累积
- `_boredom_detector` — 无聊检测
- `_interest_model` — 兴趣追踪
- `_proactive` (ProactiveSystem) — 主动系统
- `_in_correction` — 反递归 guard

---

### Memory Namespace Map

```
user/{openid}/
├── profile               # UserProfile: 昵称、画像摘要 (FR-24)
├── facts/                # 提取的用户事实 (FR-24)
├── c2c/                  # 私聊记忆 (FR-16)
│   └── conversations/    # 私聊对话归档 (_archive_turn)
└── group/{gid}/           # 群聊记忆 (FR-16)
    ├── conversations/     # 群聊对话归档
    └── observations/      # 群聊旁听记录 (FR-18)

self/
├── inner_thoughts/        # AI 内心戏 (沿用现有)
└── feelings/              # 情绪拧巴记录 (沿用现有)

global/
└── persona/               # Bot 性格设定 (沿用现有)
```

**Recall 范围** (FR-17):
- 私聊场景: `user/{openid}/c2c/*` + `user/{openid}/profile` + `user/{openid}/facts`
- 群聊场景: `user/{openid}/group/{gid}/*` + `user/{openid}/c2c/*` (联动) + `user/{openid}/profile` + `user/{openid}/facts`

---

## State Machine: WebSocket Connection

```
                     ┌──────────┐
          ┌─────────►│  Hello   │ (op=10)
          │          └────┬─────┘
          │               │
          │    ┌──────────▼──────────┐
          │    │  Identify / Resume  │
          │    └──────────┬──────────┘
          │               │
          │    ┌──────────▼──────────┐
          │    │      Running       │◄──── 心跳 (op=1)
          │    └──────────┬──────────┘
          │               │
          │          op=7/op=9/断开
          │               │
          └───────────────┘ (5s 重连)
```

---

## State Machine: Turn Processing

```
QQ 消息到达
    │
    ├─ 群聊非@? ──YES──► _passive_observe() → write observations → END (no reply)
    │
    └─ NO (私聊/@)
         │
         ├─ 获取 per-user Lock
         ├─ get_or_create TurnManager
         ├─ 首次对话? ──YES──► fetch_user_nickname() → write profile
         │
         ├─ turn_manager.process_turn(user_message)
         │    ├─ dual_recall (c2c + group 联动)
         │    ├─ inject
         │    ├─ sub_session ReAct
         │    ├─ review
         │    ├─ decide (correct / silence / twisted)
         │    └─ archive → MemoryStore
         │
         ├─ turn.reply_segments → reply_text
         ├─ send_message(ctx, reply_text)
         └─ release per-user Lock
```
