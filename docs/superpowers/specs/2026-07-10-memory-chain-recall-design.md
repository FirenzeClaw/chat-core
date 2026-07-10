# Design: 记忆联锁 + Recall 深刻化 + 记忆分级 + 自然语言回溯

> **Feature**: memory-chain-recall
> **Status**: Design Approved
> **Created**: 2026-07-10
> **Context**: chat-core 当前 MemoryStore 缺少 salience 衰减、短期/长期记忆分级、回忆联锁(recollection chaining) 和自然语言回溯。本设计补齐全部缺口。

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
-- 仅本次新增，衰减系统留后续 Phase
ALTER TABLE memories ADD COLUMN access_count INTEGER DEFAULT 0;
ALTER TABLE memories ADD COLUMN last_access TEXT;
```

`salience` 字段已存在 (DEFAULT 5.0)，本次使其真正生效。

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

### 6.2 遗忘（遗留后续 Phase）

本次不做衰减。衰减系统 (艾宾浩斯曲线、decay_curve、apply_decay) 参考 chat-engine 实现，单独一个 Phase。

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
| apply_decay() 艾宾浩斯系统 | ❌ 留后续衰减 Phase |
| auto_migrate detail→gist | ❌ 留后续衰减 Phase |
| 情绪调制检索 (mood-congruent) | ✅ 自然语言回溯中体现 |

---

## 10. 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `core/types.py` | +`ChainedMemory`, +`RecallChainConfig` | 新增数据类型 |
| `systems/memory.py` | +`access_count`/`last_access` schema, +`search_chained()`, +`_extend_chain()`, +`_dedup_by_quality()`, +`_migrate_short_to_long()`, +`_trim_short_term()`, +`_format_recall_result()`, +4级 fallback 查询方法, +salience boost | 核心逻辑 |
| `core/brain.py` | `_execute_recall` → 改用 `search_chained` (主脑配置) | 主脑 recall 升级 |
| `core/loop.py` | `register_sub_session_tools` 改为接收 `chain_config` 参数; recall handler 用 `search_chained`; 返回自然语言文本 | 子Session recall 升级 |
| `qq/adapter.py` | `_get_or_create_sub_session` 传入 `user_id` + `scene`, 构造 `chain_config` | QQ Bot 适配 |
| `tests/test_memory.py` | +联锁链测试, +深刻化测试, +分级迁移测试, +自然语言回溯测试 | 测试覆盖 |
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
| 所有现有 108 tests 通过 | 无回归 |
| 新增测试 ≥ 12 条 | pytest count 验证 |
