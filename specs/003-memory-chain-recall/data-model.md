# Data Model: 记忆联锁 + Recall 深刻化

**Feature**: `003-memory-chain-recall`
**Created**: 2026-07-10

---

## Entity Changes

### MemoryEntry (增强)

已有字段不变，新增：

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| access_count | INTEGER | 0 | 被 recall 命中的累计次数 |
| last_access | TEXT | NULL | 最后被 recall 命中的 ISO 时间戳 |
| salience | REAL | 5.0 | **已有字段，本次使其生效** — 重要性评分 [0, 10] |

salience 状态机:

```
  初始: 5.0
     │
     ├─ recall 命中 (direct)  → salience += 0.50
     ├─ recall 命中 (L1 link) → salience += 0.30
     ├─ recall 命中 (L2 tags) → salience += 0.20
     ├─ recall 命中 (L3 entity)→ salience += 0.15
     └─ recall 命中 (L4 ns)   → salience += 0.10
     │
     └─ 上限: salience ≤ 10.0
```

记忆分级判定:

```
短期记忆: NOT (salience ≥ 5 AND access_count ≥ 3)
  → 存储: short_term/*
  → 裁剪: 全局 ≤ 10 条

长期记忆: salience ≥ 5 AND access_count ≥ 3
  → 存储: user/*  (自动迁移自 short_term/*)
  → 晋升触发: save() 或 search_chained() 后

深刻记忆: salience ≥ 7
  → 标记: decay_curve='deep'
  → 原地存储 (不迁移)
  → 提升触发: search_chained() 后
```

---

### ChainedMemory (新增)

```python
@dataclass
class ChainedMemory:
    entry: MemoryEntry           # 记忆本体
    chain_level: int             # 0=direct, 1=links, 2=topic_tags, 3=entity, 4=namespace
    chain_parent_key: str | None # 来源记忆的 namespace/key (direct 时为 None)
    relevance_score: float       # FTS5 匹配度或相关度
```

去重规则: 同 `entry.namespace/entry.key` 出现多次时，保留 `chain_level` 最小的。

---

### RecallChainConfig (新增)

```python
@dataclass
class RecallChainConfig:
    top_n: int                    # FTS5 主检索返回条数
    extensions: list[int]         # 每个 rank 的延伸数量 [N₀, N₁, ...]
    max_per_level: int            # 每级 fallback 查询上限
    namespace_prefix: str | None  # 检索命名空间限制
```

两套常量配置:

```python
# 主脑
LOGIC_BRAIN_CHAIN_CONFIG = RecallChainConfig(
    top_n=5,
    extensions=[3, 2, 2, 1, 0],
    max_per_level=3,
    namespace_prefix=None,  # 全量
)

# 子Session
SUB_SESSION_CHAIN_CONFIG = RecallChainConfig(
    top_n=3,
    extensions=[2, 1, 0],
    max_per_level=2,
    namespace_prefix=None,  # 运行时注入 user/{uid}
)
```

---

## Schema Migration

```sql
-- 新增列 (已有 DEFAULT 值，对现存行透明)
ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN last_access TEXT;
ALTER TABLE memories ADD COLUMN decay_curve TEXT DEFAULT 'standard';

-- 已有列 salience 已存在: REAL DEFAULT 5.0 (无变更)
```

迁移在 `MemoryStore.open()` 时自动执行。

---

## Data Flow

### search_chained() 完整流程

```
search_chained(query, chain_config, namespace_prefix)
  │
  ├─ ① FTS5 检索: _search_fts5(query, namespace_prefix, top_n)
  │     返回 top_n 条 MemoryEntry, 记为 directs[0..top_n-1]
  │
  ├─ ② 对每个 rank i in 0..top_n-1:
  │      _extend_chain(directs[i], extensions[i], namespace_prefix)
  │         │
  │         ├─ L1 _extend_by_links(entry, remaining=extensions[i], max_per_level)
  │         ├─ L2 _extend_by_tags(entry, remaining, max_per_level)
  │         ├─ L3 _extend_by_entity(entry, remaining, max_per_level)
  │         └─ L4 _extend_by_namespace(entry, remaining, max_per_level)
  │
  ├─ ③ _dedup_by_quality(all_results) → 去重
  │
  ├─ ④ 深刻化: 对所有结果条目 UPDATE salience, access_count, last_access
  │
  ├─ ⑤ 异步触发: _migrate_short_to_long() + _trim_short_term()
  │
  └─ ⑥ _format_recall_result(chained_results) → 自然语言文本
```

### _extend_chain 内部状态机

```
_extend_chain(entry, target_n, namespace_prefix)
  results = []
  for level in [links, tags, entity, namespace]:
    candidates = _query_level(level, entry, max_per_level, namespace_prefix)
    # 过滤掉已过期/已删除条目 (FR-06b)
    candidates = [c for c in candidates if not expired(c)]
    take = min(target_n - len(results), len(candidates))
    results.extend(candidates[:take])
    if len(results) >= target_n:
      break
  return results
```

---

## Filesystem Impact

| File | New/Modify | Entities Touched |
|------|:---:|------|
| `core/types.py` | Modify | +ChainedMemory, +RecallChainConfig |
| `systems/memory.py` | Modify | MemoryStore, MemoryEntry |
| `core/brain.py` | Modify | LogicBrain._execute_recall |
| `core/loop.py` | Modify | register_sub_session_tools, _handle_recall |
| `qq/adapter.py` | Modify | BotAdapter._get_or_create_sub_session |
