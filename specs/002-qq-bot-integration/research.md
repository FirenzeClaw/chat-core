# Research: QQ Bot 集成

**Feature**: `002-qq-bot-integration`
**Created**: 2026-07-09

---

## R1: QQ WebSocket 协议实现模式

**Decision**: 采用 chat-engine `qq_protocol.py` 的状态机模式，适配 chat-core Config 体系。

**Rationale**:
- chat-engine 的 WebSocket 实现经过实战验证（hello → identify/resume → running）
- 状态机清晰、断线重连健壮
- 需要适配的部分：凭证获取方式（从独立 env 改为 chat-core Config）、消息回调接口

**Alternatives considered**:
- 从零重写：重复造轮子，chat-engine 的实现已经够用
- 使用第三方 QQ Bot SDK：增加依赖，灵活度不足

**Source**: `chat-engine/qq_protocol.py` (参考模式，非直接复用代码)

---

## R2: 架构模型 — 全局双主脑 + 多子 Session

**Decision**: 全局唯一 LogicBrain + EmotionBrain（"核心自我"），每对话者独立 ReActLoop 子 Session（"注意力线程"）。

**Rationale**:
- AI 是一个人格，不是为每个用户复制一套认知——双主脑是"我是谁"，子 Session 是"我在跟谁聊"
- 全局双主脑保证跨对话的人格一致性、情绪连续性
- 子 Session 独立上下文避免跨对话串话
- 竞态（多个子 Session 同时活跃）天然产生"注意力分配"问题——符合"一个人应付多人"的人性化设计

**Alternatives considered**:
- Per-user TurnManager：每个用户一套完整大脑 → AI 人格分裂，情绪不连续，已被否决
- 单一子 Session 串行化：一次只能跟一个人聊 → 不符合"多人在线同时聊"的 QQ 场景

---

## R8: 竞态追踪与情绪联动

**Decision**: `RaceTracker` 监控活跃子 Session 数量 → `EmotionEngine.anger` 维度加速增长。因子: `anger_acceleration = 0.1 × active_count`。

**Rationale**:
- 烦躁是"应付不过来"的自然情绪反应——AI 在多人对话压力下变得急躁
- 情绪应该真实反映系统状态（竞态压力），而非凭空模拟
- 竞态缓解后烦躁自然衰减（EmotionEngine 已有半衰期机制）

---

## R9: 潜意识注入的竞态调节

**Decision**: `SubconsciousInjector.inject(context, severity)` 按竞态级别截断潜意识上下文：
- Low (1-2 active): 保留 100%
- Medium (3-4): 保留 50%，仅高优先级内容
- High (5+): 保留方向摘要（~100 chars），丢弃细节

**Rationale**:
- "注意力不够用"时自然表现——优先保证能回复，质量次之
- 不是随机降级——优先级由对话历史长度、最近活跃度决定
- 竞态缓解后注入质量自动恢复

**Alternatives considered**:
- 随机降级：无法产生"顾此薄彼"的可感知差异
- 等比例降级所有注入：所有对话体验一起变差，没有"有些人获得更好注意力"的差异化

---

## R3: EmotionEngine 全局共享

**Decision**: 全局单例 `EmotionEngine`，所有用户的 TurnManager 共享同一实例。

**Rationale**:
- AI 是一个整体人格，情绪应该反映所有交互的累积
- `EmotionEngine.tick()` 是同步方法，`pause()/resume()` 控制写入窗口，天然并发安全
- TurnManager 通过构造函数注入，CLI 和 QQ Bot 使用相同模式

**Alternatives considered**:
- Per-user EmotionEngine：每个用户独立情绪 → AI 人格分裂，交互体验不一致，否决
- 禁用 EmotionEngine：丢弃核心功能，否决

---

## R4: 记忆命名空间设计

**Decision**: 
- 私聊: `user/{openid}/c2c/conversations`, `user/{openid}/c2c/facts`, `user/{openid}/profile`
- 群聊: `user/{openid}/group/{gid}/conversations`, `user/{openid}/group/{gid}/observations`
- 群聊 recall 时扩展检索 `c2c/*` 命名空间

**Rationale**:
- 物理隔离防止私聊信息泄露到群聊
- 但群聊中遇到曾私聊的用户时需要联动——在群聊 recall 时同时搜索 `c2c/*`
- `profile` 命名空间跨场景共享（同一 openid 的画像在私聊和群聊中一致）

**Alternatives considered**:
- 单一命名空间 + source 标记：检索可能混入不相关场景记忆，过滤逻辑复杂
- 完全隔离不联动：群聊中无法利用私聊积累的用户理解

---

## R5: QQ 昵称获取方式

**Decision**: 调 `GET /v2/users/{openid}` API，结果作为记忆存入 `user/{uid}/profile`（非缓存）。

**Rationale**:
- C2C 和群@事件不含昵称字段，必须额外获取
- 作为记忆而非缓存——昵称是用户事实的一部分，应持久化到 MemoryStore
- 与 chat-engine 的 `social.py` 模式一致

**Alternatives considered**:
- 内存缓存：重启丢失，不符合"记忆"语义
- 每次查询：多余 API 调用，QQ API 可能有频控

---

## R6: send_message 错误重试策略

**Decision**: 按错误码分类处理：
- `22009` (超频): 退避重试 3 次 (1s/3s/9s)
- `304082/304083` (富媒体失败): 重试 1 次
- 其他错误: 记录日志，丢弃

**Rationale**:
- 超频是瞬态错误，退避后可恢复
- 富媒体上传失败可能是网络波动
- 鉴权/权限类错误重试无意义

**Source**: QQ Bot API 文档 §错误码

---

## R7: ProactiveSystem 在 QQ 模式的适配

**Decision**: ProactiveSystem 自然生效，但其 reply_callback 需适配为 `send_message()` 调用。

**Rationale**:
- TurnManager 创建 ProactiveSystem 时传入 `reply_callback`
- CLI 模式下 reply_callback 是 `_on_reply`（rich 渲染）
- QQ 模式下 reply_callback 是 `send_message(ctx, content)`（QQ REST API）
- 通过 adapter 注入不同的 callback 即可，ProactiveSystem 本身零修改

**Alternatives considered**:
- 禁用 ProactiveSystem：2026 年 6 月后群聊主动发言已全量开放，禁用无依据
- 修改 ProactiveSystem 内部逻辑：违反"核心模块零修改"原则
