# Design: 注意力状态机 — 多脑协调动态模型

> **Feature**: attention-state-machine
> **Status**: Design Draft（基础系统，不等同于正式 Spec，被 Spec 005-009 多处引用）
> **Created**: 2026-07-10
> **Note**: 本设计是所有后续 Spec 的基础依赖。其他 Spec 引用时统一使用 "注意力状态机 (attention-state-machine)"。正式 Spec 编号待实施时分配。
> **Context**: chat-core 当前 AttentionModel 只有单向衰减，无回升机制。注意力过低会阻断子Session（`should_exit_sub < 0.15`），但缺乏拟人化的状态转换、多脑协调、以及情绪/记忆/竞态的联动。

---

## 1. 三态模型

```
专注态 FOCUSED  (focus ≥ 0.6)
  ├─ 正常对话质量，完整 ReAct 循环
  ├─ send_reply 可多段 (max 5)，inner_thoughts 详细
  ├─ recall 主动使用概率高
  └─ "我在认真听你说"

游离态 DRIFTING (0.3 ≤ focus < 0.6)
  ├─ 回复偏短，send_reply 倾向 1-3 段
  ├─ inner_thoughts 包含 "有点走神"
  ├─ 元认知可能触发主动道歉："抱歉，刚刚有点走神"
  └─ "听着呢，但有点心不在焉"

昏沉态 DULL     (focus < 0.3)
  ├─ 收到消息强制 focus +0.20（回升到游离下限附近）
  ├─ 回复极简，send_reply 最多 2 段
  ├─ inner_thoughts 倾向 "想早点结束"
  ├─ may_exit_sub = True（可被强刺激覆盖）
  └─ "嗯...你说什么？"
```

昏沉态不沉默——收到消息**一定回复**，但回复被 focus 调制为最简模式。`should_exit_sub()` 改为状态感知：DULL 态下返回 False（不阻断子Session）。

### 初始状态

- 新 Session 启动: **FOCUSED, focus=0.9**
- Bot 重启: **不继承旧状态**，全新开始
- 双脑 (logic/emotion): 各自独立衰减（同 drift_decay_rate），不受三态事件影响。主脑仅受疲劳因子 (§8) 影响，不参与状态机转移

### QQ Bot 多人注意力归属

- 每个子Session 有**独立 AttentionModel 实例**
- 竞态事件 (RACE_MILD/SEVERE) **影响全局** AttentionModel，再传导到当前活跃的所有子Session
- 其他事件（记忆命中、话题匹配、情绪变化）**仅影响当前子Session**

---

## 2. 状态转移

### 2.1 转移矩阵

| 事件 | → FOCUSED | → DRIFTING | → DULL |
|------|-----------|------------|--------|
| 用户消息到达 | 保持 (1.0) | P=0.7 回升 | P=0.5 回升, +0.20 |
| 情绪积极 (valence > 0.3) | +0.10 | — | — |
| 情绪消极 (valence < -0.3) | -0.10 | — | — |
| 情绪剧烈波动 (\|Δvalence\| > 0.5) | +0.30 | +0.25 | P=0.8 跳游离 |
| 记忆强命中 (salience ≥ 7) | +0.25 | +0.20 | P=0.5 跳游离 |
| 记忆空结果 | -0.03 | -0.05 | — |
| 话题匹配兴趣 | +0.08 | +0.05 | — |
| 竞态 active ≥ 3 | P=0.7 → 游离 | 保持 | — |
| 竞态 active ≥ 5 | P=0.6 → 昏沉 | P=0.8 → 昏沉 | — |
| 用户短回复 ×3 轮 | -0.10 | -0.15 | — |
| 沉默 (每分钟) | -0.03 | -0.05 | 保持 |
| 内心戏有"想要做"意图 | +0.05 | — | — |
| 纠正被触发 | -0.05 | -0.10 | — |
| 子Session 发言完成 | -0.02/段 | -0.03/段 | -0.01/段 |

### 2.2 转移惯性

状态转移不是离散跳变——0.3s 内渐进插值到目标 focus：

```python
def apply_event(self, event: AttentionEvent) -> None:
    target = self._compute_target(event)
    # 平滑过渡
    self._transition_target = target
    self._transition_start = self._states["sub"].focus
    self._transition_elapsed = 0.0
```

每次 `drift()` 调用时，检查是否有进行中的过渡，逐步靠近目标。

---

## 3. 多脑协调

### 3.1 主脑 → 子Session

主脑（逻辑/情感）的 focus 通过 `emotion_alert` 通道影响子Session：

```
情感脑检测情绪变化 → event_bus.publish("emotion_alert")
  → AttentionModel.apply_event() → 更新 sub.focus
  → 子Session 的 _should_continue() 读取更新后的 focus

逻辑脑发现事实矛盾 → event_bus.publish("logic_conflict")
  → sub.focus -0.05 (困惑/犹豫)
```

### 3.2 子Session → 主脑

子Session 的 inner_thoughts 中提取的意图 → 主脑的审查方向：

```
inner_thoughts: "我有点走神，不太想聊了"
  → 逻辑脑: 降低本轮审查权重（理解这是注意力问题，非内容错误）
  → 情感脑: 记录 boredom 信号
```

### 3.3 协调权重

| 通道 | 方向 | 机制 |
|------|------|------|
| emotion_alert | 情感 → AttentionModel | `apply_event()` |
| logic_conflict | 逻辑 → AttentionModel | `apply_event()` |
| inner_thoughts 解析 | 子Session → 双脑 | `extract_intent()` |
| 审查结果 | 双脑 → subconscious | `_async_review_and_decide()` |

---

## 4. 联动系统

### 4.1 情绪引擎 → 注意力

```
EmotionEngine.tick() → 检测各脑 valence 变化
  → |Δvalence| > 0.5: event_bus.publish("emotion_alert", {mood_shift, intensity})
  → AttentionModel.apply_event(ATTENTION_EMOTION_SHOCK)
  → 昏沉态下概率性突破唤醒
```

### 4.2 记忆系统 → 注意力

```
_handle_recall() 完成后:
  → 命中 salience ≥ 7: 通知 AttentionModel.boost("sub", 0.25)
  → 空结果: 通知 AttentionModel.apply_event(ATTENTION_MEMORY_MISS)
  → 联锁链长度 > 3: 额外 +0.05 (丰富联想 → 更投入)
```

### 4.3 竞态追踪 → 注意力

```
RaceTracker.enter():
  → active_count 从 2→3: apply_event(ATTENTION_RACE_MILD)
  → active_count 从 4→5: apply_event(ATTENTION_RACE_SEVERE)

RaceTracker.exit():
  → active_count 从 3→2: focus +0.05 (松了口气)
```

### 4.4 话题兴趣 → 注意力

```
InterestModel.match(topic):
  → 强匹配 (score > 0.7): +0.08 FOCUSED, +0.05 DRIFTING
  → 弱匹配: 无变化
  → 长时间无匹配话题: -0.02/min
```

### 4.5 内心戏 → 注意力

```
extract_intent(inner_thoughts):
  → 有 "想要做" 意图: +0.05
  → 包含 "走神/分心/困": 元认知标记 → 子Session 可能主动道歉
  
元认知触发条件:
  DRIFTING 且 inner_thoughts 含元认知关键词:
    → 子Session 提示词追加: "你可能已经有点走神了，必要时可以坦诚告诉对方"
```

---

## 5. 昏沉态 × 无聊 × 兴趣 联动

### 5.1 无聊加速

```
boredom_tick 间隔 (正常 30s):
  FOCUSED:  30s
  DRIFTING: 20s  
  DULL:     15s (加速 2×)

触发阈值 (正常 B(t) < 0.30):
  DULL: B(t) < 0.40 (更容易觉得 "无聊了，得做点什么")
```

### 5.2 兴趣受情绪调制

```
兴趣触发概率 = base_probability × mood_modifier

DULL 态 mood_modifier:
  情绪消极 (valence < -0.2): ×0.5  (提不起劲)
  情绪中性 (-0.2~0):        ×0.8
  情绪积极 (valence > 0):    ×1.2  (情绪推一把就能振作)
  情绪剧烈波动 (|Δ| > 0.5):  ×2.0  + focus +0.25 (冲击式唤醒)
```

### 5.3 ProactiveSystem 联动

```
ProactiveSystem._should_initiate():
  → 读取 AttentionModel.get_state("sub")
  → FOCUSED:  允许主动发起 (正常概率)
  → DRIFTING: 允许但概率 ×0.3
  → DULL:     禁止主动发起 (概率 0.0)

ProactiveSystem._on_boredom_trigger():
  → DULL + 无聊触发: 不发起对话，改为写入 subconscious/nudges
    (等注意力恢复后子Session 自然会看到)
```

---

## 6. 配置映射

所有注意力参数外化到 `config.yaml`:

```yaml
systems:
  attention:
    state_machine:
      initial_focus: 0.9
      state_thresholds:
        focused: 0.6
        drifting: 0.3
      boosts:
        user_message_dull: 0.20
        emotion_positive: 0.10
        emotion_shock: 0.30
        memory_strong_hit: 0.25
        topic_match_strong: 0.08
        topic_match_weak: 0.05
        intent_detected: 0.05
      penalties:
        emotion_negative: 0.10
        memory_miss: 0.03
        short_reply_streak: 0.10
        correction_triggered: 0.05
        per_segment_sent: 0.02
      transition_probabilities:
        drifting_to_focused_on_message: 0.7
        dull_to_drifting_on_message: 0.5
        dull_to_drifting_on_shock: 0.8
        focused_to_drifting_on_race3: 0.7
        focused_to_dull_on_race5: 0.6
        drifting_to_dull_on_race5: 0.8
    drift:
      decay_rate_focused: 0.001
      decay_rate_drifting: 0.002
      decay_rate_dull: 0.0005
    fatigue:
      max_turns: 50
      decay_acceleration: 0.5
    boredom_link:
      tick_interval_focused: 30
      tick_interval_drifting: 20
      tick_interval_dull: 15
      threshold_dull: 0.40
```

---

## 7. focus 调制子Session 回复

### 6.1 System Prompt 注入

```
focus ≥ 0.6: "[注意状态] 你感到专注、投入，对对话充满兴趣。"
0.3 ≤ focus < 0.6: "[注意状态] 你有点走神，注意力不太集中，回复会偏短。"
focus < 0.3: "[注意状态] 你很难集中注意力，只想简单回应，但请一定回复对方。"
```

### 6.2 行为参数调制

| 参数 | FOCUSED (≥0.6) | DRIFTING (0.3~0.6) | DULL (<0.3) |
|------|:---:|:---:|:---:|
| send_reply 最大段数 | 5 | 3 | 2 |
| inner_thoughts 详细度 | 完整 | 简略 | 极简 |
| recall 主动使用概率 | 高 | 中 | 低 |
| wait 使用概率 | 低 | 中 | 高 (犹豫) |
| 主动发起话题概率 | 0.3 | 0.1 | 0.0 |

---

## 8. 衰减速率分级

```
drift_decay_rate (focus 每秒衰减):

  FOCUSED:  0.001/s   |  1000s → 归零 | "正常走神速度"
  DRIFTING: 0.002/s   |  500s  → 归零 | "走神后加速滑落"
  DULL:     0.0005/s  |  2000s → 归零 | "到底了，跌得慢"

人类类比:
  专注时慢慢走神 → 一走神就加速 → 到底后不敏感了
```

---

## 9. 疲劳累积因子

```python
# 长时间对话/多轮后，衰减加速
fatigue = min(1.0, total_turns / 50)  # 50 轮后满疲劳
effective_decay = drift_decay_rate * (1.0 + fatigue * 0.5)
# 满疲劳时衰减加速 50%
```

---

## 10. 数据结构扩展

```python
class AttentionState(Enum):
    FOCUSED = "focused"
    DRIFTING = "drifting"
    DULL = "dull"

class AttentionEvent(Enum):
    USER_MESSAGE = "user_message"
    EMOTION_POSITIVE = "emotion_positive"
    EMOTION_NEGATIVE = "emotion_negative"
    EMOTION_SHOCK = "emotion_shock"
    MEMORY_STRONG_HIT = "memory_strong_hit"
    MEMORY_MISS = "memory_miss"
    TOPIC_MATCH = "topic_match"
    RACE_MILD = "race_mild"
    RACE_SEVERE = "race_severe"
    SHORT_REPLY_STREAK = "short_reply_streak"
    SILENCE_TICK = "silence_tick"
    INTENT_DETECTED = "intent_detected"
    CORRECTION_TRIGGERED = "correction_triggered"

@dataclass
class AttentionStateX(AttentionState):  # 扩展原有 AttentionState
    state: AttentionStateEnum
    focus: float
    dominance: float
    fatigue: float          # 新增
    transition_target: float | None  # 新增: 平滑过渡目标
    transition_elapsed: float        # 新增
```

---

## 11. 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `systems/attention.py` | 状态机核心逻辑 | +AttentionState 枚举, +apply_event(), +transition smoothing |
| `systems/emotion.py` | emotion_alert 集成 | tick() 后检测 Δvalence → publish |
| `systems/memory.py` | recall 回调 | _handle_recall 后通知 AttentionModel |
| `core/loop.py` | focus 调制注入 | _init_messages 注入 focus 状态到 system prompt |
| `core/turn_manager.py` | 事件发布 | emotion_alert, logic_conflict 事件 |
| `qq/adapter.py` | QQ Bot 集成 | RaceTracker 回调, 子Session focus 注入 |
| `config.yaml` | 注意力参数外化 | 状态机所有可调参数 |
| `chat-core-design.md` | 设计文档更新 | §5.1 补充注意力状态机 |

---

## 12. 成功标准

| 指标 | 目标 |
|------|------|
| 状态转移平滑无跳变 | 测试: focus 变化率 ≤ 0.5/s |
| 昏沉态一定回复 | 测试: DULL + 收到消息 → replies 非空 |
| 情绪冲击突破昏沉 | 测试: |Δvalence| > 0.5 + DULL → 跳游离 |
| 疲劳加速衰减 | 测试: turn=50 后 decay 为初始 1.5× |
| 元认知触发 | 测试: DRIFTING + 关键词 → inner_thoughts 含"走神" |
| 现有 154 tests 通过 | 零回归 |
