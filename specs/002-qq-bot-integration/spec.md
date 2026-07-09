# Feature Specification: QQ Bot 集成

**Feature**: `002-qq-bot-integration`
**Created**: 2026-07-09
**Status**: Draft
**Source**: 架构讨论 — 将 chat-core 四脑模型接入 QQ Bot 平台

---

## Overview

为 chat-core 添加 QQ Bot 接入能力，使终端 AI 聊天伙伴能够作为 QQ 机器人在群聊和私聊中运行。核心原则：AI 是一个有独立人格的个体，同时与多个人对话——不是为每个用户复制一套大脑，而是一个大脑在不同对话间分配注意力。多线对话的竞态产生真实的"烦躁"情绪和"顾此薄彼"的注意力分配效应。

**架构模型**：
- **全局双主脑**（LogicBrain + EmotionBrain）：AI 的核心自我，一个实例所有对话共享
- **多个子 Session**（ReActLoop）：AI 与每个对话者的"注意力线程"，独立上下文
- **竞态驱动情绪**：同时活跃的子 Session 越多，EmotionEngine 中"烦躁"维度上升越快
- **竞态调节注入**：潜意识区上下文质量随竞态动态分配，模拟"注意力不够用"

### 与 CLI 的关系

CLI (`chat-core` 命令) 和 QQ Bot (`chat-core-qq` 命令) 是两个独立入口，共享核心模块（ModelProvider、MemoryStore、PromptEngine、EmotionEngine、PersonalityEngine），但使用不同的架构模式：
- CLI：单用户、单个子 Session、prompt_toolkit REPL
- QQ Bot：多用户、多个子 Session 并行、WebSocket 事件驱动、竞态驱动情绪+注意力分配

---

## User Scenarios & Testing

### 1. 用户在 QQ 私聊中与 AI 对话

**Actor**: QQ 用户

**Flow**:
1. 用户在 QQ 私聊窗口向机器人发送消息
2. 机器人通过 WebSocket 接收消息事件
3. 系统为该用户获取或创建子 Session 实例
4. 全局双主脑执行 recall 检索相关记忆
5. 检索结果通过潜意识注入器注入子 Session（注入质量受当前竞态程度影响）
6. 子 Session 通过 ReAct 循环生成回复
7. 审查系统检测回复质量
8. 机器人通过 QQ REST API 将回复发送给用户
9. 对话归档到记忆系统

**Acceptance**:
- 用户发送消息后在 2-5 秒内收到人格化的回复
- 回复延续对话历史
- 低竞态（仅此一人在聊）时回复质量最高，高竞态时可能出现注意力不足的自然表现
- 同一用户连续对话时，子 Session 上下文跨 turn 延续

### 2. 群聊 @机器人 触发回复

**Actor**: QQ 群成员

**Flow**:
1. 群成员在群聊中 @机器人 发送消息
2. 机器人收到 `GROUP_AT_MESSAGE_CREATE` 事件
3. 机器人运行完整四脑管线生成回复并发送到群内

**Acceptance**:
- @机器人 的消息获得回复
- 群聊中非 @机器人的普通消息不触发回复（避免噪音和成本）

### 3. 群聊旁听记忆

**Actor**: QQ 群成员（未 @机器人）

**Flow**:
1. 群成员在群聊中发送普通消息（未 @机器人）
2. 机器人收到事件后不生成回复
3. 但将消息内容记录到记忆系统，积累对群成员和群话题的了解

**Acceptance**:
- 旁听的消息被写入 `user/{uid}/group/{gid}/observations` 命名空间
- 后续该用户在私聊或群内 @机器人时，机器人能基于旁听积累的记忆做出更贴切的回复
- 旁听写入不影响消息回复速度（异步 fire-and-forget）

### 4. AI 主动发起对话

**Actor**: QQ 用户 / 群聊

**Flow**:
1. 用户与 AI 对话结束后，无聊检测器启动
2. 随着时间推移，无聊值上升
3. 当无聊值超过触发阈值，且符合 QQ 频控要求时，AI 主动向用户发送消息
4. 兴趣模型和话题追踪影响主动发言的内容选择

**Acceptance**:
- AI 能在对话结束一定时间后主动发起话题
- 主动发言受 QQ 平台频控约束，不会触发超频错误
- 主动发言的内容与之前对话的话题相关

### 5. 多用户并发 — "一个人与多人在聊"

**Actor**: 多个 QQ 用户同时与机器人对话

**Flow**:
1. 用户 A、B、C 几乎同时发送消息
2. 全局双主脑（逻辑+情感）为每个子 Session 执行 recall 检索
3. 三个子 Session 并行运行 ReAct 循环生成回复
4. 竞态程度（同时活跃的子 Session 数量）被系统追踪
5. 随着并发增加，AI 的"烦躁"情绪维度上升
6. 潜意识区注入自动调节：竞态严重时，优先级较低的对话获得的潜意识上下文被缩减或降级

**Acceptance**:
- 不同用户各自收到回复
- 当并发数高时，AI 的情绪状态中烦躁维度显著上升
- 竞态缓解（并发减少）后，烦躁情绪自然衰减
- 用户能感受到 AI 在"忙"——回复质量或速度在多人并发时不如单独对话时稳定
- 同一对话者连续发送多条消息时不出现子 Session 状态竞态

### 6. 竞态驱动的潜意识调节

**Actor**: 多个 QQ 用户同时活跃

**Flow**:
1. 系统追踪当前活跃子 Session 数量作为竞态指标
2. 竞态指标输入 EmotionEngine，加速"烦躁"维度的增长
3. 每个子 Session 在 inject 阶段接收潜意识上下文，内容质量受竞态影响：
   - 低竞态（1-2 人）：完整潜意识注入
   - 中竞态（3-4 人）：部分注入，仅保留高优先级内容
   - 高竞态（5+ 人）：最小注入，仅传递基本方向
4. AI 的内心戏（inner_thoughts）反映"应付不过来"的自我感知

**Acceptance**:
- 竞态指标正确反映活跃对话数
- 潜意识注入质量随竞态递减
- 竞态缓解后注入质量自动恢复
- 竞态驱动的烦躁情绪可以被 AI 在回复中自然流露

### 7. 会话过期与重连

**Actor**: 长期未活跃的用户

**Flow**:
1. 用户上次对话后超过配置的 TTL 时间（默认 1 小时）未再发消息
2. 该用户的子 Session 实例被清理
3. 用户再次发消息时，创建新的子 Session 实例，但记忆系统保留所有历史记忆

**Acceptance**:
- 回归用户能从记忆系统中召回之前的对话内容
- 会话清理不丢失已归档的记忆
- 新子 Session 从全局双主脑获取当前状态（情绪、人格），而非从零开始

### 8. QQ API 错误降级

**Actor**: QQ 用户、系统管理员

**Flow**:
1. 机器人生成回复后调用 QQ REST API 发送消息时收到错误响应（如超频 22009、富媒体上传失败 304082/304083）
2. 机器人记录错误日志
3. 对可重试的错误（超频）执行退避重试
4. 对不可重试的错误丢弃并记录

**Acceptance**:
- QQ API 发送失败不导致系统崩溃或消息处理中断
- 错误信息被完整记录，管理员可追溯
- 超频时自动退避，不丢失消息

### 9. 用户画像自动构建

**Actor**: QQ 用户（首次对话）

**Flow**:
1. 用户首次向机器人发送消息
2. 系统调用 QQ API 获取用户昵称，存入 `user/{uid}/profile`
3. 后续对话中，LogicBrain 从对话内容提取关键事实（如"我叫小明""我在北京上学"），积累到用户画像
4. AI 在对话中自然引用已掌握的用户信息，无需用户重复介绍

**Acceptance**:
- 用户首次发消息后，昵称被记录并可被 AI 在回复中自然使用
- 跨 turn 对话中，AI 记住了之前提取的事实，并能在相关话题中主动关联
- 群聊和私聊中的用户画像共享同一数据源（同一 openid）

---

## Functional Requirements

### QQ 协议层

- **FR-1**: 系统 MUST 通过 QQ WebSocket 连接接收消息事件，支持 `C2C_MESSAGE_CREATE`（私聊）、`GROUP_AT_MESSAGE_CREATE`（群@）、`GROUP_MESSAGE_CREATE`（群全量）、`DIRECT_MESSAGE_CREATE`（频道私信）、`AT_MESSAGE_CREATE`（频道@）
- **FR-2**: 系统 MUST 支持 WebSocket 状态机：Hello → Identify/Resume → Ready → Running，含断线自动重连
- **FR-3**: 系统 MUST 通过 QQ REST API 发送回复消息，根据事件类型自动选择正确的发送端点（单聊/群聊/频道/私信）
- **FR-4**: 系统 MUST 实现 access_token 自动获取与提前 5 分钟刷新
- **FR-5**: 系统 MUST 对重复推送的消息事件进行去重

### 核心架构：一个人与多人聊

- **FR-6**: 全局逻辑主脑（LogicBrain）和情感主脑（EmotionBrain）MUST 为唯一共享实例。双主脑代表 AI 的核心自我，所有对话共享同一对主脑
- **FR-7**: 系统 MUST 为每个活跃对话维护独立的子 Session（ReActLoop 实例）。子 Session 是 AI 与单个对话者交互的"注意力线程"
- **FR-8**: 当多个子 Session 同时活跃（并发处理消息）时，系统 MUST 将其视为"竞态"——竞态严重程度由同时活跃的子 Session 数量衡量
- **FR-9**: 竞态 MUST 对 AI 的情绪产生真实影响：竞态越严重，EmotionEngine 中"烦躁"维度上升越快。AI 在多线对话中体验到"应付不过来"的烦躁感
- **FR-10**: 竞态 MUST 自动调节潜意识区注入：竞态严重时，部分子 Session 获得的潜意识上下文质量降级（顾此薄彼效应）。注入策略由当前竞态程度和对话优先级共同决定

### 子 Session 生命周期

- **FR-11**: 子 Session 的上下文压缩/退役机制（ReActLoop 自身的 70%/85% 阈值）MUST 正常工作，不添加外部裁切逻辑
- **FR-12**: 同一对话者连续消息 MUST 复用同一个子 Session 实例（保留消息历史和上下文）。超过 TTL 无活动后子 Session 被清理，再次发消息时新建
- **FR-13**: 每个 turn 的对话 MUST 通过归档机制实时写入 MemoryStore，支持跨 turn 召回
- **FR-14**: 全局 PersonalityEngine 实例 MUST 为所有子 Session 提供一致的温度调制和人格权重

> **Note**: FR-15 编号被有意跳过——旧架构中 FR-15 对应 `_archive_turn()`，已合并至 FR-13。

### 记忆系统

- **FR-16**: 不同用户的记忆 MUST 通过命名空间隔离，私聊记忆存 `user/{uid}/c2c/*`，群聊记忆存 `user/{uid}/group/{gid}/*`，互不可跨用户访问
- **FR-17**: 双脑 recall 检索 MUST 在群聊场景下联动私聊记忆——当群聊中遇到曾私聊过的用户时，同时检索该用户在 `c2c/*` 和 `group/{gid}/*` 下的记忆，产生跨场景记忆关联
- **FR-18**: 群聊旁听消息 MUST 写入 `user/{uid}/group/{gid}/observations` 命名空间

### 用户画像与社交信息

- **FR-23**: 系统 MUST 在用户首次发消息时通过 QQ API (`GET /v2/users/{openid}`) 获取用户昵称，并将昵称作为记忆存入 `user/{uid}/profile`
- **FR-24**: LogicBrain MUST 从用户对话记忆中自动提取事实（兴趣、偏好、重要事件），积累到 `user/{uid}/profile` 和 `user/{uid}/facts`，形成用户画像。画像是 AI 理解对话者的最快切入点
- **FR-25**: 群聊场景中，系统 MUST 为群内每个发言者独立维护画像（`user/{uid}/profile`），与私聊画像共享同一命名空间

### 主动行为

- **FR-19**: ProactiveSystem（含 BoredomDetector、InterestModel）MUST 在 QQ 模式下正常工作
- **FR-20**: 主动发言 MUST 遵守 QQ 平台频控（2026 年 6 月后群聊主动发言已全量开放）

### 配置

- **FR-21**: QQ Bot 凭证（AppID、Secret）MUST 通过环境变量注入，支持 `${VAR}` 语法
- **FR-22**: 用户 MUST 可通过配置文件调整：回复开关（群聊/私聊）、会话 TTL、健康检查端口

---

## Success Criteria

- **SC-1**: 用户在 QQ 私聊发送 "你好" 后 2-5 秒内收到符合 AI 人格的回复，响应速度受 AI 当前情绪状态影响
- **SC-2**: 同一用户连续 5 轮对话，AI 能准确引用第 1 轮的内容（跨 turn 记忆召回有效）
- **SC-3**: 3 个不同用户同时发消息，各自收到独立回复，AI 情绪中烦躁维度（anger）增长 ≥ 0.2（从 EmotionEngine.state.anger 读取）
- **SC-4**: 群聊中 10 条普通消息全部被旁听记录但不触发回复，@机器人 消息获得回复
- **SC-5**: WebSocket 断开后 10 秒内自动重连成功
- **SC-6**: 系统连续运行 24 小时无内存泄漏（子 Session TTL 清理正常）
- **SC-7**: 原有 84 条 CLI 模式测试全部通过（QQ 集成不引入回归）
- **SC-8**: 用户首次发消息后，AI 在后续对话中能自然称呼用户昵称，无需用户自我介绍
- **SC-9**: 5 人同时对话时（高竞态），至少 1 个子 Session 的注入上下文被截断至 ≤ 50% 原始长度；竞态恢复至低（≤2 人）后，所有注入恢复完整

---

## Key Entities

- **MessageContext**: QQ 消息事件的类型化表示
- **LogicBrain / EmotionBrain（global, singleton）**: 全局唯一的双主脑，AI 的核心自我。所有对话共享同一对主脑
- **SubSession（ReActLoop, per-conversation）**: AI 与单个对话者交互的"注意力线程"。每个活跃对话一个实例，含独立的消息历史和上下文
- **RaceTracker**: 竞态追踪器——监控当前活跃子 Session 数量，向 EmotionEngine 输出竞态指标
- **SubconsciousInjector**: 潜意识注入器——根据竞态程度调节每个子 Session 的潜意识上下文质量
- **UserSession**: 对话元数据 — user_id、session_key、活跃时间戳，关联一个子 Session
- **UserProfile**: 用户画像 — 昵称、兴趣、偏好，存储在 `user/{uid}/profile`
- **EmotionEngine（global）**: 全局 10 维情绪引擎，竞态驱动"烦躁"维度加速增长
- **PersonalityEngine（global）**: 全局 8 维人格引擎

---

## Assumptions

1. QQ Bot 已注册并获取了有效的 AppID 和 AppSecret
2. DeepSeek API Key 已配置且可用
3. 目标并发量级：数十个同时活跃对话者，而非数万
4. 群聊普通消息不回复是合理的产品决策
5. 子 Session TTL 默认 1 小时
6. 全局 EmotionEngine 的"烦躁"维度作为竞态压力的真实反映——AI 是一个整体，其情绪反映所有对话的累积压力
7. QQ API 获取昵称与消息发送共享同一 token 管理机制
8. "顾此薄彼"是预期行为，不是 bug——竞态下部分对话获得优先级更高的注意力是符合"人性化"设计的
