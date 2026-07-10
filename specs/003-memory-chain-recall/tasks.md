# Tasks: 记忆联锁 + Recall 深刻化

**Feature**: `003-memory-chain-recall`
**Source Plan**: [plan.md](./plan.md)
**Source Spec**: [spec.md](./spec.md)
**Created**: 2026-07-10

---

## Phase Mapping (plan.md → tasks.md)

| Plan Phase | Tasks Phase | Description |
|------------|-------------|-------------|
| Phase 1 (Schema + Core) | Phase 1-3 | Setup + Foundation + US1 (Chain Engine) |
| Phase 2 (Brain + Loop) | Phase 4 | US2 (Recall Upgrade) |
| Phase 3 (QQ Adapter) | Phase 5 | US3 (Namespace Isolation) |
| Phase 4 (Integration) | Phase 6 | Polish & Regression |

---

## User Stories

| ID | Priority | Story | Core FRs |
|----|----------|-------|----------|
| US1 | P1 | 记忆联锁 + 自然语言回溯 — recall 返回关联链记忆，自然语言输出 | FR-01~06, FR-14~19 |
| US2 | P1 | Recall 深刻化 + 记忆分级 — salience 递增, 短期/长期/深刻三级自动晋升 | FR-07~13 |
| US3 | P1 | 主脑/子Session recall 权限隔离 — 子Session 仅可读自身命名空间 | FR-20~22 |

---

## Phase 1: Setup — Types & Schema

**Goal**: 新增数据类型 + schema 迁移就绪。所有后续 Phase 的基石。

- [ ] T001 [P] Add `ChainedMemory` and `RecallChainConfig` dataclasses to `chat_core/core/types.py` — ChainedMemory: entry, chain_level, chain_parent_key, relevance_score; RecallChainConfig: top_n, extensions, max_per_level, namespace_prefix
- [ ] T002 [P] Add `LOGIC_BRAIN_CHAIN_CONFIG` and `SUB_SESSION_CHAIN_CONFIG` constants to `chat_core/core/types.py` — 主脑 top_n=5 extensions=[3,2,2,1,0] max_per_level=3; 子Session top_n=3 extensions=[2,1,0] max_per_level=2
- [ ] T003 Schema migration in `chat_core/systems/memory.py` → `MemoryStore.open()`: add `access_count INTEGER DEFAULT 0`, `last_access TEXT`, and `decay_curve TEXT DEFAULT 'standard'` columns (ALTER TABLE with existence check, idempotent)
- [ ] T004 Validate schema migration: launch bot or run `python -c "from chat_core.systems.memory import MemoryStore; import asyncio; asyncio.run(MemoryStore(':memory:').open())"` and verify new columns exist

**Checkpoint**: Types importable, schema columns exist.

---

## Phase 2: Foundational — Core Chain Engine

**Goal**: search_chained() 方法 + 4 级延伸 + 去重 + 格式化。所有 recall 工具依赖此层。

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete.

- [ ] T005 Implement `_extend_by_links()` in `chat_core/systems/memory.py` — query memory_links table for source entry, join to memories, filter expired/deleted (FR-06b), apply namespace_prefix + max_per_level. Return matching MemoryEntry list.
- [ ] T006 [P] Implement `_extend_by_tags()` in `chat_core/systems/memory.py` — parse entry.topic_tags JSON, query memories WHERE topic_tags overlap, filter namespace_prefix, sort by tag intersection size desc, apply max_per_level. Return list.
- [ ] T007 [P] Implement `_extend_by_entity()` in `chat_core/systems/memory.py` — query memories WHERE entity_type = entry.entity_type, exclude self, filter namespace_prefix, apply max_per_level. Return list.
- [ ] T008 [P] Implement `_extend_by_namespace()` in `chat_core/systems/memory.py` — query memories WHERE namespace LIKE prefix, exclude self, sort by recency, apply max_per_level. Return list.
- [ ] T009 Implement `_extend_chain()` in `chat_core/systems/memory.py` — orchestrator: for each level [links→tags→entity→namespace], call corresponding method, accumulate results, stop when target_n reached. Each level's results stored with correct chain_level (1=links, 2=tags, 3=entity, 4=namespace). (FR-02)
- [ ] T010 [P] Implement `_dedup_by_quality()` in `chat_core/systems/memory.py` — group by namespace/key, keep lowest chain_level per key, return deduped list sorted by (chain_level, relevance_score). (FR-05)
- [ ] T011 [P] Implement `_format_recall_result()` in `chat_core/systems/memory.py` — convert ChainedMemory list to natural language: "【记忆回溯】\n我记得：..." with random connectors (FR-16), emotion annotations where emotional_tags exist (FR-17), emotion inference where consistency high (FR-18), human fallback text when empty (FR-19)
- [ ] T012 Implement `search_chained()` in `chat_core/systems/memory.py` — main entry: ① FTS5 search top_n direct matches ② for each rank i, call _extend_chain with extensions[i] ③ _dedup_by_quality ④ salience + access_count update ⑤ _format_recall_result → return natural language string. (FR-01, FR-03, FR-04, FR-07, FR-08, FR-14, FR-15)
- [ ] T013 Ensure `MemoryStore.search()` old API unchanged — verify no signature changes, no behavior changes. (FR-06a)

**Checkpoint**: `search_chained(query, config)` returns natural language text with correct chain lengths.

---

## Phase 3: US1 — Memory Chain + Natural Language (P1)

**Goal**: search_chained() 通过测试验证，所有联锁/延伸/去重/格式化逻辑正确。

**Independent Test**: `python -m pytest tests/test_memory.py -v -k "chain"` — all chain-related tests pass.

### Tests

- [ ] T014 [P] [US1] `test_search_chained_main_brain` in `tests/test_memory.py` — seed test data, call search_chained with LOGIC_BRAIN_CHAIN_CONFIG, assert result is natural language string, assert 5 direct + 3+2+2+1+0 extension structure
- [ ] T015 [P] [US1] `test_search_chained_sub_session` in `tests/test_memory.py` — call with SUB_SESSION_CHAIN_CONFIG, assert 3 direct + 2+1 extension, assert namespace_prefix filtering works
- [ ] T016 [P] [US1] `test_extend_chain_levels` in `tests/test_memory.py` — seed memory_links, topic_tags, entity_type across entries; verify each extension level returns correct entries; verify fallback when one level empty
- [ ] T017 [P] [US1] `test_dedup_across_chains` in `tests/test_memory.py` — create same memory reachable via links (L1) and tags (L2) from different direct matches; assert only L1 version retained
- [ ] T018 [P] [US1] `test_broken_link_skip` in `tests/test_memory.py` — seed memory_link pointing to expired/deleted entry; assert chain still succeeds with fallback to next level
- [ ] T019 [P] [US1] `test_natural_language_output` in `tests/test_memory.py` — call search_chained, assert output starts with "【记忆回溯】", contains "我记得", uses varied connectors, includes emotion annotations on tagged entries
- [ ] T020 [P] [US1] `test_empty_recall_human_text` in `tests/test_memory.py` — call search_chained on empty DB, assert output is human text (not JSON, not "No results", not empty)

**Checkpoint**: `python -m pytest tests/test_memory.py -v -k "chain"` — all 7 pass.

---

## Phase 4: US2 — Recall Deepening + Memory Tiering (P1)

**Goal**: salience/access_count 深刻化生效，记忆三级自动晋升。

**Independent Test**: `python -m pytest tests/test_memory.py -v -k "salience or migrate or trim"` — all pass.

### Implementation

- [ ] T021 [US2] Implement salience + access_count update in `search_chained()` at `chat_core/systems/memory.py` — after dedup, UPDATE each entry: `salience = MIN(salience + delta, 10.0)`, `access_count = access_count + 1`, `last_access = now()`; delta by chain_level: 0→0.50, 1→0.30, 2→0.20, 3→0.15, 4→0.10. (FR-07, FR-08)
- [ ] T022 [US2] Implement `_migrate_short_to_long()` in `chat_core/systems/memory.py` — query short_term/* WHERE salience≥5 AND access_count≥3, for each: save to user/* (same key), delete from short_term. Fire-and-forget after save() and search_chained(). (FR-10, FR-12)
- [ ] T023 [P] [US2] Implement `_mark_deep_memory()` in `chat_core/systems/memory.py` — query user/* WHERE salience≥7 AND (decay_curve IS NULL OR decay_curve!='deep'), UPDATE decay_curve='deep'. Fire-and-forget after search_chained(). (FR-13)
- [ ] T024 [P] [US2] Implement `_trim_short_term()` in `chat_core/systems/memory.py` — query all short_term/* entries, keep top 10 by salience DESC, delete the rest. Fire-and-forget after save() writes to short_term. (FR-11)

### Tests

- [ ] T025 [P] [US2] `test_salience_boost` in `tests/test_memory.py` — call search_chained 3 times with same query; assert direct match salience increases by 0.5 each call; assert chain_level=1 boosts by 0.3
- [ ] T026 [P] [US2] `test_access_count_increment` in `tests/test_memory.py` — call search_chained twice; assert access_count goes from 0→1→2
- [ ] T027 [P] [US2] `test_migrate_short_to_long` in `tests/test_memory.py` — create entry in short_term/ with salience=5 access_count=3; call save(); assert entry now exists in user/ and not in short_term/
- [ ] T028 [P] [US2] `test_trim_short_term` in `tests/test_memory.py` — create 12 entries in short_term/ with varying salience; call save(); assert only 10 remain, highest salience preserved
- [ ] T029 [P] [US2] `test_mark_deep_memory` in `tests/test_memory.py` — create entry in user/ with salience=7; call search_chained(); assert decay_curve='deep'

**Checkpoint**: `python -m pytest tests/test_memory.py -v -k "salience or migrate or trim"` — all 5 pass.

---

## Phase 5: US3 — Brain & Loop Recall Isolation (P1)

**Goal**: 主脑 recall 切换至 search_chained，子Session recall 带 namespace 限制 + 自然语言输出。

**Independent Test**: `python -m pytest tests/test_brain.py tests/test_loop.py -v -k "chained or recall"` — all pass.

### Implementation

- [ ] T030 [US3] Update `LogicBrain._execute_recall()` in `chat_core/core/brain.py` — replace `self._memory.search(query, top_n=10)` with `self._memory.search_chained(query, chain_config=from types import LOGIC_BRAIN_CHAIN_CONFIG)`. **Keep return type as `list[MemoryEntry]`** — the chained results are stored as MemoryEntry list for internal use. Natural language formatting happens at injection boundary. (FR-20)
- [ ] T030a [US3] Update `LogicBrain.think_inject()` in `chat_core/core/brain.py` — when building injection context from recall results, call `_format_recall_result(chained_entries)` to convert to natural language before injecting into sub-session. The raw `list[MemoryEntry]` remains available for structured review.
- [ ] T031 [US3] Update `register_sub_session_tools()` in `chat_core/core/loop.py` — add optional `chain_config: RecallChainConfig = None` parameter. If provided, recall handler uses `search_chained(query, chain_config)`. If not provided, fallback to old `search(query, top_n=5)` for backward compat.
- [ ] T032 [US3] Update recall tool description in `chat_core/core/loop.py` — change from "从记忆中检索相关信息。只读。" to "从记忆中检索信息。会自动追溯关联记忆。只读，仅能访问你的记忆和公共信息。" when chain_config is provided. (FR-21, FR-22)
- [ ] T033 [US3] Update `BotAdapter._get_or_create_sub_session()` in `chat_core/qq/adapter.py` — construct `RecallChainConfig(top_n=3, extensions=[2,1,0], max_per_level=2, namespace_prefix=f"user/{user_id}")`. Pass to `register_sub_session_tools(tools, loop, chain_config=config)`. Add `user_id` parameter. (FR-21)
- [ ] T034 [US3] Update `BotAdapter._get_or_create_sub_session()` call site in `chat_core/qq/adapter.py` → `_process()` method — pass `ctx.user_id` to the method.

### Tests

- [ ] T035 [P] [US3] `test_logic_recall_chained` in `tests/test_brain.py` — mock MemoryStore.search_chained returning natural language; verify LogicBrain._execute_recall returns that text; verify old tests still pass
- [ ] T036 [P] [US3] `test_sub_recall_namespace_isolation` in `tests/test_loop.py` — create sub-session with namespace_prefix="user/test_user"; mock search_chained; verify called with correct chain_config; verify other user's memories excluded
- [ ] T037 [P] [US3] `test_sub_recall_natural_language` in `tests/test_loop.py` — mock search_chained returning natural language; call recall tool; assert output is text not JSON

**Checkpoint**: `python -m pytest tests/test_brain.py tests/test_loop.py -v -k "chained or recall or chain"` — all pass.

---

## Phase 6: Polish & Cross-Cutting

**Goal**: 全量回归通过，文档更新。

- [ ] T038 Run full regression: `python -m pytest tests/ -v --ignore=tests/spec_e2e_test.py` — all existing tests pass. (SC-07)
- [ ] T039 [P] Update `AGENTS.md` — update memory.py description in file index: add "search_chained (联锁检索), _format_recall_result (自然语言回溯), 记忆分级 (短期/长期/深刻)"
- [ ] T040 [P] Update `CHANGELOG.md` — add `## [Unreleased] — 记忆联锁 + Recall 深刻化` entry summarizing FR-01~FR-22
- [ ] T041 [P] Verify backward compat: `python -c "from chat_core.systems.memory import MemoryStore; ms = MemoryStore(':memory:'); import asyncio; asyncio.run(ms.open()); results = asyncio.run(ms.search('test', top_n=5)); assert isinstance(results, list)"`

---

## Dependency Graph

```
Phase 1 (Setup: Types + Schema)
  └─► Phase 2 (Foundational: Chain Engine) ── Must complete ──►
       ├─► Phase 3 (US1: Chain + NL Tests)
       │     └─► Phase 4 (US2: Deepening + Tiering) ──►
       │           ├─► Phase 5 (US3: Brain/Loop Isolation)
       │           └─► Phase 6 (Polish)
       └─► Phase 5 (US3: can start after Phase 2)
```

**US3 can start in parallel with US1+US2** after Phase 2 — it only needs the imported constants (T002) and the new `register_sub_session_tools` signature.

---

## Parallel Execution Examples

### Batch 1 (Phase 2 complete)
```
Agent A: Phase 3 (US1) — T014-T020 — Chain + NL tests
Agent B: Phase 5 (US3) — T030-T037 — Brain/Loop upgrade
```
Both work on different files: test_memory.py vs brain.py/loop.py/adapter.py.

### Batch 2 (US1 + US3 complete)
```
Agent A: Phase 4 (US2) — T021-T029 — Deepening + tiering + tests
Agent B: Phase 6 — T038-T041 — Regression + docs
```

---

## Suggested MVP Scope

**MVP = Phase 1 + Phase 2 + Phase 3 (US1)**

After MVP: `search_chained()` returns natural language chained results. Salience/access_count tracking works. All chain extension levels functional. Test coverage established.

**MVP + US2**: Adds memory tiering (short→long→deep auto-migration).

**MVP + US2 + US3**: Full feature — recall isolation between main brain and sub-session.

---

## Format Validation

All tasks follow `- [ ] [TaskID] [P?] [Story?] Description with file path` format.
- 42 tasks total
- Phase 1 (Setup): 4 tasks (T001-T004)
- Phase 2 (Foundational): 9 tasks (T005-T013)
- Phase 3 (US1): 7 tasks (T014-T020)
- Phase 4 (US2): 9 tasks (T021-T029)
- Phase 5 (US3): 9 tasks (T030-T037)
- Phase 6 (Polish): 4 tasks (T038-T041)
- Parallelizable [P]: 21 tasks
