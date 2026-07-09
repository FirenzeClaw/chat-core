# Implementation Plan: QQ Bot 集成

**Feature**: `002-qq-bot-integration`
**Source Spec**: [spec.md](./spec.md)
**Created**: 2026-07-09
**Status**: In Progress

---

## Technical Context

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Language | Python 3.12+ | 与 chat-core 统一，asyncio 原生 |
| QQ Protocol | WebSocket + REST API (aiohttp) | QQ Bot API v2 标准协议 |
| LLM SDK | openai >= 1.0 (AsyncOpenAI) | 复用现有 ModelProvider |
| Database | SQLite + FTS5 (aiosqlite) | 复用现有 MemoryStore，WAL 模式支持并发 |
| Async Runtime | asyncio | 原生 Python，QQ WS 事件驱动 |
| HTTP Health | aiohttp.web | 轻量健康检查端点 |

### Performance Target

| Metric | Target | Source |
|--------|--------|--------|
| First reply from QQ message | 2-5 seconds (emotion-dependent) | SC-1 |
| Memory recall across turns | < 1 second | SC-2 |
| Multi-user concurrent turns | Race-driven concurrency, no artificial cap | SC-3, FR-8, FR-10 |
| WebSocket reconnect | < 10 seconds | SC-5 |

### External Dependencies

| Dependency | Purpose | Fallback |
|------------|---------|----------|
| aiohttp >= 3.9 | QQ WebSocket + REST API + health HTTP | 无 (QQ 协议必需) |
| DeepSeek API (or compatible) | LLM 推理 | 复用现有 ModelProvider |
| QQ Bot Platform | 消息收发 | 无 (QQ Bot 运行必需) |

---

## Constitution Check

**Status**: N/A — No `.specify/memory/constitution.md` exists. Governance from `AGENTS.md`:

- **主脑不发言原则**: 子Session 是唯一的嘴 ✅ 不变
- **本地优先**: SQLite 本地存储 ✅ 不变，MemoryStore 复用
- **asyncio 并发模型**: 子Session 可并行（不同用户），双主脑串行 ✅ 与 CLI 模式一致
- **无外部编排依赖**: ✅ 不变，不引入任何 MCP/编排框架

---

## Existing Code Baseline

QQ 模块已有一版草稿代码，需要按新架构重构：

| 文件 | 当前状态 | 重构需求 |
|------|---------|---------|
| `qq/protocol.py` | ✅ 基本正确 | 新增 `fetch_user_nickname()`，`send_message()` 错误重试 |
| `qq/sessions.py` | ❌ 含冗余字段 | 重写：`UserSession` 关联子 Session 而非 TurnManager |
| `qq/adapter.py` | ❌ 每 turn 新建 TurnManager | 完全重写：全局双主脑 + 多子 Session + 竞态追踪 |
| `qq_bot.py` | ❌ 无 EmotionEngine | 新增强全局双主脑 + EmotionEngine + RaceTracker |
| `qq/__init__.py` | ✅ 正确 | 无需改动 |

**新文件**:
```
chat_core/qq/
├── race_tracker.py       # NEW: 竞态追踪器
└── subconscious.py       # NEW: 潜意识注入器（竞态调节）
```

---

## Phase Breakdown

### Phase 1: 协议层完善 & 重构预备

**Goal**: QQ 协议层达到生产质量，完成重构前的依赖准备。

**Files to modify**:
```
chat_core/qq/
├── protocol.py          # MODIFY: 新增 fetch_user_nickname(), send_message() 错误重试
└── sessions.py          # REWRITE: 删除 conversation_history, UserSession 仅保留元数据
```

**Key deliverables**:
- [ ] `fetch_user_nickname(openid)` — 调 QQ API 获取昵称，返回字符串 (FR-23)
- [ ] `send_message()` 错误分类处理 — 22009 退避重试，304082/304083 重试 1 次，其他丢弃
- [ ] `UserSession` 精简为 `user_id` + `session_key` + `turn_counter` + `last_active`
- [ ] 删除 `conversation_history`、`add_turn()`、`total_turns` 属性
- [ ] 新增 `sub_session` 引用字段（类型 Any，延迟绑定）

**Tests**:
- `test_qq_protocol.py`: 新增 `fetch_user_nickname` mock 测试、`send_message` 错误码分支测试
- `test_qq_sessions.py`: 更新以匹配精简后的 UserSession

**Acceptance**: 协议层单测通过，UserSession 不包含对话历史。

---

### Phase 2: 核心架构 — 全局双主脑 + 多子 Session

**Goal**: 实现"一个人与多人聊"的核心模型。全局双主脑共享，每个对话者独立子 Session。

**Files to create/modify**:
```
chat_core/qq/
├── adapter.py           # REWRITE: 全局双主脑 + 多子 Session 管理
├── race_tracker.py      # NEW: 竞态追踪器
└── subconscious.py      # NEW: 潜意识注入器
```

**Key deliverables**:
- [ ] `BotAdapter` 持有全局唯一的 `LogicBrain` + `EmotionBrain` 实例 (FR-6)
- [ ] `BotAdapter._sub_sessions: dict[session_key, ReActLoop]` — 每个对话者一个子 Session (FR-7)
- [ ] `RaceTracker` — 追踪活跃子 Session 数量，暴露 `active_count` 和 `race_severity` 属性 (FR-8)
- [ ] RaceTracker 在每个子 Session 开始/结束时更新计数
- [ ] `SubconsciousInjector` — 根据竞态程度调节注入质量 (FR-10):
  - `inject(sub_session, full_context, race_severity)` → 截断/降级后的上下文
  - 低竞态 (1-2): 保留全部
  - 中竞态 (3-4): 截断至 50%
  - 高竞态 (5+): 仅保留方向摘要
- [ ] 竞态严重时，`EmotionEngine` 中"烦躁"维度加速增长 (FR-9):
  - `EmotionEngine.accelerate("anger", factor=race_severity)`
- [ ] `process_message(ctx)`:
  - 获取/创建子 Session
  - 全局双主脑执行 recall
  - SubconsciousInjector 调节注入
  - 子 Session 运行 ReAct 循环
  - 归档 + 发送回复
- [ ] 子 Session 复用同一对话者的实例，TTL 过期清理 (FR-12)
- [ ] ReActLoop 自身压缩/退役正常 (FR-11)

**Tests**:
- `test_race_tracker.py`: active_count 增减、severity 计算
- `test_subconscious.py`: 各竞态级别注入质量验证
- `test_adapter.py`: mock 双主脑 → 验证多子 Session 创建/复用/清理

**Acceptance**: 3 个用户同时发消息 → 3 个子 Session 并行 → RaceTracker 显示 active_count=3 → 烦躁升高。

---

### Phase 3: 入口 & 情绪竞态集成

**Goal**: QQ Bot 入口正确连线全局双主脑 + EmotionEngine + RaceTracker。

**Files to modify**:
```
chat_core/
├── qq_bot.py            # MODIFY: 全局双主脑 + EmotionEngine + RaceTracker
├── qq/adapter.py        # MODIFY: 接收全局组件
├── config.yaml          # MODIFY: 移除 max_concurrent_turns
```

**Key deliverables**:
- [ ] `run_bot()` 创建全局 `LogicBrain` + `EmotionBrain` (FR-6)
- [ ] `run_bot()` 创建全局 `EmotionEngine`，启动后台 tick (FR-9)
- [ ] `run_bot()` 创建 `RaceTracker`，注入 EmotionEngine (FR-8, FR-9)
- [ ] `BotAdapter` 接收 `logic_brain`, `emotion_brain`, `emotion_engine`, `race_tracker`, `subconscious_injector`
- [ ] 优雅关闭：SubSession 清理 → EmotionEngine.stop() → MemoryStore.close()
- [ ] `config.yaml` 移除 `max_concurrent_turns`

**Tests**:
- 启动流程集成测试

**Acceptance**: `python -m chat_core.qq_bot` 启动，日志显示 "全局双主脑就绪, RaceTracker 已初始化"。

---

### Phase 4: 记忆命名空间 & 跨场景联动

(unchanged from plan — FR-16, FR-17, FR-18)

---

### Phase 5: 主动行为 & 画像构建

**Goal**: ProactiveSystem 通过全局双主脑工作，用户画像自动积累。

**Files to modify**:
```
chat_core/qq/
├── adapter.py           # MODIFY: ProactiveSystem 通过双主脑调度
├── protocol.py          # MODIFY: 用户画像初始化
```

**Key deliverables**:
- [ ] ProactiveSystem 通过全局双主脑 + 全局 EmotionEngine 工作 (FR-19)
- [ ] 主动发言 callback → `send_message()` QQ REST API (FR-20)
- [ ] 用户首次发消息 → `fetch_user_nickname()` → 写 `user/{uid}/profile` (FR-23)
- [ ] LogicBrain 提取事实 → `user/{uid}/facts` (FR-24)
- [ ] 群聊每个发言者独立画像 (FR-25)
- [ ] 竞态环境下，ProactiveSystem 的主动发言可能被降级或延迟 (与 FR-10 一致)

---

### Phase 6: 集成测试 & 文档

**Goal**: 全量测试通过，文档更新。

**Files to create/modify**:
```
chat_core/
├── AGENTS.md            # MODIFY: 新增 QQ Bot 集成说明
├── CHANGELOG.md         # MODIFY: 记录变更
└── tests/
    └── test_qq_adapter.py    # NEW: adapter 集成测试
```

**Key deliverables**:
- [ ] 全量回归：原有 84 tests 全部通过 (SC-7)
- [ ] 新增 QQ 模块测试覆盖 ≥ 85%
- [ ] `AGENTS.md` 更新：文件索引 + 架构图 + 回调链路
- [ ] `CHANGELOG.md` 更新：记录 QQ Bot 集成

**Tests**:
- `test_qq_adapter.py`: mock QQ 协议 → mock TurnManager → 验证完整消息处理流程
- 全量 pytest 通过

**Acceptance**: `python -m pytest tests/ -v` 全部通过，AGENTS.md 反映最新架构。

---

## Dependency Graph

```
Phase 1 (Protocol Polish + Sessions)
├── qq/protocol.py [MODIFY]
└── qq/sessions.py [REWRITE]
    └── 依赖: 无

Phase 2 (Core Architecture: Dual-Brain + Multi SubSession)
├── qq/adapter.py [REWRITE]
├── qq/race_tracker.py [NEW]
├── qq/subconscious.py [NEW]
    └── 依赖: Phase 1, LogicBrain, EmotionBrain, ReActLoop, EmotionEngine, MemoryStore

Phase 3 (Entry + Emotion + Race Integration)
├── qq_bot.py [MODIFY]
├── config.yaml [MODIFY]
    └── 依赖: Phase 2

Phase 4 (Namespace + Cross-Scene Recall)
├── qq/adapter.py [MODIFY]
├── qq/protocol.py [MODIFY]
    └── 依赖: Phase 2, MemoryStore

Phase 5 (Proactive + Profile)
├── qq/adapter.py [MODIFY]
├── qq/protocol.py [MODIFY]
    └── 依赖: Phase 4

Phase 6 (Integration + Docs)
├── AGENTS.md [MODIFY]
├── CHANGELOG.md [MODIFY]
├── tests/test_qq_adapter.py [NEW]
├── tests/test_race_tracker.py [NEW]
    └── 依赖: Phase 5
```

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| 全局双主脑成为性能瓶颈（每 turn 都要调 recall） | 中 | 双主脑内部 memory recall 是 MemoryStore 读取，非 LLM 调用。LLM 调用可通过异步并发 |
| 子 Session 并发过多导致内存压力 | 中 | TTL 过期清理 + 子 Session ReActLoop 自身轻量（消息队列 + 工具注册） |
| 竞态追踪与情绪联动的参数调优无参考 | 低 | 初始参数保守（烦躁加速因子 0.1/active_session），后续可调 |
| ProactiveSystem 在全局双主脑模式下回调链复杂 | 中 | 通过 BotAdapter 统一管理所有 callback，注入时明确来源 |
| WebSocket 断连时活跃子 Session 状态 | 低 | Resume 机制恢复，子 Session 在内存中不受影响 |

### Naming Convention

- session_key: `user_{openid}` (私聊) / `group_{group_openid}` (群聊)
- memory namespace: `user/{openid}/c2c/*` (私聊) / `user/{openid}/group/{gid}/*` (群聊) / `user/{openid}/profile` (画像)
- sub_session key: same as session_key
- race_severity: `len(active_sub_sessions)` → mapped to `low`/`medium`/`high`

---

## Implementation Notes

### Key Differences from CLI Mode

| Aspect | CLI | QQ Bot |
|--------|-----|--------|
| 双主脑 (LogicBrain + EmotionBrain) | 单实例 | **全局单实例（所有对话共享）** |
| 子 Session (ReActLoop) | 1 个 | **每对话者 1 个，可多个并行** |
| 情绪引擎 | 全局单实例 | 全局单实例（相同），竞态驱动烦躁加速 |
| 竞态处理 | 无（单用户） | **RaceTracker + SubconsciousInjector** |
| 回复回调 | rich.Live + Panel | send_message() QQ REST API |
| 流式回调 | rich.Live | 禁用 |
| 记忆命名空间 | `user/default/*` | `user/{uid}/c2c/*` + `user/{uid}/group/{gid}/*` |
| 潜意识注入 | TurnManager 统一注入 | **SubconsciousInjector 按竞态分配** |
