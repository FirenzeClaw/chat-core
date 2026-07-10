# Design: 具身感知 — 疲劳 + 主观时间

> **Feature**: embodied-perception (Spec 007)
> **Status**: Design Draft
> **Created**: 2026-07-10
> **Context**: chat-core 当前只有注意力衰减和无聊检测两条时间线——注意力 fade 是纯时间函数，boredom 是纯墙钟函数。缺少人类身体的疲劳感受（精力消耗/恢复）、以及对时间的主观感受（投入时飞逝、无聊时煎熬）。本设计补齐这两种具身感知能力。

---

## 1. 设计目标

- **疲劳模型**：独立于注意力的 EnergyBar，事件驱动消耗 + idle 恢复 + 防御互动
- **主观时钟**：注意力/情绪/兴趣三维调制时间感知，boredom 改读主观时间
- **记忆标记**：主观时间感知变为可回忆的记忆内容 → Spec 003 回溯消费
- **与已有四系统联动**：Spec 003(记忆)、Spec 005(情绪+防御)、Spec 006(元认知)、注意力状态机

---

## 2. 疲劳模型 (EnergyBar)

### 2.1 核心逻辑

```python
@dataclass
class EnergyState:
    energy: float = 0.9          # 0.0 ~ 1.0
    last_update: float = 0.0     # timestamp
    total_turns_today: int = 0   # 累计轮数
```

**消耗事件** (每 turn 结束):
| 事件 | 消耗量 | 说明 |
|------|:---:|------|
| 正常发言 | -0.03 | 基础消耗 |
| 长回复 (>3 send_reply 段) | -0.06 | 多说更累 |
| 深度情绪波动 (\|Δcompound\| > 0.4) | -0.10 | 情绪冲击耗能 |
| 纠正被触发 | -0.05 | 自我纠正是认知负荷 |
| 防御被激活 | -0.04 | 防御机制额外消耗 |

**恢复事件** (idle 期间, 每 60s):
| 区间 | 速度 | 说明 |
|------|:---:|------|
| energy > 0.6 (精力充沛) | +0.02/min | 快速恢复 |
| 0.3 ~ 0.6 (一般疲劳) | +0.01/min | 正常恢复 |
| energy < 0.3 (深度疲劳) | +0.005/min | 缓慢恢复 |

### 2.2 防御联动 (→ Spec 005)

```
PROJECT:  guilt -0.05, energy +0.02  ← "投射成功后的短暂解脱"
DENIAL:   resentment +0.02, energy -0.02 ← "内心冲突消耗精力"
```

### 2.3 Exit 阈值

`energy < 0.15` → `should_exit_sub = True`。与注意力状态机的 DULL + should_exit 形成双保险——精力耗尽和注意力涣散分别独立触发终止。

---

## 3. 主观时钟 (SubjectiveClock)

### 3.1 核心实现

```python
class SubjectiveClock:
    """独立于 wall clock 的主观时间感知器。"""
    
    def __init__(self):
        self._accumulated: float = 0.0       # 累计主观时间 (秒)
        self._last_tick_real: float = 0.0
        self._speed_factor: float = 1.0
        self._current_perception: str = "normal"  # "immersed" | "normal" | "dragging"
        
    def tick(self, wall_dt: float, attention_state: AttentionState,
             emotion: EmotionState, interest_match: float) -> float:
        sf = self._compute_speed_factor(attention_state, emotion, interest_match)
        subjective_dt = wall_dt * sf
        self._accumulated += subjective_dt
        self._speed_factor = sf
        self._update_perception(sf)
        return subjective_dt
    
    def _compute_speed_factor(self, state, emotion, interest) -> float:
        """factor > 1 = 时间过得慢（煎熬）, factor < 1 = 时间过得快（投入）"""
        base = 1.0
        
        # 注意力调制 (→ 注意力状态机)
        if state == FOCUSED:    base *= 0.3
        elif state == DRIFTING: base *= 0.8
        else:                   base *= 2.0  # DULL
        
        # 情绪调制 (→ Spec 005)
        if emotion.joy > 0.5:             base *= 0.7
        if emotion.sadness > 0.5:         base *= 1.3
        if emotion.gratification > 0.4:   base *= 0.8
        
        # 兴趣调制
        if interest_match > 0.7:          base *= 0.6
        
        return base
```

### 3.2 Boredom 公式更新

```
原: B(t) = eval × e^(-t_wall / 600)
新: B(t) = eval × e^(-t_subjective / 600)
```

DULL 态下 subjective time 走 2× 快 → boredom 加速触发。FOCUSED 态下走 0.3× 慢 → 聊得投入不易觉得无聊。

---

## 4. 记忆标记 (→ Spec 003 联动)

每次对话结束，主观时间感知以 `emotional_tags` 形式写入当前 turn 的 conversation 记忆：

```json
{
    "subjective_time_perception": {
        "speed_factor": 0.4,
        "perception": "immersed",
        "description": "感觉聊得特别投入，半小时像一瞬间",
        "fatigue_at_end": 0.72
    }
}
```

**Spec 003 `_format_recall_result()` 消费**：
```
"我记得上次聊游戏的时候聊得特别投入，感觉时间过得飞快。"
"那次对话好像特别漫长，当时觉得有点累。"
```

**元认知联动 (→ Spec 006)**：上下文追加 fatigue 趋势和主观时间感知：
```
精力趋势: 0.85→0.72→0.55→0.48→0.41 ↓ (持续消耗, 未充分恢复)
主观时间感知: 平均 speed_factor 0.65 (偏向沉浸)
```

---

## 5. 数据流集成

```
每轮 turn 结束:
  │
  ├─ EnergyBar.consume(turn_type, has_defense, compound_delta)
  │     ├─ 正常发言 → -0.03
  │     ├─ 防御联动 → PROJECT +0.02 / DENIAL -0.02
  │     └─ 情绪冲击 → -0.10
  │
  ├─ EnergyBar < 0.15? → should_exit_sub flag ↑
  │
  └─ EnergyBar.get_state() → 写入 turn memory (fatigue_at_end)

idle 期间 (boredom tick):
  │
  ├─ EnergyBar.recover(wall_dt) → 慢恢复
  │
  ├─ SubjectiveClock.tick(wall_dt, attention_state, emotion, interest)
  │     → 返回 subjective_dt
  │
  └─ BoredomDetector._tick(subjective_dt)  ← 用主观时间而非墙钟
        B(t) = eval × e^(-subjective_dt / 600)

turn 结束归档:
  └─ memory.save() 附加 subjective_time_perception → 供 Spec 003 消费

元认知上下文 (→ Spec 006):
  └─ 追加 energy 趋势 + 主观时间感知
```

---

## 6. 配置外化

```yaml
systems:
  energy:
    enabled: true
    initial: 0.9
    range: [0.0, 1.0]
    consumption:
      normal_turn: 0.03
      long_reply_threshold: 3
      long_reply: 0.06
      emotion_shock: 0.10
      correction: 0.05
      defense: 0.04
    recovery:
      interval: 60
      rate_high: 0.02
      rate_mid: 0.01
      rate_low: 0.005
    exit_threshold: 0.15
    defense_interaction:              # ← Spec 005 联动
      project_relief: 0.02
      denial_drain: 0.02

  subjective_time:
    enabled: true
    speed_modifiers:
      attention:                      # ← 注意力状态机联动
        focused: 0.3
        drifting: 0.8
        dull: 2.0
      emotion:                        # ← Spec 005 联动
        joy_threshold: 0.5
        sadness_threshold: 0.5
        gratification_threshold: 0.4
      interest:
        match_threshold: 0.7           # interest_match > 0.7 → ×0.6
```

---

## 7. 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `core/types.py` | + `EnergyState` dataclass；+ `SubjectiveTimePerception` dataclass | 数据结构 |
| `systems/energy.py` | **新建** — `EnergyBar`: consume(), recover(), Spec 005 联动 | 核心—精力 |
| `systems/subjective_time.py` | **新建** — `SubjectiveClock`: tick(), compute_speed_factor() | 核心—主观时间 |
| `core/loop.py` | `_should_continue` 新增 energy 检查 (<0.15→exit)；每 turn 完成触发 consume | 集成 |
| `core/turn_manager.py` | `process_turn()` 后触发 energy.consume()；boredom 改用主观时钟；_init_messages 注入 energy 状态 | 集成 |
| `systems/boredom.py` | `BoredomDetector._tick()` 改用 `SubjectiveClock.tick()` | 集成 |
| `systems/emotion.py` | `EmotionEngine` 连接 `SubjectiveClock` 提供 joy/sadness/gratification | 提供 |
| `systems/attention.py` | `AttentionModel` 连接 `SubjectiveClock` 提供 state | 提供 |
| `systems/memory.py` | `_format_recall_result()` 检测 `subjective_time_perception` → 追加注解 | 消费 |
| `systems/metacognition.py` | `build_context()` 追加 energy 趋势 + 主观时间感知 | 消费 |
| `config.yaml` | + `systems.energy` + `systems.subjective_time` 段 | 配置 |
| `tests/test_energy.py` | **新建** — consume/recover 曲线、exit 阈值、防御联动 | 测试 |
| `tests/test_subjective_time.py` | **新建** — speed_factor 计算、boredom 联动、记忆标记 | 测试 |

---

## 8. 联动矩阵（与其他归档系统的关系）

| 提供方 | → 消费方 | 内容 |
|--------|---------|------|
| EnergyBar | `ReActLoop._should_continue` | energy < 0.15 → exit |
| EnergyBar | `MetacognitionEngine` (Spec 006) | energy 趋势进入元认知上下文 |
| SubjectiveClock | `BoredomDetector` | 替换 wall clock |
| SubjectiveClock → MemoryStore | `_format_recall_result` (Spec 003) | 时间感知注解 |
| SubjectiveClock | `MetacognitionEngine` (Spec 006) | 主观时间感知进入元认知上下文 |
| EmotionEngine (Spec 005) | SubjectiveClock | joy/sadness/gratification → speed_factor 调制 |
| AttentionModel | SubjectiveClock | FOCUSED/DRIFTING/DULL → speed_factor 调制 |
| InterestModel | SubjectiveClock | interest_match → speed_factor 调制 |
| DefenseEngine (Spec 005) | EnergyBar | PROJECT → +0.02, DENIAL → -0.02 |
| `|Δcompound| > 0.4` (Spec 005) | EnergyBar | 情绪冲击 → -0.10 energy |
| EnergyBar | turn memory → Spec 003 | fatigue_at_end 写入记忆，回溯时注解 |

---

## 9. 成功标准

| ID | 标准 | 验证 |
|----|------|------|
| SC-01 | 正常 turn 消耗 | 发言后 energy 下降 0.03 |
| SC-02 | 长回复加速消耗 | >3 段 → 下降 0.06 |
| SC-03 | 情绪冲击消耗 | \|Δcompound\| > 0.4 → 下降 0.10 |
| SC-04 | 防御联动 — PROJECT 解脱 | energy +0.02 |
| SC-05 | 防御联动 — DENIAL 内耗 | energy -0.02 |
| SC-06 | idle 恢复 — 高分位 | energy > 0.6 → +0.02/min |
| SC-07 | idle 恢复 — 低分位 | energy < 0.3 → +0.005/min |
| SC-08 | exit 阈值 | energy < 0.15 → should_exit_sub = True |
| SC-09 | 主观时间 — FOCUSED 加速 | speed_factor = 0.3, boredom 衰减 3× 慢 |
| SC-10 | 主观时间 — DULL 减速 | speed_factor = 2.0, boredom 衰减 2× 快 |
| SC-11 | 主观时间 — 情绪调制 | joy>0.5 → ×0.7; sadness>0.5 → ×1.3 |
| SC-12 | 主观时间 — 兴趣调制 | interest_match>0.7 → ×0.6 |
| SC-13 | 记忆标记写入 | turn 记忆含 subjective_time_perception |
| SC-14 | 记忆回溯注解 | recall 文本含 "感觉时间过得飞快" |
| SC-15 | 零回归 | 所有现有 154 tests 通过 |
| SC-16 | 新增测试 ≥ 8 条 | pytest count 验证 |
