# Spec 011 沉默语义 + 动机系统 — 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现 5 类沉默语义（犹豫/默契/愤怒克制/策略/过载）+ 双层动机引擎（Drive Reduction + Value Pursuit）+ 孤独检测器（主观时钟驱动），全部与 Spec 003/005/006/007/008/010 联动。

**架构：** 3 个新系统文件 + 2 个新测试文件，修改 7 个现有文件。沉默改造 SILENCE 决策路径为语义化分类；动机写入 subconscious/motivations 供 _init_messages 注入；孤独受主观时钟驱动并影响动机引擎。

**技术栈：** Python 3.12+, dataclasses, asyncio, 纯规则引擎（零增量 LLM 成本）

---

## 架构决策

- **SilenceClassifier 纯规则，零 LLM**：5 类判定基于 ReviewResult + EmotionState + EnergyBar + RelationshipStage 的算术条件，不调用 LLM。
- **MotivationEngine 双写输出**：动机写入 `subconscious/motivations`（供 _init_messages 注入），同时提供 `get_strongest_drive()` 接口（供 ProactiveSystem 使用）。
- **LonelinessDetector 复用 BoredomDetector 模式**：指数衰减模型 + 主观时钟调制 + per-user relationship 检查。与 BoredomDetector 共享 tick 机制但独立值域。
- **ProactiveSystem 兼容旧接口**：新增 `set_motivation_engine()` 可选注入，有 MotivationEngine 时用 strongest_drive 替代 boredom-only；无时回退旧逻辑。
- **SilenceClassifier 不替代 SilenceAccumulator**：SilenceAccumulator 保留做纯计数（供 FuzzyParam 使用），SilenceClassifier 做语义分类。两者共存互补。

---

## 任务列表

### 阶段 1：数据类型 + 配置

- [ ] **任务 1：核心类型定义**

**文件：** `chat_core/core/types.py`（末尾追加）

```python
# ── Spec 011: 沉默语义 + 动机系统 ──────────────────────────

class SilenceType(Enum):
    HESITANT = "hesitant"
    TACIT = "tacit"
    ANGRY = "angry"
    STRATEGIC = "strategic"
    OVERLOAD = "overload"


@dataclass
class SilenceRecord:
    silence_type: SilenceType = SilenceType.STRATEGIC
    turn_id: str = ""
    trigger: str = ""
    emotion_snapshot: dict[str, float] | None = None
    reasoning: str = ""


@dataclass
class DriveSignal:
    """单个驱动信号"""
    name: str = ""               # "socialize" | "rest" | "seek_close" | "clarify" | "vent"
    strength: float = 0.0        # [0, 1]
    source: str = ""             # "boredom" | "energy" | "loneliness" | "confusion" | "anger"
    layer: str = "drive"         # "drive" | "value"


@dataclass
class MotivationState:
    """当前动机集合"""
    active_drives: list[DriveSignal] = field(default_factory=list)
    active_values: list[DriveSignal] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    strongest: str = ""          # 最强烈动机 name


@dataclass
class LonelinessState:
    """孤独状态"""
    level: float = 0.0           # [0, 1]
    last_tick: float = 0.0       # unix timestamp
    has_close_relationship: bool = False
```

验证：`python -c "from chat_core.core.types import SilenceType, SilenceRecord, DriveSignal, MotivationState, LonelinessState; print('OK')"`

---

- [ ] **任务 2：Config 配置段 + 访问器**

**文件：**
- `chat_core/config.yaml`（在 systems.moral_conflict 之后追加 silence_semantics/motivations/loneliness 三段）
- `chat_core/config.py`（追加 3 个 accessor）

YAML 从设计 §4 完整复制。accessor：`silence_semantics_config()`, `motivations_config()`, `loneliness_config()`

验证：`python -m pytest tests/ -q --tb=short` → 374 passed

---

### 检查点：阶段 1
- [ ] 类型导入 + 配置加载成功，374 passed

---

### 阶段 2：三个核心引擎

- [ ] **任务 3：SilenceClassifier**

**文件：** `chat_core/systems/silence.py`（新建）

```python
"""SilenceClassifier — 5 类沉默语义判定 (Spec 011)"""

from chat_core.config import get_config
from chat_core.core.types import (
    EmotionState, RelationshipStage, ReviewResult,
    SilenceRecord, SilenceType,
)


class SilenceClassifier:
    def __init__(self) -> None:
        cfg = get_config()
        sc = cfg.silence_semantics_config()
        self._enabled = bool(sc.get("enabled", True))
        types_cfg = sc.get("types", {})
        h = types_cfg.get("hesitant", {})
        self._hesitant_confusion = float(h.get("confusion_threshold", 0.4))
        self._hesitant_streak = int(h.get("streak_threshold", 3))
        h_inc = h.get("silence_increment", 1)
        t = types_cfg.get("tacit", {})
        self._tacit_min_stage = t.get("min_stage", "friend")
        self._tacit_max_severity = float(t.get("max_severity", 0.3))
        t_inc = t.get("silence_increment", 0)
        a = types_cfg.get("angry", {})
        self._angry_anger = float(a.get("anger_threshold", 0.5))
        self._angry_sadness_max = float(a.get("sadness_max", 0.3))
        a_inc = a.get("silence_increment", 1)
        s = types_cfg.get("strategic", {})
        s_inc = s.get("silence_increment", 1)
        o = types_cfg.get("overload", {})
        self._overload_energy = float(o.get("energy_threshold", 0.2))
        self._overload_min_turns = int(o.get("min_turns", 10))
        self._overload_recovery_boost = float(o.get("recovery_boost", 2.0))
        o_inc = o.get("silence_increment", 0)

        self._silence_increment = {
            SilenceType.HESITANT: h_inc, SilenceType.TACIT: t_inc,
            SilenceType.ANGRY: a_inc, SilenceType.STRATEGIC: s_inc,
            SilenceType.OVERLOAD: o_inc,
        }

    @property
    def enabled(self) -> bool: return self._enabled

    def classify(
        self, review: ReviewResult, emotion: EmotionState | None,
        energy: float, relationship_stage: RelationshipStage | None,
        silence_streak: int = 0, active_turns: int = 0,
    ) -> SilenceRecord:
        if not self._enabled:
            return SilenceRecord(silence_type=SilenceType.STRATEGIC)

        stage = relationship_stage.value if relationship_stage else "stranger"
        anger = emotion.anger if emotion else 0.0
        sadness = emotion.sadness if emotion else 0.0
        confusion = emotion.confusion if emotion else 0.0
        severity = review.combined_weight

        # OVERLOAD
        if energy < self._overload_energy and active_turns > self._overload_min_turns:
            return SilenceRecord(silence_type=SilenceType.OVERLOAD, reasoning="精力耗尽+轮次过多")

        # ANGRY
        if anger > self._angry_anger and sadness < self._angry_sadness_max:
            return SilenceRecord(silence_type=SilenceType.ANGRY, reasoning="生气但克制")

        # TACIT
        if stage in ("friend", "close_friend") and severity < self._tacit_max_severity:
            return SilenceRecord(silence_type=SilenceType.TACIT, reasoning="默契，不用多说")

        # HESITANT
        if silence_streak >= self._hesitant_streak and confusion > self._hesitant_confusion:
            return SilenceRecord(silence_type=SilenceType.HESITANT, reasoning="不确定该不该说")

        return SilenceRecord(silence_type=SilenceType.STRATEGIC, reasoning="选择不参与")

    def get_silence_increment(self, st: SilenceType) -> int:
        return self._silence_increment.get(st, 1)

    def get_recovery_boost(self) -> float:
        return self._overload_recovery_boost
```

验证：`python -c "from chat_core.systems.silence import SilenceClassifier; sc = SilenceClassifier(); from chat_core.core.types import ReviewResult, SilenceType; r = sc.classify(ReviewResult(), None, 0.9, None); assert r.silence_type == SilenceType.STRATEGIC; print('OK')"`

---

- [ ] **任务 4：EnergyBar.boost_recovery() 小扩展**

**文件：** `chat_core/systems/energy.py`（在 `recover()` 方法之后追加）

⚠️ EnergyBar 当前无 `boost_recovery` 方法，Spec 011 需要新增：

```python
    def boost_recovery(self, multiplier: float = 2.0) -> None:
        """Spec 011: OVERLOAD 沉默 → 加速恢复。
        直接给 energy 加一跳，受 multiplier 放大。
        """
        if not self._enabled:
            return
        boost = self._rate_high * multiplier
        self._state.energy = min(1.0, self._state.energy + boost)
```

验证：`python -c "from chat_core.systems.silence import SilenceClassifier; sc = SilenceClassifier(); from chat_core.core.types import ReviewResult, SilenceType; r = sc.classify(ReviewResult(), None, 0.9, None); assert r.silence_type == SilenceType.STRATEGIC; print('OK')"`

---

- [ ] **任务 4：MotivationEngine**

**文件：** `chat_core/systems/motivation.py`（新建）

```python
"""MotivationEngine — 双层动机 + 冲突解决 (Spec 011)"""

from chat_core.config import get_config
from chat_core.core.types import DriveSignal, MotivationState


class MotivationEngine:
    def __init__(self) -> None:
        cfg = get_config()
        mc = cfg.motivations_config()
        self._enabled = bool(mc.get("enabled", True))
        drives_cfg = mc.get("drives", {})
        self._drive_cfg = drives_cfg
        values_cfg = mc.get("values_pursuit", {})
        self._value_cfg = values_cfg
        cr = mc.get("conflict_resolution", {})
        self._drive_over_value = bool(cr.get("drive_over_value", True))
        self._merge_compatible = bool(cr.get("merge_compatible", True))

    @property
    def enabled(self) -> bool: return self._enabled

    def evaluate(
        self, boredom: float = 0.0, energy: float = 1.0,
        loneliness: float = 0.0, confusion: float = 0.0,
        unexpressed_anger: float = 0.0,
        value_weights: dict[str, float] | None = None,
    ) -> MotivationState:
        if not self._enabled:
            return MotivationState()

        drives = self._eval_drives(boredom, energy, loneliness, confusion, unexpressed_anger)
        values = self._eval_values(value_weights or {})
        conflicts = self._resolve(drives, values)
        all_active = drives + values
        strongest = max(all_active, key=lambda d: d.strength).name if all_active else ""

        return MotivationState(
            active_drives=drives, active_values=values,
            conflicts=conflicts, strongest=strongest,
        )

    def _eval_drives(self, boredom, energy, loneliness, confusion, anger_unexp):
        drives = []
        for name, cfg in self._drive_cfg.items():
            threshold = float(cfg.get("threshold", 1.0))
            source = cfg.get("source", "")
            value_map = {"boredom": boredom, "energy": 1.0 - energy, "loneliness": loneliness,
                         "confusion": confusion, "anger_unexpressed": anger_unexp}
            raw = value_map.get(source, 0.0)
            if raw > threshold:
                drives.append(DriveSignal(name=name, strength=raw, source=source, layer="drive"))
        return sorted(drives, key=lambda d: d.strength, reverse=True)

    def _eval_values(self, weights: dict[str, float]):
        values = []
        value_source_map = {"explore": "growth", "check_on": "care",
                            "confront": "honesty", "reflect": "self_improvement"}
        for name, cfg in self._value_cfg.items():
            threshold = float(cfg.get("threshold", 1.0))
            source = value_source_map.get(name, cfg.get("source", ""))
            w = weights.get(source, 0.0)
            if w > threshold:
                values.append(DriveSignal(name=name, strength=w, source=source, layer="value"))
        return sorted(values, key=lambda d: d.strength, reverse=True)

    def _resolve(self, drives, values):
        conflicts = []
        if not self._drive_over_value:
            return conflicts
        for d in drives:
            for v in values:
                if d.name == "rest" and v.name in ("explore", "reflect"):
                    conflicts.append(f"体力优先: [{d.name}] > [{v.name}]")
                elif d.name == "socialize" and v.name == "check_on" and self._merge_compatible:
                    pass  # 合并，不冲突
        return conflicts

    def get_strongest_drive_name(self, state: MotivationState) -> str:
        return state.strongest

    def build_injection(self, state: MotivationState) -> str | None:
        if not state.active_drives and not state.active_values:
            return None
        lines = ["[内在驱动]"]
        if state.active_drives:
            drive_strs = [f"{d.name}({d.strength:.2f})" for d in state.active_drives[:3]]
            lines.append(f"  当前需求: {', '.join(drive_strs)}")
        if state.active_values:
            value_strs = [f"{v.name}({v.strength:.2f})" for v in state.active_values[:3]]
            lines.append(f"  正在追求: {', '.join(value_strs)}")
        if state.conflicts:
            lines.append(f"  内部冲突: {'; '.join(state.conflicts)}")
        return "\n".join(lines) if len(lines) > 1 else None
```

验证：`python -c "from chat_core.systems.motivation import MotivationEngine; me = MotivationEngine(); s = me.evaluate(boredom=0.6, energy=0.8); assert s.strongest == 'socialize'; print('OK')"`

---

- [ ] **任务 5：LonelinessDetector**

**文件：** `chat_core/systems/loneliness.py`（新建）

```python
"""LonelinessDetector — 孤独驱动维度 (Spec 011)"""

import math, time
from chat_core.config import get_config
from chat_core.core.types import LonelinessState, RelationshipStage


class LonelinessDetector:
    def __init__(self) -> None:
        cfg = get_config()
        lc = cfg.loneliness_config()
        self._enabled = bool(lc.get("enabled", True))
        self._halflife = float(lc.get("decay_halflife", 1200))
        self._require_close = bool(lc.get("require_close_relationship", True))
        self._state = LonelinessState()

    @property
    def enabled(self) -> bool: return self._enabled

    @property
    def level(self) -> float: return self._state.level

    def tick(self, wall_dt: float, relationships: list[tuple[str, str]],
             subjective_speed: float = 1.0) -> float:
        """每 tick 更新孤独水平。

        Args:
            wall_dt: 墙钟流逝秒数
            relationships: [(user_id, stage_value), ...]
            subjective_speed: 主观时钟速度因子 (>1 = 时间过得快)
        """
        if not self._enabled:
            return 0.0

        has_close = any(stage in ("friend", "close_friend") for _, stage in relationships)
        self._state.has_close_relationship = has_close

        if self._require_close and not has_close:
            self._state.level = 0.0
            return 0.0

        effective_dt = wall_dt * subjective_speed
        decay = math.exp(-effective_dt / self._halflife)
        self._state.level = max(0.0, min(1.0, 1.0 - decay * (1.0 - self._state.level)))
        self._state.last_tick = time.time()
        return self._state.level
```

验证：`python -c "from chat_core.systems.loneliness import LonelinessDetector; ld = LonelinessDetector(); l = ld.tick(60, [('u1','friend')], 1.0); print(f'l={l:.3f}')"`

---

### 检查点：阶段 2
- [ ] 三个引擎导入 + 基本逻辑验证
- [ ] 374 passed

---

### 阶段 3：集成

- [ ] **任务 6：turn_manager.py — 沉默语义 + 动机注入**

**文件：** `chat_core/core/turn_manager.py`

a) 添加 import：`SilenceClassifier, MotivationEngine, LonelinessDetector`
b) 在 `__init__` 中初始化三引擎
c) 在 `_silent_archive()` 方法中（当前位于 turn_manager.py:1178），将沉默归档逻辑替换为 SilenceClassifier 驱动：

```python
silence_record = self._silence_classifier.classify(
    review=review, emotion=sub_emotion_state if self._emotion_engine else None,
    energy=self._energy_bar._state.energy,
    relationship_stage=self._relationship_engine.get_stage(user_id),
    silence_streak=self._silence_counters.get(error_type_key, 0),
    active_turns=self._turn_counter,
)
# 按 silence 类型差异化处理
increment = self._silence_classifier.get_silence_increment(silence_record.silence_type)
self._silence_accumulator.increment(increment)
# OVERLOAD → 加速恢复
if silence_record.silence_type == SilenceType.OVERLOAD:
    self._energy_bar.boost_recovery(self._silence_classifier.get_recovery_boost())
# 归档 silence record
await self._memory.save(MemoryEntry(
    namespace="self/silences",
    key=str(self._turn_counter),
    value={"type": silence_record.silence_type.value, "reasoning": silence_record.reasoning},
    entity_type="silence_record", salience=5.0, ttl=86400*30,
))
```

d) 每 turn 后评估动机 + 注入：

```python
loneliness = self._loneliness_detector.tick(
    wall_dt=time_since_last_tick,
    relationships=[(uid, self._relationship_engine.get_stage(uid).value)],
    subjective_speed=self._subjective_clock.speed_factor,
)
motivation_state = self._motivation_engine.evaluate(
    boredom=self._boredom_detector.get_boredom() if self._boredom_detector else 0,
    energy=self._energy_bar._state.energy,
    loneliness=loneliness,
    confusion=sub_confusion,
    value_weights={
        "growth": self._value_engine.values.growth,
        "care": self._value_engine.values.care,
        "honesty": self._value_engine.values.honesty,
        "self_improvement": self._value_engine.values.self_improvement,
    } if self._value_engine else None,
)
await self._memory.save(MemoryEntry(
    namespace="subconscious/motivations", key="current",
    value={"state": motivation_state.__dict__},
))
```

---

- [ ] **任务 7：loop.py — _init_messages 注入动机**

在 `_init_messages()` 中 `_inject_humor_hint()` 之后追加：

```python
self._inject_motivation()  # Spec 011
```

新增方法：

```python
def _inject_motivation(self) -> None:
    hint = getattr(self, '_motivation_hint', None)
    if hint:
        self._messages.insert(-1, Message(role="system", content=hint))

def set_motivation_hint(self, hint: str) -> None:
    self._motivation_hint = hint
```

在 `ReActLoop.__init__` 中追加 `self._motivation_hint: str | None = None`。

turn_manager 在 `_run_sub_session()` 中注入前先读取 `subconscious/motivations` 并调用 `loop.set_motivation_hint()`。

---

- [ ] **任务 8：proactive.py — 动机驱动主动发起**

**文件：** `chat_core/systems/proactive.py`

在 `ProactiveSystem.__init__` 中新增可选注入：

```python
self._motivation_engine: Any = None

def set_motivation_engine(self, engine: Any) -> None:
    self._motivation_engine = engine
```

在 `_on_boredom_trigger()` 顶部追加动机检查：

```python
if self._motivation_engine and self._motivation_engine.enabled:
    from chat_core.systems.motivation import MotivationEngine
    strongest = self._motivation_engine.get_strongest_drive_name(state)
    if strongest == "rest":
        return  # 需要休息，不主动发起
    if strongest == "seek_close":
        # 将主动发起目标调整为最近的密友
        ...
```

---

- [ ] **任务 9：metacognition.py — silence 模式 + 动机上下文**

**文件：** `chat_core/systems/metacognition.py`

在 `build_context()` 参数中追加：

```python
silence_pattern: str | None = None,       # Spec 011
active_motivations: str | None = None,    # Spec 011
```

在 context 组装末尾追加对应段。

---

- [ ] **任务 10：memory.py + narrative.py（轻量消费）**

memory.py：确认 `self/silences/*` 已在 search_chained 检索范围内。✅ 无需改动（search_chained 无 namespace 白名单）。

narrative.py：`NarrativeEngine` 已有 `append_chapter(event_type, text, turn)` 方法，"silence_streak" 事件类型也已预定义。在 turn_manager 检测到 silence streak ≥ 阈值时调用：

```python
# Spec 011: silence streak → 叙事事件增量
if silence_streak_count >= 3 and self._narrative_engine:
    self._narrative_engine.append_chapter(
        "silence_streak",
        f"连续{silence_streak_count}次沉默，最近一次原因: {silence_record.reasoning}",
        self._turn_counter,
    )
```

---

### 检查点：阶段 3
- [ ] 所有集成点就位，374 passed

---

### 阶段 4：测试

- [ ] **任务 11：test_silence.py**（~10 tests, SC-01~SC-05）
- [ ] **任务 12：test_motivation.py**（~12 tests, SC-06~SC-15）

---

### 检查点：阶段 4
- [ ] 374 + ~22 = ~396 passed
- [ ] 新增测试覆盖 SC-01~SC-15

---

## 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| SilenceClassifier 判定优先级可能覆盖不准确 | 中 | 严格按设计顺序 (OVERLOAD→ANGRY→TACIT→HESITANT→STRATEGIC)，测试覆盖每种组合 |
| EnergyBar 需新增 `boost_recovery()` 方法 | 低 | 已在 Task 4 中追加实现，~5 行代码 |
| ProactiveSystem 改造需兼容无 MotivationEngine 场景 | 低 | set_motivation_engine 为可选注入，无引擎时回退旧逻辑 |
| turn_manager `_silent_archive()` 改造范围大 | 中 | 渐进式改造——先追加 SilenceClassifier.classify() 在归档前，保留原有写入逻辑，只替换 silence_counter 增量方式 |

## 待定问题

- turn_manager 中 SILENCE 路径的精确改造点需在实施时 Grep 定位
- BoredomDetector.level 属性是否存在需在实施时确认
- narrative.py record_event 的精确签名和事件分发逻辑需实施时读取
