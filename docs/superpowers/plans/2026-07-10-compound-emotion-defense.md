# Spec 005 复合情绪 + 防御机制 + 脆弱感 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。

**目标：** 实现 12 维复合情绪（交互矩阵生成 + 衰减 + 传染）、三种心理防御机制（DENIAL/RATIONALIZE/PROJECT）和脆弱感系统（极端情绪触发 + 行为调制）。

**架构：** 新建 `systems/defense.py`（DefenseEngine），扩展 `EmotionState` 12 字段 + INTERACTION_MATRIX 在 `tick()` 中生成复合情绪，`_async_review_and_decide()` 接入防御判定管线，脆弱感通过 compound_alert 联动防御和系统提示词。

**技术栈：** Python 3.12+, asyncio, dataclass, Enum

**设计文档：** `docs/superpowers/specs/2026-07-10-compound-emotion-defense-design.md`

---

## 当前基线

| 组件 | 状态 |
|------|:---:|
| EmotionState (10 基础维度) | ✅ |
| EmotionEngine.tick() (衰减+传染) | ✅ |
| _async_review_and_decide() 异步审查管线 | ✅ |
| _issue_correction() 写 subconscious/corrections | ✅ |
| 注意力状态机集成 (emotion_alert) | ✅ |
| 复合情绪 12 维 | ❌ 本节实现 |
| INTERACTION_MATRIX | ❌ 本节实现 |
| DefenseEngine 三种防御 | ❌ 本节实现 |
| 脆弱感触发+调制 | ❌ 本节实现 |

---

## 任务列表

### 阶段 1：基础层（类型 + 配置）

- [ ] **任务 1：EmotionState 扩展 12 复合字段 + DefenseType/DefenseResult**
- [ ] **任务 2：config.yaml 新增 compound/defense/vulnerability 段**

**检查点：任务 1-2 之后**
- [ ] `python -c "from chat_core.core.types import EmotionState; s=EmotionState(); print(s.bittersweet, s.gratification)"` → `0.0 0.0`
- [ ] `python -c "from chat_core.core.types import DefenseType; print(DefenseType.DENIAL.value)"` → `denial`

---

### 阶段 2：复合情绪核心

- [ ] **任务 3：INTERACTION_MATRIX + tick() 步骤① 维度交互**
- [ ] **任务 4：复合情绪衰减 (tick 步骤②) + 跨脑传染扩展 (步骤④)**
- [ ] **任务 5：compound_alert 检测 (tick 步骤⑤) + get_emotion_summary 复合文本**

**检查点：任务 3-5 之后**
- [ ] `python -m pytest tests/test_compound_emotion.py -v` 全部通过
- [ ] `python -m pytest tests/test_phase6_emotion.py -v` 现有测试零回归

---

### 阶段 3：防御机制

- [ ] **任务 6：新建 `systems/defense.py` — DefenseEngine + 三种防御路径**
- [ ] **任务 7：`turn_manager.py` — `_async_review_and_decide()` 接入 DefenseEngine**
- [ ] **任务 8：`loop.py` — `_init_messages` 读取 subconscious/defense_awareness**

**检查点：任务 6-8 之后**
- [ ] `python -m pytest tests/test_defense.py -v` 全部通过
- [ ] `python -m pytest tests/test_design_alignment.py -v` 现有测试零回归

---

### 阶段 4：脆弱感

- [ ] **任务 9：`emotion.py` — `_check_vulnerability()` 极端情绪检测**
- [ ] **任务 10：`turn_manager.py` — 脆弱行为调制 + 后效应记忆注入**
- [ ] **任务 11：`defense.py` — DefenseEngine 读取脆弱标志 → 防御 ×0.3**

**检查点：任务 9-11 之后**
- [ ] `python -m pytest tests/ -q` 全量零回归 (175 baseline + 新增)

---

## 详细任务

---

### 任务 1：EmotionState 扩展 + DefenseType/DefenseResult

**文件：**
- 修改：`chat_core/core/types.py`

**描述：** 在 `EmotionState` 中新增 12 个复合情绪字段（全部默认 0.0）；新增 `DefenseType` 枚举和 `DefenseResult` dataclass。

- [ ] **步骤 1：扩展 EmotionState**

在 `last_tick` 字段后追加 12 个复合情绪：

```python
    # Spec 005: 12 维复合情绪
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

- [ ] **步骤 2：新增 DefenseType + DefenseResult**

在 `EmotionState` 之后追加：

```python
# ── Spec 005: 防御机制 ──────────────────────────────────────────

class DefenseType(Enum):
    DIRECT = "direct"           # 无防御，直接纠正
    DENIAL = "denial"           # 否认：不写 correction
    RATIONALIZE = "rationalize" # 合理化：写 correction + 辩护
    PROJECT = "project"         # 投射：归因转向用户


@dataclass
class DefenseResult:
    defense_type: DefenseType
    correction_text: str | None = None       # 写入 corrections 的文本 (DENIAL 为 None)
    inner_reflection: str = ""               # self/defenses 归档
    defense_awareness: str = ""              # subconscious/defense_awareness
    emotion_delta: dict[str, float] = field(default_factory=dict)
    silence_increment: int = 0               # DENIAL → 1, 其余 → 0
```

- [ ] **步骤 3：验证导入**

```bash
python -c "from chat_core.core.types import EmotionState, DefenseType, DefenseResult; s=EmotionState(); print(s.bittersweet, DefenseType.DENIAL)"
```

**预估规模：** S

---

### 任务 2：config.yaml 新增 compound/defense/vulnerability

**文件：**
- 修改：`chat_core/config.yaml`

**描述：** 在 `systems.emotion` 下追加 `compound`、`defense`、`vulnerability` 三段。

- [ ] **步骤 1：追加配置**

在 `systems.emotion` 段末尾（`introspect_threshold` 之后）追加：

```yaml
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

- [ ] **步骤 2：EmotionEngine.__init__ 读取新配置**

```python
# compound
cc = ec.get("compound", {})
self._compound_enabled: bool = bool(cc.get("enabled", True))
self._interaction_threshold: float = float(cc.get("interaction_threshold", 0.3))
self._interaction_tick_coeff: float = float(cc.get("interaction_tick_coeff", 1.0))
self._decay_halflife_ratio: float = float(cc.get("decay_halflife_ratio", 0.5))
# defense (DefenseEngine 读取)
# vulnerability
self._vulnerability_enabled: bool = ...
self._vulnerability_thresholds: dict[str, float] = ...
```

- [ ] **步骤 3：验证 YAML**

```bash
python -c "import yaml; yaml.safe_load(open('chat_core/config.yaml')); print('OK')"
```

**预估规模：** S

---

### 任务 3：INTERACTION_MATRIX + tick() 维度交互

**文件：**
- 修改：`chat_core/systems/emotion.py`
- 创建：`tests/test_compound_emotion.py`

**描述：** 定义 `COMPOUND_DIMS` 列表 + `INTERACTION_MATRIX` 字典。tick() 新增步骤①：遍历矩阵，双维度均 ≥ threshold 时，compound += min(A,B) × coeff × tick_coeff。

- [ ] **步骤 1：定义常量**

```python
COMPOUND_DIMS = [
    "bittersweet", "guilt", "anxiety", "contempt",
    "gratification", "disappointment", "envy", "pride",
    "resentment", "awe", "nostalgia", "bewilderment",
]

INTERACTION_MATRIX: dict[str, list[tuple[str, float, str | None]]] = {
    "joy": [
        ("sadness", 0.02, "bittersweet"),
        ("trust", 0.03, "gratification"),
        ("anticipation", 0.03, "pride"),
        ("interest", 0.02, None),            # joy × interest → 泛化愉悦（不单独成维度）
    ],
    "sadness": [
        ("joy", 0.02, "bittersweet"),
        ("fear", 0.03, "guilt"),
        ("anger", 0.02, "resentment"),
        ("surprise", 0.03, "disappointment"),
        ("interest", 0.02, "nostalgia"),
        ("anticipation", 0.01, "envy"),      # 预期他人比自己好 → 嫉妒
    ],
    "anger": [
        ("disgust", 0.03, "contempt"),
        ("sadness", 0.02, "resentment"),
    ],
    "fear": [
        ("anticipation", 0.03, "anxiety"),
        ("sadness", 0.02, "guilt"),
        ("confusion", 0.03, "bewilderment"),
        ("surprise", 0.02, "awe"),
        ("trust", 0.01, "awe"),              # awe = fear + surprise + trust 组合
    ],
    "anticipation": [
        ("fear", 0.03, "anxiety"),
        ("joy", 0.03, "pride"),
    ],
    "trust": [
        ("joy", 0.03, "gratification"),
        ("fear", 0.01, "awe"),
    ],
    "disgust": [
        ("anger", 0.03, "contempt"),
    ],
    "surprise": [
        ("sadness", 0.03, "disappointment"),
        ("fear", 0.02, "awe"),
    ],
    "confusion": [
        ("fear", 0.03, "bewilderment"),
    ],
    "interest": [
        ("joy", 0.02, "nostalgia"),
        ("sadness", 0.02, "nostalgia"),
    ],
}
```

- [ ] **步骤 2：tick() 新增步骤①**

在现有 `tick()` 的衰减和传染之前插入：

```python
        # ① 维度交互 → 生成复合情绪
        if self._compound_enabled:
            for brain_name in BRAIN_NAMES:
                state = self._states[brain_name]
                for dim_a, interactions in INTERACTION_MATRIX.items():
                    a_val = getattr(state, dim_a, 0.0)
                    if a_val < self._interaction_threshold:
                        continue
                    for dim_b, coeff, compound_name in interactions:
                        b_val = getattr(state, dim_b, 0.0)
                        if b_val < self._interaction_threshold:
                            continue
                        if compound_name:
                            contribution = min(a_val, b_val) * coeff * self._interaction_tick_coeff
                            current = getattr(state, compound_name, 0.0)
                            setattr(state, compound_name, min(1.0, current + contribution))
```

- [ ] **步骤 3：编写测试**

```python
# tests/test_compound_emotion.py
class TestCompoundEmotion:
    def test_interaction_matrix_generates_gratification(self):
        """joy=0.5 trust=0.5 → gratification > 0 after tick"""
        engine = EmotionEngine()
        engine._states["sub"].joy = 0.5
        engine._states["sub"].trust = 0.5
        engine.tick()
        assert engine._states["sub"].gratification > 0

    def test_interaction_below_threshold_no_effect(self):
        """dim < 0.3 → 不触发交互"""
        engine = EmotionEngine()
        engine._states["sub"].joy = 0.2
        engine._states["sub"].trust = 0.5
        engine.tick()
        assert engine._states["sub"].gratification == 0.0

    def test_disabled_compound_no_generation(self):
        """compound.enabled=false → 无复合生成"""
        engine = EmotionEngine()
        engine._compound_enabled = False
        engine._states["sub"].joy = 0.8
        engine._states["sub"].trust = 0.8
        engine.tick()
        assert engine._states["sub"].gratification == 0.0
```

- [ ] **步骤 4：运行测试**

```bash
python -m pytest tests/test_compound_emotion.py -v
```

**预估规模：** M

---

### 任务 4：复合情绪衰减 + 跨脑传染扩展

**文件：**
- 修改：`chat_core/systems/emotion.py`

**描述：** tick() 步骤②：对 12 复合维度施加指数衰减（半衰期 = 构成维均值 × decay_halflife_ratio）；步骤④：跨脑传染扩展到 10+12 共 22 个维度。

- [ ] **步骤 1：计算复合半衰期**

```python
def _compound_half_lives(self) -> dict[str, float]:
    """计算 12 复合维度的半衰期"""
    from itertools import chain
    base_map: dict[str, list[str]] = {}
    for dim_a, interactions in INTERACTION_MATRIX.items():
        for dim_b, coeff, compound_name in interactions:
            if compound_name and compound_name not in base_map:
                base_map[compound_name] = list(dict.fromkeys([dim_a, dim_b]))
    # 手动补充 awe (3 维) 和 nostalgia (3 维)
    base_map["awe"] = ["fear", "surprise", "trust"]
    base_map["nostalgia"] = ["joy", "sadness", "interest"]

    result = {}
    for comp, bases in base_map.items():
        avg = sum(self._half_lives.get(b, 600) for b in bases) / len(bases)
        result[comp] = avg * self._decay_halflife_ratio
    return result
```

- [ ] **步骤 2：tick() 步骤② — 复合衰减**

```python
        # ② 复合情绪衰减
        if self._compound_enabled:
            comp_halves = self._compound_half_lives()
            for brain_name in BRAIN_NAMES:
                state = self._states[brain_name]
                dt = now - state.last_tick.timestamp()
                if dt <= 0:
                    continue
                for dim in COMPOUND_DIMS:
                    current = getattr(state, dim, 0.0)
                    hl = comp_halves.get(dim, 600)
                    if hl <= 0:
                        continue
                    decayed = current * (2 ** (-dt / hl))
                    setattr(state, dim, max(0.0, decayed))
```

- [ ] **步骤 3：扩展跨脑传染到复合维度**

在现有传染循环中增加复合维度：

```python
        # ④ 跨脑传染 (扩展: 10 基础 + 12 复合)
        all_dims = EMOTION_DIMS + (COMPOUND_DIMS if self._compound_enabled else [])
        for from_brain, to_brain in CONTAGION_FLOW:
            from_state = self._states[from_brain]
            to_state = self._states[to_brain]
            for dim in all_dims:
                from_val = getattr(from_state, dim, 0.0)
                to_val = getattr(to_state, dim, 0.0)
                delta = (from_val - to_val) * self._contagion_strength
                setattr(to_state, dim, _clamp(to_val + delta))
```

- [ ] **步骤 4：扩展 `set_dimension`/`accelerate` 支持复合维度**

防御机制需要操作复合情绪（如 `guilt`, `gratification`）。修改 `set_dimension` 的校验：

```python
def set_dimension(self, brain: str, dim: str, value: float) -> None:
    if brain not in self._states:
        raise ValueError(...)
    if dim not in EMOTION_DIMS and dim not in COMPOUND_DIMS:
        raise ValueError(f"Unknown dimension: {dim}")
    setattr(self._states[brain], dim, _clamp(value))
```

`accelerate()` 无需修改——它调用 `set_dimension`，自动获得复合维度支持。

- [ ] **步骤 5：`get_state()` 包含复合字段**

在 `EmotionEngine.get_state()` 返回的 `EmotionState(brain=..., ...)` 中增加复合字段拷贝：

```python
bittersweet=self._states[brain].bittersweet,
guilt=self._states[brain].guilt,
# ... 全部 12 个
```

- [ ] **步骤 5：运行测试**

```bash
python -m pytest tests/test_compound_emotion.py -v
```

**预估规模：** M

---

### 任务 5：compound_alert 检测 + get_emotion_summary 复合文本

**文件：**
- 修改：`chat_core/systems/emotion.py`

**描述：** tick() 步骤⑤检测 |Δcompound| > 0.4 → 写 `last_compound_delta` → 发布 `compound_alert`。`get_emotion_summary()` 输出含复合情绪文本。

- [ ] **步骤 1：tick() 步骤⑤**

```python
        # ⑤ compound_alert 检测
        if self._compound_enabled:
            for brain_name in BRAIN_NAMES:
                state = self._states[brain_name]
                max_delta = 0.0
                for dim in COMPOUND_DIMS:
                    current = getattr(state, dim, 0.0)
                    prev = getattr(self, '_prev_compound', {}).get(f"{brain_name}_{dim}", current)
                    delta = abs(current - prev)
                    if delta > max_delta:
                        max_delta = delta
                self.last_compound_delta = max_delta
                if max_delta > 0.4 and self._event_bus:
                    try:
                        loop = asyncio.get_running_loop()
                        loop.call_soon_threadsafe(
                            lambda d=max_delta, bn=brain_name: asyncio.ensure_future(
                                self._event_bus.publish("compound_alert", {
                                    "delta": d, "brain": bn,
                                })
                            )
                        )
                    except RuntimeError:
                        pass
            # 保存当前值为 prev
            if not hasattr(self, '_prev_compound'):
                self._prev_compound: dict[str, float] = {}
            for brain_name in BRAIN_NAMES:
                for dim in COMPOUND_DIMS:
                    self._prev_compound[f"{brain_name}_{dim}"] = getattr(
                        self._states[brain_name], dim, 0.0
                    )
```

- [ ] **步骤 2：`get_emotion_summary()` 扩展**

```python
def get_emotion_summary(self, brain: str) -> str:
    state = self._states.get(brain)
    if state is None:
        return ""
    parts = []
    for dim in EMOTION_DIMS:
        val = getattr(state, dim, 0.0)
        if val > 0.01:
            parts.append(f"{dim}={val:.2f}")
    # 复合情绪
    compound_parts = []
    for dim in COMPOUND_DIMS:
        val = getattr(state, dim, 0.0)
        if val > 0.01:
            compound_parts.append(f"{dim}={val:.2f}")
    base = ", ".join(parts) if parts else "neutral"
    if compound_parts:
        base += " | " + ", ".join(compound_parts)
    return base
```

- [ ] **步骤 3：`EmotionEngine.__init__` 新增实例变量**

```python
self.last_compound_delta: float = 0.0
```

**预估规模：** M

---

### 任务 6：新建 `systems/defense.py` — DefenseEngine

**文件：**
- 创建：`chat_core/systems/defense.py`
- 创建：`tests/test_defense.py`

**描述：** 实现 `DefenseEngine` 类：`evaluate()` 方法计算 `base_prob = 1.0 - impulsiveness` × 条件修饰 → 选择防御类型 → 返回 `DefenseResult`。

- [ ] **步骤 1：实现 DefenseEngine 骨架**

```python
"""DefenseEngine — 心理防御机制：DENIAL / RATIONALIZE / PROJECT"""

from __future__ import annotations
import random
from chat_core.config import get_config
from chat_core.core.types import DefenseResult, DefenseType, ReviewResult


class DefenseEngine:
    def __init__(self) -> None:
        cfg = get_config()
        dc = cfg.emotion_config().get("defense", {})
        self._enabled: bool = bool(dc.get("enabled", True))
        mods = dc.get("condition_modifiers", {})
        self._self_threat_boost: float = float(mods.get("self_threat_boost", 2.0))
        self._repeat_error_boost: float = float(mods.get("repeat_error_boost", 1.5))
        self._emotion_shock_boost: float = float(mods.get("emotion_shock_boost", 2.0))
        tw = dc.get("type_weights", {})
        self._type_weights = {
            DefenseType.DENIAL: float(tw.get("denial", 0.35)),
            DefenseType.RATIONALIZE: float(tw.get("rationalize", 0.40)),
            DefenseType.PROJECT: float(tw.get("projection", 0.25)),
        }
        self._vulnerability_defense_mod: float = 0.3  # from vulnerability config

    def evaluate(
        self,
        review: ReviewResult,
        error_history: dict[str, int],
        impulsiveness: float,
        last_compound_delta: float = 0.0,
        is_vulnerable: bool = False,
    ) -> DefenseResult:
        """返回防御判定结果"""
        if not self._enabled or review.decision.value == "silence":
            return DefenseResult(defense_type=DefenseType.DIRECT)
        # base_prob
        base_prob = max(0.0, 1.0 - impulsiveness)
        # 条件修饰
        modifier = 1.0
        if self._is_self_threatened(review):
            modifier *= self._self_threat_boost
        for count in error_history.values():
            if count >= 2:
                modifier *= self._repeat_error_boost
                break
        if abs(last_compound_delta) > 0.4:
            modifier *= self._emotion_shock_boost
        # 脆弱感调制
        if is_vulnerable:
            modifier *= self._vulnerability_defense_mod
        final_prob = min(base_prob * modifier, 0.95)
        if random.random() > final_prob:
            return DefenseResult(defense_type=DefenseType.DIRECT)
        # 按权重选择防御类型
        return self._select_defense(review, error_history)

    def _is_self_threatened(self, review: ReviewResult, memory_store: Any = None) -> bool:
        """判定审查错误是否威胁自我认知。
        
        FactError.conflicting_memory_key 格式为 "namespace/key"。
        检查是否属于 self/* 命名空间。
        若 memory_store 可用，进一步检查对应 memory entry 的 salience ≥ 7。
        """
        for e in review.logic_errors:
            key = e.conflicting_memory_key
            if not key:
                continue
            # conflicting_memory_key 格式: "namespace/key" 或直接就是 key
            if key.startswith("self/"):
                if memory_store is None:
                    return True  # 无 DB 引用时保守判定为威胁
                # 尝试查询 salience
                try:
                    # 同步版本: 无法在同步方法中 await，降级为字符串判定
                    # 若 key 是 self/feelings/* 或 self/inner_thoughts/* → 高 salience
                    return True  # 简化：self/* 命名空间统一视为高 salience
                except Exception:
                    return True
        return False

    def _select_defense(self, review: ReviewResult, error_history: dict[str, int], memory_store: Any = None) -> DefenseResult:
        """按 type_weights 随机抽样防御类型，构造 DefenseResult"""
        types = list(self._type_weights.keys())
        weights = list(self._type_weights.values())
        # 条件加权
        if self._is_self_threatened(review, memory_store):
            idx = types.index(DefenseType.DENIAL)
            weights[idx] *= self._self_threat_boost
        if any(c >= 2 for c in error_history.values()):
            idx = types.index(DefenseType.RATIONALIZE)
            weights[idx] *= self._repeat_error_boost
        chosen = random.choices(types, weights=weights, k=1)[0]
        return self._build_result(chosen, review)

    def _build_result(self, defense_type: DefenseType, review: ReviewResult) -> DefenseResult:
        """构造 DefenseResult"""
        errors = [e.description for e in review.logic_errors[:2]]
        error_str = "；".join(errors) if errors else "审查发现问题"
        if defense_type == DefenseType.DENIAL:
            return DefenseResult(
                defense_type=DefenseType.DENIAL,
                correction_text=None,
                inner_reflection=f"否认了关于{error_str}的错误，认为记忆可能不准确",
                defense_awareness=f"[自我感知] 你之前有防御反应，拒绝了关于{error_str}的纠正",
                emotion_delta={"guilt": 0.05, "resentment": 0.02},
                silence_increment=1,
            )
        elif defense_type == DefenseType.RATIONALIZE:
            return DefenseResult(
                defense_type=DefenseType.RATIONALIZE,
                correction_text=f"{error_str} (自我辩护: 这种情况很复杂，不完全是我的错)",
                inner_reflection=f"意识到了{error_str}，但做了合理化解释",
                defense_awareness=f"[自我感知] 你意识到{error_str}但仍做了合理化解释",
                emotion_delta={"guilt": -0.02, "gratification": 0.03},
                silence_increment=0,
            )
        else:  # PROJECT
            return DefenseResult(
                defense_type=DefenseType.PROJECT,
                correction_text=f"用户表达可能造成误解（原问题: {error_str}），后续注意澄清",
                inner_reflection="把部分错误归因于外部因素",
                defense_awareness="[自我感知] 你把部分错误归因于外部因素",
                emotion_delta={"guilt": -0.05, "anger": 0.03},
                silence_increment=0,
            )
```

- [ ] **步骤 2：编写测试**

```python
# tests/test_defense.py
class TestDefenseEngine:
    def test_direct_when_high_impulsiveness(self):
        """impulsiveness=0.9 → base_prob≈0.1 → 大概率 DIRECT"""
        engine = DefenseEngine()
        review = ReviewResult(logic_errors=[...])
        result = engine.evaluate(review, {}, impulsiveness=0.9)
        assert result.defense_type == DefenseType.DIRECT

    def test_denial_writes_no_correction(self):
        """DENIAL → correction_text is None"""
        ...

    def test_rationalize_correction_contains_defense(self):
        """RATIONALIZE → correction_text 含 '自我辩护'"""
        ...

    def test_project_shifts_emotion(self):
        """PROJECT → emotion_delta 含 guilt-0.05 anger+0.03"""
        ...

    def test_self_threat_boosts_denial(self):
        """self_threat → DENIAL 权重 ×2"""
        ...
```

**预估规模：** M

---

### 任务 7：turn_manager.py — _async_review_and_decide() 接入 DefenseEngine

**文件：**
- 修改：`chat_core/core/turn_manager.py`

**描述：** 在 `_async_review_and_decide()` 中，审查完成后调用 `DefenseEngine.evaluate()`，按 `DefenseResult` 决定后续路径。维护 `self._error_history: dict[str, int]`。

- [ ] **步骤 1：__init__ 初始化 DefenseEngine + error_history**

```python
from chat_core.systems.defense import DefenseEngine
from chat_core.systems.emotion import COMPOUND_DIMS
# __init__:
self._defense_engine = DefenseEngine()
self._error_history: dict[str, int] = {}
```

- [ ] **步骤 2：_async_review_and_decide() 插入防御判定**

在 `review = await self._review(...)` 之后、原有 correction 逻辑之前插入：

```python
            # ── Spec 005: 防御判定 ──
            impulsiveness = (
                self._personality_engine.weights.impulsiveness
                if self._personality_engine else 0.2
            )
            # 更新 error_history
            for e in review.logic_errors:
                error_type_str = e.error_type.value if hasattr(e.error_type, 'value') else str(e.error_type)
                self._error_history[error_type_str] = self._error_history.get(error_type_str, 0) + 1

            compound_delta = (
                self._emotion_engine.last_compound_delta
                if self._emotion_engine else 0.0
            )
            defense = self._defense_engine.evaluate(
                review, self._error_history,
                impulsiveness=impulsiveness,
                last_compound_delta=compound_delta,
            )
            if defense.defense_type != DefenseType.DIRECT:
                await self._apply_defense(defense, review, replies)
                return  # 防御路径短路正常纠正流
```

- [ ] **步骤 3：新增 _apply_defense()**

```python
async def _apply_defense(
    self, defense: DefenseResult, review: ReviewResult, replies: list[str]
) -> None:
    """执行防御裁定"""
    turn_id = self._current_turn.turn_id if self._current_turn else "unknown"
    # 归档 defense
    await self._memory.save(MemoryEntry(
        namespace="self/defenses",
        key=f"defense_{turn_id}",
        value={
            "defense_type": defense.defense_type.value,
            "reflection": defense.inner_reflection,
            "original_errors": [e.description for e in review.logic_errors],
        },
    ))
    # 写入 defense_awareness（下一轮 _init_messages 读取）
    if defense.defense_awareness:
        await self._memory.save(MemoryEntry(
            namespace="subconscious/defense_awareness",
            key=f"awareness_{turn_id}",
            value={"text": defense.defense_awareness, "defense_type": defense.defense_type.value},
        ))
    # DENIAL: 不写 correction
    # RATIONALIZE/PROJECT: 写 correction
    if defense.correction_text:
        await self._memory.save(MemoryEntry(
            namespace="subconscious/corrections",
            key=f"correction_{turn_id}",
            value={
                "logic_errors": [e.description for e in review.logic_errors],
                "combined_weight": review.combined_weight,
                "defense_note": defense.correction_text,
            },
        ))
    # 情绪调整 (支持复合维度 — set_dimension 已扩展)
    if defense.emotion_delta and self._emotion_engine:
        for dim, delta in defense.emotion_delta.items():
            try:
                self._emotion_engine.accelerate("sub", dim, delta)
            except ValueError:
                pass  # 未知维度静默跳过
    # 沉默累积器
    if defense.silence_increment > 0:
        self._silence_accumulator.increment("defense_denial")
```

**预估规模：** M

---

### 任务 8：`loop.py` — `_init_messages` 读取 subconscious/defense_awareness

**文件：**
- 修改：`chat_core/core/loop.py`

**描述：** 在 `_inject_subconscious_corrections()` 中同时查询 `subconscious/defense_awareness`，注入为系统消息。

- [ ] **步骤 1：扩展 `_inject_subconscious_corrections()`**

在查询 `subconscious/corrections` 之后追加：

```python
            # Spec 005: 读取 defense_awareness
            awareness_entries = await self._memory_store.query("subconscious/defense_awareness")
            for entry in awareness_entries:
                value = entry.value
                text = value.get("text", "") if isinstance(value, dict) else str(value)
                if text:
                    self._messages.insert(
                        -1,
                        Message(role="system", content=text),
                    )
```

**预估规模：** XS

---

### 任务 9：emotion.py — _check_vulnerability() 极端情绪检测

**文件：**
- 修改：`chat_core/systems/emotion.py`

**描述：** 在 `tick()` 步骤⑤之后检测任一复合情绪 ≥ 0.7 → 标记 `self.is_vulnerable = True`。设置冷却计数器 `self._vulnerability_cooldown`。

- [ ] **步骤 1：实现 `_check_vulnerability()`**

```python
def _check_vulnerability(self) -> bool:
    """检测是否处于脆弱状态"""
    if not self._vulnerability_enabled:
        return False
    if self._vulnerability_cooldown > 0:
        self._vulnerability_cooldown -= 1
        return False
    for brain_name in ["sub"]:
        state = self._states[brain_name]
        for dim, threshold in self._vulnerability_thresholds.items():
            if getattr(state, dim, 0.0) >= threshold:
                self.is_vulnerable = True
                self._vulnerability_cooldown = self._vulnerability_cooldown_max
                return True
    self.is_vulnerable = False
    return False
```

- [ ] **步骤 2：__init__ 初始化脆弱状态**

```python
self.is_vulnerable: bool = False
self._vulnerability_cooldown: int = 0
self._vulnerability_cooldown_max: int = 5
```

**预估规模：** S

---

### 任务 10：turn_manager.py — 脆弱行为调制 + 后效应记忆

**文件：**
- 修改：`chat_core/core/turn_manager.py`

**描述：** 在 `process_turn()` 的 `_init_messages` 前检测脆弱状态 → 注入脆弱提示 + 后效应记忆。

- [ ] **步骤 1：`_run_sub_session()` 中注入脆弱提示**

在构建 `system_prompt` 之前检查 `emotion_engine.is_vulnerable`：

```python
# Spec 005: 脆弱时刻 system prompt 注入
if self._emotion_engine and self._emotion_engine.is_vulnerable:
    # 找出触发脆弱的复合情绪
    sub_state = self._emotion_engine.get_state("sub")
    vuln_emotion = ""
    for dim in COMPOUND_DIMS:
        if getattr(sub_state, dim, 0.0) >= 0.7:
            vuln_emotion = dim
            break
    runtime_state["vulnerability"] = (
        f"[脆弱时刻] 你现在情绪很强烈（{vuln_emotion}达到高水平）。"
        "你比平时更愿意分享真实感受。可以诚实表达你的不安、恐惧、或遗憾。"
        "但不要说教或过度分析——这是感受的时刻，不是分析的时刻。"
    )
    # self_disclosure ×2.0: 临时提升 empathy 和 playfulness，使回复更开放
    if self._personality_engine:
        original_empathy = self._personality_engine.weights.empathy
        original_playfulness = self._personality_engine.weights.playfulness
        self._personality_engine.weights.empathy = min(1.0, original_empathy * 2.0)
        self._personality_engine.weights.playfulness = min(1.0, original_playfulness * 2.0)
        # 记录原始值用于恢复
        runtime_state["_vuln_orig_empathy"] = original_empathy
        runtime_state["_vuln_orig_playfulness"] = original_playfulness
```

- [ ] **步骤 2：process_turn() 末尾注入后效应**

```python
# Spec 005: 脆弱后效应（下一轮注入）
if self._emotion_engine and self._emotion_engine.is_vulnerable:
    vuln_emotion = "..."  # 同上方法获取
    await self._memory.save(MemoryEntry(
        namespace="subconscious/defense_awareness",
        key=f"vulnerability_aftermath_{turn.turn_id}",
        value={
            "text": f"[脆弱回忆] 上一轮你在这段关系中暴露了脆弱（{vuln_emotion}）。你现在可能感到更亲近，也可能有点尴尬——取决于对方的反应。",
            "type": "vulnerability_aftermath",
        },
    ))
    self._emotion_engine.is_vulnerable = False  # 重置
    # 恢复人格权重 (self_disclosure 调制清除)
    if self._personality_engine and "_vuln_orig_empathy" in runtime_state:
        self._personality_engine.weights.empathy = runtime_state["_vuln_orig_empathy"]
        self._personality_engine.weights.playfulness = runtime_state["_vuln_orig_playfulness"]
```

**预估规模：** S

---

### 任务 11：defense.py — DefenseEngine 读取脆弱标志

**文件：**
- 修改：`chat_core/systems/defense.py`

**描述：** `evaluate()` 中 `is_vulnerable=True` → `modifier *= 0.3`（防御概率骤降）。

已在任务 6 的 `evaluate()` 实现中包含（`if is_vulnerable: modifier *= self._vulnerability_defense_mod`）。此任务为验证 + 测试。

- [ ] **步骤 1：验证测试**

```python
def test_vulnerability_reduces_defense(self):
    """脆弱状态下防御概率 ×0.3"""
    engine = DefenseEngine()
    review = ReviewResult(logic_errors=[...])
    # is_vulnerable=True → defense_prob 大幅降低
    ...

def test_cooldown_prevents_spam(self):
    """冷却期内不重复触发"""
    ...
```

- [ ] **步骤 2：运行全量测试**

```bash
python -m pytest tests/ -q
```

**预估规模：** XS

---

### 检查点：完成

- [ ] `python -m pytest tests/ -q` 全量零回归
- [ ] `python -c "from chat_core.systems.emotion import COMPOUND_DIMS; print(len(COMPOUND_DIMS))"` → `12`
- [ ] `python -c "from chat_core.systems.defense import DefenseEngine; d=DefenseEngine(); print(d._enabled)"` → `True`
- [ ] 新增 tests ≥ 15 条 (test_compound_emotion + test_defense)

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| EmotionState 新增 12 字段影响所有 `get_state()` 调用 | 中 | 全部默认 0.0，dataclass 向后兼容 |
| tick() 复杂度增加（22 维衰减 + 传染） | 低 | tick_interval=10s，计算量可忽略 |
| DefenseEngine 与审查管线耦合 | 中 | 防御路径短路正常纠正流，互斥执行 |
| 脆弱感需 Spec 008 关系数据 | 低 | 当前阶段关系安全门 fallback 为"默认通过"，Spec 008 实施后接入 |

## 待定问题

- 脆弱感的关系安全门 (`min_relationship_stage: friend`) 依赖 Spec 008，当前阶段降级为"需要时默认通过"
- `get_emotion_summary` 的复合文本格式可后续优化为更自然的中文描述 ("欣慰中带着失落")
