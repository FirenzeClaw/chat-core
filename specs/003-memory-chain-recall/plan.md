# Implementation Plan: 记忆联锁 + Recall 深刻化

**Feature**: `003-memory-chain-recall`
**Source Spec**: [spec.md](./spec.md)
**Source Design**: [design.md](../../docs/superpowers/specs/2026-07-10-memory-chain-recall-design.md)
**Created**: 2026-07-10
**Status**: Planned

---

## Technical Context

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Language | Python 3.12+ | chat-core 统一语言，asyncio 原生 |
| Database | SQLite + FTS5 via aiosqlite | 复用现有 MemoryStore，新增列 |
| Async Runtime | asyncio | 复用现有并发模型 |
| New Dependencies | 无 | 纯内部改造，不引入新依赖 |
| Test Framework | pytest + pytest-asyncio | 复用现有测试体系 |

### Performance Target

| Metric | Target | Source |
|--------|--------|--------|
| search_chained() 主脑 (13条上限) | < 500ms | 新增约束 |
| search_chained() 子Session (6条上限) | < 200ms | 新增约束 |
| salience + access_count 更新 | 原子操作，无额外延迟 | FR-07, FR-08 |
| 记忆迁移触发 | 异步后台，不阻塞 recall | FR-12, FR-13 |

### External Dependencies

| Dependency | Purpose | Fallback |
|------------|---------|----------|
| 无新增 | — | — |

---

## Constitution Check

**Status**: N/A — No `.specify/memory/constitution.md` exists. Governance from `AGENTS.md`:

- **四脑分离** ✅ 主脑/子Session recall 权限隔离保持不变的结构原则
- **主脑不发言原则** ✅ 不变 — recall 工具仅检索，不影响发言机制
- **本地优先** ✅ SQLite 纯内部改造，无外部依赖
- **asyncio 并发模型** ✅ MemoryStore 已有 WAL 模式，支持并发读写
- **无外部编排依赖** ✅ 纯库内改造

---

## Phase Breakdown

### Phase 1: Schema & Core Chain Engine

**Goal**: MemoryStore schema 升级 + search_chained() 核心链引擎。

**Files to modify/create**:

```
chat_core/
├── core/types.py          # MODIFY: +ChainedMemory, +RecallChainConfig
└── systems/memory.py      # MODIFY: +schema migration, +search_chained(), 
                           #   +_extend_chain(), +_dedup_by_quality(),
                           #   +4-level fallback queries, +salience boost,
                           #   +_format_recall_result(), +_migrate_short_to_long(),
                           #   +_trim_short_term()
```

**Key deliverables**:
- [ ] Schema 迁移: `access_count INTEGER DEFAULT 0`, `last_access TEXT` (FR-09)
- [ ] `RecallChainConfig` 和 `ChainedMemory` dataclass (FR-03, FR-04)
- [ ] `search_chained(query, chain_config, namespace_prefix)` 方法:
  - FTS5 主检索 → _extend_chain 每 rank → 全局去重 → salience boost → 自然语言格式化
- [ ] `_extend_chain(entry, target_n, namespace_prefix)` 4 级 fallback:
  - L1: memory_links → L2: topic_tags → L3: entity_type → L4: namespace (FR-02)
  - 每级受 namespace_prefix + max_per_level 约束 (FR-06)
  - 断链静默跳过 (FR-06b)
- [ ] `_dedup_by_quality()` 全局去重: priority links>tags>entity>namespace (FR-05)
- [ ] salience + access_count 深刻化: 命中即递增，按链深度衰减 (FR-07, FR-08) — **见 Phase 2 (US2)**
- [ ] `_format_recall_result()` 自然语言格式化: 连接词、情绪注解、情绪推演、空结果文本 (FR-15~19)
- [ ] `_migrate_short_to_long()` + `_trim_short_term()` + `_mark_deep_memory()` 记忆分级 (FR-10~13) — **见 Phase 2 (US2)**
- [ ] `search()` 旧 API 保持不变 (FR-06a)

**Tests** (`tests/test_memory.py`):
- [ ] `test_search_chained_main_brain` — 验证 5+3+2+2+1+0 条链
- [ ] `test_search_chained_sub_session` — 验证 3+2+1 条链 + namespace 限制
- [ ] `test_extend_chain_levels` — 4 级 fallback 各自返回正确条目
- [ ] `test_dedup_across_chains` — 去重保留最高 priority
- [ ] `test_broken_link_skip` — 断链静默跳过 + 降级
- [ ] `test_natural_language_output` — 连接词、情绪注解、推演
- [ ] `test_empty_recall_human_text` — 空结果人性化文本
- [ ] `test_old_search_unchanged` — 旧 API 不受影响

**Acceptance**: `python -m pytest tests/test_memory.py -v -k "chain"` 全部通过。

---

### Phase 2: Brain & Loop Recall 升级

**Goal**: 主脑和子Session 的 recall 工具切换至 search_chained() + 权限隔离。

**Files to modify**:

```
chat_core/core/
├── brain.py               # MODIFY: _execute_recall → search_chained()
└── loop.py                # MODIFY: register_sub_session_tools 接收 chain_config,
                           #   recall handler 用 search_chained() + namespace 限制
```

**Key deliverables**:
- [ ] `brain.py:_execute_recall()` 改用 `search_chained(query, LOGIC_BRAIN_CONFIG)` (FR-20)
- [ ] `loop.py:register_sub_session_tools()` 新增 `chain_config` 参数 (FR-04, FR-21, FR-22)
  - recall handler 闭包捕获 `chain_config`, 调用 `search_chained(query, config)`
  - 工具描述更新: "从记忆中检索信息。会自动追溯关联记忆。只读。"
- [ ] 子Session recall 输出格式切换为自然语言文本，不再返回 JSON (FR-14)

**Tests**:
- [ ] `tests/test_brain.py`: +`test_logic_recall_chained`
- [ ] `tests/test_loop.py`: +`test_sub_recall_namespace_isolation`, +`test_sub_recall_natural_language`

**Acceptance**: 主脑 recall 返回联锁链，子Session recall 受 namespace 限制且输出自然语言。

---

### Phase 3: QQ Bot Adapter 适配

**Goal**: BotAdapter 的 _get_or_create_sub_session 注入 user_id + scene 到子Session recall。

**Files to modify**:

```
chat_core/qq/
└── adapter.py             # MODIFY: _get_or_create_sub_session 传入 user_id
```

**Key deliverables**:
- [ ] `_get_or_create_sub_session` 新增参数 `user_id`, `scene` (FR-21)
- [ ] 构造子Session `RecallChainConfig`: top_n=3, extensions=[2,1,0], namespace_prefix=`user/{uid}`
- [ ] 传入 `register_sub_session_tools(tools, loop, chain_config=config)`

**Tests**:
- [ ] `tests/test_qq_adapter.py`: +`test_sub_session_namespace_restriction`

**Acceptance**: QQ Bot 子Session recall 仅能检索当前用户记忆。

---

### Phase 4: Integration & Regression

**Goal**: 全量测试通过，文档更新。

**Files to modify**:

```
AGENTS.md                  # MODIFY: 更新 memory.py 文件索引
CHANGELOG.md               # MODIFY: 记录变更
```

**Key deliverables**:
- [ ] 全量回归: `python -m pytest tests/ -v --ignore=tests/spec_e2e_test.py` 全部通过 (SC-07)
- [ ] AGENTS.md 更新 memory.py 描述：新增 search_chained + 记忆分级能力
- [ ] CHANGELOG.md 追加 003 条目

---

## Dependency Graph

```
Phase 1 (Schema + Chain Engine)     ← 所有阶段的基石
  ├─► Phase 2 (Brain + Loop)       ← 依赖 search_chained()
  │     └─► Phase 3 (QQ Adapter)   ← 依赖 register_sub_session_tools 新签名
  └─► Phase 4 (Integration)        ← 所有阶段完成后执行
```

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| 联锁链查询量大 → recall 延迟增加 | 中 | 每级 max_per_level 上限 + 索引优化；Phase 1 测试中验证 < 500ms |
| 并发 recall 时 salience 读写竞态 | 低 | SQLite WAL 模式 + 单条 UPDATE 原子操作 |
| 已有 DB 无新列 → 迁移失败 | 低 | ALTER TABLE ADD COLUMN 含 DEFAULT，现存行自动填充 |
| _format_recall_result 输出过长 → 撑爆子Session 上下文 | 中 | 每条记忆摘要 ≤ 100 字, 联锁总数子Session 最多 6 条 |
| 自然语言格式变化 → LLM 行为变化 | 低 | LLM 只需读文本，不需要解析 JSON，实际更友好 |

---

## Implementation Notes

### Key Differences from Current Implementation

| Aspect | 当前 | 升级后 |
|--------|------|--------|
| recall 输出 | JSON `{"results": [...]}` | 自然语言 "我记得... 还想起..." |
| 检索方式 | `search(query, top_n=N)` | `search_chained(query, config)` |
| salience | 存在但从未使用 | 每次命中 +0.5, 驱动记忆分级 |
| access_count | 不存在 | 每次命中 +1 |
| 主脑/子Session recall | 无区别 (都调 search) | 配置不同, namespace 隔离 |
| 记忆分级 | 无 | short_term → user/* → deep |

### Backward Compatibility

- `MemoryStore.search()` 行为不变 (FR-06a)
- `MemoryStore.save()`, `get()`, `delete()` 不变
- 现有 108 tests 必须全部通过 (SC-07)
- 新增 schema 列含 DEFAULT，对已有数据透明
