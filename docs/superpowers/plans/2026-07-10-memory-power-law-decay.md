# Spec 003 §12 幂律遗忘曲线 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现幂律衰减公式 `S/(1+β×t^α)`，替换 salience 为时间感知的有效值，支持双向迁移（晋升+降级）和配置开关。

**架构：** 新增 `effective_salience()` 函数在 `_apply_salience_boost()` 中先衰减后 boost；新增 `_downgrade_long_to_short()` 和 `_unmark_deep_memory()` 实现双向迁移；新增 `created_at_epoch` REAL 列；更新 `config.yaml` 的 `systems.memory.decay` 段。

**技术栈：** Python 3.12+, aiosqlite, pytest

**设计文档：** `docs/superpowers/specs/2026-07-10-memory-chain-recall-design.md` §12

---

## 当前基线

Spec 003 的核心功能已全部落地，28 个测试通过。仅 §12 幂律遗忘待实现：

| 组件 | 状态 |
|------|:---:|
| `search_chained()` 4级联锁 | ✅ |
| `_format_recall_result()` 自然语言回溯 | ✅ |
| `_apply_salience_boost()` 深刻化 | ✅ |
| `_migrate_short_to_long()` 晋升迁移 | ✅ |
| `_mark_deep_memory()` 深刻标记 | ✅ |
| `_trim_short_term()` 裁剪 | ✅ |
| `apply_decay()` TTL 过期清理 | ✅ |
| `effective_salience()` 幂律衰减 | ❌ 本节实现 |
| `_downgrade_long_to_short()` 降级迁移 | ❌ 本节实现 |
| `_unmark_deep_memory()` deep 回退 | ❌ 本节实现 |
| `created_at_epoch` 列 | ❌ 本节实现 |
| `decay.enabled` 配置开关 | ❌ 本节实现 |

---

## 架构决策

- **幂律衰减是"软衰减"**：改变 salience 值而非设置 expires_at，与现有 `apply_decay()`（硬过期清理）互补共存
- **衰减顺序：先衰减后 boost**：`effective_salience()` → 写回 DB → `salience += chain_boost` → `MIN(10.0)`。确保"这次被回忆所以又被强化"
- **滞后带防抖**：晋升 ≥5，降级 <3（2 分滞后带）；deep 晋升 ≥7，回退 <5
- **降级保留 access_count**：迁回 short_term 不重置计数器
- **`decay.enabled=false` 回退到原始行为**：跳过 effective_salience 计算和降级迁移

---

## 任务列表

### 阶段 1：数据层（Schema + Config）

- [ ] **任务 1：`config.yaml` 新增 `systems.memory.decay` 幂律段**
- [ ] **任务 2：`memory.py` Schema 迁移 — 新增 `created_at_epoch` REAL 列**

**检查点：任务 1-2 之后**
- [ ] `python -c "from chat_core.config import get_config; print(get_config().memory_config().get('decay',{}).get('enabled'))"` → `True`
- [ ] `python -c "import sqlite3; c=sqlite3.connect('data/memory.db'); print('created_at_epoch' in [r[1] for r in c.execute('PRAGMA table_info(memories)')])"` → `True`

---

### 阶段 2：核心算法

- [ ] **任务 3：`effective_salience()` 幂律衰减函数**
- [ ] **任务 4：`_apply_salience_boost()` 集成衰减→boost 顺序**
- [ ] **任务 5：`_downgrade_long_to_short()` 降级迁移**
- [ ] **任务 6：`_unmark_deep_memory()` deep 回退**
- [ ] **任务 7：`search_chained()` 调用降级/回退迁移**

**检查点：任务 3-7 之后**
- [ ] `python -m pytest tests/test_memory.py -v` — 28 个现有测试零回归
- [ ] 新增幂律衰减测试通过

---

## 详细任务

---

### 任务 1：`config.yaml` 新增 `systems.memory.decay` 幂律段

**文件：**
- 修改：`chat_core/config.yaml:94-98`

**描述：** 将旧 `decay` 段替换为 §12.5 格式，保留旧 key 作为 `legacy_*` 向后兼容。

- [ ] **步骤 1：替换 `systems.memory.decay` 段**

```yaml
    decay:
      enabled: true               # false = 不衰减不降级 (回退到 Spec 003 原始行为)
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
      # 旧 TTL 过期清理参数 (apply_decay 仍使用)
      legacy_detail_auto_migrate_days: 60
      legacy_gist_expire_salience_5: 90
      legacy_gist_expire_salience_7: 135
      legacy_gist_expire_salience_10: 180
```

- [ ] **步骤 2：更新 `__init__` 读取新配置**

在 `MemoryStore.__init__` 中新增配置读取：

```python
cfg = get_config()
mc = cfg.brain_config("memory")  # 或 memory_config()
decay_cfg = mc.get("decay", {})
self._decay_enabled: bool = bool(decay_cfg.get("enabled", True))
self._decay_standard_beta: float = float(decay_cfg.get("standard_beta", 0.01))
self._decay_deep_beta: float = float(decay_cfg.get("deep_beta", 0.001))
self._decay_alpha: float = float(decay_cfg.get("alpha", 0.5))
mig = decay_cfg.get("migration", {})
self._migrate_up_threshold: float = float(mig.get("short_to_long_salience", 5))
self._migrate_down_threshold: float = float(mig.get("long_to_short_salience", 3))
self._deep_threshold: float = float(mig.get("deep_salience", 7))
self._deep_fallback: float = float(mig.get("deep_fallback", 5))
self._trim_short_max: int = int(decay_cfg.get("trim_short_max", 10))
```

- [ ] **步骤 3：验证 YAML 语法**

```bash
python -c "import yaml; yaml.safe_load(open('chat_core/config.yaml')); print('YAML OK')"
```

- [ ] **步骤 4：确认 `_migrate_short_to_long()` 和 `_mark_deep_memory()` 使用配置阈值**

将硬编码的 `salience >= 5`, `access_count >= 3` 等替换为 `self._migrate_up_threshold` 等：

```python
# _migrate_short_to_long: salience >= 5 AND access_count >= 3
# → salience >= self._migrate_up_threshold AND access_count >= 3
```

**预估规模：** S

---

### 任务 2：Schema 迁移 — 新增 `created_at_epoch` REAL 列

**文件：**
- 修改：`chat_core/systems/memory.py` (schema migration)

**描述：** 在 `_migrate_schema_003` 或新迁移方法中添加 `created_at_epoch REAL DEFAULT (unixepoch())` 列。对存量数据回填 `unixepoch(created_at)`。

- [ ] **步骤 1：新增迁移方法**

```python
async def _migrate_schema_012(self) -> None:
    """Spec 003 §12: 添加 created_at_epoch REAL 列用于幂律时间基准"""
    assert self._db
    cursor = await self._db.execute("PRAGMA table_info(memories)")
    columns = [row[1] for row in await cursor.fetchall()]
    if "created_at_epoch" not in columns:
        await self._db.execute(
            "ALTER TABLE memories ADD COLUMN created_at_epoch REAL DEFAULT (unixepoch())"
        )
        # 存量数据回填
        await self._db.execute(
            "UPDATE memories SET created_at_epoch = unixepoch(created_at) "
            "WHERE created_at_epoch IS NULL AND created_at IS NOT NULL"
        )
        await self._db.commit()
```

- [ ] **步骤 2：在 `open()` 中调用**

```python
await self._migrate_schema_012()
```

- [ ] **步骤 3：`save()` 中写入 `created_at_epoch`**

在 `MemoryStore.save()` 的 INSERT/UPDATE 中增加 `created_at_epoch` 字段：

```python
await self._db.execute(
    """INSERT OR REPLACE INTO memories (...) VALUES (..., ?)""",
    (..., time.time() if entry.created_at_epoch is None else entry.created_at_epoch),
)
```

- [ ] **步骤 4：`_row_to_entry()` 读取新列**

```python
created_at_epoch=float(row["created_at_epoch"]) if row["created_at_epoch"] is not None else None,
```

- [ ] **步骤 5：验证**

```bash
python -c "
import sqlite3, os
os.chdir('D:/code/chat-core')
c = sqlite3.connect('data/memory.db')
cols = [r[1] for r in c.execute('PRAGMA table_info(memories)')]
print('created_at_epoch' in cols)
"
```

**预估规模：** S

---

### 任务 3：`effective_salience()` 幂律衰减函数

**文件：**
- 修改：`chat_core/systems/memory.py`
- 修改：`tests/test_memory.py`（新增 `TestPowerLawDecay` 类）

**描述：** 实现幂律公式 `S / (1 + β × t^α)`。`t` 为距 `created_at_epoch` 的天数。

- [ ] **步骤 1：编写测试**

```python
class TestPowerLawDecay:
    """§12 幂律衰减: effective_salience + 双向迁移"""

    def test_effective_salience_standard_curve(self):
        """salience=5, 90天 → ~4.57 (standard β=0.01)"""
        now = time.time()
        created = now - 90 * 86400  # 90 天前
        result = MemoryStore.effective_salience(5.0, created, now, "standard", 0.01, 0.001, 0.5)
        assert 4.4 < result < 4.7, f"Expected ~4.57, got {result}"

    def test_effective_salience_deep_curve(self):
        """salience=5, 90天 → ~4.99 (deep β=0.001, 10× 慢)"""
        now = time.time()
        created = now - 90 * 86400
        result = MemoryStore.effective_salience(5.0, created, now, "deep", 0.01, 0.001, 0.5)
        assert result > 4.9, f"Expected ~4.99, got {result}"

    def test_effective_salience_recent_no_decay(self):
        """刚创建 (t≈0) → 几乎不衰减"""
        now = time.time()
        created = now - 60  # 1 分钟前
        result = MemoryStore.effective_salience(5.0, created, now, "standard", 0.01, 0.001, 0.5)
        assert result > 4.99, f"Expected ~5.0, got {result}"

    def test_effective_salience_config_disabled(self):
        """decay.enabled=false → 返回原始 salience"""
        result = MemoryStore.effective_salience(
            5.0, 0, time.time(), "standard", 0.01, 0.001, 0.5, enabled=False
        )
        assert result == 5.0
```

- [ ] **步骤 2：实现 `effective_salience()` 静态方法**

```python
@staticmethod
def effective_salience(
    salience: float,
    created_at_epoch: float | None,
    now_ts: float,
    decay_curve: str = "standard",
    standard_beta: float = 0.01,
    deep_beta: float = 0.001,
    alpha: float = 0.5,
    enabled: bool = True,
) -> float:
    """幂律衰减后的有效 salience。S / (1 + β × t^α)

    Args:
        salience: 原始 salience [0, 10]
        created_at_epoch: 创建时间 (unix timestamp)，None 则不衰减
        now_ts: 当前时间 (unix timestamp)
        decay_curve: "standard" | "deep" | "none"
        standard_beta: standard 曲线 β
        deep_beta: deep 曲线 β
        alpha: 曲率
        enabled: 全局开关
    """
    if not enabled or created_at_epoch is None or decay_curve == "none":
        return salience
    t_days = (now_ts - created_at_epoch) / 86400.0
    if t_days <= 0:
        return salience
    beta = deep_beta if decay_curve == "deep" else standard_beta
    return salience / (1.0 + beta * (t_days ** alpha))
```

- [ ] **步骤 3：运行测试**

```bash
python -m pytest tests/test_memory.py::TestPowerLawDecay -v
```

**预估规模：** S

---

### 任务 4：`_apply_salience_boost()` 集成衰减→boost 顺序

**文件：**
- 修改：`chat_core/systems/memory.py`

**描述：** 修改 `_apply_salience_boost()` 实现设计 §12.3 的"先衰减后 boost"顺序。

- [ ] **步骤 1：修改 `_apply_salience_boost()`**

```python
async def _apply_salience_boost(self, results: list[ChainedMemory]) -> None:
    """对所有命中记忆执行：幂律衰减 → salience boost → 硬上限。

    顺序: effective_salience() → 写回 DB → salience += chain_boost → MIN(10.0)
    """
    assert self._db
    now = time.time()
    now_iso = datetime.now().isoformat()
    for cm in results:
        e = cm.entry
        # ① 幂律衰减
        effective = self.effective_salience(
            e.salience,
            getattr(e, 'created_at_epoch', None),
            now,
            getattr(e, 'decay_curve', 'standard'),
            self._decay_standard_beta,
            self._decay_deep_beta,
            self._decay_alpha,
            enabled=self._decay_enabled,
        )
        # ② boost
        boost = self._SALIENCE_BOOST.get(cm.chain_level, 0.10)
        new_salience = min(effective + boost, 10.0)
        # ③ 写回
        await self._db.execute(
            """UPDATE memories SET
               salience = ?,
               access_count = access_count + 1,
               last_access = ?
               WHERE namespace = ? AND key = ?""",
            (new_salience, now_iso, e.namespace, e.key),
        )
    await self._db.commit()
```

- [ ] **步骤 2：`decay.enabled=false` 时跳过衰减**

当 `self._decay_enabled` 为 False 时，`effective_salience()` 返回原始值，仅执行 boost — 与 Spec 003 原始行为一致。

- [ ] **步骤 3：运行现有测试确认零回归**

```bash
python -m pytest tests/test_memory.py -v
```

**预估规模：** S

---

### 任务 5：`_downgrade_long_to_short()` 降级迁移

**文件：**
- 修改：`chat_core/systems/memory.py`
- 修改：`tests/test_memory.py`

**描述：** 长期记忆 salience < 3 → 迁移回 `short_term/*`。保留 access_count 和 last_access。

- [ ] **步骤 1：编写测试**

```python
class TestBidirectionalMigration:
    """§12.4: 双向迁移 (晋升+降级)"""

    async def test_downgrade_long_to_short(self, store: MemoryStore):
        """user/* 中 salience < 3 → 迁回 short_term/*"""
        await store.save(MemoryEntry(
            namespace="user/test/downgrade", key="low_salience",
            value={"fact": "被遗忘的事实"}, salience=2.0,
            access_count=5, last_access="2026-01-01T00:00:00",
            created_at=datetime.now(),
        ))
        await store._downgrade_long_to_short()
        # 应迁移到 short_term
        short = await store.query("short_term/user/test/downgrade")
        long = await store.query("user/test/downgrade")
        assert len(short) == 1
        assert len(long) == 0
        # access_count 保留
        assert short[0].access_count == 5

    async def test_hysteresis_no_migration(self, store: MemoryStore):
        """salience 3-5 之间的条目不迁移 (滞后带)"""
        await store.save(MemoryEntry(
            namespace="user/test/hysteresis", key="mid",
            value={"fact": "边界值"}, salience=4.0,
            created_at=datetime.now(),
        ))
        await store._downgrade_long_to_short()
        # 不应迁移
        long = await store.query("user/test/hysteresis")
        assert len(long) == 1
```

- [ ] **步骤 2：实现 `_downgrade_long_to_short()`**

```python
async def _downgrade_long_to_short(self) -> None:
    """长期记忆 → 短期记忆：salience < downgrade_threshold 的迁回 short_term/*"""
    if not self._decay_enabled:
        return
    assert self._db
    try:
        cursor = await self._db.execute(
            "SELECT * FROM memories WHERE namespace NOT LIKE ? AND salience < ?",
            ("short_term/%", self._migrate_down_threshold),
        )
        rows = await cursor.fetchall()
        for row in rows:
            entry = self._row_to_entry(row)
            old_ns = entry.namespace
            # 构造新 namespace: user/... → short_term/user/...
            new_ns = f"short_term/{old_ns}"
            # 不重置 access_count 和 last_access
            await self.save(MemoryEntry(
                namespace=new_ns, key=entry.key,
                value=entry.value, salience=entry.salience,
                access_count=entry.access_count,
                last_access=entry.last_access,
                decay_curve=entry.decay_curve,
                created_at=entry.created_at,
                created_at_epoch=entry.created_at_epoch,
            ))
            await self._db.execute(
                "DELETE FROM memories WHERE namespace = ? AND key = ?",
                (old_ns, entry.key),
            )
        if rows:
            await self._db.commit()
    except Exception:
        pass
```

- [ ] **步骤 3：运行测试**

```bash
python -m pytest tests/test_memory.py::TestBidirectionalMigration -v
```

**预估规模：** S

---

### 任务 6：`_unmark_deep_memory()` deep 回退

**文件：**
- 修改：`chat_core/systems/memory.py`
- 修改：`tests/test_memory.py`

**描述：** 深刻记忆 salience < 5 → `decay_curve` 回退为 `'standard'`。

- [ ] **步骤 1：编写测试**

```python
async def test_unmark_deep_fallback(self, store: MemoryStore):
    """deep 记忆 salience < 5 → decay_curve 变回 'standard'"""
    await store.save(MemoryEntry(
        namespace="user/test/deep_fb", key="fading_deep",
        value={"fact": "褪色的深刻记忆"}, salience=4.0,
        decay_curve="deep", created_at=datetime.now(),
    ))
    await store._unmark_deep_memory()
    entry = await store.get("user/test/deep_fb", "fading_deep")
    assert entry.decay_curve == "standard"
```

- [ ] **步骤 2：实现 `_unmark_deep_memory()`**

```python
async def _unmark_deep_memory(self) -> None:
    """深刻记忆回退：salience < deep_fallback 的标记 decay_curve='standard'"""
    if not self._decay_enabled:
        return
    assert self._db
    try:
        await self._db.execute(
            "UPDATE memories SET decay_curve = 'standard' "
            "WHERE salience < ? AND decay_curve = 'deep' "
            "AND namespace NOT LIKE 'short_term/%'",
            (self._deep_fallback,),
        )
        await self._db.commit()
    except Exception:
        pass
```

- [ ] **步骤 3：运行测试**

```bash
python -m pytest tests/test_memory.py::TestBidirectionalMigration -v
```

**预估规模：** XS

---

### 任务 7：`search_chained()` 调用降级/回退迁移

**文件：**
- 修改：`chat_core/systems/memory.py`

**描述：** 在 `search_chained()` 的步骤 ⑤ 异步触发降级和回退迁移（设计 §12.4）。

- [ ] **步骤 1：修改 `search_chained()` 末尾**

```python
# ⑤ 异步触发记忆分级迁移 (双向)
asyncio.create_task(self._migrate_short_to_long())
asyncio.create_task(self._downgrade_long_to_short())
asyncio.create_task(self._mark_deep_memory())
asyncio.create_task(self._unmark_deep_memory())
asyncio.create_task(self._trim_short_term())
```

- [ ] **步骤 2：运行全量测试**

```bash
python -m pytest tests/ -q
```

预期：168 + 新增 ≈ 175+ tests 零回归。

**预估规模：** XS

---

### 检查点：完成

- [ ] `python -m pytest tests/ -q` — 全量零回归
- [ ] `python -c "from chat_core.systems.memory import MemoryStore; print(MemoryStore.effective_salience(5.0, 0, 1e12, 'standard'))"` → < 5.0（确认衰减生效）
- [ ] `python -c "from chat_core.config import get_config; print(get_config().brain_config('memory').get('decay',{}).get('enabled'))"` → `True`
- [ ] 新增 `TestPowerLawDecay` + `TestBidirectionalMigration` 测试全部通过

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| `_apply_salience_boost()` 改为先衰减后 boost，可能改变现有行为 | 中 | `decay.enabled=false` 开关可回退到原始行为 |
| `created_at_epoch` 存量数据 NULL | 低 | 迁移时自动回填 `unixepoch(created_at)` |
| 双向迁移异步触发可能丢失条目 | 低 | 所有迁移使用 `try/except` 静默降级，下次 search 重试 |
| 阈值变更影响测试断言 | 低 | 新配置值通过 config 读取，测试使用默认值 |

## 待定问题

- `MemoryStore.__init__` 中新增的 `decay_cfg` 读取——需要确认 `brain_config("memory")` 或 `memory_config()` 方法是否存在、返回什么结构
