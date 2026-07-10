# Feature Specification: 记忆联锁 + Recall 深刻化

**Feature**: `003-memory-chain-recall`
**Created**: 2026-07-10
**Status**: Draft
**Source**: [Design Document](../../docs/superpowers/specs/2026-07-10-memory-chain-recall-design.md) — 脑力激荡产出

---

## Overview

升级 chat-core 的记忆检索系统，从"单条匹配返回 JSON"进化为"联锁回忆 + 自然语言回溯"。核心理念：人类回忆不是数据库查询——想起一件事会自然联想到相关的其他事，回忆越频繁记忆越深刻。AI 的回溯方式也应像人一样自然，不是机械地 dump JSON。

**关键变化**：
- **记忆联锁**：每次 recall 不仅返回最匹配的记忆，还按 4 级关联链（显式 links → 话题标签 → 实体类型 → 命名空间）延伸出关联记忆
- **Recall 深刻化**：每次被 recall 命中的记忆，其重要性评分 (salience) 和访问次数自动上升，模拟"越想越深刻"
- **记忆分级**：引入短期记忆 / 长期记忆 / 深刻记忆三级，按 salience 和 access_count 自动晋升
- **自然语言回溯**：recall 返回的不再是 JSON 数组，而是带随机连接词和情绪注解的自然语言文本
- **主脑/子Session recall 权限隔离**：主脑可读全量记忆，子Session 只能读当前用户的记忆和短期记忆

---

## User Scenarios & Testing

### 1. AI 在对话中自然引用关联记忆

**Actor**: 用户（通过 CLI 或 QQ 与 AI 对话）

**Flow**:
1. 用户提到某个话题（如"小刚最近怎么样"）
2. 子 Session 调用 recall 工具，传入关键词"小刚"
3. 系统检索到最匹配的记忆（小刚的基本信息），并沿关联链延伸出相关记忆（他的项目、他的猫、他的情绪状态）
4. recall 以自然语言返回："我记得小刚是产品经理。还想起他上周提到在做一个 AI 项目。说到这个，他养了一只叫豆包的猫。这些记忆让我觉得他最近成就感和压力并存。"
5. AI 基于回溯自然生成回复

**Acceptance**:
- recall 返回的是自然语言文本，不是 JSON 数组
- 连接词多样化（我记得、还想起、说到这个、对了……），不机械重复
- 有情绪标签的记忆附带情绪注解

### 2. 多次 recall 同一记忆后记忆变得更深刻

**Actor**: 系统

**Flow**:
1. 用户在多次对话中反复提到同一话题
2. 每次 recall 命中该记忆时，其 salience 自动递增 (+0.5)
3. 当 salience 积累到 ≥ 5 且被访问 ≥ 3 次，短期记忆自动晋升为长期记忆
4. 当 salience ≥ 7，晋升为深刻记忆，几乎不再衰减

**Acceptance**:
- salience 从初始值 (5.0) 随 recall 命中上升
- access_count 每次命中 +1
- short_term/* 下达标条目自动迁移到 user/* 
- salience ≥ 7 条目标记为深刻记忆

### 3. 子 Session 只能读取自己的记忆

**Actor**: 系统

**Flow**:
1. 用户 A 的子 Session 调用 recall
2. 系统自动将检索范围限制在 `user/A/*` 和 `short_term/*`
3. 用户 B 的私聊记忆、AI 的内心戏 (self/inner_thoughts) 均不可见

**Acceptance**:
- 子 Session recall 无法返回其他用户的记忆
- 子 Session recall 无法返回 self/* 或 subconscious/* 内容
- 主脑 recall 保持全量访问

### 4. 群聊旁听记忆被 AI 自然引用

**Actor**: QQ 群用户

**Flow**:
1. 用户在群聊中多次发言，被旁听系统记录
2. 该用户后来私聊 AI，AI 通过 recall 检索到群聊旁听记忆
3. recall 以自然语言回溯群聊中观察到的信息

**Acceptance**:
- 群聊旁听写入 `user/{uid}/group/{gid}/observations` 后可在 recall 中检索
- recall 回溯内容不暴露"这是我偷听来的"——自然融入

### 5. 无记忆时自然表达"想不起来"

**Actor**: 用户

**Flow**:
1. 用户首次与 AI 对话，无任何先验记忆
2. 子 Session 调用 recall
3. recall 返回自然语言："脑子里暂时一片空白。"（而非 JSON 空数组或"No results"）

**Acceptance**:
- 无记忆时不机械输出空 JSON
- 使用人性化的"想不起来"表达

---

## Functional Requirements

### 记忆联锁

- **FR-01**: 系统 MUST 在 recall 检索时，对每条主匹配结果沿关联链延伸出指定数量的关联记忆
- **FR-02**: 关联链 MUST 按 4 级优先级执行：显式 memory_links → topic_tags 交集 → entity_type 同类型 → namespace 同前缀。每级取足配额后停止
- **FR-03**: 主脑 (LogicBrain) recall MUST 使用配置 top_n=5, extensions=[3,2,2,1,0]，即主匹配 5 条，分别延伸 3/2/2/1/0 条
- **FR-04**: 子Session (ReActLoop) recall MUST 使用配置 top_n=3, extensions=[2,1,0]
- **FR-05**: 同一记忆在多条延伸链中重复出现时，MUST 仅保留优先级最高 (links > tags > entity > namespace) 的来源
- **FR-06**: 所有延伸检索 MUST 遵守 namespace_prefix 限制（子Session 为 user/{uid} 和 short_term）
- **FR-06a**: 已有的 MemoryStore.search() 方法 MUST 保持原有行为不变，search_chained() 为新增入口，不影响现有调用方
- **FR-06b**: 延伸检索遇到已删除或已过期的目标记忆时，MUST 静默跳过（不计入配额），自动降级到下一级 fallback

### Recall 深刻化

- **FR-07**: 每次 recall 返回的每条记忆（含延伸链条中的），其 access_count MUST 自动 +1, last_access 更新为当前时间
- **FR-08**: 每条命中记忆的 salience MUST 按链深度递增：direct match +0.50, links +0.30, topic_tags +0.20, entity +0.15, namespace +0.10。salience 上限为 10.0
- **FR-09**: schema 中 MUST 保留已有的 salience 字段 (REAL DEFAULT 5.0) 并使其生效；MUST 新增 access_count (INTEGER DEFAULT 0)、last_access (TEXT) 和 decay_curve (TEXT DEFAULT 'standard') 字段

### 记忆分级

- **FR-10**: 系统 MUST 维护三级记忆：短期记忆（尚未巩固，不满足长期条件）、长期记忆 (salience≥5 且 access_count≥3)、深刻记忆 (salience≥7)。新创建的条目默认归类为短期记忆
- **FR-11**: 短期记忆存储于 short_term/* namespace，MUST 自动裁剪至最多 10 条（按 salience 降序保留）
- **FR-12**: 当短期记忆中某条目同时满足 salience≥5 且 access_count≥3 时，MUST 自动迁移到 user/* namespace
- **FR-13**: 当长期记忆中某条目 salience≥7 时，MUST 标记 decay_curve='deep'，成为深刻记忆

### 自然语言回溯

- **FR-14**: recall 工具返回给 LLM 的内容 MUST 是自然语言文本，不再使用 JSON 格式
- **FR-15**: 回溯文本 MUST 以"【记忆回溯】"开头，第一条记忆用"我记得"开头
- **FR-16**: 延伸记忆 MUST 使用随机轮换的连接词："还想起"、"哦对"、"说到这个"、"这让我想起"、"顺便一提"、"对了"、"说起来"、"那次也是"
- **FR-17**: 有 emotional_tags 的记忆 MUST 在内容后附加情绪注解，格式为"（{情绪描述}）"，无情绪标签的记忆不添加注解
- **FR-18**: 多条回溯记忆的情绪一致性高时（dominant 情绪维度占比 > 60%），MUST 在末尾添加情绪推演，如"这些记忆让我觉得他最近成就感和压力并存。"
- **FR-19**: 检索无结果时 MUST 返回人性化文本（如"目前没什么特别的记忆浮现。"），不返回空数组或"No results"

### 主脑/子Session recall 隔离

- **FR-20**: 主脑 (LogicBrain) recall MUST 可读全部命名空间，包括 self/*、subconscious/*
- **FR-21**: 子Session (ReActLoop) recall MUST 仅可读当前用户的 user/{uid}/* 和 short_term/* 命名空间
- **FR-22**: 子Session recall MUST 不可读 self/* (AI 内心戏)、subconscious/* (潜意识区) 和其他用户的 user/{other_uid}/*

---

## Success Criteria

| ID | Criterion | Target |
|----|-----------|--------|
| SC-01 | 主脑 recall 返回联锁记忆链 | 每条主匹配附带指定数量的延伸记忆 (5+3+2+2+1+0=13 条上限) |
| SC-02 | recall 命中后 salience 递增 | 每次命中 +0.5 (direct), 可验证 |
| SC-03 | 短期记忆 → 长期记忆自动迁移 | salience≥5 且 access_count≥3 时迁移, 可验证 |
| SC-04 | recall 返回自然语言 | 含随机连接词、情绪注解, 无 JSON 残留 |
| SC-05 | 子Session recall 无法读取其他用户记忆 | namespace_prefix 限制生效, 可验证 |
| SC-06 | 无记忆时返回人性化文本 | 不返回空数组、"No results" 或 JSON |
| SC-07 | 现有 108 tests 全部通过 | 无回归 |
| SC-08 | 短期记忆裁剪 | short_term/* 下不超过 10 条 |

---

## Key Entities

- **ChainedMemory**: 带联锁元数据的记忆条目 — 包含 MemoryEntry 本体 + 链深度 (direct/link/tags/entity/namespace) + 来源父记忆 key + 相关度评分
- **RecallChainConfig**: recall 配置 — 包含 top_n、extensions 数组、max_per_level、namespace_prefix。主脑和子Session 各自持有一份不同的配置
- **MemoryEntry** (增强): 已有实体 — 新增 access_count、last_access 字段，salience 字段生效
- **MemoryLink**: 已有实体 — 显式记忆关联，联锁链的第一优先级
- **MemoryStore** (增强): 新增 search_chained() 方法替代原 search() 为 recall 使用

---

## Assumptions

1. salience 初始值为 5.0（已有 DEFAULT），衰减曲线留后续 Phase 实施
2. 遗忘/衰减系统 (艾宾浩斯曲线) 不在本次范围，参考 chat-engine 实现但独立推进
3. short_term 10 条上限按全局 short_term/* 计算（非 per-user）
4. recall 自然语言回溯的连接词和情绪推演模板硬编码在系统中，不可通过配置文件修改（简化实现）
5. 情绪注解文本由 emotional_tags JSON 字段自动生成，不依赖 LLM
6. 主脑 recall 配置和子Session recall 配置为常量，不可运行时动态调整
7. 联锁延伸检索每级的上限 (max_per_level) 对主脑为 3，子Session 为 2
8. 已有数据库的现存条目，access_count 和 last_access 缺省为 0/NULL，不影响检索（仅在首次被 recall 命中后开始累积）

---

## Out of Scope

- 艾宾浩斯遗忘曲线 (apply_decay)
- decay_curve 的 'standard' 曲线自动衰减
- detail → gist 层的 auto_migrate 模糊化
- 高频访问 boost (7天≥3次延长15天)
- LLM 精排 rerank
- 记忆→情绪反哺
- 话题→兴趣联动 (已由 interest.py 覆盖)
