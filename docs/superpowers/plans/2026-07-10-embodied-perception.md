# Spec 007 具身感知 — EnergyBar + SubjectiveClock 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。

**目标：** 实现精力条 (EnergyBar) 事件驱动消耗+idle恢复+防御联动，主观时钟 (SubjectiveClock) 注意力/情绪/兴趣三维调制+驱动boredom+记忆标记。

**架构：** 新建 `systems/energy.py` 和 `systems/subjective_time.py`。EnergyBar 在 turn 结束时消耗、idle 期恢复、与 Spec 005 防御联动。SubjectiveClock 读取 AttentionModel/EmotionEngine/InterestModel 计算 speed_factor，替换 BoredomDetector 的墙钟。turn 归档时写入 subjective_time_perception 到 memory。

**技术栈：** Python 3.12+, asyncio, dataclass

**设计文档：** `docs/superpowers/specs/2026-07-10-embodied-perception-design.md`

**依赖：** Spec 005 (复合情绪/防御) ✅ 已完成

---

## 任务列表

### 阶段 1：类型 + 配置

- [ ] **任务 1：新增 EnergyState + SubjectiveTimePerception 类型**
- [ ] **任务 2：config.yaml 新增 energy + subjective_time 段**

### 阶段 2：EnergyBar

- [ ] **任务 3：新建 `systems/energy.py` — EnergyBar 核心**
- [ ] **任务 4：`turn_manager.py` 集成 — consume + recover + 归档**
- [ ] **任务 4b：`adapter.py` BotAdapter 集成 — 镜像 TurnManager**

### 阶段 3：SubjectiveClock

- [ ] **任务 5：新建 `systems/subjective_time.py` — SubjectiveClock 核心**
- [ ] **任务 6：`boredom.py` 改用主观时钟 + 更新两处调用方**

### 阶段 4：下游联动

- [ ] **任务 7：`loop.py` — energy exit 阈值**
- [ ] **任务 8：`memory.py` — `_format_recall_result` 消费时间感知**

---

## 详细任务

### 任务 1：新增 EnergyState + SubjectiveTimePerception 类型

**文件：** 修改 `chat_core/core/types.py`

- [ ] **步骤 1：新增 EnergyState**

```python
@dataclass
class EnergyState:
    """精力状态 (Spec 007)"""
    energy: float = 0.9          # [0.0, 1.0]
    last_update: float = 0.0     # unix timestamp
    total_turns_today: int = 0
```

- [ ] **步骤 2：新增 SubjectiveTimePerception**

```python
@dataclass
class SubjectiveTimePerception:
    """主观时间感知 (Spec 007) — 写入 turn memory 供 Spec 003 回溯"""
    speed_factor: float = 1.0
    perception: str = "normal"   # "immersed" | "normal" | "dragging"
    description: str = ""
    fatigue_at_end: float = 0.9
```

- [ ] **步骤 3：验证**

```bash
python -c "from chat_core.core.types import EnergyState, SubjectiveTimePerception; print(EnergyState().energy, SubjectiveTimePerception().perception)"
```

**预估规模：** XS

---

### 任务 2：config.yaml 新增 energy + subjective_time 段

**文件：** 修改 `chat_core/config.yaml`

追加到 `systems:` 下：

```yaml
  energy:
    enabled: true
    initial: 0.9
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
    defense_interaction:
      project_relief: 0.02
      denial_drain: 0.02

  subjective_time:
    enabled: true
    speed_modifiers:
      attention:
        focused: 0.3
        drifting: 0.8
        dull: 2.0
      emotion:
        joy_threshold: 0.5
        sadness_threshold: 0.5
        gratification_threshold: 0.4
      interest:
        match_threshold: 0.7
```

**预估规模：** XS

---

### 任务 3：新建 `systems/energy.py` — EnergyBar 核心

**文件：** 创建 `chat_core/systems/energy.py`，创建 `tests/test_energy.py`

**描述：** 实现 EnergyBar 类：`consume()` 事件驱动消耗、`recover()` idle 恢复、`should_exit()` 阈值检查、`get_state()` 返回 EnergyState。

- [ ] **步骤 1：实现 EnergyBar**

```python
"""EnergyBar — 精力管理 (Spec 007)"""

from __future__ import annotations
import time
from chat_core.config import get_config
from chat_core.core.types import EnergyState


class EnergyBar:
    def __init__(self) -> None:
        cfg = get_config()
        ec = cfg.systems.get("energy", {})
        self._enabled: bool = bool(ec.get("enabled", True))
        self._initial: float = float(ec.get("initial", 0.9))
        cons = ec.get("consumption", {})
        self._cost_normal: float = float(cons.get("normal_turn", 0.03))
        self._cost_long: float = float(cons.get("long_reply", 0.06))
        self._cost_emotion_shock: float = float(cons.get("emotion_shock", 0.10))
        self._cost_correction: float = float(cons.get("correction", 0.05))
        self._cost_defense: float = float(cons.get("defense", 0.04))
        self._long_threshold: int = int(cons.get("long_reply_threshold", 3))
        rec = ec.get("recovery", {})
        self._recovery_interval: int = int(rec.get("interval", 60))
        self._rate_high: float = float(rec.get("rate_high", 0.02))
        self._rate_mid: float = float(rec.get("rate_mid", 0.01))
        self._rate_low: float = float(rec.get("rate_low", 0.005))
        self._exit_threshold: float = float(ec.get("exit_threshold", 0.15))
        di = ec.get("defense_interaction", {})
        self._project_relief: float = float(di.get("project_relief", 0.02))
        self._denial_drain: float = float(di.get("denial_drain", 0.02))

        self._state = EnergyState(energy=self._initial, last_update=time.time())

    def consume(self, reply_count: int = 1, has_correction: bool = False,
                has_defense_denial: bool = False, has_defense_project: bool = False,
                compound_delta: float = 0.0) -> float:
        if not self._enabled:
            return self._state.energy
        cost = self._cost_normal
        if reply_count > self._long_threshold:
            cost = self._cost_long
        if abs(compound_delta) > 0.4:
            cost += self._cost_emotion_shock
        if has_correction:
            cost += self._cost_correction
        if has_defense_denial:
            cost += self._cost_defense + self._denial_drain
        if has_defense_project:
            self._state.energy = min(1.0, self._state.energy + self._project_relief)
        self._state.energy = max(0.0, self._state.energy - cost)
        self._state.total_turns_today += 1
        self._state.last_update = time.time()
        return self._state.energy

    def recover(self, wall_dt: float) -> float:
        if not self._enabled or wall_dt <= 0:
            return self._state.energy
        if self._state.energy > 0.6:
            rate = self._rate_high
        elif self._state.energy > 0.3:
            rate = self._rate_mid
        else:
            rate = self._rate_low
        self._state.energy = min(1.0, self._state.energy + rate * (wall_dt / self._recovery_interval))
        self._state.last_update = time.time()
        return self._state.energy

    def should_exit(self) -> bool:
        return self._enabled and self._state.energy < self._exit_threshold

    def get_state(self) -> EnergyState:
        return EnergyState(
            energy=self._state.energy,
            last_update=self._state.last_update,
            total_turns_today=self._state.total_turns_today,
        )
```

- [ ] **步骤 2：编写测试**

```python
# tests/test_energy.py
class TestEnergyBar:
    def test_normal_turn_consumes_003(self):
        bar = EnergyBar()
        bar.consume(reply_count=1)
        assert bar.get_state().energy == pytest.approx(0.87)

    def test_long_reply_consumes_006(self):
        bar = EnergyBar()
        bar.consume(reply_count=5)
        assert bar.get_state().energy == pytest.approx(0.84)

    def test_emotion_shock_adds_010(self):
        bar = EnergyBar()
        bar.consume(compound_delta=0.5)
        assert bar.get_state().energy == pytest.approx(0.77)

    def test_defense_project_relief(self):
        bar = EnergyBar()
        bar.consume(has_defense_project=True)
        assert bar.get_state().energy > 0.9  # +0.02 relief

    def test_defense_denial_drain(self):
        bar = EnergyBar()
        bar.consume(has_defense_denial=True)
        assert bar.get_state().energy < 0.87

    def test_recovery_high(self):
        bar = EnergyBar()
        bar._state.energy = 0.8
        bar.recover(60)
        assert bar.get_state().energy == pytest.approx(0.82)

    def test_recovery_low(self):
        bar = EnergyBar()
        bar._state.energy = 0.2
        bar.recover(60)
        assert bar.get_state().energy == pytest.approx(0.205)

    def test_exit_threshold(self):
        bar = EnergyBar()
        bar._state.energy = 0.10
        assert bar.should_exit() is True
```

- [ ] **步骤 3：运行测试**

```bash
python -m pytest tests/test_energy.py -v
```

**预估规模：** M

---

### 任务 4：`turn_manager.py` 集成 — consume + recover + 归档

**文件：** 修改 `chat_core/core/turn_manager.py`

**关键决策：** defense 信息来自异步 `_async_review_and_decide`，process_turn 不等待它。consume 必须在两处分别调用：基础消耗（正常发言/情绪冲击）在 process_turn 同步路径；防御联动消耗在 `_apply_defense` 内部。

- [ ] **步骤 1：__init__ 创建 EnergyBar + SubjectiveClock**

```python
from chat_core.systems.energy import EnergyBar
from chat_core.systems.subjective_time import SubjectiveClock
self._energy_bar = EnergyBar()
self._subjective_clock = SubjectiveClock()
```

- [ ] **步骤 2：process_turn() 末尾 — 基础消耗（同步路径）**

在 `replies` 获取后、vulnerability aftermath 之前：

```python
# Spec 007: 基础精力消耗（防御联动在 _apply_defense 内部异步触发）
if self._energy_bar and replies:
    compound_delta = self._emotion_engine.last_compound_delta if self._emotion_engine else 0.0
    self._energy_bar.consume(
        reply_count=len(replies),
        compound_delta=compound_delta,
    )
```

- [ ] **步骤 3：`_apply_defense()` 内部 — 防御联动消耗**

在现有情绪调整和沉默累积器之间插入：

```python
# Spec 007: 防御联动精力消耗
if self._energy_bar:
    if defense.defense_type == DefenseType.DENIAL:
        self._energy_bar.consume(has_defense_denial=True)
    elif defense.defense_type == DefenseType.PROJECT:
        self._energy_bar.consume(has_defense_project=True)
```

- [ ] **步骤 4：`_archive_turn()` 写入主观时间感知**

在 summary dict 构造之后、memory.save 之前：

```python
# Spec 007: 附加主观时间感知
if self._subjective_clock:
    fatigue = self._energy_bar.get_state().energy if self._energy_bar else 0.9
    stp = self._subjective_clock.get_perception(fatigue)
    summary["subjective_time_perception"] = {
        "speed_factor": stp.speed_factor,
        "perception": stp.perception,
        "description": stp.description,
        "fatigue_at_end": stp.fatigue_at_end,
    }
```

- [ ] **步骤 5：process_turn() 末尾 — energy exit 检查**

在 `_should_continue` 调用前（由 TurnManager 在进入下一轮前检查）：

```python
# Spec 007: 精力耗尽标记（传递给子Session）
if self._energy_bar and self._energy_bar.should_exit():
    runtime_state["energy_exit"] = True
```

**预估规模：** M

---

### 任务 4b：`adapter.py` BotAdapter 集成 — 镜像 TurnManager

**文件：** 修改 `chat_core/qq/adapter.py`

**描述：** QQ Bot 模式绕过了 TurnManager，需在 BotAdapter 中独立实现 EnergyBar 和 SubjectiveClock 集成。

- [ ] **步骤 1：__init__ 创建 EnergyBar + SubjectiveClock + 更新 BoredomDetector**

```python
from chat_core.systems.energy import EnergyBar
from chat_core.systems.subjective_time import SubjectiveClock
self._energy_bar = EnergyBar()
self._subjective_clock = SubjectiveClock()
self._boredom_detector = BoredomDetector(
    attention_model=self._attention_model,
    subjective_clock=self._subjective_clock,
)
```

- [ ] **步骤 2：`_process()` 末尾 — 基础消耗**

在 `return segs` 之前：

```python
# Spec 007: 基础精力消耗
if self._energy_bar and segs:
    compound_delta = self._emotion_engine.last_compound_delta if self._emotion_engine else 0.0
    self._energy_bar.consume(reply_count=len(segs), compound_delta=compound_delta)
```

- [ ] **步骤 3：`_async_review_and_decide` — 防御联动消耗**

在 defense 判定后、写入前：

```python
if self._energy_bar and defense.defense_type != DefenseType.DIRECT:
    if defense.defense_type == DefenseType.DENIAL:
        self._energy_bar.consume(has_defense_denial=True)
    elif defense.defense_type == DefenseType.PROJECT:
        self._energy_bar.consume(has_defense_project=True)
```

- [ ] **步骤 4：`_archive()` 写入主观时间感知**

```python
if self._subjective_clock:
    stp = self._subjective_clock.get_perception(
        self._energy_bar.get_state().energy if self._energy_bar else 0.9
    )
    summary["subjective_time_perception"] = {
        "speed_factor": stp.speed_factor,
        "perception": stp.perception,
        "description": stp.description,
        "fatigue_at_end": stp.fatigue_at_end,
    }
```

**预估规模：** S

---

### 任务 5：新建 `systems/subjective_time.py` — SubjectiveClock

**文件：** 创建 `chat_core/systems/subjective_time.py`，创建 `tests/test_subjective_time.py`

- [ ] **步骤 1：实现 SubjectiveClock**

```python
"""SubjectiveClock — 主观时间感知 (Spec 007)"""

from __future__ import annotations
import time
from chat_core.config import get_config
from chat_core.core.types import SubjectiveTimePerception


class SubjectiveClock:
    def __init__(self) -> None:
        cfg = get_config()
        st = cfg.systems.get("subjective_time", {})
        self._enabled: bool = bool(st.get("enabled", True))
        sm = st.get("speed_modifiers", {})
        attn = sm.get("attention", {})
        self._attn_focused: float = float(attn.get("focused", 0.3))
        self._attn_drifting: float = float(attn.get("drifting", 0.8))
        self._attn_dull: float = float(attn.get("dull", 2.0))
        emo = sm.get("emotion", {})
        self._joy_threshold: float = float(emo.get("joy_threshold", 0.5))
        self._sadness_threshold: float = float(emo.get("sadness_threshold", 0.5))
        self._gratification_threshold: float = float(emo.get("gratification_threshold", 0.4))
        intr = sm.get("interest", {})
        self._interest_threshold: float = float(intr.get("match_threshold", 0.7))

        self._accumulated: float = 0.0
        self._last_tick_real: float = time.time()
        self._speed_factor: float = 1.0
        self._perception: str = "normal"

    def tick(self, wall_dt: float, attention_state_enum=None, emotion_state=None,
             interest_match: float = 0.0) -> float:
        if not self._enabled:
            self._accumulated += wall_dt
            return wall_dt
        sf = self._compute_speed_factor(attention_state_enum, emotion_state, interest_match)
        subjective_dt = wall_dt * sf
        self._accumulated += subjective_dt
        self._speed_factor = sf
        self._last_tick_real = time.time()
        self._update_perception(sf)
        return subjective_dt

    def _compute_speed_factor(self, attention_state_enum, emotion_state, interest_match) -> float:
        base = 1.0
        # 注意力 (factor > 1 = 煎熬, < 1 = 投入)
        if attention_state_enum is not None:
            from chat_core.core.types import AttentionStateEnum
            if attention_state_enum == AttentionStateEnum.FOCUSED:
                base *= self._attn_focused
            elif attention_state_enum == AttentionStateEnum.DRIFTING:
                base *= self._attn_drifting
            else:
                base *= self._attn_dull
        # 情绪
        if emotion_state is not None:
            if getattr(emotion_state, 'joy', 0) > self._joy_threshold:
                base *= 0.7
            if getattr(emotion_state, 'sadness', 0) > self._sadness_threshold:
                base *= 1.3
            if getattr(emotion_state, 'gratification', 0) > self._gratification_threshold:
                base *= 0.8
        # 兴趣
        if interest_match > self._interest_threshold:
            base *= 0.6
        return base

    def _update_perception(self, sf: float) -> None:
        if sf < 0.5:
            self._perception = "immersed"
        elif sf > 1.5:
            self._perception = "dragging"
        else:
            self._perception = "normal"

    def get_perception(self, fatigue: float) -> SubjectiveTimePerception:
        descriptions = {
            "immersed": "感觉聊得特别投入，时间像飞一样",
            "dragging": "时间过得特别慢，有点煎熬",
            "normal": "时间感正常",
        }
        return SubjectiveTimePerception(
            speed_factor=self._speed_factor,
            perception=self._perception,
            description=descriptions.get(self._perception, ""),
            fatigue_at_end=fatigue,
        )

    @property
    def accumulated(self) -> float:
        return self._accumulated
```

- [ ] **步骤 2：编写测试**

```python
# tests/test_subjective_time.py
class TestSubjectiveClock:
    def test_focused_speeds_up_time(self):
        clock = SubjectiveClock()
        sf = clock._compute_speed_factor(AttentionStateEnum.FOCUSED, None, 0)
        assert sf < 1.0  # 投入 → 时间快

    def test_dull_slows_down_time(self):
        clock = SubjectiveClock()
        sf = clock._compute_speed_factor(AttentionStateEnum.DULL, None, 0)
        assert sf > 1.0  # 昏沉 → 时间慢

    def test_joy_speeds_up(self):
        clock = SubjectiveClock()
        emo = EmotionState(joy=0.6)
        sf = clock._compute_speed_factor(None, emo, 0)
        assert sf < 1.0

    def test_interest_speeds_up(self):
        clock = SubjectiveClock()
        sf = clock._compute_speed_factor(None, None, 0.8)
        assert sf < 1.0

    def test_tick_accumulates_subjective_time(self):
        clock = SubjectiveClock()
        clock.tick(10, AttentionStateEnum.DULL, None, 0)  # 2× → 20s subjective
        assert clock.accumulated > 15
```

- [ ] **步骤 3：运行测试**

```bash
python -m pytest tests/test_subjective_time.py -v
```

**预估规模：** M

---

### 任务 6：`boredom.py` 改用主观时钟 + 更新两处调用方

**文件：** 修改 `chat_core/systems/boredom.py`，修改 `chat_core/core/turn_manager.py`，修改 `chat_core/qq/adapter.py`

**描述：** `BoredomDetector.__init__` 新增可选 `subjective_clock` 参数。`_tick_loop` 每次 tick 后调用 `SubjectiveClock.tick()` 获取主观时间用于 boredom 计算。**恢复也在 tick 循环中触发**：每次 tick 时调用 `energy_bar.recover(interval)`。

- [ ] **步骤 1：BoredomDetector.__init__ 新增参数**

```python
def __init__(self, attention_model: Any = None, subjective_clock: Any = None,
             energy_bar: Any = None) -> None:
    self._subjective_clock = subjective_clock
    self._energy_bar = energy_bar
```

- [ ] **步骤 2：`_tick_loop` 中使用主观时间 + 触发恢复**

```python
async def _tick_loop(self) -> None:
    while self._stop_event and not self._stop_event.is_set():
        self._check_thresholds()
        interval = self._get_tick_interval()
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        # 主观时间采样
        if self._subjective_clock and self._attention_model:
            attn_state = self._attention_model.get_state_enum("sub")
            self._subjective_clock.tick(interval, attention_state_enum=attn_state)
        # 精力恢复
        if self._energy_bar:
            self._energy_bar.recover(interval)
```

- [ ] **步骤 3：更新两处调用方**

```python
# turn_manager.py (已在任务4创建):
self._boredom_detector = BoredomDetector(
    attention_model=attention_model,
    subjective_clock=self._subjective_clock,
    energy_bar=self._energy_bar,
)

# adapter.py (已在任务4b步骤1创建):
self._boredom_detector = BoredomDetector(
    attention_model=self._attention_model,
    subjective_clock=self._subjective_clock,
    energy_bar=self._energy_bar,
)
```

**预估规模：** S

---

### 任务 7：`loop.py` — energy exit 阈值

**文件：** 修改 `chat_core/core/loop.py`

**描述：** `ReActLoop.__init__` 新增可选 `energy_bar` 参数。`_should_continue()` 检查 `energy_bar.should_exit()`。TurnManager 和 BotAdapter 创建 ReActLoop 时传入。

- [ ] **步骤 1：__init__ 新增参数**

```python
def __init__(self, ..., energy_bar: Any = None):
    self._energy_bar = energy_bar
```

- [ ] **步骤 2：`_should_continue` 新增 energy 检查**

```python
# Spec 007: 精力耗尽 → exit
if self._energy_bar and self._energy_bar.should_exit():
    return False
```

- [ ] **步骤 3：TurnManager 和 BotAdapter 创建 ReActLoop 时传入**

```python
# turn_manager.py _run_sub_session 和 adapter.py _get_or_create_sub_session:
loop = ReActLoop(
    ...,
    energy_bar=self._energy_bar,
)
```

**预估规模：** XS

---

### 任务 8：`memory.py` — `_format_recall_result` 消费时间感知

**文件：** 修改 `chat_core/systems/memory.py`

- [ ] **步骤 1：检测 subjective_time_perception 并追加注解**

在 `_format_recall_result` 中，对记忆条目检查是否含 `subjective_time_perception`。若有，追加如 "那次对话好像特别投入，感觉时间过得飞快"。

```python
# 在 summarize 逻辑中
if hasattr(cm.entry, 'value') and isinstance(cm.entry.value, dict):
    stp = cm.entry.value.get("subjective_time_perception")
    if stp and stp.get("perception") == "immersed":
        summary += "（那次聊得特别投入，时间过得飞快）"
```

**预估规模：** XS

---

### 检查点：完成

- [ ] `python -m pytest tests/ -q` 全量零回归
- [ ] `python -c "from chat_core.systems.energy import EnergyBar; b=EnergyBar(); b.consume(); print(b.get_state().energy)"` → ~0.87
- [ ] `python -c "from chat_core.systems.subjective_time import SubjectiveClock; c=SubjectiveClock(); print(c.tick(10, None, None, 0))"` → ~10.0
- [ ] 新增 tests ≥ 8 条 (test_energy + test_subjective_time)

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| SubjectiveClock 引入循环依赖（依赖 AttentionModel/EmotionEngine） | 中 | tick() 接受枚举值和 State 对象为参数，不持有引用 |
| EnergyBar 与 defense 联动需要跨 turn 传递 defense 类型 | 低 | 从 turn_manager._apply_defense 中标记 |
| boredom tick 改为主观时间后行为变化 | 低 | 配置开关可回退 |

## 待定问题

- 主观时间感知写入哪条 memory entry？设计说"当前 turn 的 conversation 记忆"——即归档到 `user/{uid}/conversations` 的条目中
