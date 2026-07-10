# Design: 记忆联锁 + Recall 深刻化 + 记忆分级 + 自然语言回溯 + 遗忘曲线

> **Feature**: memory-chain-recall
> **Status**: Design Approved (extended 2026-07-10 with §12 遗忘曲线)
> **Created**: 2026-07-10
> **Context**: chat-core 当前 MemoryStore 缺少 salience 衰减、短期/长期记忆分级、回忆联锁(recollection chaining)、自然语言回溯和遗忘曲线。本设计补齐全部缺口。§12 遗忘曲线在 brainstorming session 中追加，与 Spec 003 一次性实施以避免两次 schema 迁移。

---

## 1. 背景与目标

### 1.1 现状缺口

| 缺口 | 影响 | 本设计覆盖 |
|------|------|-----------|
| 主脑/子Session recall 工具无区分 | 子Session 可读 `self/inner_thoughts` 等禁区 | ✅ §3, §4 |
| recall 只返回单条匹配 | 无记忆关联，检索结果孤立 | ✅ §5 记忆联锁 |
| salience 字段存在但从未读写 | 无遗忘曲线，无深刻化 | ✅ §6 salience + access_count |
| 无短期/长期/深刻记忆分级 | 所有 namespace 等价，无晋升机制 | ✅ §7 三级记忆 |
| recall 返回 JSON 机械死板 | AI 回复缺乏记忆回溯的自然感 | ✅ §8 自然语言回溯 + 情绪注解 |
| 缺少 chat-engine 中的 decay/boost 经验 | chat-core 从头造轮子 | ✅ §9 参考 chat-engine |

### 1.2 目标

```
用户消息 → search_chained(query, config)
  │
  ├─ FTS5 主检索 (top_n 条, 按匹配度)
  │
  ├─ 每条 top 结果 → 延伸链 (N₀, N₁, ... 条关联记忆)
  │     │
  │     4 级 fallback: links → topic_tags → entity → namespace
  │
  ├─ 质量去重 (同 key 保留最高 priority 来源)
  │
  ├─ 深刻化: 每条命中记忆 salience↑, access_count↑
  │
  └─ 自然语言回溯: "我记得:... 还想起:... 说到这个:..."
       + 编码情绪注解 + 情绪推演
```

---

## 2. MemoryStore Schema 新增

```sql
-- Spec 003 + 遗忘曲线 一次性新增 4 列
ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN last_access TEXT;
ALTER TABLE memories ADD COLUMN decay_curve TEXT DEFAULT 'standard';
ALTER TABLE memories ADD COLUMN created_at_epoch REAL DEFAULT (unixepoch());
```

`salience` 字段已存在 (REAL DEFAULT 5.0)，本次使其真正生效。
`decay_curve` 取值: `'standard'` | `'deep'` | `'none'`。`created_at_epoch` 为幂律衰减的时间基准（§12）。

> **实施注意**：`created_at_epoch` 列仅在设计文档中定义，代码尚未落地。现有代码使用 `created_at` TEXT 列。实施时需通过 ALTER TABLE 新增此 REAL 列，并对存量数据回填 `unixepoch(created_at)`。

---

## 3. RecallChainConfig — 主脑 vs 子Session

```python
@dataclass
class RecallChainConfig:
    top_n: int                # FTS5 主检索条数
    extensions: list[int]     # 每条 rank 的延伸数量 [N₀, N₁, ...]
    max_per_level: int        # 每级 fallback 单次取出的上限
    namespace_prefix: str | None  # 命名空间限制
```

### 3.1 两套配置

| 参数 | 主脑 (LogicBrain) | 子Session (ReActLoop) |
|------|:---:|:---:|
| top_n | 5 | 3 |
| extensions | [3, 2, 2, 1, 0] | [2, 1, 0] |
| max_per_level | 3 | 2 |
| namespace_prefix | None (全量) | `user/{uid}` + `short_term` |

### 3.2 子Session namespace 注入

```python
# BotAdapter._get_or_create_sub_session() 中
chain_config = RecallChainConfig(
    top_n=3, extensions=[2, 1, 0],
    namespace_prefix=f"user/{user_id}",
    max_per_level=2,
)
register_sub_session_tools(registry, loop, memory_store, chain_config)
```

LLM 无感知 namespace 限制——工具描述保持简洁："从记忆中检索相关信息，会自动追溯关联记忆。"

---

## 4. 主脑/子Session Recall 工具隔离

| | 主脑 | 子Session |
|------|------|------|
| 工具集 | recall + memory_save + memory_link + inject_to_sub | recall (只读) |
| recall 描述 | "深度记忆检索。返回最佳匹配及关联记忆链。" | "从记忆中检索信息。会自动追溯关联记忆。只读。" |
| namespace | 全量 | `user/{uid}/*` + `short_term/*` |
| 写权限 | ✅ | ❌ |
| 可读 self/* | ✅ | ❌ |
| 可读 subconscious/* | ✅ (显式注入) | ❌ |

---

## 5. 记忆联锁

### 5.1 总流程

```
search_chained(query, chain_config, namespace_prefix)
  │
  ├─ ① FTS5 主检索 → top_n 条 (按匹配度降序)
  │
  ├─ ② 对每个 rank 0..top_n-1:
  │      _extend_chain(entry, extensions[rank], namespace_prefix)
  │
  ├─ ③ 全局去重: 同 key 保留最高 priority 来源
  │
  ├─ ④ 深刻化: 对所有命中记忆 salience↑, access_count↑
  │
  └─ ⑤ 按 rank 分组返回: [direct₀, ext₀₁, ext₀₂...] [direct₁, ext₁₁...] ...
```

### 5.2 4 级延伸 fallback

```
_extend_chain(entry, target_n, namespace_prefix) → list[MemoryEntry]

  Level 1 — memory_links (显式关联, priority=0)
    SELECT target FROM memory_links WHERE source = entry.key
    → 精确, 可信

  Level 2 — topic_tags 交集 (priority=1)
    WHERE 目标记忆的 topic_tags 与 entry.topic_tags 有交集
    → 语义相似

  Level 3 — entity_type 同类型 (priority=2)
    WHERE entity_type = entry.entity_type
    → 实体聚类

  Level 4 — namespace 同前缀 (priority=3)
    WHERE namespace LIKE entry.namespace 的前缀 + "%"
    → 上下文关联

  每级取 min(target_n - len(results), max_per_level) 条
  results 够了就 break
  全部受 namespace_prefix 约束过滤
```

### 5.3 质量去重

```python
seen: dict[str, (MemoryEntry, int)] = {}  # key → (entry, priority)

for each result in all_results:
    key = f"{result.namespace}/{result.key}"
    if key not in seen or result.priority < seen[key].priority:
        seen[key] = result

final = sorted(seen.values(), key=lambda x: (x.chain_level, x.relevance_score))
```

优先级: links(0) > topic_tags(1) > entity(2) > namespace(3)

### 5.4 输出结构

```python
@dataclass
class ChainedMemory:
    entry: MemoryEntry
    chain_level: int            # 0=direct, 1=link, 2=tags, 3=entity, 4=namespace
    chain_parent_key: str | None  # direct 时为 None
    relevance_score: float
    emotion_at_encoding: dict | None  # 用于自然语言回溯的情绪注解

# 返回示例 (主脑 top_n=5):
# [ChainedMemory(direct₀), ChainedMemory(ext₀₁), ChainedMemory(ext₀₂),
#  ChainedMemory(direct₁), ChainedMemory(ext₁₁), ChainedMemory(ext₁₂),
#  ChainedMemory(direct₂), ChainedMemory(ext₂₁), ChainedMemory(ext₂₂),
#  ChainedMemory(direct₃), ChainedMemory(ext₃₁),
#  ChainedMemory(direct₄)]
# 共计: 5 + 3+2+2+1+0 = 13 条
```

---

## 6. Recall 深刻化

### 6.1 命中 boost

每次 `search_chained()` 返回结果时，对所有出现在结果中的记忆条目：

```python
entry.access_count += 1
entry.last_access = now()
entry.salience = min(entry.salience + 0.5, 10.0)
```

联锁链中的延伸记忆也获得 boost，但按链深度衰减：

```
direct match (L0):     salience += 0.50
memory_links (L1):     salience += 0.30
topic_tags (L2):       salience += 0.20
entity_type (L3):      salience += 0.15
namespace (L4):        salience += 0.10
```

### 6.2 遗忘曲线

幂律衰减已纳入本次实施范围——详见 **§12 遗忘曲线**。衰减在 `search_chained()` 深刻化步骤中与 boost 合并执行（先衰减后 boost），支持 bidirectional 迁移（晋升 + 降级 + deep 回退）。配置开关 `decay.enabled=false` 可回退到 Spec 003 原始行为（只升不降）。

---

## 7. 记忆三级分级

```
短期记忆
  ├─ 条件: 尚未巩固 — NOT (salience ≥ 5 AND access_count ≥ 3)
  ├─ 存储: short_term/* namespace
  ├─ 特点: 近 10 条 (写入时裁剪), 尚未巩固, 联锁时排序靠后
  └─ 晋升条件: salience ≥ 5 且 access_count ≥ 3

长期记忆
  ├─ 条件: salience ≥ 5 且 access_count ≥ 3
  ├─ 存储: user/* namespace (自动迁移自 short_term/*)
  ├─ 特点: 已巩固, FTS5 正常检索, 正常衰减
  └─ 晋升条件: salience ≥ 7

深刻记忆
  ├─ 条件: salience ≥ 7
  ├─ 存储: 原地 (不迁移), decay_curve 标记为 'deep'
  ├─ 特点: 高权重, 联锁时优先延伸, 几乎不衰减
  └─ 不会过期 (除非手动删除)
```

### 7.1 自动迁移

```python
async def _migrate_short_to_long(memory_store):
    """每次 MemoryStore.save() 后触发检查"""
    # 短期记忆 → 长期记忆
    entries = await memory_store.query("short_term", 
        where="salience >= 5 AND access_count >= 3")
    for e in entries:
        new_ns = e.namespace.replace("short_term/", "user/")
        await memory_store.save(MemoryEntry(namespace=new_ns, ...))
        await memory_store.delete(e.namespace, e.key)

    # 长期记忆 → 深刻记忆
    entries = await memory_store.query("user",
        where="salience >= 7 AND decay_curve != 'deep'")
    for e in entries:
        await memory_store.execute(
            "UPDATE memories SET decay_curve='deep' WHERE namespace=? AND key=?",
            (e.namespace, e.key))
```

### 7.2 short_term 10 条上限

```python
async def _trim_short_term(memory_store, namespace_prefix):
    """写入 short_term/* 后裁剪：保留 salience 最高的 10 条"""
    entries = await memory_store.query(namespace_prefix)
    if len(entries) > 10:
        sorted = sorted(entries, key=lambda e: e.salience, reverse=True)
        for e in sorted[10:]:
            await memory_store.delete(e.namespace, e.key)
```

---

## 8. 自然语言回溯

### 8.1 回溯模板

```
【记忆回溯】
我记得：{主记忆内容}。（{情绪注解}）
{延伸记忆连接词}：{内容}。（{情绪注解}）
...
{情绪推演}
```

### 8.2 连接词随机轮换

```python
CONNECTORS = [
    "还想起", "哦对", "说到这个",
    "这让我想起", "顺便一提", "对了",
    "说起来", "那次也是",
]
# 每条延伸记忆随机抽一个，避免机械重复
```

### 8.3 情绪注解规则

- 有 `emotional_tags` → "（当时聊这个的时候挺开心的）" / "（他好像有点焦虑）"
- 无 `emotional_tags` → 不添加注解，保持自然
- 多条记忆显式关联 → 情绪一致性高 → 触发推演：

```python
def _derive_emotion_inference(entries: list[ChainedMemory]) -> str | None:
    """检测多条记忆的情绪一致性，生成推演"""
    emotions = [e.emotion_at_encoding for e in entries if e.emotion_at_encoding]
    if len(emotions) < 2:
        return None
    dominant = _most_common_dimension(emotions)
    if dominant and dominant["confidence"] > 0.6:
        return random.choice(DERIVE_TEMPLATES[dominant["dim"]])
    return None

DERIVE_TEMPLATES = {
    "joy": ["这些记忆让我觉得他最近状态不错。", "看来那段时间挺开心的。"],
    "anxiety": ["这些记忆让我觉得他最近压力不小。", "能感觉到他有些焦虑。"],
    "mixed": ["他的情绪似乎有些复杂。", "成就感和压力好像都有。"],
}
```

### 8.4 无结果自然处理

```
# 不说 "无结果" / "未找到记忆"
# 而是:
"目前没什么特别的记忆浮现。"
"脑子里暂时一片空白。"
"一下子想不起相关的事。"
```

### 8.5 回溯示例

```
用户: 小刚最近在做什么？

【记忆回溯】
我记得：小刚是产品经理，在北京字节跳动工作。（当时聊这个的时候他还挺自豪的）
还想起：上周他说在做一个 AI 相关的项目。（提到这事的时候感觉他有点焦虑）
说到这个，他养了一只叫豆包的猫，还给我发过照片。（特别开心）
这些记忆让我觉得他最近成就感和压力并存。
```

### 8.6 recall 工具输出格式

```python
# 原格式 (删除):
# {"results": [{"key": "...", "value": {...}}], "count": N}

# 新格式 — 自然语言文本:
def _format_recall_result(entries: list[ChainedMemory]) -> str:
    if not entries:
        return random.choice(NO_MEMORY_PHRASES)
    
    direct = entries[0]
    lines = [f"我记得：{_summarize(direct)}。{_emotion_annotate(direct)}"]
    
    for e in entries[1:]:
        connector = random.choice(CONNECTORS)
        lines.append(f"{connector}：{_summarize(e)}。{_emotion_annotate(e)}")
    
    inference = _derive_emotion_inference(entries)
    if inference:
        lines.append(inference)
    
    return "\n".join(lines)
```

---

## 9. 参考 chat-engine 成熟方案

本设计参考 `D:/code/chat-engine/memory_store.py` 的成熟实现：

| chat-engine 能力 | 本设计采用 |
|------|------|
| salience 评分 0-10 | ✅ 继承，添加深刻化 boost |
| access_count / last_access | ✅ 新增 |
| decay_curve (standard/deep/none) | ✅ 深刻记忆标记 'deep'，标准化曲线留后续 |
| 高频访问 boost (7天≥3次→延长15天) | ❌ 留后续衰减 Phase |
| apply_decay() 幂律衰减 | ✅ §12 — 幂律公式 `S/(1+β×t^α)`，双曲线 + 双向迁移 |
| auto_migrate detail→gist | ❌ 留后续 Phase |
| 情绪调制检索 (mood-congruent) | ✅ 自然语言回溯中体现 |

---

## 10. 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `core/types.py` | +`ChainedMemory`, +`RecallChainConfig` | 新增数据类型 |
| `systems/memory.py` | +4列 schema, +`search_chained()`, +`_extend_chain()` 4级 fallback, +`_dedup_by_quality()`, +`_migrate_short_to_long()`, +`_downgrade_long_to_short()`, +`_mark_deep_memory()`, +`_unmark_deep_memory()`, +`_trim_short_term()`, +`_format_recall_result()`, +`effective_salience()`, +salience boost + 衰减嵌入 | 核心逻辑 |
| `config.yaml` | + `systems.memory.decay` 段（衰减公式、迁移阈值、裁剪上限） | 配置外化 |
| `core/brain.py` | `_execute_recall` → 改用 `search_chained` (主脑配置) | 主脑 recall 升级 |
| `core/loop.py` | `register_sub_session_tools` 改为接收 `chain_config` 参数; recall handler 用 `search_chained`; 返回自然语言文本 | 子Session recall 升级 |
| `qq/adapter.py` | `_get_or_create_sub_session` 传入 `user_id` + `scene`, 构造 `chain_config` | QQ Bot 适配 |
| `tests/test_memory.py` | +联锁链测试, +深刻化测试, +分级迁移测试, +自然语言回溯测试, +衰减测试, +双向迁移测试 | 测试覆盖 |
| `tests/test_loop.py` | +子Session recall 隔离测试 | 工具权限测试 |
| `tests/test_brain.py` | +主脑联锁 recall 测试 | 主脑测试 |
| `tests/test_qq_adapter.py` | +子Session recall namespace 限制测试 | QQ Bot 集成 |

---

## 11. 成功标准

| 指标 | 目标 |
|------|------|
| 主脑 recall 返回联锁链 (5 + 3+2+2+1+0 = 13 条) | `search_chained` 调用可验证 |
| 子Session recall 仅返回 `user/{uid}/*` + `short_term/*` | namespace_prefix 过滤可验证 |
| recall 命中 → salience +0.5, access_count +1 | 数据库可验证 |
| short_term 条目 salience≥5 且 access≥3 → 自动迁移 | `_migrate_short_to_long` 测试 |
| 自然语言回溯含情绪注解和随机连接词 | 输出文本可验证 |
| 无记忆时不机械输出 JSON | 返回自然语言 "暂时空白" 短语 |
| 所有现有 154 tests 通过 | 无回归 |
| 新增测试 ≥ 12 条 (Spec 003) | pytest count 验证 |
| 幂律衰减：salience=5, 90天 → ~4.57 | search_chained 后 salience 可验证 |
| deep 曲线减速 10× (β=0.001 vs 0.01) | 同条件 deep 衰减仅 ~1% |
| 降级迁移：salience<3 迁回 short_term | `_downgrade_long_to_short` 测试 |
| deep 回退：salience<5 → decay_curve='standard' | `_unmark_deep_memory` 测试 |
| 滞后带防抖：salience∈[3,5) 不迁移 | 边界值测试 |
| 配置开关：decay.enabled=false → 不衰减 | 功能开关测试 |

---

## 12. 遗忘曲线 (幂律衰减)

> **Design Extension**: 在 Spec 003 记忆联锁基础上补齐遗忘能力。
> **触发**: brainstorming session 2026-07-10，一次性覆盖联锁+遗忘，避免两次 schema 迁移。

### 12.1 设计目标

- 模拟艾宾浩斯遗忘：刚记住时忘得快，随时间推移遗忘速率变慢
- 深刻记忆（decay_curve='deep'）几乎不衰减
- salience 双向变化：recall 命中 → boost ↑，时间流逝 → decay ↓
- 分级可降：长期记忆可降回短期，深刻可回退为常规

### 12.2 幂律公式

```python
def effective_salience(entry: MemoryEntry, now_ts: float) -> float:
    """计算时间衰减后的有效 salience。幂律：S / (1 + β × t^α)"""
    t_days = (now_ts - entry.created_at_epoch) / 86400.0
    if entry.decay_curve == 'deep':
        beta = 0.001   # 10× 慢于 standard
    else:
        beta = 0.01
    alpha = 0.5        # 曲率：α 越小初期越陡
    return entry.salience / (1.0 + beta * (t_days ** alpha))
```

| curve | β | 30天后保留 | 90天后保留 | 365天后保留 |
|-------|------|-----------|-----------|------------|
| standard | 0.01 | 94.8% | 91.3% | 83.7% |
| deep | 0.001 | 99.5% | 99.1% | 97.0% |

**设计说明**：衰减系数温和——短期对话（分钟/小时级）几乎不可见衰减。主要靠"长时间不被 recall 访问"来拉低 salience（没有 boost 对抗自然衰减）。若一条记忆 90 天从未被 recall，salience=5 → 4.57，再 90 天 → 4.17，稳步滑落。

### 12.3 search_chained() 衰减执行点

在 Spec 003 §5.1 "④ 深刻化"步骤中，衰减先于 boost 执行：

```
④ 深刻化 + 衰减 (合并):
   for each entry in results:
     ① effective = effective_salience(entry, now)  ← 时间衰减
     ② entry.salience = effective                   ← 衰减写回
     ③ entry.salience += chain_boost(level)         ← 深刻化 boost
     ④ MIN(entry.salience, 10.0)                    ← 硬上限
     ⑤ entry.access_count += 1
     ⑥ entry.last_access = now()
```

**顺序关键**：衰减→boost，确保"这次被回忆所以又被强化"。若先 boost 再衰减，同一次检索内衰减会吃掉 boost，丧失强化效果。

**decay.enabled=false 时**：跳过步骤 ①②，直接从步骤 ③ 开始（与 Spec 003 原始行为一致）。

### 12.4 双向迁移

```
save() / search_chained() 后异步触发:
  │
  ├─ _migrate_short_to_long()    晋升: short_term/* WHERE salience≥5 AND access_count≥3 → user/*
  ├─ _downgrade_long_to_short()  降级: user/* WHERE salience<3 → short_term/*       ← 新增
  ├─ _mark_deep_memory()         深刻: user/* WHERE salience≥7 AND decay_curve!='deep' → UPDATE
  ├─ _unmark_deep_memory()       回退: user/* WHERE salience<5 AND decay_curve='deep' → UPDATE ← 新增
  └─ _trim_short_term()          裁剪: short_term/* → 保留 top 10
```

**阈值设计**：晋升 ≥5，降级 <3 → 2 分滞后带防止边界抖动。deep 晋升 ≥7，回退 <5 → 同样滞后。

**降级保留 access_count**：迁回 short_term 时不重置 access_count 和 last_access。若再次被 recall 命中，从已有计数继续累积——"忘了但一提醒就想起来"。

### 12.5 配置外化

```yaml
# config.yaml → systems.memory.decay (新增段)
systems:
  memory:
    decay:
      enabled: true               # false = 不衰减不降级 (Spec 003 原始行为)
      formula: "power_law"        # power_law | none
      standard_beta: 0.01         # standard 曲线衰减系数
      deep_beta: 0.001            # deep 曲线衰减系数 (10× 慢)
      alpha: 0.5                  # 幂律曲率 (0<α≤1)
      migration:
        short_to_long_salience: 5
        long_to_short_salience: 3
        deep_salience: 7
        deep_fallback: 5
      trim_short_max: 10
```

### 12.6 Schema 新增

```sql
-- 在 Spec 003 的 3 列基础上，新增 1 列用于幂律时间基准
ALTER TABLE memories ADD COLUMN created_at_epoch REAL DEFAULT (unixepoch());
```

`created_at_epoch`：幂律公式的 t 基准——"遗忘速度与距编码时间相关"比"距上次访问"更符合艾宾浩斯。已有 `created_at` TEXT 列保持不变（用于显示），新增 REAL 列用于高效计算。

### 12.7 新增成功标准

| ID | 标准 | 验证方式 |
|----|------|---------|
| SC-09 | 幂律衰减生效 | salience=5, created_at 设为 90天前 → search_chained 后 ≈4.57 |
| SC-10 | deep 曲线 10× 减速 | deep 记忆同条件衰减仅 ~1% |
| SC-11 | 降级迁移触发 | salience<3 的 user/* → 迁回 short_term/* |
| SC-12 | deep 可回退 | salience<5 的 deep → decay_curve 变回 'standard' |
| SC-13 | 滞后带防抖 | salience∈[3,5) 不被迁移 |
| SC-14 | 配置开关 | decay.enabled=false → 不衰减不降级 |
| SC-15 | 零回归 | 所有现有 154 tests 通过 |

### 12.8 扩展改动文件

| 文件 | 原 Spec 003 改动 | 本次扩展 |
|------|-----------------|---------|
| `systems/memory.py` | schema 3列 + search_chained + 延伸 + 去重 + 格式化 + 晋升迁移 + 裁剪 | + effective_salience() + _downgrade_long_to_short() + _unmark_deep_memory() + 衰减计算嵌入 search_chained |
| `core/types.py` | + ChainedMemory, + RecallChainConfig | 无额外改动 |
| `config.yaml` | 无 | + systems.memory.decay 段 |

### 12.9 与 Spec 003 的整合

不新建独立 spec，直接扩展 Spec 003：

| 维度 | 扩展内容 |
|------|---------|
| spec.md | 新增 FR-23~26（衰减公式、降级迁移、deep回退、配置开关） |
| plan.md | Phase 2 新增 WP-2.5（遗忘曲线），或合并 WP 到 Phase 2 |
| tasks.md | 新增 ~8 tasks（衰减公式 2 + 降级迁移 2 + deep回退 1 + 配置 1 + 测试 2） |
| data-model.md | MemoryEntry 新增 created_at_epoch 字段描述 |
| 设计文档 (本文件) | 新增 §12 遗忘曲线（本文） |
