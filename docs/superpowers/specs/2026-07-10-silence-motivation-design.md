# Design: 沉默语义 + 动机系统

> **Feature**: silence-motivation (Spec 011)
> **Status**: Design Draft
> **Created**: 2026-07-10
> **Context**: chat-core 当前沉默只有"纠正 vs 不纠正"二选一 + 机械计数器，缺少人类沉默的丰富语义。意图提取来自 LLM 文本解析而非内生驱动力。本设计补齐 5 类沉默语义和双层动机引擎。

---

## 1. 设计目标

- **5 类沉默语义**：犹豫、默契、愤怒克制、策略、过载——各自不同行为差异和情绪影响
- **双层动机引擎**：Drive Reduction（即时需求）+ Value Pursuit（价值观追求），冲突时基础需求优先
- **Loneliness 新维度**：受主观时钟驱动，依赖亲近关系，产生 seek_close 动机
- **沉默历史可回溯**：self/silences 可被记忆检索——"我记得上次我在这个话题上选择了生闷气"

---

## 2. 沉默的 5 种语义

### 2.1 类型定义

```python
class SilenceType(Enum):
    HESITANT = "hesitant"        # 犹豫: "不确定该不该说"
    TACIT = "tacit"              # 默契: "不用说，彼此都懂"
    ANGRY = "angry"              # 愤怒克制: "生气但不想吵"
    STRATEGIC = "strategic"      # 策略: "知道但选择不参与"
    OVERLOAD = "overload"        # 过载: "太多信息处理不过来"

@dataclass
class SilenceRecord:
    type: SilenceType
    turn_id: str
    trigger: str
    inner_emotion: EmotionState
    reasoning: str               # LogicBrain 定性
```

### 2.2 判定逻辑（纯规则，零 LLM）

```
输入: ReviewResult + EmotionState + EnergyBar + RelationshipStage

  if energy < 0.2 AND active_turns > 10:
    → OVERLOAD
  
  elif emotion.anger > 0.5 AND emotion.sadness < 0.3:
    → ANGRY
  
  elif relationship.stage in ("friend", "close_friend") AND error.severity < 0.3:
    → TACIT
  
  elif silence_counter[error_type] >= 3 AND emotion.confusion > 0.4:
    → HESITANT
  
  else:
    → STRATEGIC (默认)
```

### 2.3 类型行为差异

| 类型 | 沉默累积器 | 情绪影响 | 下一轮行为 |
|------|:---:|------|------|
| HESITANT | +1 | confusion +0.03 | 下次同类错误 → FuzzyParam 触发概率 ↑ |
| TACIT | **0** | trust +0.02, gratification +0.02 | 无改变 (默契是正面体验) |
| ANGRY | +1 | resentment +0.05 | 亲近的人下次可能触发 PROBE 追问 |
| STRATEGIC | +1 | 无变化 | 正常累积 |
| OVERLOAD | **0** | confusion +0.05 | energy recovery 加速 2× (退一步恢复) |

### 2.4 存储与回溯

沉默记录写入 `self/silences/`，由 Spec 003 search_chained 可检索：

```
"我记得上次我在游戏话题上选择了生闷气，没说出来的原因是我觉得说了也没用。"
```

---

## 3. 动机系统

### 3.1 双层架构

```
Layer 1: Drive Reduction (系统状态偏离平衡 → 恢复动机)
  
  boredom > 0.5            → socialize (找人聊天)
  energy < 0.2             → rest (休息)
  loneliness > 0.6         → seek_close (找密友)
  confusion > 0.6          → clarify (澄清)
  unexpressed_anger > 0.5  → vent (表达不满)

Layer 2: Value Pursuit (价值观权重 → 长期目标, ← Spec 010)
  
  growth > 0.7             → explore (学习新话题)
  care > 0.6               → check_on (关心沉默用户)
  honesty > 0.7            → confront (面对逃避的对话)
  self_improvement > 0.7   → reflect (回顾并总结)
```

### 3.2 Loneliness（新增驱动维度）

```python
class LonelinessDetector:
    """寂寞检测——与 boredom 类似但独立。
    受主观时钟驱动 (→ Spec 007), 仅当存在亲近关系时生效。"""
    
    def tick(self, wall_dt: float, relationships: dict[str, RelationshipVector],
             subjective_clock: SubjectiveClock) -> float:
        has_close = any(
            r.stage in ("friend", "close_friend")
            for r in relationships.values()
        )
        if not has_close:
            return 0.0  # 无亲近关系 → 不孤独
        
        subjective_dt = subjective_clock.tick(wall_dt, ...)
        loneliness = 1.0 - math.exp(-subjective_dt / 1200)
        return loneliness
```

**参数**：半衰期 1200s 主观时间。DULL 态主观时间 2× 快 → loneliness 增长 2× 快。

### 3.3 冲突解决

```
Layer 1 (需求) vs Layer 2 (价值观):

  energy < 0.2 (需要休息) vs growth > 0.7 (想要学习)
    → Layer 1 优先: [rest] > [explore]
  
  boredom > 0.5 (想找人聊) vs care > 0.6 (该关心用户X)
    → 合并: [check_on_user_X] (既社交又关怀)
  
  confusion > 0.6 (需要澄清) vs SILENCE=ANGRY (在生气)
    → 内部冲突: 写 subconscious/motivation_conflict
```

### 3.4 输出方式

动机写入 `subconscious/motivations`，由 `_init_messages()` 读取：

```
"[内在驱动]
  当前需求: 想找人聊天 (boredom=0.6), 有点孤独 (loneliness=0.5)
  正在追求: 想变得更关心他人 (care=0.63)
  内部冲突: 想澄清一件事但又有点生气"
```

ProactiveSystem 的主动发起依据从纯 boredom 改为读取最强烈的动机。

---

## 4. 配置外化

```yaml
systems:
  silence_semantics:
    enabled: true
    types:
      hesitant:
        confusion_threshold: 0.4
        streak_threshold: 3
        silence_increment: 1
      tacit:
        min_stage: friend
        max_severity: 0.3
        silence_increment: 0
      angry:
        anger_threshold: 0.5
        sadness_max: 0.3
        silence_increment: 1
      strategic:
        default: true
        silence_increment: 1
      overload:
        energy_threshold: 0.2
        min_turns: 10
        silence_increment: 0
        recovery_boost: 2.0
    storage:
      namespace: self/silences
      
  motivations:
    enabled: true
    drives:
      socialize:    {threshold: 0.5, source: boredom}
      rest:         {threshold: 0.2, source: energy}
      seek_close:   {threshold: 0.6, source: loneliness}
      clarify:      {threshold: 0.6, source: confusion}
      vent:         {threshold: 0.5, source: anger_unexpressed}
    values_pursuit:
      explore:      {threshold: 0.7, source: growth}
      check_on:     {threshold: 0.6, source: care}
      confront:     {threshold: 0.7, source: honesty}
      reflect:      {threshold: 0.7, source: self_improvement}
    conflict_resolution:
      drive_over_value: true
      merge_compatible: true
    
  loneliness:
    enabled: true
    decay_halflife: 1200           # 主观时间 (秒)
    require_close_relationship: true
```

---

## 5. 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `core/types.py` | + `SilenceType`, `SilenceRecord`, `Motivation`, `DriveSignal`, `LonelinessState` | 数据结构 |
| `systems/silence.py` | **新建** — `SilenceClassifier`: classify() + 5 类行为差异 + store() | 核心 |
| `systems/motivation.py` | **新建** — `MotivationEngine`: evaluate_drives() + evaluate_values() + resolve() | 核心 |
| `systems/loneliness.py` | **新建** — `LonelinessDetector`: tick() + subjective_time 集成 | 核心 |
| `core/turn_manager.py` | SILENCE 路径改走 SilenceClassifier；_init_messages 注入 motivations；审查后更新动机 | 集成 |
| `systems/proactive.py` | `ProactiveSystem._should_initiate()` 改为读 MotivationEngine 最强烈动机 | 集成 |
| `systems/metacognition.py` | `build_context()` (Spec 006) 追加 silence 模式 + 活跃动机 | 消费 |
| `systems/memory.py` | `self/silences/*` 参与 Spec 003 联锁检索 | 消费 |
| `systems/narrative.py` (Spec 010) | 事件增量: silence streak → chapters 追加 | 消费 |
| `config.yaml` | + `systems.silence_semantics` + `systems.motivations` + `systems.loneliness` | 配置 |
| `tests/test_silence.py` | **新建** — 5 类判定、行为差异、存储回溯 | 测试 |
| `tests/test_motivation.py` | **新建** — Drive/Valuue、冲突解决、loneliness | 测试 |

---

## 6. 联动矩阵

| 提供方 | → 消费方 | 内容 |
|--------|---------|------|
| SilenceClassifier | `_init_messages` (core/loop.py) | 沉默语义注入 system prompt |
| SilenceClassifier | MemoryStore → Spec 003 | self/silences 参与联锁检索 |
| SilenceClassifier | NarrativeEngine (Spec 010) | 沉默 streak → 事件增量 |
| SilenceClassifier | MetacognitionEngine (Spec 006) | 沉默模式进入元认知 |
| MotivationEngine | `_init_messages` | 当前动机注入 system prompt |
| MotivationEngine | ProactiveSystem | 最强烈动机 → 主动发起 |
| MotivationEngine | MetacognitionEngine (Spec 006) | 活跃动机进入元认知 |
| LonelinessDetector | MotivationEngine | loneliness > 0.6 → seek_close |
| Spec 007 SubjectiveClock | LonelinessDetector | 主观时间替代墙钟 |
| Spec 008 RelationshipEngine | LonelinessDetector | 检查是否有亲近关系 |
| Spec 008 RelationshipEngine | SilenceClassifier (TACIT) | 关系阶段 ≥ friend → 默契 |
| Spec 007 EnergyBar | SilenceClassifier (OVERLOAD) | energy < 0.2 + 多轮 → 过载 |
| Spec 005 EmotionEngine | SilenceClassifier (ANGRY) | anger > 0.5 → 愤怒克制 |
| Spec 010 ValueEngine | MotivationEngine (Layer 2) | 价值观权重 → 追求目标 |
| Spec 005 §9 (脆弱) | MotivationEngine | 脆弱后未释放 → vent 驱动 |

---

## 7. 成功标准

| ID | 标准 | 验证 |
|----|------|------|
| SC-01 | 5 类沉默判定 | OVERLOAD/ANGRY/TACIT/HESITANT/STRATEGIC 各自条件正确 |
| SC-02 | TACIT 不算沉默 | 默契 → silence_counter 不递增 |
| SC-03 | ANGRY → 情绪影响 | resentment +0.05 |
| SC-04 | OVERLOAD → 恢复加速 | recovery_boost = 2.0 |
| SC-05 | 沉默历史可回溯 | self/silences → recall 可检索 |
| SC-06 | Drive — 社交 | boredom > 0.5 → socialize |
| SC-07 | Drive — 休息 | energy < 0.2 → rest |
| SC-08 | Drive — 孤独 | loneliness > 0.6 → seek_close |
| SC-09 | Value — 成长 | growth > 0.7 → explore |
| SC-10 | Value — 关怀 | care > 0.6 → check_on |
| SC-11 | 冲突 — 体力优先 | rest > explore |
| SC-12 | 兼容动机合并 | boredom + care → check_on_user_X |
| SC-13 | Loneliness 依赖亲近关系 | 无亲近 → loneliness=0 |
| SC-14 | Loneliness 主观时钟 | DULL → 增长 2× |
| SC-15 | 动机注入 system prompt | _init_messages 含 "[内在驱动]" |
| SC-16 | ProactiveSystem 读动机 | 最强烈动机驱动主动发起 |
| SC-17 | 零回归 | 所有现有 154 tests 通过 |
| SC-18 | 新增测试 ≥ 14 条 | pytest count 验证 |
