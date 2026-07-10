# Research: 记忆联锁 + Recall 深刻化

**Feature**: `003-memory-chain-recall`
**Created**: 2026-07-10

---

## Decision Log

### R1: 联锁链实现方式

**Decision**: 在 MemoryStore 内新增 `search_chained()` 方法，内部调用现有 FTS5 检索 L0，然后按 4 级 fallback 循环查询延伸。

**Rationale**: 
- 复用现有 FTS5 + memory_links + topic_tags 基础设施
- 单方法封装避免调用方关心链逻辑
- 与 chat-engine 的 `_cluster_boost` + `_spread_activate` 模式一致

**Alternatives considered**:
- A) 在 Brain/Loop 层分别实现链逻辑 → 代码重复，违反 DRY
- B) 完全独立的新 SearchEngine 类 → 过度设计，当前规模不需要

---

### R2: salience 初始值

**Decision**: 保持已有 DEFAULT 5.0 不变。

**Rationale**:
- 设计文档 §7 的分级条件基于 salience≥5 判定长期记忆
- 5.0 恰好是边界值——新记忆初始 access_count=0，不满足长期条件，自动归为短期
- 若改为 3.0，一次 recall(+0.5) 仍只有 3.5，需要多次才能到 5，拖慢晋升
- 5.0 一次 recall 即到 5.5，3 次 recall 后 salience≈6.5 + access=3 → 长期记忆，节奏合理

**Alternatives considered**:
- A) 改为 0 → 晋升太慢，新记忆永远是短期
- B) 改为 8 → 新记忆不经过短期，直接长期

---

### R3: 自然语言回溯模板

**Decision**: 8 种连接词硬编码在 `_format_recall_result()` 中随机轮换。情绪推演按 dominant dim 从 3 组模板中抽取。

**Rationale**:
- 连接词多样化是自然的必要条件，硬编码保证可控
- 情绪推演模板按 joy/anxiety/mixed 分 3 组，覆盖常见情绪场景
- 不依赖 LLM 生成回溯文本——调用方（子Session LLM）只需接收自然语言输入

**Alternatives considered**:
- A) 用 LLM 格式化回溯 → 增加延迟和成本，违背工具轻量原则
- B) 配置文件驱动模板 → 当前无此需求，过度设计

---

### R4: 短期记忆裁剪策略

**Decision**: 短期记忆按 `short_term/*` 全局裁剪（非 per-user），保留 salience 最高的 10 条。

**Rationale**:
- short_term/* 来源是行为脑搜索结果和近期话题，属于全局共享资源
- per-user 裁剪需要引入 user_id 到 short_term 命名空间，增加复杂性
- 10 条上限参考 chat-engine 设计文档中的"保留近 10 条"

**Alternatives considered**:
- A) per-user 裁剪 → 需要改造 short_term namespace 结构
- B) 不裁剪 → 无限增长

---

### R5: 记忆迁移触发时机

**Decision**: 在 `save()` 和 `search_chained()` 结束时触发迁移检查（异步 fire-and-forget）。

**Rationale**:
- save 时可能新增 short_term 条目 → 需检查是否触发裁剪/晋升
- search_chained 后 salience 可能变化 → 需检查是否需要标记 deep
- 异步执行不阻塞主检索路径

**Alternatives considered**:
- A) 定时任务 (类似 decay tick) → salience 变化后延迟迁移，不够即时
- B) 手动触发 API → 增加调用方负担

---

### R6: Max_per_level 取值

**Decision**: 主脑 3，子Session 2。

**Rationale**:
- 主脑需要更多延伸记忆用于审查和注入，max_per_level=3 保证单级可获取足够候选
- 子Session 上下文敏感，max_per_level=2 避免注入信息过载
- 配合 extensions 数组限制，子Session 最多返回 3+2+1=6 条，上下文安全

**Reference**: chat-core-design.md §11.3 审查系统期望主脑获取更多上下文用于审查。

---

### R7: 断链处理

**Decision**: memory_link 指向已删除/过期记忆时，静默跳过，不占配额，自动 fallback 下一级。

**Rationale**:
- 显式关联可能因记忆过期而断裂，这是正常现象
- 静默跳过保持回溯输出清洁，不暴露内部错误
- 通过 fallback 保证每条 rank 的延伸数量尽可能达标

**Alternatives considered**:
- A) 输出 "某条关联记忆已丢失" → 暴露内部状态，不符合自然语言原则
- B) 提前清理断链 → 需要额外的维护任务，增加复杂度
