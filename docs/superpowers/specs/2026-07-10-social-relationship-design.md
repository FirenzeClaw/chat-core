# Design: 社交与关系 — 关系梯度 + 群体动力学 + 仪式感/习惯

> **Feature**: social-relationship (Spec 008)
> **Status**: Design Draft
> **Created**: 2026-07-10
> **Context**: chat-core 当前所有用户（CLI default / QQ 多用户）在记忆层面隔离但 AI 对他们的态度没有"亲疏远近"。缺关系梯度、群体角色感知、跨群社交记忆、以及重复互动中自然形成的仪式感和习惯。本设计补齐社交维度的全部能力。

---

## 1. 设计目标

- **关系梯度**：4 维关系向量 + 自动阶段判定，调制人格权重而非硬编码行为
- **群体动力学**：群内角色感知（统计 + LLM 定性）、群氛围聚合、跨群社交记忆
- **仪式感/习惯**：重复问候模式、时间规律、内部笑话的检测与注入
- **与已有五系统联动**：Spec 003(记忆)、Spec 005(情绪+防御)、Spec 006(元认知)、Spec 007(具身感知)、人格系统

---

## 2. 关系梯度

### 2.1 4 维关系向量

```python
@dataclass
class RelationshipVector:
    user_id: str
    trust: float = 0.0          # 信任：recall 命中 + 深度对话
    closeness: float = 0.0      # 亲近：turn 数 + 情感共鸣 + 自我暴露
    respect: float = 0.0        # 尊重：话题质量 + 纠正被接受
    familiarity: float = 0.0    # 熟悉度：纯统计 (turn 数 + 记忆条目数)
    last_interaction: float = 0.0
```

### 2.2 增长规则（与现有系统联动）

| 维度 | 增长源 | 触发条件 | 增量 |
|------|--------|---------|:---:|
| trust | recall 命中 | 用户消息触发 recall 且命中 ≥3 条记忆（说明用户记得 AI） | +0.03 |
| trust | 深度对话 | 审查 `combined_weight < 0.3`（低错误率 = 聊得来 = 信任高） | +0.05 |
| closeness | 每 turn | 每次对话 | +0.01 |
| closeness | 情感共鸣 | 用户情绪与 AI 情绪同频 (|valence_diff| < 0.2, ← Spec 005) | +0.03 |
| closeness | 自我暴露 | inner_thoughts 含私人内容（检测关键词：私人/秘密/只跟你说） | +0.02 |
| respect | 话题质量 | 非简单问候（消息长度 > 20 字且非纯表情） | +0.02 |
| respect | 纠正被接受 | subconscious correction 被下一轮子Session 读取且照做 | +0.05 |
| familiarity | 每 turn | 每次对话 | +0.005 |
| familiarity | 记忆条目 | 每有一条关于此用户的记忆（user/{uid}/*） | +0.002 |

### 2.3 衰减规则

| 维度 | 衰减速率 | 触发时机 | 说明 |
|------|:---:|------|------|
| trust | 0.001/day | 每 turn `RelationshipEngine.update()` 被调用时，基于 `time_since_last_interaction`（当前时间 − `last_interaction`）回溯计算衰减量 | 信任消退很慢 |
| closeness | 0.003/day | 同上 | 亲近感较快消退 |
| respect | 0.0 | — | 几乎不衰减（"尊重一旦建立不易消失"） |
| familiarity | 0.0 | — | 纯统计量，不衰减 |

### 2.4 关系阶段判定

```
if familiarity < 0.1:                    → "陌生人"
elif trust > 0.5 and closeness > 0.4:    → "密友"
elif trust > 0.3 and closeness > 0.2:    → "朋友"
elif familiarity >= 0.1:                 → "熟人"
else:                                    → "陌生人"
```

### 2.5 阶段 → 人格权重调制

关系阶段不硬编码行为，而是调制现有 `PersonalityEngine` 的输出（叠加在 personality → behavior 映射上）：

| 参数 | 陌生人 | 熟人 | 朋友 | 密友 |
|------|:---:|:---:|:---:|:---:|
| empathy 乘数 | ×0.7 | ×0.9 | ×1.0 | ×1.2 |
| self_disclosure 乘数 | ×0.3 | ×0.6 | ×1.0 | ×1.5 |
| defense_prob 乘数 | ×1.5 | ×1.1 | ×0.8 | ×0.5 |
| proactive_prob 乘数 | ×0.0 | ×0.3 | ×1.0 | ×1.3 |

全部可通过 `config.yaml` 配置，不同人设（persona.yaml）可调不同值。

---

## 3. 群体动力学

### 3.1 群内角色感知

**统计层（实时，零 LLM 成本）**：

```python
@dataclass
class GroupRoleMetrics:
    group_id: str
    total_messages: int = 0         # 群内总消息数（旁听计数）
    at_count: int = 0               # 被 @ 次数
    reply_count: int = 0            # AI 在群内的回复次数
    member_reply_to_ai: int = 0    # 群成员回复 AI 的次数
    active_days: int = 0
    member_count: int = 0
    
    @property
    def at_ratio(self) -> float:
        return self.at_count / max(self.total_messages, 1)
    
    @property
    def engagement_rate(self) -> float:
        return self.member_reply_to_ai / max(self.reply_count, 1)
    
    @property
    def role_score(self) -> float:
        """0~1, 越高越活跃"""
        return min(1.0,
            self.at_ratio * 10 + self.engagement_rate * 0.5 +
            min(self.active_days / 30, 0.3))
```

**LLM 定性层**：由 Spec 006 元认知顺带覆盖——群角色摘要进入 `metacognition_pass` 上下文，LogicBrain 在定期元认知审查时自然输出定性判断，无需新增 LLM 调用。

### 3.2 群氛围感知

```python
@dataclass
class GroupAtmosphere:
    group_id: str
    avg_emotion: EmotionState       # 群成员消息的情绪聚合
    dominant_topics: list[str]      # 最近热门话题 top 5
    conflict_events: int = 0        # 冲突事件数 (anger 峰值)
    last_conflict_turn: int = 0
    emotional_volatility: float = 0.0
```

情绪聚合来源：AI 的 `inner_thoughts → user_read.mood` 字段——每轮群聊回复中，AI 感知到的群氛围。不直接分析成员消息（隐私 + 无权限），而是通过 AI 自身的感知反推。

**持久化**：快照每 N 轮写入 `global/group/{gid}/atmosphere`，供跨 session 回忆。

### 3.3 跨群社交记忆

利用现有命名空间隔离（同一 openid 下 `c2c/`、`group/A/`、`group/B/`）。Spec 003 的 `_format_recall_result()` 新增逻辑：当某用户的记忆条目跨多个 namespace 时，追加跨场景上下文：

```
"我记得小刚。在群A他比较活跃，在群B几乎不说话。
 他在私聊里跟我聊过职业规划的事。"
```

无需新增存储结构——现有命名空间已天然支持。

---

## 4. 仪式感/习惯

### 4.1 PatternDetector

检测四种模式：

| 模式类型 | 检测逻辑 | 最小重复 |
|---------|---------|:---:|
| greeting | 相同问候文本 ≥ N 次 | 3 |
| timing | 时间段聚类（如 09:00-10:00 占比 > 60%） | 5 |
| topic_cycle | 相同话题被提及 ≥ N 次 | 3 |
| inside_joke | inner_thoughts 含 "好笑/有趣/笑了" + 同话题 ≥ 2 次 | 2 |

### 4.2 存储格式

写入 `user/{uid}/patterns/`：

```json
{
    "pattern_type": "greeting",
    "template": "早啊",
    "count": 12,
    "last_seen": "2026-07-10T09:15:00",
    "time_distribution": {"09:00-10:00": 8, "10:00-11:00": 4}
}
```

**中间态持久化**：模式检测需要跨 session 记住中间计数（如"已出现 2 次，还在等第 3 次"）。这些中间计数存储在 `user/{uid}/patterns/_pending/{pattern_type}` 命名空间下，格式为 `{"current_streak": 2, "last_seen": "ISO8601"}`。达标后迁移至 `user/{uid}/patterns/` 并删除 `_pending`。

### 4.3 消费

`_init_messages()` 读取 patterns，注入 system prompt：

```
"[社交模式] 这个用户通常在上午跟你说'早啊'。
 你们之间有个内部梗：'抽风'而不是'抽卡'——可以在适当的时候自然提起。"
```

---

## 5. 数据流集成

```
用户消息 → BotAdapter.process_message()
  │
  ├─ 关系更新: RelationshipEngine.update(user_id, turn_context)
  │     ├─ 基础增长: per_turn + memory_recall + inner_thoughts 分析
  │     ├─ 衰减计算: 基于上次互动间隔
  │     └─ 阶段判定: 4维 → stage
  │
  ├─ 群体统计 (群聊): GroupRoleMetrics.update(group_id, event)
  │     ├─ 被 @ → at_count++
  │     ├─ 旁听消息 → total_messages++
  │     └─ 成员回复 AI → member_reply_to_ai++
  │
  ├─ 氛围快照 (群聊, 每 N 轮):
  │     └─ GroupAtmosphere.snapshot() → global/group/{gid}/atmosphere
  │
  ├─ 模式检测: PatternDetector.detect(user_id, message, inner_thoughts)
  │     └─ 达标 → user/{uid}/patterns/ 写入
  │
  ├─ _init_messages() 注入:
  │     ├─ 关系阶段 → "[关系状态] 你与用户的关系: 朋友"
  │     ├─ 社交模式 → "[社交模式] 这个用户..."
  │     └─ 跨群上下文 (群聊) → "[跨群记忆] 这个用户在..."
  │
  ├─ PersonalityEngine.apply_relationship_modulation(stage)
  │     → empathy, self_disclosure, proactive 加权
  │
  └─ DefenseEngine.evaluate() 读取:
        defense_prob_multiplier = stage_modulation[stage].defense_prob
```

---

## 6. 配置外化

```yaml
systems:
  relationship:
    enabled: true
    dimensions:
      trust:
        recall_hit_boost: 0.03
        deep_conversation_threshold: 0.3
        deep_conversation_boost: 0.05
        decay_rate: 0.001
      closeness:
        per_turn: 0.01
        emotional_resonance_threshold: 0.6
        emotional_resonance_boost: 0.03
        self_disclosure_boost: 0.02
        self_disclosure_keywords:
          - "私人"
          - "秘密"
          - "只跟你说"
          - "别告诉别人"
        decay_rate: 0.003
      respect:
        topic_quality_boost: 0.02
        topic_quality_min_length: 20
        correction_accepted_boost: 0.05
        decay_rate: 0.0
      familiarity:
        per_turn: 0.005
        per_memory_entry: 0.002
        decay_rate: 0.0
    
    stages:
      stranger:    {familiarity_max: 0.1}
      acquaintance: {familiarity_min: 0.1}
      friend:      {trust_min: 0.3, closeness_min: 0.2}
      close_friend: {trust_min: 0.5, closeness_min: 0.4}
    
    personality_modulation:
      stranger:
        empathy: 0.7
        self_disclosure: 0.3
        defense_prob: 1.5
        proactive_prob: 0.0
      acquaintance:
        empathy: 0.9
        self_disclosure: 0.6
        defense_prob: 1.1
        proactive_prob: 0.3
      friend:
        empathy: 1.0
        self_disclosure: 1.0
        defense_prob: 0.8
        proactive_prob: 1.0
      close_friend:
        empathy: 1.2
        self_disclosure: 1.5
        defense_prob: 0.5
        proactive_prob: 1.3

  group_dynamics:
    enabled: true
    atmosphere_snapshot_interval: 10
    role_metrics_window: 100
    
  patterns:
    enabled: true
    min_repetitions: 3
    pattern_types: [greeting, timing, topic_cycle, inside_joke]
    inside_joke_keywords:
      - "好笑"
      - "有趣"
      - "笑了"
      - "哈哈哈"
      - "笑死"
```

---

## 7. 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `core/types.py` | + `RelationshipVector`, `RelationshipStage`, `GroupRoleMetrics`, `GroupAtmosphere`, `InteractionPattern` | 数据结构 |
| `systems/relationship.py` | **新建** — `RelationshipEngine`: 4维计算、阶段判定、人格调制系数输出 | 核心 |
| `systems/group_dynamics.py` | **新建** — `GroupRoleMetrics`: role_score 统计；`GroupAtmosphere`: 氛围快照 | 核心 |
| `systems/patterns.py` | **新建** — `PatternDetector`: 重复检测、模板匹配、时间聚类 | 核心 |
| `core/loop.py` | `_init_messages()` 注入关系阶段 + 社交模式 + 跨群上下文 | 集成 |
| `core/turn_manager.py` | 每 turn 后更新 relationship；PersonalityEngine 级联 relationship 调制 | 集成 |
| `systems/personality.py` | `PersonalityEngine` 新增 `apply_relationship_modulation(stage)` → empathy/self_disclosure/proactive | 消费 |
| `systems/defense.py` | `DefenseEngine` (Spec 005) 读取 `defense_prob` 调制 | 消费 |
| `systems/memory.py` | `_format_recall_result()` (Spec 003) 检测跨 namespace → 追加跨群注解 | 消费 |
| `systems/metacognition.py` | `build_context()` (Spec 006) 追加群角色摘要 + 关系阶段 | 消费 |
| `qq/adapter.py` | 消息处理中更新群角色统计；旁听写入群氛围情绪聚合 | 集成 |
| `config.yaml` | + `systems.relationship` + `systems.group_dynamics` + `systems.patterns` | 配置 |
| `tests/test_relationship.py` | **新建** — 4维计算、阶段判定、人格调制、衰减 | 测试 |
| `tests/test_group_dynamics.py` | **新建** — 角色统计、氛围聚合、跨群记忆注解 | 测试 |
| `tests/test_patterns.py` | **新建** — 问候检测、时间规律、内部梗 | 测试 |

---

## 8. 联动矩阵

| 提供方 | → 消费方 | 内容 |
|--------|---------|------|
| RelationshipEngine | PersonalityEngine | 关系阶段 → empathy/self_disclosure/proactive 调制 |
| RelationshipEngine | DefenseEngine (Spec 005) | 防御概率调制 |
| RelationshipEngine | `_init_messages` (core/loop.py) | 关系阶段注入 system prompt |
| RelationshipEngine | MetacognitionEngine (Spec 006) | 关系阶段进入元认知上下文 |
| GroupDynamics | `_format_recall_result` (Spec 003) | 跨群社交注解 |
| GroupDynamics | MetacognitionEngine (Spec 006) | 群角色摘要进入元认知上下文 |
| PatternDetector | `_init_messages` (core/loop.py) | 社交模式注入 system prompt |
| MemoryStore (Spec 003) | RelationshipEngine | recall 命中 → trust boost |
| inner_thoughts 文本 | RelationshipEngine | 自我暴露检测 → closeness boost |
| inner_thoughts → user_read.mood | GroupDynamics | 群氛围情绪聚合 |
| EmotionEngine (Spec 005) | RelationshipEngine | 情感共鸣检测 → closeness boost |
| EnergyBar (Spec 007) | RelationshipEngine | 低精力 → 降低 proactive_prob (累了不想主动社交) |

---

## 9. 成功标准

| ID | 标准 | 验证 |
|----|------|------|
| SC-01 | 4维独立计算 | trust/closeness/respect/familiarity 各自按事件增减 |
| SC-02 | 关系阶段自动判定 | 阈值条件下阶段正确切换 |
| SC-03 | 阶段 → 人格调制 | 密友 empathy ×1.2, 陌生人 defense_prob ×1.5 |
| SC-04 | recall 命中 → trust 增长 | 3+ 条记忆命中 → trust +0.03 |
| SC-05 | closeness 衰减 | 7 天不聊 → closeness 下降 ~2% |
| SC-06 | 群角色统计 — 被 @ | at_count / total_messages 正确 |
| SC-07 | 群角色统计 — 互动率 | member_reply_to_ai / reply_count 正确 |
| SC-08 | 群氛围快照 | 每 N 轮存档 global/group/{gid}/atmosphere |
| SC-09 | 跨群社交注解 | recall 含 "群A和群B都出现过" |
| SC-10 | 模式检测 — 问候 | 同问候 ≥3 → pattern 入库 |
| SC-11 | 模式检测 — 时间 | 时间段聚类 → time_distribution |
| SC-12 | 模式检测 — 内部梗 | inner_thoughts 含关键词 + repeat → inside_joke |
| SC-13 | 模式注入 system prompt | _init_messages 含社交模式提示 |
| SC-14 | Spec 007 联动 — 低精力降主动 | energy < 0.3 → proactive_prob 降 |
| SC-15 | 零回归 | 所有现有 154 tests 通过 |
| SC-16 | 新增测试 ≥ 12 条 | pytest count 验证 |
