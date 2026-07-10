# Feature Specification: 设计-实现对齐

**Feature**: `004-design-alignment`
**Created**: 2026-07-10
**Status**: Draft
**Source**: `chat-core-design.md` 更新后与当前实现的全面对照审查

---

## Overview

`chat-core-design.md` 经过全面审查后更新了核心架构设计，关键变化包括：审查异步非阻塞、纠正延迟到下一轮生效、子Session 读取潜意识区、纠正恢复完整子Session 能力、双脑权重平等。当前 CLI 和 QQ Bot 实现均存在与设计的多处偏差，需要对齐。

**核心设计原则**：子Session 的 `send_reply` 立即输出给用户，不等审查。审查异步后台运行，发现错误通过 `subconscious/corrections` 在**下一轮**子Session `_think()` 时自动注入纠正。这模拟了"我刚才想了想，其实…"的人类对话节奏。

---

## User Scenarios & Testing

### 1. CLI 用户收到即时回复，后续发现 AI 自我纠正

**Actor**: CLI 用户

**Flow**:
1. 用户输入消息
2. 双脑检索记忆并注入子Session
3. 子Session 逐段 `send_reply`，每段**立即**显示在终端
4. 子Session 结束后，双脑异步启动审查
5. 若审查发现错误 → 写入 `subconscious/corrections`
6. 用户输入下一条消息时，子Session 自动读取 corrections 并在回复中自然纠正
7. AI 可能说 "啊等等，我上次说错了，其实是…"

**Acceptance**:
- 用户从发消息到看到第一段回复的时间 ≤ 3 秒
- 审查不阻塞用户看到回复
- 上一轮的纠正延迟到下一轮自然体现
- 纠正时 AI 的语气自然，不像系统指令

### 2. QQ Bot 用户经历同样的审查纠正流程

**Actor**: QQ 用户

**Flow**:
1. 用户在 QQ 发送消息
2. 全局双脑异步后台执行 recall + inject
3. 子Session 立即启动回复，`send_reply` 直接发到 QQ
4. 子Session 结束后，双脑异步审查
5. 错误 → 写 `subconscious/corrections`
6. 下一轮子Session 自动读取并纠正

**Acceptance**:
- 首段回复在 2-5 秒内到达
- 审查不增加首段回复延迟
- QQ Bot 拥有与 CLI 一致的审查纠正能力
- 多用户并发时审查各自独立

### 3. 沉默场景：AI 发现错误但选择不说

**Actor**: 系统

**Flow**:
1. 子Session 发言完毕
2. 双脑审查，`combined ≤ 0.5`
3. 审查结果仅归档到 `self/noticed/`，不写 `subconscious/corrections`
4. 沉默累积器 +1
5. 同类错误累积到阈值后，调整 FuzzyParam → 下一次可能选择纠正

**Acceptance**:
- 低权重错误不触发纠正
- 沉默有累积效应，不是永久的

---

## Functional Requirements

### FR-1: CLI 审查异步化
`TurnManager.process_turn()` 中的审查步骤改为 `asyncio.create_task()` 异步执行。子Session 的 `send_reply` 流式输出后 `process_turn()` 立即返回给 CLI，审查在后台运行。用户可以在审查进行中发送下一条消息。审查任务异常时静默降级（记录日志），不阻塞后续 turn。

### FR-2: 纠正延迟到下一轮生效
`_issue_correction()` 不再立即运行纠正子Session。改为只写入 `subconscious/corrections`，由下一轮子Session `_init_messages()` 读取并注入。拧巴(TWISTED)场景同样延迟：按逻辑执行纠正写入 subconscious，同时归档拧巴记录到 `self/feelings/twisted`。

### FR-3: 子Session 读取潜意识区
`ReActLoop._init_messages()` 新增步骤：查询 `subconscious/corrections` 中匹配当前用户的条目，以 system message 形式注入消息历史。读取后标记为已处理或按 TTL 过期。

### FR-4: 纠正子Session 恢复完整能力
纠正子Session 恢复为完整的 ReActLoop 实例：包含 `inner_thoughts`、`send_reply`（可多段）、`wait`。不再是 `max_iter=2` 的单次模式。纠正深度递归保护改为计数器（`≤2 层`），替换当前的布尔 `_in_correction`。

### FR-5: QQ Bot 新增审查纠正管线
`BotAdapter._process()` 在 `loop.run()` 完成后，异步启动审查（`asyncio.create_task`）。审查写 `subconscious/corrections`。新增 `_async_review_and_decide()` 方法。

### FR-6: 双脑权重平等
`ReviewSystem` 中 `combined` 公式从 `logic × 0.6 + emotion × 0.4` 改为 `logic × 0.5 + emotion × 0.5`。

### FR-7: QQ Bot send_reply 立即发送
`BotAdapter._on_reply` 回调恢复为立即调用 `send_fn` 发送到 QQ，不缓冲等待审查。审查在后台异步运行，不阻塞消息发送。

### FR-8: 纠正内容归档
纠正子Session 的 `inner_thoughts` 归档到 `self/inner_thoughts/`，与正常发言一致。纠正的 `send_reply` 内容归档到 `user/{uid}/conversations/`。

### FR-9: 双脑持续情绪通知通道
新增 `emotion_alert` 事件通道：情感主脑检测到显著情绪变化时实时通知逻辑主脑调整注入方向，不等待审查窗口。

### FR-10: 向后兼容
所有现有测试保持通过。CLI `--direct` 降级模式不受影响。QQ Bot 的竞态追踪和潜意识注入调节不受影响。

---

## Success Criteria

| # | 标准 | 测量方式 |
|---|------|---------|
| SC-1 | CLI 首段回复延迟 ≤ 3 秒 | 计时：用户消息到第一条 `send_reply` 输出 |
| SC-2 | 审查不影响子Session 输出时序 | 审查启用/禁用时，首段延迟差异 ≤ 0.3s |
| SC-3 | QQ Bot 首段回复延迟 ≤ 5 秒 | 计时：WebSocket 消息到第一条 QQ 消息发送 |
| SC-4 | 纠正延迟生效可验证 | 上一轮纠正写入后，下一轮子Session `_init_messages` 中包含 corrections |
| SC-5 | 所有现有 129 tests 通过 | `pytest tests/ -q` 零失败 |
| SC-6 | 权重公式更新可验证 | `combined = 0.5 × logic + 0.5 × emotion` 在代码中生效 |
| SC-7 | 纠正递归不超过 2 层 | 单元测试：连续 3 次纠正触发后，第 3 次被跳过 |
| SC-8 | 新增测试覆盖 ≥ 8 条 | 覆盖异步审查、subconscious 注入、纠正递归深度、权重公式 |

---

## Key Entities

- **Correction**: 纠正记录，写入 `subconscious/corrections/{turn_id}`，包含逻辑错误列表、情感问题列表、权重值、待注入子Session 的纠正文本
- **ReviewResult**: 审查结果，扩展为异步任务输出，增加 `correction_written: bool` 标记
- **CorrectionDepth**: 纠正递归深度计数器，替代布尔 `_in_correction`

---

## Assumptions

1. 子Session 读取 subconscious 发生在 `_init_messages()` 阶段，在首次 `_think()` 之前
2. 已处理的 corrections 不需要物理删除——通过 `last_access` 或 `turn_counter` 标记已读即可
3. QQ Bot 的异步审查任务可以复用与双脑相同的 `ModelProvider` 实例
4. 情感通知通道使用现有 `EventBus`，无需新的基础设施
5. CLI `--direct` 模式跳过双脑+审查，本 spec 中的改动不影响 direct 路径
6. 审查异步执行存在跨 turn 时序窗口：若用户在审查完成前发送下一条消息，上一轮的纠正将在再下一轮才生效。这是设计允许的——模拟"事后才想起来"的人类行为

---

## Dependencies

- `chat-core-design.md` (v2026-07-10 更新版) — 设计基线
- `ReviewSystem` (`systems/review.py`) — 权重公式需要更新
- `TurnManager` (`core/turn_manager.py`) — CLI 审查+纠正重构
- `ReActLoop` (`core/loop.py`) — 子Session 读 subconscious
- `BotAdapter` (`qq/adapter.py`) — QQ Bot 审查+纠正新增
- 现有 129 个测试 — 不能回归

---

## Out of Scope

- 多副脑并行发言（Phase 4）
- 主脑退役机制
- 意图提取异步化
- 多模态降级优化
