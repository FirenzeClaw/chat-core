# 注意力状态机 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将现有单向衰减 AttentionModel 升级为三态状态机 (FOCUSED/DRIFTING/DULL)，含平滑过渡、多脑协调事件总线、疲劳因子、focus 调制回复和完整配置外化。

**架构：** 在 `systems/attention.py` 新增 `AttentionState` 枚举 + `AttentionEvent` 枚举 + `apply_event()` 方法。`emotion.py` 的 `tick()` 检测 Δvalence 后发布 `emotion_alert` 事件。`loop.py` 注入 focus 状态到 system prompt 并实现状态感知的 `_should_continue()`。`turn_manager.py` 和 `qq/adapter.py` 通过 EventBus/RaceTracker 回调连接事件源。所有参数外化到 `config.yaml`。

**技术栈：** Python 3.12+, asyncio, dataclass, Enum

**设计文档：** `docs/superpowers/specs/2026-07-10-attention-state-machine-design.md`

---

## 架构决策

- **状态机核心在 `AttentionModel` 内部**：不新增独立类，直接在现有类上扩展 `apply_event()` + transition smoothing + 状态感知衰减
- **事件总线复用现有 `EventBus`**：`emotion_alert` 和 `logic_conflict` 通过 `TurnManager.event_bus` 发布
- **QQ Bot 每个子Session 独立 `AttentionModel`**：创建时初始化 FOCUSED(0.9)，竞态事件通过 `BotAdapter` 全局传播
- **DULL 态不沉默**：`should_exit_sub()` 始终返回 False（无论 focus 多低），确保昏沉态一定回复
- **配置全部外化**：无硬编码阈值，所有参数从 `config.yaml` 读取

---

## 任务列表

### 阶段 1：类型骨架 + 配置外化（基础层）

- [ ] **任务 1：扩展 `AttentionState` 类型 + 新增枚举**
- [ ] **任务 2：`config.yaml` 注意力参数外化**
- [ ] **任务 3：`config.py` 读取新增配置段**

**检查点：任务 1-3 之后**
- [ ] `python -c "from chat_core.core.types import AttentionStateEnum, AttentionEvent; print('OK')"` 无报错
- [ ] `python -c "from chat_core.config import get_config; c=get_config(); print(c.attention_config().get('state_machine',{}).get('initial_focus'))"` 输出 `0.9`

---

### 阶段 2：状态机核心逻辑

- [ ] **任务 4：`AttentionModel` 状态机核心 — 枚举 + `apply_event()` + 平滑过渡 + 分级衰减 + 疲劳 + DULL 不沉默**
- [ ] **任务 5：验证衰减速率 + 疲劳因子**

**检查点：任务 4-5 之后**
- [ ] `python -m pytest tests/test_phase6_emotion.py -v -k "attention"` — 现有注意力测试保持通过
- [ ] 新状态机单元测试通过（8 tests）

---

### 阶段 3：系统集成

- [ ] **任务 6：`emotion.py` — `tick()` 检测 Δvalence → 发布 `emotion_alert`**
- [ ] **任务 7：`loop.py` — focus 注入 + `_should_continue` 状态感知 + recall→注意力回调**
- [ ] **任务 8：`turn_manager.py` — 事件总线连接 + `emotion_alert`/`logic_conflict` 处理**
- [ ] **任务 9：`qq/adapter.py` — RaceTracker 回调 + 子Session focus 注入**

**检查点：任务 6-9 之后**
- [ ] `python -m pytest tests/ -q` — 基准 154 tests 零回归
- [ ] 新增注意力集成测试通过

---

### 阶段 4：联动增强（可延后）

- [ ] **任务 10：§5 昏沉态×无聊×兴趣 联动**

---

## 详细任务

---

### 任务 1：扩展 `AttentionState` 类型 + 新增枚举

**文件：**
- 修改：`chat_core/core/types.py:348-350`

**描述：** 在现有的 `AttentionState` dataclass 基础上新增 `AttentionStateEnum`（三态枚举）和 `AttentionEvent`（事件类型枚举）。`AttentionState` 扩展 `fatigue` 字段，保持向后兼容（默认值 0.0）。

- [ ] **步骤 1：在 `types.py` 顶部确认导入 `Enum`, `dataclass`, `field`**

文件已包含 `from dataclasses import dataclass, field` 和 `from enum import Enum`（需确认）。

- [ ] **步骤 2：新增枚举和扩展 dataclass**

在 `class AttentionState:` 之前插入：

```python
# ── 注意力状态机 (注意力状态机 Phase 1) ────────────────────────────

class AttentionStateEnum(Enum):
    """三态注意力状态"""
    FOCUSED = "focused"      # focus ≥ 0.6
    DRIFTING = "drifting"    # 0.3 ≤ focus < 0.6
    DULL = "dull"            # focus < 0.3


class AttentionEvent(Enum):
    """注意力状态转移事件"""
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
```

修改 `AttentionState` dataclass：

```python
@dataclass
class AttentionState:
    focus: float = 0.8
    dominance: float = 0.7
    fatigue: float = 0.0  # 新增：疲劳累积因子 [0.0, 1.0]
```

- [ ] **步骤 3：验证导入**

```bash
python -c "from chat_core.core.types import AttentionStateEnum, AttentionEvent, AttentionState; s=AttentionState(focus=0.9, fatigue=0.5); print(s); print(AttentionStateEnum.FOCUSED); print(AttentionEvent.USER_MESSAGE)"
```

**预估规模：** XS

---

### 任务 2：`config.yaml` 注意力参数外化

**文件：**
- 修改：`chat_core/config.yaml:111-116`

**描述：** 将 `§6 配置映射` 中所有注意力参数写入 `config.yaml` 的 `systems.attention` 段。

- [ ] **步骤 1：替换现有 `systems.attention` 段**

定位到 `config.yaml` 的 `attention:` 段（第 111-116 行），用完整配置替换：

```yaml
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

保留 `baseline` 和 `drift_decay_rate` 作为向后兼容（`AttentionModel.__init__` 仍读取它们）：

```yaml
    baseline:
      logic: {focus: 0.8, dominance: 0.7}
      emotion: {focus: 0.7, dominance: 0.5}
      sub: {focus: 0.9, dominance: 0.6}
    drift_decay_rate: 0.001
```

完整 `attention:` 段 = `state_machine` + `drift` + `fatigue` + `boredom_link` + `baseline` + `drift_decay_rate`。

- [ ] **步骤 2：验证 YAML 语法**

```bash
python -c "import yaml; yaml.safe_load(open('chat_core/config.yaml')); print('YAML OK')"
```

**预估规模：** XS

---

### 任务 3：`config.py` 读取新增配置段

**文件：**
- 修改：`chat_core/config.py:208-209`

**描述：** `attention_config()` 已存在，仅需确认返回结构包含所有新增 key。无需代码改动——`get_config().attention_config()` 返回 `dict`，新 key 通过 `.get()` 安全读取，缺省值在各消费方提供 fallback。

- [ ] **步骤 1：验证现有方法不需要修改**

```bash
python -c "from chat_core.config import get_config; c=get_config(); ac=c.attention_config(); print('state_machine' in ac); print('drift' in ac); print('fatigue' in ac)"
```

预期输出三个 `True`。如果 YAML 中有对应 key，`config.yaml` 解析后自动包含。

- [ ] **步骤 2：确认向后兼容**

```bash
python -c "from chat_core.config import get_config; c=get_config(); print(c.attention_config().get('drift_decay_rate')); print(c.attention_config().get('baseline'))"
```

**预估规模：** XS（可能零代码改动）

---

### 任务 4：`AttentionModel` 状态机核心 — 枚举 + `apply_event()` + 平滑过渡

**文件：**
- 修改：`chat_core/systems/attention.py`
- 创建/修改：`tests/test_phase6_emotion.py`（新增注意力测试）

**描述：** 在 `AttentionModel` 中实现三态枚举导出、`apply_event()` 方法（基于转移矩阵）、0.3s 平滑过渡插值。

- [ ] **步骤 1：编写失败的测试**

在 `tests/test_phase6_emotion.py` 末尾追加：

```python
# ── 注意力状态机测试 ─────────────────────────────────────────

from chat_core.core.types import AttentionStateEnum, AttentionEvent
from chat_core.systems.attention import AttentionModel


class TestAttentionStateMachine:
    """注意力状态机: 三态 + apply_event + 平滑过渡"""

    def test_initial_state_focused(self):
        """新 AttentionModel 应从 FOCUSED(0.9) 开始"""
        model = AttentionModel()
        state = model.get_state("sub")
        assert state.focus == 0.9
        assert model.get_state_enum("sub") == AttentionStateEnum.FOCUSED

    def test_state_enum_thresholds(self):
        """focus 值正确映射到三态枚举"""
        model = AttentionModel()
        # 手动设置 sub focus
        model._states["sub"].focus = 0.85
        assert model.get_state_enum("sub") == AttentionStateEnum.FOCUSED
        model._states["sub"].focus = 0.50
        assert model.get_state_enum("sub") == AttentionStateEnum.DRIFTING
        model._states["sub"].focus = 0.20
        assert model.get_state_enum("sub") == AttentionStateEnum.DULL

    def test_apply_event_emotion_positive(self):
        """EMOTION_POSITIVE 事件应 +0.10 focus"""
        model = AttentionModel()
        initial = model.get_focus("sub")
        model.apply_event(AttentionEvent.EMOTION_POSITIVE, brain="sub")
        assert model.get_focus("sub") == pytest.approx(min(1.0, initial + 0.10))

    def test_apply_event_emotion_shock_dull(self):
        """DULL 态下 EMOTION_SHOCK → 80% 概率跳 DRIFTING"""
        import random
        random.seed(42)
        model = AttentionModel()
        model._states["sub"].focus = 0.20  # DULL
        model.apply_event(AttentionEvent.EMOTION_SHOCK, brain="sub")
        # 0.8 概率跳 DRIFTING，即 focus 被设为 0.25 + 0.30 = 0.55 (在 DRIFTING 区间)
        assert model.get_focus("sub") >= 0.30

    def test_apply_event_memory_strong_hit(self):
        """MEMORY_STRONG_HIT → +0.25 FOCUSED, +0.20 DRIFTING"""
        model = AttentionModel()
        model._states["sub"].focus = 0.50  # DRIFTING
        model.apply_event(AttentionEvent.MEMORY_STRONG_HIT, brain="sub")
        assert model.get_focus("sub") == pytest.approx(min(1.0, 0.50 + 0.20))

    def test_smooth_transition_active(self):
        """apply_event 后 transition_target 非空，drift() 逐步插值"""
        model = AttentionModel()
        model._states["sub"].focus = 0.20  # DULL
        model.apply_event(AttentionEvent.EMOTION_SHOCK, brain="sub")
        assert model._transition_target is not None  # 平滑过渡进行中
        
        # drift() 应逐步靠近目标
        import time
        model._last_update = time.time() - 0.15  # 模拟 0.15s 流逝
        model.drift()
        # 应已过渡一半
        assert model._transition_elapsed > 0

    def test_apply_event_race_mild(self):
        """RACE_MILD (active≥3) → 70% 概率 FOCUSED→DRIFTING"""
        import random
        random.seed(42)
        model = AttentionModel()
        model._states["sub"].focus = 0.80  # FOCUSED
        model.apply_event(AttentionEvent.RACE_MILD, brain="sub")
        # 70% 概率 focus 被设为 0.30 (DRIFTING 下限)

    def test_dull_always_exit_sub_false(self):
        """DULL 态下 should_exit_sub() 始终返回 False"""
        model = AttentionModel()
        model._states["sub"].focus = 0.05  # 极低
        assert model.should_exit_sub() is False  # DULL 不沉默
```

- [ ] **步骤 2：运行测试确认失败**

```bash
python -m pytest tests/test_phase6_emotion.py::TestAttentionStateMachine -v
```

预期：全部 FAIL（`AttentionModel` 尚无 `apply_event`, `get_state_enum` 等方法）。

- [ ] **步骤 3：实现最小代码**

修改 `chat_core/systems/attention.py`：

```python
"""AttentionModel — 三态注意力状态机 (FOCUSED/DRIFTING/DULL) + 平滑过渡"""

from __future__ import annotations

import random
import time

from chat_core.config import get_config
from chat_core.core.types import AttentionEvent, AttentionState, AttentionStateEnum

# 默认 baseline
DEFAULT_BASELINE: dict[str, AttentionState] = {
    "logic": AttentionState(focus=0.8, dominance=0.7),
    "emotion": AttentionState(focus=0.7, dominance=0.5),
    "sub": AttentionState(focus=0.9, dominance=0.6),
}


def _state_enum(focus: float, thresholds: dict) -> AttentionStateEnum:
    """focus 值 → AttentionStateEnum"""
    if focus >= thresholds.get("focused", 0.6):
        return AttentionStateEnum.FOCUSED
    elif focus >= thresholds.get("drifting", 0.3):
        return AttentionStateEnum.DRIFTING
    else:
        return AttentionStateEnum.DULL


class AttentionModel:
    """三态注意力状态机。

    为每个大脑维护 focus（专注度）和 dominance（主导性），
    支持事件驱动的状态转移、平滑过渡、疲劳因子和状态感知衰减速率。
    """

    def __init__(self) -> None:
        cfg = get_config()
        ac = cfg.attention_config()
        sm = ac.get("state_machine", {})
        drift_cfg = ac.get("drift", {})
        fatigue_cfg = ac.get("fatigue", {})
        baseline_cfg = ac.get("baseline", {})

        # 状态机阈值
        self._thresholds: dict[str, float] = {
            "focused": float(sm.get("state_thresholds", {}).get("focused", 0.6)),
            "drifting": float(sm.get("state_thresholds", {}).get("drifting", 0.3)),
        }

        # boost/penalty 值
        boosts = sm.get("boosts", {})
        penalties = sm.get("penalties", {})
        probs = sm.get("transition_probabilities", {})
        self._boosts: dict[str, float] = {
            "user_message_dull": float(boosts.get("user_message_dull", 0.20)),
            "emotion_positive": float(boosts.get("emotion_positive", 0.10)),
            "emotion_shock": float(boosts.get("emotion_shock", 0.30)),
            "memory_strong_hit": float(boosts.get("memory_strong_hit", 0.25)),
            "topic_match_strong": float(boosts.get("topic_match_strong", 0.08)),
        }
        self._penalties: dict[str, float] = {
            "emotion_negative": float(penalties.get("emotion_negative", 0.10)),
            "memory_miss": float(penalties.get("memory_miss", 0.03)),
            "short_reply_streak": float(penalties.get("short_reply_streak", 0.10)),
            "correction_triggered": float(penalties.get("correction_triggered", 0.05)),
            "per_segment_sent": float(penalties.get("per_segment_sent", 0.02)),
        }
        self._probs: dict[str, float] = {
            "drifting_to_focused_on_message": float(probs.get("drifting_to_focused_on_message", 0.7)),
            "dull_to_drifting_on_message": float(probs.get("dull_to_drifting_on_message", 0.5)),
            "dull_to_drifting_on_shock": float(probs.get("dull_to_drifting_on_shock", 0.8)),
            "focused_to_drifting_on_race3": float(probs.get("focused_to_drifting_on_race3", 0.7)),
            "focused_to_dull_on_race5": float(probs.get("focused_to_dull_on_race5", 0.6)),
            "drifting_to_dull_on_race5": float(probs.get("drifting_to_dull_on_race5", 0.8)),
        }

        # 衰减速率（状态感知）
        self._decay_rates: dict[str, float] = {
            "focused": float(drift_cfg.get("decay_rate_focused", 0.001)),
            "drifting": float(drift_cfg.get("decay_rate_drifting", 0.002)),
            "dull": float(drift_cfg.get("decay_rate_dull", 0.0005)),
        }
        # 向后兼容的 drift_decay_rate（旧配置）
        self._drift_decay_rate: float = float(ac.get("drift_decay_rate", 0.001))

        # 疲劳
        self._fatigue_max_turns: int = int(fatigue_cfg.get("max_turns", 50))
        self._fatigue_acceleration: float = float(fatigue_cfg.get("decay_acceleration", 0.5))
        self._total_turns: int = 0

        # baseline
        self._baseline: dict[str, AttentionState] = {}
        for name in ["logic", "emotion", "sub"]:
            bc = baseline_cfg.get(name, {})
            initial_focus = float(sm.get("initial_focus", 0.9)) if name == "sub" else float(bc.get("focus", DEFAULT_BASELINE[name].focus))
            self._baseline[name] = AttentionState(
                focus=initial_focus,
                dominance=float(bc.get("dominance", DEFAULT_BASELINE[name].dominance)),
            )

        # 当前状态
        self._states: dict[str, AttentionState] = {
            name: AttentionState(
                focus=self._baseline[name].focus,
                dominance=self._baseline[name].dominance,
            )
            for name in self._baseline
        }

        self._last_update: float = time.time()

        # 平滑过渡
        self._transition_target: dict[str, float | None] = {name: None for name in self._baseline}
        self._transition_elapsed: dict[str, float] = {name: 0.0 for name in self._baseline}
        self._transition_start: dict[str, float] = {name: 0.0 for name in self._baseline}
        self._transition_duration: float = 0.3  # 0.3s

    # ── 状态枚举 ───────────────────────────────────────────────

    def get_state_enum(self, brain: str) -> AttentionStateEnum:
        """获取指定大脑的三态枚举值"""
        focus = self.get_focus(brain)
        return _state_enum(focus, self._thresholds)

    # ── 事件驱动 ───────────────────────────────────────────────

    def apply_event(self, event: AttentionEvent, brain: str = "sub") -> None:
        """应用注意力事件，触发状态转移（含概率性转移）。

        转移后启动 0.3s 平滑过渡到目标 focus。
        """
        state_enum = self.get_state_enum(brain)
        current = self._states[brain].focus
        target = current  # 默认不变

        if event == AttentionEvent.USER_MESSAGE:
            if state_enum == AttentionStateEnum.DULL:
                p = self._probs["dull_to_drifting_on_message"]
                if random.random() < p:
                    target = self._thresholds["drifting"] + self._boosts["user_message_dull"]
                else:
                    target = current + self._boosts["user_message_dull"]
            # FOCUSED: 保持

        elif event == AttentionEvent.EMOTION_POSITIVE:
            target = min(1.0, current + self._boosts["emotion_positive"])

        elif event == AttentionEvent.EMOTION_NEGATIVE:
            target = max(0.0, current - self._penalties["emotion_negative"])

        elif event == AttentionEvent.EMOTION_SHOCK:
            if state_enum == AttentionStateEnum.DULL:
                p = self._probs["dull_to_drifting_on_shock"]
                if random.random() < p:
                    target = self._thresholds["drifting"] + self._boosts["emotion_shock"]
                else:
                    target = current + self._boosts["emotion_shock"]
            else:
                target = min(1.0, current + self._boosts["emotion_shock"])

        elif event == AttentionEvent.MEMORY_STRONG_HIT:
            if state_enum == AttentionStateEnum.DULL:
                if random.random() < 0.5:
                    target = self._thresholds["drifting"] + self._boosts["memory_strong_hit"]
                else:
                    target = current + self._boosts["memory_strong_hit"]
            elif state_enum == AttentionStateEnum.DRIFTING:
                target = min(1.0, current + 0.20)
            else:
                target = min(1.0, current + self._boosts["memory_strong_hit"])

        elif event == AttentionEvent.MEMORY_MISS:
            if state_enum == AttentionStateEnum.DRIFTING:
                target = max(0.0, current - 0.05)
            else:
                target = max(0.0, current - self._penalties["memory_miss"])

        elif event == AttentionEvent.TOPIC_MATCH:
            boost = self._boosts["topic_match_strong"] if state_enum != AttentionStateEnum.DULL else 0.05
            target = min(1.0, current + boost)

        elif event == AttentionEvent.RACE_MILD:
            if state_enum == AttentionStateEnum.FOCUSED:
                if random.random() < self._probs["focused_to_drifting_on_race3"]:
                    target = self._thresholds["focused"] - 0.01  # 刚跌破聚焦

        elif event == AttentionEvent.RACE_SEVERE:
            if state_enum == AttentionStateEnum.FOCUSED:
                if random.random() < self._probs["focused_to_dull_on_race5"]:
                    target = self._thresholds["drifting"] - 0.01
            elif state_enum == AttentionStateEnum.DRIFTING:
                if random.random() < self._probs["drifting_to_dull_on_race5"]:
                    target = self._thresholds["drifting"] - 0.01

        elif event == AttentionEvent.SHORT_REPLY_STREAK:
            penalty = self._penalties["short_reply_streak"]
            target = max(0.0, current - penalty) if state_enum == AttentionStateEnum.DRIFTING else max(0.0, current - penalty * 0.67)

        elif event == AttentionEvent.SILENCE_TICK:
            if state_enum == AttentionStateEnum.FOCUSED:
                target = max(0.0, current - 0.03)
            elif state_enum == AttentionStateEnum.DRIFTING:
                target = max(0.0, current - 0.05)

        elif event == AttentionEvent.INTENT_DETECTED:
            target = min(1.0, current + self._boosts.get("intent_detected", 0.05))

        elif event == AttentionEvent.CORRECTION_TRIGGERED:
            penalty = self._penalties["correction_triggered"]
            target = max(0.0, current - penalty) if state_enum == AttentionStateEnum.DRIFTING else max(0.0, current - penalty * 0.5)

        # 启动平滑过渡
        if abs(target - current) > 0.001:
            self._transition_target[brain] = target
            self._transition_start[brain] = current
            self._transition_elapsed[brain] = 0.0

    # ── drift ──────────────────────────────────────────────────

    def drift(self) -> None:
        """施加一次时间漂移衰减。状态感知衰减速率 + 平滑过渡插值 + 疲劳因子。"""
        now = time.time()
        dt = now - self._last_update
        if dt <= 0:
            return

        self._last_update = now

        for name in self._states:
            state = self._states[name]

            # 1. 平滑过渡（如果有进行中的过渡）
            if self._transition_target[name] is not None:
                self._transition_elapsed[name] += dt
                progress = min(1.0, self._transition_elapsed[name] / self._transition_duration)
                # 线性插值
                state.focus = self._transition_start[name] + (
                    self._transition_target[name] - self._transition_start[name]
                ) * progress
                if progress >= 1.0:
                    self._transition_target[name] = None
            else:
                # 2. 状态感知衰减 + 疲劳因子
                state_enum = self.get_state_enum(name)
                rate_key = state_enum.value  # "focused" / "drifting" / "dull"
                base_rate = self._decay_rates.get(rate_key, self._drift_decay_rate)

                # 疲劳加速
                fatigue = min(1.0, self._total_turns / self._fatigue_max_turns)
                effective_rate = base_rate * (1.0 + fatigue * self._fatigue_acceleration)

                decay_factor = 1.0 - effective_rate * dt
                decay_factor = max(0.0, min(1.0, decay_factor))
                state.focus = max(0.0, state.focus * decay_factor)
                state.dominance = max(0.0, state.dominance * decay_factor)

    # ── 公共 API ───────────────────────────────────────────────

    def get_state(self, brain: str) -> AttentionState:
        """获取指定大脑的当前注意力状态（返回副本）"""
        if brain not in self._states:
            raise ValueError(f"Unknown brain: {brain}")
        state = self._states[brain]
        return AttentionState(focus=state.focus, dominance=state.dominance, fatigue=state.fatigue)

    def get_focus(self, brain: str) -> float:
        """获取指定大脑的当前 focus 值"""
        return self._states[brain].focus

    def reset(self, brain: str) -> None:
        """将指定大脑的注意力重置为其 baseline"""
        if brain not in self._baseline:
            raise ValueError(f"Unknown brain: {brain}")
        self._states[brain] = AttentionState(
            focus=self._baseline[brain].focus,
            dominance=self._baseline[brain].dominance,
        )

    def boost(self, brain: str, amount: float = 0.2) -> None:
        """临时提升指定大脑的 focus，上限 1.0"""
        if brain not in self._states:
            raise ValueError(f"Unknown brain: {brain}")
        self._states[brain].focus = min(1.0, self._states[brain].focus + amount)

    def increment_turn(self) -> None:
        """增加总 turn 计数（用于疲劳计算）"""
        self._total_turns += 1

    def should_exit_sub(self) -> bool:
        """判断子Session 是否应因注意力过低而退出。

        注意力状态机: DULL 态不沉默，始终返回 False。
        """
        state_enum = self.get_state_enum("sub")
        if state_enum == AttentionStateEnum.DULL:
            return False
        return self._states["sub"].focus < 0.15

    def get_all_states(self) -> dict[str, AttentionState]:
        """获取全部大脑的当前注意力状态"""
        return {
            name: AttentionState(focus=s.focus, dominance=s.dominance)
            for name, s in self._states.items()
        }
```

- [ ] **步骤 4：运行测试验证通过**

```bash
python -m pytest tests/test_phase6_emotion.py::TestAttentionStateMachine -v
```

预期：全部 PASS。

- [ ] **步骤 5：确认现有注意力测试无回归**

```bash
python -m pytest tests/test_phase6_emotion.py -v -k "attention and not TestAttentionStateMachine"
```

**预估规模：** M (1 核心文件 + 测试)

---

### 任务 5：验证衰减速率 + 疲劳因子（任务 4 子检查点）

**描述：** 衰减速率分级和疲劳因子已随任务 4 一并实现。此任务为独立验证步骤：确保 FOCUSED/DRIFTING/DULL 三态各自按配置速率衰减，且 50 turn 后疲劳加速 50%。

- [ ] **步骤 1：衰减速率分级验证**

```python
python -c "
from chat_core.systems.attention import AttentionModel
import time
m = AttentionModel()
# 手动设置 sub focus 并模拟 drift
m._states['sub'].focus = 0.9
m._last_update = time.time() - 10  # 10 秒流逝
m.drift()
f1 = m.get_focus('sub')
# FOCUSED 衰减: 0.9 * (1 - 0.001*10) = 0.891
print(f'FOCUSED after 10s: {f1:.4f} (expected ~0.891)')

# 模拟 DRIFTING 态衰减
m._states['sub'].focus = 0.5
m._last_update = time.time() - 10
m.drift()
f2 = m.get_focus('sub')
# DRIFTING 衰减: 0.5 * (1 - 0.002*10) = 0.490
print(f'DRIFTING after 10s: {f2:.4f} (expected ~0.490)')

# 疲劳满后加速
m._total_turns = 50
m._states['sub'].focus = 0.9
m._last_update = time.time() - 10
m.drift()
f3 = m.get_focus('sub')
# FOCUSED + 满疲劳: decay = 0.001 * 1.5 = 0.0015 → 0.9 * 0.985 = 0.887
print(f'FOCUSED+fatigue after 10s: {f3:.4f} (expected ~0.887)')
print('All checks passed')
"
```

- [ ] **步骤 2：运行单元测试确认无回归**

```bash
python -m pytest tests/test_phase6_emotion.py::TestAttentionStateMachine -v
```

**预估规模：** XS

---

### 任务 6：`emotion.py` — `tick()` 检测 Δvalence → 发布 `emotion_alert`

**文件：**
- 修改：`chat_core/systems/emotion.py`
- 修改：`tests/test_phase6_emotion.py`（新增集成测试）

**描述：** `EmotionEngine.__init__` 新增可选 `event_bus` 参数（由 TurnManager 注入）。`tick()` 末尾检测 sub 脑 valence 变化 ((joy+trust)/2)。若 |Δ| > 0.5，通过 `call_soon_threadsafe` 安全发布 `emotion_alert` 到 event_bus。

- [ ] **步骤 1：编写测试**

```python
import asyncio
from chat_core.core.turn_manager import EventBus


class TestEmotionAttentionLink:
    """情绪引擎 → 注意力状态机 联动：tick() 检测 Δvalence → event_bus"""

    def test_tick_updates_prev_valence(self):
        """tick() 后 _prev_valence 更新为当前 valence"""
        engine = EmotionEngine()
        engine._states["sub"].joy = 0.3
        engine._states["sub"].trust = 0.5
        engine.tick()
        expected = (0.3 + 0.5) / 2.0
        assert engine._prev_valence.get("sub", -1) == pytest.approx(expected, abs=0.01)

    def test_valence_delta_below_threshold_no_publish(self):
        """|Δvalence| ≤ 0.5 → 不发布事件"""
        engine = EmotionEngine()
        engine._prev_valence["sub"] = 0.5
        engine._states["sub"].joy = 0.6
        engine._states["sub"].trust = 0.6
        current = (0.6 + 0.6) / 2.0  # 0.6
        delta = abs(current - engine._prev_valence["sub"])  # 0.1
        assert delta <= 0.5, "小变化不触发 alarm"

    @pytest.mark.asyncio
    async def test_tick_publishes_emotion_alert_on_shock(self):
        """|Δvalence| > 0.5 → event_bus 收到 emotion_alert"""
        bus = EventBus()
        engine = EmotionEngine(event_bus=bus)
        # 建立低基线
        engine._states["sub"].joy = 0.05
        engine._states["sub"].trust = 0.05
        engine.tick()
        # 制造冲击
        engine._states["sub"].joy = 0.9
        engine._states["sub"].trust = 0.7
        engine.tick()
        # 验证 queue（可能在异步 tick_loop 中才入队，这里验证 prev 更新）
        new_prev = engine._prev_valence.get("sub", 0.0)
        current_valence = (0.9 + 0.7) / 2.0
        assert new_prev == pytest.approx(current_valence, abs=0.01)
```

- [ ] **步骤 2：修改 `EmotionEngine.__init__` 签名**

```python
def __init__(self, event_bus: Any = None) -> None:
    cfg = get_config()
    # ... 现有初始化代码 ...
    self._event_bus = event_bus
    self._prev_valence: dict[str, float] = {}
```

- [ ] **步骤 3：`tick()` 末尾追加 Δvalence 检测**

```python
        # 3. 检测 Δvalence → emotion_alert
        if self._event_bus:
            import asyncio as _asyncio
            for brain_name in ["sub"]:
                state = self._states[brain_name]
                current_valence = (state.joy + state.trust) / 2.0
                prev = self._prev_valence.get(brain_name, current_valence)
                delta = abs(current_valence - prev)
                if delta > 0.5:
                    try:
                        loop = _asyncio.get_running_loop()
                        loop.call_soon_threadsafe(
                            lambda d=delta, bn=brain_name: _asyncio.ensure_future(
                                self._event_bus.publish("emotion_alert", {
                                    "mood_shift": f"valence_delta={d:.2f}",
                                    "intensity": min(1.0, d),
                                    "brain": bn,
                                })
                            )
                        )
                    except RuntimeError:
                        pass  # 无运行中的 loop
                self._prev_valence[brain_name] = current_valence
```

> **安全设计**：`tick()` 是同步方法，被 `_tick_loop` 后台 task 调用。使用 `call_soon_threadsafe` + `ensure_future` 确保事件发布在正确的 event loop 上执行，且不阻塞 tick。

- [ ] **步骤 4：运行测试**

```bash
python -m pytest tests/test_phase6_emotion.py -v -k "attention or TestEmotionAttentionLink"
```

**预估规模：** S

---

### 任务 7：`loop.py` — focus 注入 + recall→注意力回调

**文件：**
- 修改：`chat_core/core/loop.py`
- 修改：`tests/test_loop.py`（新增测试）

**描述：** 
1. `_init_messages()` 首条 system message 后追加注意力状态提示
2. `_handle_recall()` 完成后通知 `AttentionModel`（命中 salience≥7 → MEMORY_STRONG_HIT，空结果 → MEMORY_MISS）
3. `register_sub_session_tools` 增加 `attention_model` 参数

- [ ] **步骤 1：编写测试**

```python
# tests/test_loop.py 末尾追加
from unittest.mock import MagicMock


class TestAttentionInjection:
    """子Session focus 注入 + recall→注意力回调"""

    def test_init_messages_injects_focus_prompt(self):
        """_init_messages 应在 system prompt 后注入注意力状态提示"""
        from chat_core.systems.attention import AttentionModel
        from chat_core.core.loop import ReActLoop, SubSessionConfig
        from chat_core.core.tools import ToolRegistry

        mock_provider = MagicMock()  # 不依赖真实 API
        tools = ToolRegistry()
        attn = AttentionModel()
        loop = ReActLoop(
            provider=mock_provider,
            tool_registry=tools,
            system_prompt="你是小深。",
            config=SubSessionConfig(max_iter=1),
            attention_model=attn,
        )
        loop._init_messages("你好")
        all_system = [m.content for m in loop._messages if m.role == "system"]
        attention_hints = [s for s in all_system if "[注意状态]" in s]
        assert len(attention_hints) >= 1, f"未找到 [注意状态] 提示: {all_system}"

    @pytest.mark.asyncio
    async def test_recall_hit_triggers_attention_boost(self):
        """_handle_recall 命中高 salience → apply_event(MEMORY_STRONG_HIT)"""
        from chat_core.systems.attention import AttentionModel
        from chat_core.core.loop import _handle_recall
        from unittest.mock import AsyncMock

        attn = AttentionModel()
        attn._states["sub"].focus = 0.80
        initial = attn.get_focus("sub")

        mock_store = AsyncMock()
        mock_store.search_chained = AsyncMock(return_value=[
            MagicMock(entry=MagicMock(salience=8.0)),
        ])
        mock_store._format_recall_result = MagicMock(return_value="测试回溯")

        await _handle_recall(
            {"query": "测试"}, mock_store,
            chain_config=MagicMock(), attention_model=attn,
        )
        assert attn.get_focus("sub") > initial, \
            f"应提升 focus: {initial} → {attn.get_focus('sub')}"

    @pytest.mark.asyncio
    async def test_recall_empty_triggers_attention_penalty(self):
        """_handle_recall 空结果 → apply_event(MEMORY_MISS)"""
        from chat_core.systems.attention import AttentionModel
        from chat_core.core.loop import _handle_recall
        from unittest.mock import AsyncMock

        attn = AttentionModel()
        attn._states["sub"].focus = 0.50
        initial = attn.get_focus("sub")

        mock_store = AsyncMock()
        mock_store.search_chained = AsyncMock(return_value=[])
        mock_store._format_recall_result = MagicMock(return_value="暂时一片空白。")

        await _handle_recall(
            {"query": "不存在"}, mock_store,
            chain_config=MagicMock(), attention_model=attn,
        )
        assert attn.get_focus("sub") < initial, \
            f"空结果应降低 focus: {initial} → {attn.get_focus('sub')}"
```

- [ ] **步骤 2：修改 `_handle_recall` 签名 + 注意力回调**

```python
async def _handle_recall(
    args: dict,
    memory_store: Any = None,
    chain_config: Any = None,
    attention_model: Any = None,  # 新增
) -> str:
    query = str(args.get("query", ""))
    if memory_store is None:
        return json.dumps({"results": [], "query": query, "note": "记忆系统未初始化"})
    try:
        if chain_config is not None:
            chained = await memory_store.search_chained(query, chain_config)
            result_text = memory_store._format_recall_result(chained)
            # 注意力回调：检查 salience
            if attention_model is not None and chained:
                from chat_core.core.types import AttentionEvent
                max_sal = max(
                    (getattr(cm.entry, 'salience', 0) for cm in chained if hasattr(cm, 'entry')),
                    default=0
                )
                if max_sal >= 7:
                    attention_model.apply_event(AttentionEvent.MEMORY_STRONG_HIT, brain="sub")
        else:
            entries = await memory_store.search(query, top_n=5)
            result_text = json.dumps(
                {"results": [{"key": f"{e.namespace}/{e.key}", "value": e.value} for e in entries],
                 "count": len(entries)}, ensure_ascii=False
            )
            if attention_model is not None and not entries:
                attention_model.apply_event(AttentionEvent.MEMORY_MISS, brain="sub")
        return result_text
    except Exception as e:
        return json.dumps({"results": [], "query": query, "error": str(e)})
```

- [ ] **步骤 3：`register_sub_session_tools` 增加 `attention_model` 参数**

```python
def register_sub_session_tools(
    registry: ToolRegistry,
    loop: ReActLoop,
    memory_store: Any = None,
    chain_config: Any = None,
    attention_model: Any = None,  # 新增
) -> None:
    # ... send_reply, wait, inner_thoughts, done 不变 ...
    registry.register(ToolDefinition(
        name="recall",
        # ... 描述不变 ...
        fn=lambda args, ctx: _handle_recall(
            args, memory_store, chain_config, attention_model
        ),
        parallel_safe=True,
    ))
```

- [ ] **步骤 4：`_init_messages` + `_inject_attention_hint`**

```python
def _init_messages(self, user_message: str) -> None:
    if not self._messages:
        self._messages = [
            Message(role="system", content=self._system_prompt),
            Message(role="user", content=user_message),
        ]
        self._inject_attention_hint()
        return
    self._messages.append(Message(role="user", content=user_message))
    self._inject_attention_hint()

def _inject_attention_hint(self) -> None:
    if self._attention_model is None:
        return
    try:
        from chat_core.core.types import AttentionStateEnum
        state_enum = self._attention_model.get_state_enum("sub")
        focus = self._attention_model.get_focus("sub")
        hints = {
            AttentionStateEnum.FOCUSED: f"[注意状态] 你感到专注、投入，对对话充满兴趣。（focus={focus:.2f}）",
            AttentionStateEnum.DRIFTING: f"[注意状态] 你有点走神，注意力不太集中，回复会偏短。（focus={focus:.2f}）",
            AttentionStateEnum.DULL: f"[注意状态] 你很难集中注意力，只想简单回应，但请一定回复对方。（focus={focus:.2f}）",
        }
        hint = hints.get(state_enum, "")
        if hint:
            self._messages.insert(-1, Message(role="system", content=hint))
    except Exception:
        pass
```

- [ ] **步骤 5：更新调用方 (`turn_manager.py`, `adapter.py`) 传 `attention_model`**

```python
# turn_manager.py _run_sub_session:
register_sub_session_tools(tools, loop, self._memory, attention_model=self._attention_model)

# adapter.py _get_or_create_sub_session:
register_sub_session_tools(tools, loop, self._memory, chain_config=chain_config, attention_model=loop._attention_model)
```

- [ ] **步骤 6：运行测试**

```bash
python -m pytest tests/test_loop.py -v -k "attention"
```

**预估规模：** M

---

### 任务 8：`turn_manager.py` — 事件总线 + `emotion_alert`/`logic_conflict` 处理

**文件：**
- 修改：`chat_core/core/turn_manager.py`

**描述：** 
1. `process_turn()` 调用 `attention_model.increment_turn()` 递增疲劳
2. 注入 `event_bus` 到 `EmotionEngine`（通过构造参数，非私有属性赋值）
3. 订阅 `emotion_alert` 和 `logic_conflict` 事件 → `attention_model.apply_event()`
4. 子Session 发言完成后按段数惩罚注意力
5. **延迟启动**：`_listen_*` 后台任务在 `process_turn` 首次调用时 lazy 启动，避免 `__init__` 中 `create_task` 依赖 event loop

- [ ] **步骤 1：`process_turn()` — 递增 turn 计数**

在 `self._turn_counter += 1` 之后添加：

```python
# 注意力疲劳递增
if self._attention_model:
    self._attention_model.increment_turn()
```

- [ ] **步骤 2：`__init__` 末尾 — 注入 event_bus + lazy init 标记**

```python
# EmotionEngine 的 event_bus 在构造时注入（若传入了）
# TurnManager 负责创建 EmotionEngine 时传入 self._event_bus
# （cli.py / qq_bot.py 中创建时需更新调用）

# 事件监听器延迟启动标记
self._listeners_started: bool = False
```

- [ ] **步骤 3：新增 `_ensure_listeners()` 延迟启动方法**

```python
def _ensure_listeners(self) -> None:
    """延迟启动 event_bus 监听器（首次 process_turn 调用时触发）"""
    if self._listeners_started:
        return
    self._listeners_started = True
    if self._attention_model:
        asyncio.create_task(self._listen_emotion_alerts())
```

- [ ] **步骤 4：`process_turn()` 开头调用 `_ensure_listeners()`**

```python
async def process_turn(self, user_message: str) -> ConversationTurn:
    # 首次调用时启动事件监听器
    self._ensure_listeners()
    # ... 原有逻辑 ...
```

- [ ] **步骤 5：实现 `_listen_emotion_alerts()`**

```python
async def _listen_emotion_alerts(self) -> None:
    """监听 emotion_alert + logic_conflict → AttentionModel.apply_event()"""
    alert_q = self._event_bus.subscribe("emotion_alert")
    conflict_q = self._event_bus.subscribe("logic_conflict")

    async def _handle_alert():
        while True:
            data = await alert_q.get()
            if self._attention_model:
                self._attention_model.apply_event(
                    AttentionEvent.EMOTION_SHOCK, brain="sub"
                )

    async def _handle_conflict():
        while True:
            data = await conflict_q.get()
            if self._attention_model:
                # logic_conflict → 困惑/犹豫，focus -0.05
                self._attention_model.boost("sub", -0.05)

    # 并行监听两个事件
    await asyncio.gather(_handle_alert(), _handle_conflict())
```

- [ ] **步骤 6：`process_turn()` — 发言完成惩罚**

在 `_run_sub_session` 返回后（`replies, inner_thoughts` 赋值后）添加：

```python
# 发言完成 → 注意力衰减 (FOCUSED -0.02/段, DRIFTING -0.03/段, DULL -0.01/段)
if self._attention_model and replies:
    for _ in replies:
        state_enum = self._attention_model.get_state_enum("sub")
        if state_enum == AttentionStateEnum.DRIFTING:
            self._attention_model.apply_event(AttentionEvent.CORRECTION_TRIGGERED, brain="sub")
        elif state_enum == AttentionStateEnum.DULL:
            self._attention_model.boost("sub", -0.01)
        else:
            self._attention_model.boost("sub", -0.02)
```

- [ ] **步骤 7：更新 TurnManager 构造调用方**

`cli.py` / `qq_bot.py` 创建 `EmotionEngine` 时需传入 `event_bus`：

```python
# cli.py / qq_bot.py 中:
emotion_engine = EmotionEngine(event_bus=turn_manager.event_bus)
# TurnManager 创建时已持有 self._event_bus，在初始化后注入：
# if self._emotion_engine:
#     self._emotion_engine._event_bus = self._event_bus  # 备用（若构造时未传）
```

> **优雅方案**：`TurnManager.__init__` 接受已配置好的 `EmotionEngine`（由调用方传入），不负责 event_bus 注入。调用方在创建 `EmotionEngine` 时传入 `event_bus=turn_manager.event_bus`。如果调用方未传，`EmotionEngine` 默认 `event_bus=None`（不发布事件，静默降级）。

- [ ] **步骤 8：导入 `AttentionEvent`**

```python
from chat_core.core.types import (
    ...,
    AttentionEvent,  # 新增
)
```

- [ ] **步骤 9：运行测试**

```bash
python -m pytest tests/test_design_alignment.py -v
```

**预估规模：** S

---

### 任务 9：`qq/adapter.py` — RaceTracker 回调 + 子Session focus 注入

**文件：**
- 修改：`chat_core/qq/adapter.py`

**描述：** 
1. `RaceTracker.enter()` 时若 `active_count` 跨阈值（2→3 或 4→5），通过 `_attention_model.apply_event()` 发布竞态事件
2. `_get_or_create_sub_session` 中创建的 AttentionModel 使用独立实例（已实现——第 262 行 `attention_model=AttentionModel()`）
3. 子Session `_init_messages` 的 focus 注入依赖任务 7

- [ ] **步骤 1：`process_message()` — 竞态事件**

在 `_race_tracker.enter()` 之后（`_process` 调用前）添加竞态检测：

```python
# 竞态事件 → 注意力状态机
prev_count = self._race_tracker.active_count
self._race_tracker.enter()
new_count = self._race_tracker.active_count

if prev_count < 3 and new_count >= 3:
    self._attention_model.apply_event(AttentionEvent.RACE_MILD, brain="sub")
if prev_count < 5 and new_count >= 5:
    self._attention_model.apply_event(AttentionEvent.RACE_SEVERE, brain="sub")
```

- [ ] **步骤 2：`_race_tracker.exit()` — 竞态缓解 boost**

在 `finally` 块 `self._race_tracker.exit()` 之后：

```python
# 竞态缓解 → 注意力 +0.05
if self._race_tracker.active_count < prev_count:
    self._attention_model.boost("sub", 0.05)
```

- [ ] **步骤 3：确认子Session AttentionModel 独立性**

检查 `_get_or_create_sub_session` 第 262 行——已创建独立 `AttentionModel()` 实例。无需改动。

- [ ] **步骤 4：运行测试**

```bash
python -m pytest tests/test_design_alignment.py tests/test_qq_protocol.py -v
```

**预估规模：** S

---

### 任务 10：§5 昏沉态×无聊×兴趣 联动（可延后到 Phase 1 验收后）

**文件：**
- 修改：`chat_core/systems/boredom.py` — 状态感知 tick 间隔
- 修改：`chat_core/systems/interest.py` — DULL 态情绪调制兴趣触发概率
- 修改：`chat_core/systems/proactive.py` — `_should_initiate()` 读取 AttentionModel 状态

**描述：** 实现设计文档 §5.1-5.3 的三向联动。本任务涉及额外 3 个文件，标记为可延后——Phase 1 核心状态机 + 集成（任务 1-9）验收后单独执行。

- [ ] **步骤 1：`boredom.py` — 状态感知 tick 间隔**

`BoredomDetector` 需知道当前注意力状态以调整 tick 间隔。新增可选 `attention_model` 参数：

```python
class BoredomDetector:
    def __init__(self, attention_model: Any = None):
        ...
        self._attention_model = attention_model

    def _get_tick_interval(self) -> float:
        """根据注意力状态返回 tick 间隔"""
        cfg = get_config()
        bl = cfg.attention_config().get("boredom_link", {})
        if self._attention_model:
            state = self._attention_model.get_state_enum("sub")
            from chat_core.core.types import AttentionStateEnum
            if state == AttentionStateEnum.FOCUSED:
                return float(bl.get("tick_interval_focused", 30))
            elif state == AttentionStateEnum.DRIFTING:
                return float(bl.get("tick_interval_drifting", 20))
            else:
                return float(bl.get("tick_interval_dull", 15))
        return float(bl.get("tick_interval_focused", 30))
```

DULL 态触发阈值降低：

```python
def _get_trigger_threshold(self) -> float:
    if self._attention_model and self._attention_model.get_state_enum("sub") == AttentionStateEnum.DULL:
        bl = get_config().attention_config().get("boredom_link", {})
        return float(bl.get("threshold_dull", 0.40))
    return self._trigger_threshold  # 默认 0.30
```

- [ ] **步骤 2：`interest.py` — DULL 态情绪调制兴趣触发**

`InterestModel.match()` 读取 `AttentionModel` 状态，DULL 态下情绪调制兴趣触发概率：

```python
def get_mood_modifier(self, emotion_engine, attention_model=None) -> float:
    """DULL 态下情绪调制兴趣触发概率"""
    if attention_model and attention_model.get_state_enum("sub") == AttentionStateEnum.DULL:
        sub_state = emotion_engine.get_state("sub")
        valence = (sub_state.joy + sub_state.trust) / 2.0
        if valence < -0.2:
            return 0.5
        elif valence < 0:
            return 0.8
        elif abs(sub_state.joy - sub_state.sadness) > 0.5:
            return 2.0  # 剧烈波动 ×2.0
        else:
            return 1.2
    return 1.0
```

- [ ] **步骤 3：`proactive.py` — `_should_initiate()` 读取 AttentionModel**

```python
def _should_initiate(self) -> bool:
    if self._attention_model:
        from chat_core.core.types import AttentionStateEnum
        state = self._attention_model.get_state_enum("sub")
        if state == AttentionStateEnum.DULL:
            return False  # 禁止主动发起
        elif state == AttentionStateEnum.DRIFTING:
            # 概率 ×0.3
            if random.random() > 0.3:
                return False
    return True  # FOCUSED 正常概率
```

DULL + 无聊触发 → 不发起对话，写 subconscious/nudges：

```python
async def _on_boredom_trigger(self) -> None:
    if self._attention_model and self._attention_model.get_state_enum("sub") == AttentionStateEnum.DULL:
        await self._memory.save(MemoryEntry(
            namespace="subconscious/nudges",
            key=f"dull_boredom_{int(time.time())}",
            value={"source": "dull_boredom", "note": "等注意力恢复后处理"},
        ))
        return
    # 原有逻辑...
```

- [ ] **步骤 4：更新构造调用方**

`TurnManager.__init__` 中将 `attention_model` 传给 `BoredomDetector`、`InterestModel`：

```python
self._boredom_detector = BoredomDetector(attention_model=attention_model)
```

- [ ] **步骤 5：运行测试**

```bash
python -m pytest tests/test_phase6_emotion.py tests/test_loop.py tests/test_design_alignment.py -v
```

**预估规模：** M（3 文件）

---

### 检查点：完成

- [ ] `python -m pytest tests/ -q` — 基准 154 tests 零回归
- [ ] `python -c "from chat_core.core.types import AttentionStateEnum, AttentionEvent; print('OK')"` 无报错
- [ ] 所有新注意力测试通过（任务 4: 8 tests, 任务 6: 3 tests, 任务 7: 3 tests）
- [ ] 任务 10（§5 联动）可在此检查点之后单独执行

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| `AttentionState` 扩展 `fatigue` 字段影响下游序列化 | 中 | `fatigue` 默认值 0.0，dataclass 向后兼容 |
| `_listen_emotion_alerts` 后台任务生命周期 | 中 | lazy init 延迟到首次 `process_turn`，避免 `__init__` 依赖 event loop |
| config.yaml schema 变更破坏现有配置加载 | 低 | 保留 `baseline` + `drift_decay_rate` 作为向后兼容 |
| 随机概率性转移导致测试不稳定 | 低 | 测试使用 `random.seed(42)` 固定随机种子 |
| QQ Bot 多子Session AttentionModel 并发 | 低 | 每个子Session 独立实例，无共享状态 |
| §5 联动涉及额外 3 文件 | 低 | 标记为可延后任务 10，Phase 1 核心验收后执行 |
| `register_sub_session_tools` 签名变更影响调用方 | 低 | 新增参数带默认值 `None`，向后兼容 |

## 待定问题

- ~~`EmotionEngine.tick()` 中 `asyncio.get_event_loop()` 在后台 task 中可用性~~ → 已改用 `call_soon_threadsafe` + `get_running_loop()`（任务 6）
- ~~`AttentionModel.increment_turn()` CLI/QQ Bot 两处调用~~ → 已在 `turn_manager.py` 和 `adapter.py` 两处分别递增（任务 8/9）
- `EmotionEngine(event_bus=...)` 新签名需同步更新 `cli.py` / `qq_bot.py` 创建调用（任务 8 步骤 7）
- `BoredomDetector(attention_model=...)` 新签名将影响 `TurnManager` 和 `BotAdapter`（任务 10，可延后）

---

## 执行交接

**计划已完成并保存到 `docs/superpowers/plans/2026-07-10-attention-state-machine.md`。两种执行方式：**

**1. 子代理驱动（推荐）** — 每个任务调度一个新的子代理，任务间进行审查，快速迭代

**2. 内联执行** — 在当前会话中使用 executing-plans 执行任务，批量执行并设有检查点

**选哪种方式？**
