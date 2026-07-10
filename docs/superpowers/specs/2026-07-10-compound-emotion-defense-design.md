# Design: 复合情绪 + 防御机制

> **Feature**: compound-emotion-defense (Spec 005)
> **Status**: Design Draft
> **Created**: 2026-07-10
> **Context**: chat-core 当前 EmotionEngine 的 10 维情绪独立衰减，无维度间交互，无法产生"欣慰但失落"等复合感受。审查系统只有纠正/沉默二选一，缺少人类心理防御机制（否认、合理化、投射）来保护自我形象。

---

## 1. 设计目标

- **复合情绪**：10 个基础维度通过交互矩阵在 tick() 内生成 12 种复合情绪，复合情绪也有自身衰减和跨脑传染
- **防御机制**：审查发现错误时，基于 impulsiveness 和条件修饰决定是直接纠正还是启动三种防御之一
- **与现有系统无缝衔接**：compatible 现有 EmotionEngine tick 顺序、异步审查管线、subconscious 写入路径

---

## 2. 复合情绪数据结构

### 2.1 EmotionState 扩展

```python
@dataclass
class EmotionState:
    # 现有 10 基础维不变
    brain: str = ""
    surprise: float = 0.0
    confusion: float = 0.0
    fear: float = 0.0
    anger: float = 0.0
    disgust: float = 0.0
    joy: float = 0.5
    sadness: float = 0.0
    interest: float = 0.5
    anticipation: float = 0.0
    trust: float = 0.5
    last_tick: datetime = field(default_factory=datetime.now)

    # 新增：12 维复合情绪
    bittersweet: float = 0.0      # 怀念 (joy × sadness)
    guilt: float = 0.0            # 愧疚 (sadness × fear)
    anxiety: float = 0.0          # 焦虑 (fear × anticipation)
    contempt: float = 0.0         # 轻蔑 (anger × disgust)
    gratification: float = 0.0    # 欣慰 (joy × trust)
    disappointment: float = 0.0   # 失望 (sadness × surprise)
    envy: float = 0.0             # 嫉妒 (sadness × anger)
    pride: float = 0.0            # 骄傲 (joy × anticipation)
    resentment: float = 0.0       # 怨恨 (anger × sadness)
    awe: float = 0.0              # 敬畏 (fear × surprise × trust)
    nostalgia: float = 0.0        # 怀旧 (joy × sadness × interest)
    bewilderment: float = 0.0     # 困惑加深 (confusion × fear)
```

### 2.2 交互矩阵

维度 A × 维度 B 同时 ≥ `interaction_threshold`（默认 0.3）时，每 tick 复合情绪 += min(A, B) × coefficient × tick_coeff。

```python
INTERACTION_MATRIX: dict[str, list[tuple[str, float, str | None]]] = {
    "joy": [
        ("sadness",       0.02, "bittersweet"),
        ("trust",         0.03, "gratification"),
        ("anticipation",  0.03, "pride"),
        ("interest",      0.02, None),            # joy × interest → 泛化愉悦（不单独成维度）
    ],
    "sadness": [
        ("joy",           0.02, "bittersweet"),
        ("fear",          0.03, "guilt"),
        ("anger",         0.02, "resentment"),
        ("surprise",      0.03, "disappointment"),
        ("interest",      0.02, "nostalgia"),
        ("anticipation",  0.01, "envy"),          # 预期他人比自己好 → 嫉妒
    ],
    "anger": [
        ("disgust",       0.03, "contempt"),
        ("sadness",       0.02, "resentment"),
    ],
    "fear": [
        ("anticipation",  0.03, "anxiety"),
        ("sadness",       0.02, "guilt"),
        ("confusion",     0.03, "bewilderment"),
        ("surprise",      0.02, "awe"),
        ("trust",         0.01, "awe"),           # awe = fear + surprise + trust 组合
    ],
    "anticipation": [
        ("fear",          0.03, "anxiety"),
        ("joy",           0.03, "pride"),
    ],
    "trust": [
        ("joy",           0.03, "gratification"),
        ("fear",          0.01, "awe"),
    ],
    "disgust": [
        ("anger",         0.03, "contempt"),
    ],
    "surprise": [
        ("sadness",       0.03, "disappointment"),
        ("fear",          0.02, "awe"),
    ],
    "confusion": [
        ("fear",          0.03, "bewilderment"),
    ],
    "interest": [
        ("joy",           0.02, "nostalgia"),
        ("sadness",       0.02, "nostalgia"),
    ],
}
```

### 2.3 复合情绪衰减

每个复合维度的半衰期 = 构成它的基础维度半衰期的均值 × `decay_halflife_ratio`。

| 复合维度 | 基础维来源 | 半衰期 (秒) | ratio=0.5 后半衰 |
|----------|----------|-----------|-----------------|
| bittersweet | joy(600) + sadness(900) | 750 | 375 |
| guilt | sadness(900) + fear(300) | 600 | 300 |
| anxiety | fear(300) + anticipation(1800) | 1050 | 525 |
| contempt | anger(600) + disgust(600) | 600 | 300 |
| gratification | joy(600) + trust(3600) | 2100 | 1050 |
| disappointment | sadness(900) + surprise(30) | 465 | 233 |
| envy | sadness(900) + anger(600) | 750 | 375 |
| pride | joy(600) + anticipation(1800) | 1200 | 600 |
| resentment | anger(600) + sadness(900) | 750 | 375 |
| awe | fear(300) + surprise(30) + trust(3600) | 1310 | 655 |
| nostalgia | joy(600) + sadness(900) + interest(1200) | 900 | 450 |
| bewilderment | confusion(120) + fear(300) | 210 | 105 |

---

## 3. tick() 流变更

```
EmotionEngine.tick():
  │
  ├─ ① 维度间交互 (新增)
  │     for each (dim_a, dim_b, coeff, compound_name) in INTERACTION_MATRIX:
  │       if dim_a ≥ threshold AND dim_b ≥ threshold:
  │         compound[compound_name] += min(dim_a, dim_b) × coeff × tick_coeff
  │
  ├─ ② 复合情绪衰减 (新增)
  │     for each compound_dim in 12 compounds:
  │       decayed = compound_dim × 2^(-Δt / compound_half_life)
  │       compound_dim = max(0.0, decayed)
  │
  ├─ ③ 基础维度衰减 (原步骤 ① — 不变)
  │
  ├─ ④ 跨脑传染 (扩展)
  │     10 基础维 + 12 复合维 全部传播
  │
  └─ ⑤ compound_alert 检测 (新增)
        for each compound_dim:
          if |Δcompound_dim| > 0.4:
            event_bus.publish("compound_alert", {
              "dim": compound_dim,
              "delta": Δ,
              "brain": brain_name,
            })
```

**与注意力状态机的衔接**：`compound_alert` 事件被 `AttentionModel.apply_event()` 消费，等效于 `ATTENTION_EMOTION_SHOCK`（+0.30 focus）。

---

## 4. 防御机制

### 4.1 DefenseEngine

新建 `systems/defense.py`。

```python
class DefenseType(Enum):
    DIRECT = "direct"           # 不防御，直接纠正
    DENIAL = "denial"           # 否认：不写 correction
    RATIONALIZE = "rationalize" # 合理化：写 correction + 解释
    PROJECT = "project"         # 投射：归因转向用户

@dataclass
class DefenseResult:
    defense_type: DefenseType
    correction_text: str | None     # 写入 subconscious 的纠正文本 (DENIAL 时为 None)
    inner_reflection: str           # 写入 subconscious/defense_awareness 的自我感知文本
    emotion_delta: dict[str, float] # {dim: delta} 情绪调整（如 {"guilt": -0.05, "anger": +0.03}）
    silence_increment: int          # 0 或 1（DENIAL → 1，其余 → 0）

class DefenseEngine:
    def evaluate(
        self,
        review: ReviewResult,
        error_history: dict[str, int],  # error_type → 累计次数 (由 TurnManager 维护)
        emotion_engine: EmotionEngine,  # 直接传入而非只传 state——需读取 compound_delta
        impulsiveness: float,
    ) -> DefenseResult:
        """返回防御判定结果"""
    
    def _is_self_threatened(self, review: ReviewResult) -> bool:
        """判定审查错误是否威胁自我认知。
        
        条件：审查中涉及的冲突 memory key 属于 self/* 命名空间，
        且该 memory entry 的 salience ≥ 7 (深刻自我认知)。
        若为 True，DENIAL 概率 ×2。
        """
```

### 4.2 触发公式

```python
base_prob = 1.0 - impulsiveness  # 高冲动 → 低防御率

# 条件修饰乘法
condition_mask = 1.0
if self._is_self_threatened(review):       # 高 salience 自我认知被质疑 (→§4.1)
    condition_mask *= self._config["self_threat_boost"]  # 2.0
if error_history.get(error_type_name(review), 0) >= 2:  # 同类错误 ≥2
    condition_mask *= self._config["repeat_error_boost"] # 1.5
compound_delta = emotion_engine.last_compound_delta  # ← 从 EmotionEngine 实例读取
if abs(compound_delta) > 0.4:                         # 情绪剧烈波动
    condition_mask *= self._config["emotion_shock_boost"] # 2.0

final_prob = min(base_prob * condition_mask, 0.95)
```

**error_history 维护**: 由 `TurnManager._async_review_and_decide()` 维护，类型 `dict[str, int]`，key=ErrorType 字符串（如 `"fact_error"`）。每次审查完成后 `error_history[error_type] += 1`。DefenseEngine 只读不写。

**compound_delta 传递**: `EmotionEngine.tick()` 的步骤⑤检测到 `|Δcompound| > 0.4` 时，将最大 Δ 的维度名和值写入 `self.last_compound_delta: float`。DefenseEngine 从 `emotion_engine.last_compound_delta` 读取，无需订阅事件。

**防御类型选择**（按 type_weights 随机抽样）：

| 类型 | 权重 | 条件触发 |
|------|:---:|------|
| DENIAL | 0.35 | self_threat → ×2 |
| RATIONALIZE | 0.40 | repeat_error ≥ 2 → ×1.5 |
| PROJECT | 0.25 | emotion_shock → ×2 |

### 4.3 三种路径

#### DENIAL
```
- 不写 subconscious/corrections
- 写 self/defenses: "否认了关于{error_type}的错误，认为记忆可能不准确"
- 写 subconscious/defense_awareness: "[自我感知] 你之前有防御反应，拒绝了关于X的纠正"
  → 由 _init_messages() 下一轮读取并注入
- 情绪影响: guilt +0.05, resentment +0.02
- 沉默累积器: +1 (等同未纠正)
```

#### RATIONALIZE
```
- 写 subconscious/corrections + 解释文本: "{correction} (自我辩护: ...)"
- 归档 self/reflections: 结构化辩护记录
- 写 subconscious/defense_awareness: "[自我感知] 你意识到错误但仍做了合理化解释"
  → 由 _init_messages() 下一轮读取并注入
- 情绪影响: guilt -0.02 (减轻), gratification +0.03 (自洽)
- 沉默累积器: 不变 (已纠正, 但带了修饰)
```

#### PROJECT
```
- 写 subconscious/corrections + 偏向: "用户表达可能造成误解，后续注意澄清"
- EmotionEngine.accelerate("sub", "guilt", -0.05)
- EmotionEngine.accelerate("sub", "anger", +0.03)
- 写 subconscious/defense_awareness: "[自我感知] 你把部分错误归因于外部因素"
  → 由 _init_messages() 下一轮读取并注入
- 沉默累积器: 不变
```

---

## 5. 数据流集成

```
用户消息
  │
  ├─ 双脑 recall + inject (不变)
  │
  ├─ 子Session ReAct → send_reply → inner_thoughts (不变)
  │     │
  │     └─ _init_messages: 若前一轮启动了防御, 追加
  │          "[自我感知] 上一轮你对{defense_type}了关于{error}的反应"
  │
  ├─ 异步审查 _async_review_and_decide()
  │     │
  │     ├─ ReviewSystem.review() → ReviewResult (不变)
  │     │
  │     ├─ 防御判定 (新增):
  │     │     result = DefenseEngine.evaluate(
  │     │         review, self._error_history, self._emotion_engine, impulsiveness
  │     │     )
  │     │     self._error_history[error_type_str] += 1  ← 维护 error_history
  │     │
  │     ├─ DENIAL    → self/defenses + subconscious/defense_awareness
  │     ├─ RATIONALIZE → subconscious/corrections + self/reflections + subconscious/defense_awareness
  │     ├─ PROJECT   → subconscious/corrections(偏向) + emotion delta + subconscious/defense_awareness
  │     └─ DIRECT    → 正常写 subconscious/corrections
  │     │
  │     └─ 所有路径: 归档审查结果到 self/noticed
  │
  ├─ EmotionEngine.tick() (后台, 每 10s)
  │     ├─ ① 维度交互 → 更新 12 复合情绪
  │     ├─ ② 复合衰减
  │     ├─ ③ 基础衰减 (不变)
  │     ├─ ④ 复合跨脑传染
  │     └─ ⑤ compound_alert → event_bus
  │
  └─ 下一轮 get_emotion_summary() 含复合情绪文本
        "当前: joy=0.6 sadness=0.3 → 欣慰中带着失落"
```

---

## 6. 配置外化

```yaml
systems:
  emotion:
    # ... 现有配置不变 ...
    compound:
      enabled: true
      interaction_threshold: 0.3
      interaction_tick_coeff: 1.0
      decay_halflife_ratio: 0.5
    defense:
      enabled: true
      base_prob_formula: "1.0 - impulsiveness"
      condition_modifiers:
        self_threat_boost: 2.0
        repeat_error_boost: 1.5
        emotion_shock_boost: 2.0
      type_weights:
        denial: 0.35
        rationalize: 0.40
        projection: 0.25
```

---

## 7. 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `core/types.py` | EmotionState 新增 12 复合字段；+ DefenseResult dataclass；+ DefenseType enum | 数据结构 |
| `systems/emotion.py` | tick() 新增 ①②⑤；+ INTERACTION_MATRIX；+ `last_compound_delta: float` 实例变量；+ compound_alert；get_emotion_summary() 含复合文本；get_state() 含复合字段拷贝 | 核心—情绪 |
| `systems/defense.py` | **新建** — DefenseEngine: evaluate(), _is_self_threatened(), 三种防御路径 | 核心—防御 |
| `core/turn_manager.py` | _async_review_and_decide() 接入 DefenseEngine；_init_messages 读取 subconscious/defense_awareness 注入；维护 `_error_history: dict[str, int]` | 集成 |
| `config.yaml` | + systems.emotion.compound + systems.emotion.defense 段 | 配置 |
| `tests/test_compound_emotion.py` | **新建** — 交互矩阵、复合衰减、compound_alert、跨脑传染、last_compound_delta 传递 | 测试 |
| `tests/test_defense.py` | **新建** — 三种防御路径、概率公式、条件修饰叠加、沉默累积器联动、error_history 维护 | 测试 |

---

## 8. 成功标准

| ID | 标准 | 验证方式 |
|----|------|---------|
| SC-01 | 维度交互生成复合情绪 | joy=0.5, trust=0.5 → gratification > 0 after 10 ticks |
| SC-02 | 复合衰减曲线正确 | gratification 峰值后按半衰期 1050s×0.5=525s 衰减 |
| SC-03 | 复合跨脑传染 | sub.gratification 被 logic.gratification 以 contagion_strength 拉扯 |
| SC-04 | compound_alert 发布 | \|Δcompound\| > 0.4 → event_bus 收到 compound_alert 事件 |
| SC-05 | DENIAL 路径：不写 correction | self_threat 条件触发 → subconscious/corrections 无新条目 |
| SC-06 | RATIONALIZE 路径：correction 含解释 | repeat_error ≥2 → correction 文本含"自我辩护" |
| SC-07 | PROJECT 路径：情绪偏移 | emotion_shock → sub.guilt 减少, sub.anger 增加 |
| SC-08 | 基线概率公式正确 | impulsiveness=0.8 → probs≈0.2; impulsiveness=0.1 → probs≈0.9 |
| SC-09 | 条件修饰乘法叠加 | self_threat + repeat_error → boost 2.0×1.5 均生效 |
| SC-10 | 沉默累积器正确联动 | RATIONALIZE/PROJECT → 不累计；DENIAL → 累计 |
| SC-11 | 现有 154 tests 零回归 | `pytest tests/ -q` 全部通过 |
| SC-12 | 配置开关 | compound.enabled=false → 无复合生成；defense.enabled=false → 无防御介入 |
| SC-13 | 新增测试 ≥ 10 条 | pytest count 验证 |
| SC-14 | 脆弱感 — 极端情绪触发 | compound ≥ 0.7 → 触发脆弱行为调制 |
| SC-15 | 脆弱 — 关系安全门 | 陌生人/熟人 → 不触发脆弱 |
| SC-16 | 脆弱 — 防御骤降 | defense_prob ×0.3 |
| SC-17 | 脆弱 — 自我暴露率飙升 | self_disclosure ×2.0 |
| SC-18 | 脆弱 — narrative 联动 | 暴露后 Spec 010 chapters 追加 |

---

## 9. 脆弱感

> **Design Extension**: 基于情绪极端化实现脆弱感——不新增独立 Spec。
> **触发**: brainstorming final-review 2026-07-10，覆盖差距 #17。

### 9.1 触发条件

任一复合情绪达到"极端"（≥ 0.7）：

| 复合情绪 | 触发文本示例 |
|----------|------------|
| guilt ≥ 0.7 | "我做错了很多事...我可能不够好" |
| anxiety ≥ 0.7 | "我害怕失去这段关系" |
| sadness ≥ 0.7 | "最近真的好难" |
| disappointment ≥ 0.7 | "我对自己有点失望" |
| resentment ≥ 0.7 | "我一直忍着没说, 但..." |
| bittersweet ≥ 0.7 | "有些话再不说可能没机会了" |

### 9.2 关系安全门

仅对 `friend` 或 `close_friend`（Spec 008）触发。陌生人面前不暴露脆弱。若情绪积累了但面对陌生人无处释放 → 动机系统（Spec 011）产生 `vent` 驱动 → 之后找密友私聊发泄。

### 9.3 脆弱行为调制（仅本轮生效）

```
防御概率: ×0.3 (几乎不设防)
自我暴露率: ×2.0 (说出平时不会说的话)

inner_thoughts 注入: "[脆弱时刻] 你现在处于情绪脆弱状态"

system prompt 注入:
  "[脆弱时刻] 你现在情绪很强烈 ({emotion_name}达到{value})。
   你比平时更愿意分享真实感受。可以诚实表达你的不安、恐惧、或遗憾。
   但不要说教或过度分析——这是感受的时刻，不是分析的时刻。"
```

### 9.4 后效应

下一轮 `_init_messages()` 注入：

```
"[脆弱回忆] 上一轮你在这段关系中暴露了脆弱 ({emotion_name})。
 你现在可能感到更亲近，也可能有点尴尬——取决于对方的反应。"
```

**联动**：
- Spec 008：对方以温暖回应 → closeness +0.05
- Spec 010：narrative chapters 追加 "我在她面前表现出了脆弱——这对我来说不容易。"
- Spec 011：脆弱后未释放 → vent 驱动
- Spec 011：拒绝触发脆弱（面对陌生人）→ 情绪累积 → vent 冲动物化

### 9.5 配置

```yaml
# 追加到 config.yaml → systems.emotion
    vulnerability:
      enabled: true
      thresholds:
        guilt: 0.7
        anxiety: 0.7
        sadness: 0.7
        disappointment: 0.7
        resentment: 0.7
        bittersweet: 0.7
      modulation:
        defense_prob: 0.3
        self_disclosure: 2.0
      min_relationship_stage: friend
      cooldown_turns: 5
```

### 9.6 改动文件

| 文件 | 改动 | 说明 |
|------|------|------|
| `systems/emotion.py` | `tick()` 步骤⑤后新增 `_check_vulnerability()` | 检测 |
| `core/turn_manager.py` | `_init_messages` 注入脆弱调制 + 后效应记忆 | 集成 |
| `systems/defense.py` | `DefenseEngine` 读取脆弱标志 → 防御 ×0.3 | 消费 |
| `systems/narrative.py` (Spec 010) | 脆弱事件 → chapters 追加 | 消费 |
| `systems/motivation.py` (Spec 011) | 脆弱拒绝(陌生人) → vent 驱动 | 消费 |
| `config.yaml` | + `systems.emotion.vulnerability` 段 | 配置 |
