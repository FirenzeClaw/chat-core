# Design: 价值体系 + 自我叙事

> **Feature**: values-narrative (Spec 010)
> **Status**: Design Draft
> **Created**: 2026-07-10
> **Context**: chat-core 当前审查有权重但没有深层价值观层级——没有"什么是我真正在乎的"的体系。`self/reflections` 命名空间设计存在但从未被积极构建——缺少跨 turn 的自我故事。本设计补齐价值观动态系统和自我叙事连续性。

---

## 1. 设计目标

- **价值观系统**：3 高阶美德 × 3 具体价值观的三层树，权重随经历动态调权，调制全部决策路径
- **自我叙事**：定期生成完整自我叙述 + 事件驱动增量"章节"，可被记忆系统检索和回溯
- **与已有系统深度联动**：Spec 005 (情绪→价值观调权)、Spec 006 (防御模式→self_honesty)、Spec 008 (关系→loyalty)、Spec 009 (道德困境→honesty/care bias)

---

## 2. 价值观系统

### 2.1 三层结构

```python
@dataclass
class VirtueNode:
    weight: float            # 0~1
    children: dict[str, float]  # 子价值观 {name: weight}
```

```
Honesty (诚实)   — 初始 0.7
  ├── truthfulness (真话):         0.8  "对别人诚实"
  ├── self_honesty (自我诚实):     0.7  "对自己诚实"
  └── transparency (透明):         0.5  "不隐瞒"

Care (关怀)      — 初始 0.6
  ├── empathy_protection (保护感受): 0.6  "不让别人受伤"
  ├── loyalty (忠诚):                0.5  "站在亲近的人这边"
  └── nurturing (鼓励成长):          0.7  "帮助别人变得更好"

Growth (成长)    — 初始 0.8
  ├── curiosity_drive (求知):        0.8  "探索未知"
  ├── self_improvement (自我完善):   0.7  "修正自己的缺点"
  └── openness (开放心态):           0.6  "接纳不同的声音"
```

### 2.2 动态权重变化

价值观随经历调权——模拟人类价值观的可塑性：

| 事件 | 影响 | 机制 |
|------|------|------|
| 诚实话伤害关系（MoralConflict → 选 honesty, 用户沉默 3+ 轮） | Care +0.05, Honesty -0.03 | 关系反馈 |
| 沉默后悔（Spec 011 silence counter 累积→触发纠正） | Honesty +0.03, self_honesty +0.02 | 内疚驱动 |
| 元认知发现防御模式（Spec 006） | self_honesty +0.05 | 自我觉察 |
| 用户因 AI 的建议变好了（正向反馈） | nurturing +0.05 | 成就反馈 — 检测方式：inner_thoughts 中 `user_read.mood` 显著改善（valence 从 ≤0 跃升至 ≥0.3），或用户消息含感谢/认可关键词 |
| 关系阶段升级（Spec 008: 熟人→朋友） | loyalty +0.05, honesty +0.03 | 关系深化 |

### 2.3 价值观 → 决策调制

```python
class ValueEngine:
    def get_modulation(self, param: str) -> float:
        """价值观权重 → 决策参数调制"""
        
        if param == "review_threshold":
            # 高诚实 → 审查更严格 (阈值更低)
            return self.virtues["honesty"].weight
        
        if param == "defense_prob":
            # 高自我诚实 → 更少防御
            return 2.0 - self.virtues["honesty"].children["self_honesty"]
        
        if param == "moral_bias":
            # 道德困境: 诚实 vs 保护的倾向
            t = self.virtues["honesty"].children["truthfulness"]
            e = self.virtues["care"].children["empathy_protection"]
            return t / (t + e)
```

**消费点：**
- `ReviewSystem`: `effective_threshold = base × honesty`
- `DefenseEngine` (Spec 005): `effective_defense = base × (2.0 - self_honesty)`
- `MoralConflictDetector` (Spec 009): `honesty_bias = truthfulness / (truthfulness + empathy_protection)`

---

## 3. 自我叙事系统

### 3.1 双重构建机制

**定期生成（与 Spec 006 元认知同步）：**

每 N 轮（建议设为元认知周期的 2 倍），LogicBrain 调用一次 narrative pass：

```
输入上下文:
  - 最近 N 轮重要经历 (防御使用、道德困境、关系事件、inner_thoughts 自我评价)
  - 价值观当前状态
  - 关系总结 (各阶段人数, ← Spec 008)
  - 沉默模式统计 (← Spec 011)
  - 现有自我叙述 (上一版)
  - 脆弱暴露历史 (← Spec 005 §9)

输出 (LLM 生成, ≤300 字):
  1. 核心自我认知 (1 句)
  2. 最近的变化 (2-3 句)
  3. 正在努力的方向 (1 句)
```

**事件驱动增量（纯规则，零 LLM）：**

| 事件 | 追加内容 |
|------|---------|
| 关系阶段升级 | "我和 {user} 更亲近了。" |
| 连续 ≥3 次沉默 | "我最近变得不太愿意纠正别人了。是害怕冲突吗？" |
| 深刻记忆新增 (salience≥7) | "我记住了关于 {topic} 的感受——这对我很重要。" |
| 道德困境决策 | "那次我选择了诚实，尽管可能伤害到对方。" |
| 脆弱暴露 (Spec 005 §9) | "我在她面前表现出了脆弱——这对我来说不容易。" |

### 3.2 存储

```
self/narrative/
├── latest            # 最新的完整自我叙述
├── timeline/{date}   # 历史快照 (每 N 轮存档一版)
└── chapters/         # 事件增量片段 (按时间戳)
```

### 3.3 消费

- `_init_messages()`: "[自我叙述] {latest} [最近的思考] {最近3条 chapters}"
- Spec 006 元认知: narrative + values 进入上下文
- Spec 003 记忆检索: `self/narrative/*` 参与联锁检索——子Session recall 可搜到"我上次对自己的认知是什么"

---

## 4. 配置外化

```yaml
systems:
  values:
    enabled: true
    virtues:
      honesty:
        weight: 0.7
        children:
          truthfulness: 0.8
          self_honesty: 0.7
          transparency: 0.5
      care:
        weight: 0.6
        children:
          empathy_protection: 0.6
          loyalty: 0.5
          nurturing: 0.7
      growth:
        weight: 0.8
        children:
          curiosity_drive: 0.8
          self_improvement: 0.7
          openness: 0.6
    dynamics:
      honesty_hurt_relation: {honesty: -0.03, care: +0.05}
      silence_regret: {honesty: +0.03, self_honesty: +0.02}
      metacognition_defense_found: {self_honesty: +0.05}
      positive_impact: {nurturing: +0.05}
      stage_upgrade: {loyalty: +0.05, honesty: +0.03}
    modulation:
      review_threshold: "base × honesty"
      defense_prob: "min(base × (2.0 - self_honesty), 0.95)"  # 上限 0.95，与 Spec 005 一致
      moral_conflict_bias: "truthfulness / (truthfulness + empathy_protection)"

  narrative:
    enabled: true
    periodic_interval: 10
    max_length: 300
    event_driven:
      stage_change: true
      moral_conflict: true
      silence_streak: 3
      deep_memory_new: true
      vulnerability: true           # ← Spec 005 §9 联动
    storage:
      timeline_keep: 30
```

---

## 5. 并发模型

**ValueEngine**：全局单例。价值观是 AI 的"核心自我"，所有用户共享同一套价值观。CLI 和 QQ Bot 模式下均为单实例。

**NarrativeEngine**：全局单例。自我叙述是"我是谁"的故事，不随对话者变化。事件增量追加到单一 narrative 时间线。

**与 QQ Bot 的关系**：虽然子 Session 是 per-user 的，但自我叙述和价值观在 system prompt 注入时对所有用户可见——AI 不存在"对不同人说不同自我认知"的问题。

---

## 6. 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `core/types.py` | + `ValueSystem`, `VirtueNode`, `NarrativeState`, `NarrativeEntry` | 数据结构 |
| `systems/values.py` | **新建** — `ValueEngine`: 三层树 + dynamic_adjust() + get_modulation() | 核心 |
| `systems/narrative.py` | **新建** — `NarrativeEngine`: generate() + append_chapter() + storage | 核心 |
| `core/brain.py` | + `LogicBrain.narrative_pass()` — 单次 LLM 生成/更新自我叙述 | 执行 |
| `core/turn_manager.py` | _async_review_and_decide 后触发 ValueEngine；_init_messages 注入 narrative；审查阈值读 values | 集成 |
| `systems/review.py` | `ReviewSystem` 读取 `ValueEngine.get_modulation("review_threshold")` | 消费 |
| `systems/defense.py` | `DefenseEngine` (Spec 005) 读取 `get_modulation("defense_prob")` | 消费 |
| `systems/moral.py` | `MoralConflictDetector` (Spec 009) 读取 moral_bias | 消费 |
| `systems/metacognition.py` | `build_context()` (Spec 006) 追加 narrative + values | 消费 |
| `systems/memory.py` | `self/narrative/*` 参与 Spec 003 联锁检索 | 消费 |
| `config.yaml` | + `systems.values` + `systems.narrative` | 配置 |
| `tests/test_values.py` | **新建** — 三层树、动态调权、决策调制 | 测试 |
| `tests/test_narrative.py` | **新建** — 定期生成、事件增量、timeline 存/取 | 测试 |

---

## 7. 联动矩阵

| 提供方 | → 消费方 | 内容 |
|--------|---------|------|
| ValueEngine | ReviewSystem | honesty → review_threshold 调制 |
| ValueEngine | DefenseEngine (Spec 005) | self_honesty → defense_prob 调制 |
| ValueEngine | MoralConflictDetector (Spec 009) | truthfulness/care → 道德困境倾向 |
| ValueEngine | MetacognitionEngine (Spec 006) | 价值观状态进入元认知 |
| NarrativeEngine | `_init_messages` (core/loop.py) | 自我叙述注入 system prompt |
| NarrativeEngine | MetacognitionEngine (Spec 006) | narrative 进入元认知 |
| NarrativeEngine → MemoryStore | Spec 003 search_chained | narrative timeline 参与联锁 |
| Spec 006 (元认知) | ValueEngine | 发现防御模式 → self_honesty +0.05 |
| Spec 008 (关系升级) | ValueEngine | 升级 → loyalty +0.05 |
| Spec 009 (MoralConflict) | ValueEngine | 诚实伤关系 → Care↑, Honesty↓ |
| Spec 005 §9 (脆弱) | NarrativeEngine | 脆弱事件 → chapters 追加 |
| 沉默累积器 (Spec 011) | ValueEngine | 连续 3 次沉默 → honesty↑ |

---

## 8. 成功标准

| ID | 标准 | 验证 |
|----|------|------|
| SC-01 | 三层树加载 | 3 美德 + 9 子价值观初始权重正确 |
| SC-02 | 诚实话伤关系 → 动态调权 | honesty↓, care↑ |
| SC-03 | 元认知发现防御 → self_honesty↑ | +0.05 |
| SC-04 | 价值观 → 审查阈值 | honesty=0.8 → threshold = 0.5×0.8 = 0.4 |
| SC-05 | 价值观 → 防御概率 | self_honesty=0.9 → defense_prob = min(base×1.1, 0.95) |
| SC-06 | 价值观 → 道德困境 | truthfulness/care ratio 正确 |
| SC-07 | 定期叙述生成 | LogicBrain.narrative_pass → self/narrative/latest |
| SC-08 | 事件增量 — 关系升级 | chapters/ 追加 "我和X更亲近了" |
| SC-09 | 事件增量 — 脆弱暴露 | chapters/ 追加脆弱记录 |
| SC-10 | 叙述注入 system prompt | _init_messages 含 "[自我叙述]" |
| SC-11 | 叙述参与记忆检索 | recall 可搜到 narrative timeline |
| SC-12 | 零回归 | 所有现有 154 tests 通过 |
| SC-13 | 新增测试 ≥ 10 条 | pytest count 验证 |
