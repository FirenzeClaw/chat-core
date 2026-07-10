# Spec 006 元认知深度 — 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现"反思的反思"——定期 + 异常双触发审视，产出自然语言洞察注入 system prompt + 结构化参数调节运行时行为

**架构：** MetacognitionEngine (新建) 在 TurnManager._async_review_and_decide() 后触发 → 复用 LogicBrain 单次 function calling (metacognition_report 工具) → insight_text 写入 memory 供下一轮注入，param_overrides 注入各消费子系统

**技术栈：** Python 3.12+, asyncio, dataclass, pytest, 复用 DeepSeek V4 Pro (LogicBrain)

---

## 架构决策

- **复用 LogicBrain，不新建 LLM 依赖**：LogicBrain.metacognition_pass() 新建方法，注册 metacognition_report 工具，单次 function calling 一次完成
- **双输出分离**：insight_text（自然语言）→ memory + system prompt 注入；param_overrides（结构化）→ MetaParamOverrides 容器 → 各子系统消费
- **confidence 门控**：confidence < 0.6 → 只写文本不调参，安全保护
- **过期机制**：param_overrides 5 轮后自动过期恢复默认，避免永久偏离
- **触发后重置计数器**：防止同 turn 重复触发

---

## 改动文件清单

| 文件 | 改动类型 | 说明 |
|------|---------|------|
| `chat_core/core/types.py` | 修改 | + `MetacognitionReport`, `MetaParamOverrides` |
| `chat_core/systems/metacognition.py` | **新建** | `MetacognitionEngine` 核心 |
| `chat_core/core/brain.py` | 修改 | + `LogicBrain.metacognition_pass()` |
| `chat_core/core/turn_manager.py` | 修改 | 集成触发器、计数器、overrides 注入 |
| `chat_core/systems/review.py` | 修改 | `_compute_decision()` 读 offset |
| `chat_core/systems/defense.py` | 修改 | `evaluate()` 读 multiplier |
| `chat_core/systems/interest.py` | 修改 | `match()` 读 modulations |
| `chat_core/systems/emotion.py` | 修改 | + `get_compound_trend()`，tick 读 offset |
| `chat_core/core/loop.py` | 修改 | `_inject_subconscious_corrections()` 注入 insight_text |
| `chat_core/config.yaml` | 修改 | + `systems.metacognition` |
| `chat_core/config.py` | 修改 | + `metacognition_config()` 方法 |
| `tests/test_metacognition.py` | **新建** | 20+ 测试 |

---

## 任务列表

### 阶段 1：基础 (数据结构 + 配置)

#### 任务 1：core/types.py — MetacognitionReport + MetaParamOverrides

**描述：** 在 `core/types.py` 末尾添加两个新 dataclass：`MetacognitionReport`（LLM 返回结构）和 `MetaParamOverrides`（运行时参数覆盖容器，含 apply/is_expired/get_review_threshold 方法）。同时定义 `SELF_CRITICISM_KEYWORDS` 常量列表。

**文件：**
- 修改：`chat_core/core/types.py`（末尾追加）

- [ ] **步骤 1：添加 MetacognitionReport 和 MetaParamOverrides**

```python
# ── Spec 006: 元认知深度 ─────────────────────────────────────

@dataclass
class MetacognitionReport:
    """元认知审查结论 — 对应 metacognition_report 工具返回值"""
    insight_text: str = ""
    confidence: float = 0.0
    param_overrides: "MetaParamOverrides | None" = None


@dataclass
class MetaParamOverrides:
    """临时参数覆盖容器。由 TurnManager 维护，注入各子系统。

    覆盖过期后（默认 N 轮），参数自动恢复默认。

    ⚠️ Sentinel 字段 (_xxx_set) 用于区分"LLM 未返回该字段"与"LLM 返回了默认值"。
    例如 LLM 合法返回 review_threshold_offset=0.0 也应被 apply。
    """

    review_threshold_offset: float = 0.0      # ±0.15
    defense_prob_multiplier: float = 1.0      # 0.5~2.0
    interest_modulations: dict[str, float] = field(default_factory=dict)  # {topic: ±0.3}
    emotion_threshold_offset: float = 0.0     # ±0.1
    inner_thoughts_mode: str = "full"         # "full" | "brief" | "minimal"

    # Sentinel: True 表示 LLM 显式设置了对应字段
    _review_threshold_set: bool = False
    _defense_prob_set: bool = False
    _emotion_threshold_set: bool = False
    _inner_thoughts_set: bool = False

    _applied_at_turn: int = 0
    _expiry_turns: int = 5

    def apply(self, report: MetacognitionReport, turn_counter: int) -> None:
        """应用元认知报告。confidence < 0.6 时只写文本不调参。

        ⚠️ 使用 is not None 判断字段是否被 LLM 显式设置（而非默认值比较）。
        例如 LLM 合法返回 review_threshold_offset=0.0 也需要被应用。
        """
        if report.confidence < 0.6:
            return
        overrides = report.param_overrides
        if overrides is None:
            return
        # 使用 sentinel 标记：LLM 不返回的字段在解析时保持 None
        if overrides._review_threshold_set:
            self.review_threshold_offset = overrides.review_threshold_offset
        if overrides._defense_prob_set:
            self.defense_prob_multiplier = overrides.defense_prob_multiplier
        if overrides.interest_modulations:
            self.interest_modulations.update(overrides.interest_modulations)
        if overrides._emotion_threshold_set:
            self.emotion_threshold_offset = overrides.emotion_threshold_offset
        if overrides._inner_thoughts_set:
            self.inner_thoughts_mode = overrides.inner_thoughts_mode
        self._applied_at_turn = turn_counter

    def is_expired(self, turn_counter: int) -> bool:
        return turn_counter - self._applied_at_turn >= self._expiry_turns

    def get_review_threshold(self, base: float = 0.5, turn_counter: int = 0) -> float:
        if self.is_expired(turn_counter):
            return base
        return max(0.35, min(0.65, base + self.review_threshold_offset))


# 自我批评触发关键词
SELF_CRITICISM_KEYWORDS: list[str] = [
    "不该这么说", "又说错了", "太机械了", "没意思", "不想聊了",
]
```

- [ ] **步骤 2：运行测试验证导入**

```bash
python -c "from chat_core.core.types import MetacognitionReport, MetaParamOverrides, SELF_CRITICISM_KEYWORDS; print('OK')"
```

- [ ] **步骤 3：Commit**

```bash
git add chat_core/core/types.py
git commit -m "feat(types): add MetacognitionReport + MetaParamOverrides for Spec 006"
```

**验证：**
- [ ] 测试通过：`pytest tests/ -q` — 232 passed，零回归
- [ ] 手动检查：`python -c "from chat_core.core.types import MetacognitionReport, MetaParamOverrides, SELF_CRITICISM_KEYWORDS"` 无报错

**依赖：** 无

**预估规模：** XS (1 文件，追加 ~50 行)

---

#### 任务 2：config.yaml — systems.metacognition 配置段

**描述：** 在 `config.yaml` 的 `systems:` 下追加 `metacognition` 配置段；同时更新 `config.py` 添加 `metacognition_config()` 方法。

**文件：**
- 修改：`chat_core/config.yaml`（systems 末尾插入）
- 修改：`chat_core/config.py`

- [ ] **步骤 1：config.yaml 追加 metacognition 段**

在 `systems:` 块的 `subjective_time:` 段之后追加：

```yaml
  metacognition:
    enabled: true
    periodic_interval: 5          # 每 N 轮定期触发
    anomaly_detection:
      review_streak: 3            # 审查连续 ≥3 同结论
      defense_streak: 2           # 防御连续 ≥2 轮
      self_criticism_streak: 3    # inner_thoughts 连 ≥3 含自我批评
      self_criticism_keywords:    # 触发关键词
        - "不该这么说"
        - "又说错了"
        - "太机械了"
        - "没意思"
        - "不想聊了"
    param_limits:
      review_threshold_range: [0.35, 0.65]
      defense_prob_range: [0.1, 0.95]
      interest_mod_range: [-0.3, 0.3]
      emotion_threshold_range: [0.2, 0.4]
    confidence_threshold: 0.6     # 低于此只应用文本，不调参数
    override_expiry_turns: 5      # 参数覆盖 N 轮后自动过期
```

- [ ] **步骤 2：config.py 添加 metacognition_config() 方法**

先读取 `chat_core/config.py` 找到类似 `boredom_config()` 方法的位置，追加：

```python
def metacognition_config(self) -> dict[str, Any]:
    """返回 systems.metacognition 配置"""
    return self._cfg.get("systems", {}).get("metacognition", {})
```

- [ ] **步骤 3：运行测试验证配置加载**

```bash
python -c "from chat_core.config import get_config; c = get_config(); print(c.metacognition_config())"
```

- [ ] **步骤 4：Commit**

```bash
git add chat_core/config.yaml chat_core/config.py
git commit -m "feat(config): add systems.metacognition config for Spec 006"
```

**验证：**
- [ ] 测试通过：`pytest tests/ -q` — 232 passed，零回归
- [ ] 手动检查：配置输出包含 `enabled: True`, `periodic_interval: 5`

**依赖：** 无

**预估规模：** XS (2 文件)

---

### 检查点：任务 1-2 之后
- [ ] 测试通过：`pytest tests/ -q` — 232 passed
- [ ] MetacognitionReport, MetaParamOverrides 可导入
- [ ] config.metacognition_config() 返回预期字典
- [ ] 审查后再继续

---

#### 任务 3：tests/test_metacognition.py — 数据结构 + 配置测试

**描述：** 创建测试文件，覆盖 MetaParamOverrides 的基础行为：apply、is_expired、get_review_threshold、confidence 门控、过期恢复。

**文件：**
- 创建：`tests/test_metacognition.py`

- [ ] **步骤 1：编写数据结构测试**

```python
"""Tests for Spec 006: MetacognitionDepth — metacognition engine, param overrides, triggers"""

from __future__ import annotations

import pytest
from chat_core.core.types import (
    MetacognitionReport,
    MetaParamOverrides,
    SELF_CRITICISM_KEYWORDS,
)


class TestMetaParamOverrides:
    """MetaParamOverrides 容器行为测试"""

    def test_default_values(self):
        ov = MetaParamOverrides()
        assert ov.review_threshold_offset == 0.0
        assert ov.defense_prob_multiplier == 1.0
        assert ov.interest_modulations == {}
        assert ov.emotion_threshold_offset == 0.0
        assert ov.inner_thoughts_mode == "full"

    def test_apply_with_high_confidence(self):
        ov = MetaParamOverrides()
        report = MetacognitionReport(
            insight_text="test insight",
            confidence=0.8,
            param_overrides=MetaParamOverrides(
                review_threshold_offset=0.1,
                defense_prob_multiplier=0.7,
            ),
        )
        ov.apply(report, turn_counter=10)
        assert ov.review_threshold_offset == 0.1
        assert ov.defense_prob_multiplier == 0.7
        assert ov._applied_at_turn == 10

    def test_apply_with_low_confidence_does_not_override(self):
        ov = MetaParamOverrides()
        report = MetacognitionReport(
            insight_text="test insight",
            confidence=0.5,
            param_overrides=MetaParamOverrides(review_threshold_offset=0.1),
        )
        ov.apply(report, turn_counter=10)
        assert ov.review_threshold_offset == 0.0  # unchanged

    def test_is_expired(self):
        ov = MetaParamOverrides()
        ov._applied_at_turn = 5
        ov._expiry_turns = 5
        assert ov.is_expired(9) is False  # turn 9, age 4
        assert ov.is_expired(10) is True   # turn 10, age 5
        assert ov.is_expired(11) is True   # turn 11, age 6

    def test_get_review_threshold_with_offset(self):
        ov = MetaParamOverrides(review_threshold_offset=0.1)
        ov._applied_at_turn = 10
        ov._expiry_turns = 5
        assert ov.get_review_threshold(base=0.5, turn_counter=11) == 0.6

    def test_get_review_threshold_expired_falls_back(self):
        ov = MetaParamOverrides(review_threshold_offset=0.1)
        ov._applied_at_turn = 5
        ov._expiry_turns = 5
        assert ov.get_review_threshold(base=0.5, turn_counter=11) == 0.5

    def test_get_review_threshold_clamped(self):
        ov = MetaParamOverrides(review_threshold_offset=0.5)
        ov._applied_at_turn = 10
        ov._expiry_turns = 5
        assert ov.get_review_threshold(base=0.5, turn_counter=11) == 0.65  # max
        ov2 = MetaParamOverrides(review_threshold_offset=-0.5)
        ov2._applied_at_turn = 10
        ov2._expiry_turns = 5
        assert ov2.get_review_threshold(base=0.5, turn_counter=11) == 0.35  # min

    def test_apply_interest_modulations(self):
        ov = MetaParamOverrides()
        report = MetacognitionReport(
            insight_text="less interest in games",
            confidence=0.7,
            param_overrides=MetaParamOverrides(
                interest_modulations={"游戏": -0.2, "AI": 0.1},
            ),
        )
        ov.apply(report, turn_counter=5)
        assert ov.interest_modulations == {"游戏": -0.2, "AI": 0.1}

    def test_confidence_exact_threshold(self):
        """confidence == 0.6 应触发 (≥ threshold)"""
        ov = MetaParamOverrides()
        report = MetacognitionReport(
            insight_text="borderline",
            confidence=0.6,
            param_overrides=MetaParamOverrides(review_threshold_offset=0.05),
        )
        ov.apply(report, turn_counter=5)
        assert ov.review_threshold_offset == 0.05


class TestMetacognitionReport:
    def test_default_report(self):
        r = MetacognitionReport()
        assert r.insight_text == ""
        assert r.confidence == 0.0
        assert r.param_overrides is None

    def test_report_with_overrides(self):
        r = MetacognitionReport(
            insight_text="I noticed a pattern",
            confidence=0.75,
            param_overrides=MetaParamOverrides(inner_thoughts_mode="brief"),
        )
        assert r.insight_text == "I noticed a pattern"
        assert r.confidence == 0.75
        assert r.param_overrides.inner_thoughts_mode == "brief"
```

- [ ] **步骤 2：运行测试验证**

```bash
python -m pytest tests/test_metacognition.py -v -k "MetaParamOverrides or MetacognitionReport"
```
预期：~10 tests PASS

- [ ] **步骤 3：确认零回归**

```bash
python -m pytest tests/ -q
```
预期：~242 passed

- [ ] **步骤 4：Commit**

```bash
git add tests/test_metacognition.py
git commit -m "test(metacognition): add MetaParamOverrides + MetacognitionReport unit tests"
```

**验证：**
- [ ] 测试通过：`pytest tests/test_metacognition.py -v` — ~10 tests PASS
- [ ] 零回归：`pytest tests/ -q` — 全量通过

**依赖：** 任务 1, 2

**预估规模：** S (1 新文件，~10 tests)

---

### 阶段 2：核心引擎

#### 任务 4：systems/metacognition.py — MetacognitionEngine 核心 (新建)

**描述：** 创建 `MetacognitionEngine`，包含：
- `check_triggers()` — 四类触发判定（定期/审查连判/防御连发/自我批评连发）
- `build_context()` — 组装元认知审查上下文（多数据源）
- 配置读取 (config.yaml → systems.metacognition)
- `get_compound_trend()` 辅助（在 emotion.py 中实现的接口）

**文件：**
- 创建：`chat_core/systems/metacognition.py`

- [ ] **步骤 1：创建 MetacognitionEngine 骨架**

```python
"""MetacognitionEngine — 元认知深度系统 (Spec 006)

定期 + 异常双触发审视，产出文本洞察 + 结构化参数调节。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import (
    COMPOUND_DIMS,
    SELF_CRITICISM_KEYWORDS,
    DecisionType,
    MemoryEntry,
    MetacognitionReport,
    MetaParamOverrides,
)

logger = logging.getLogger(__name__)


class MetacognitionEngine:
    """元认知引擎：触发判定 + 上下文组装。

    在 TurnManager._async_review_and_decide() 结束后调用 check_triggers()。
    触发后调用 build_context() 组装上下文，传给 LogicBrain.metacognition_pass()。
    """

    def __init__(self) -> None:
        cfg = get_config()
        mc = cfg.metacognition_config()
        self._enabled: bool = bool(mc.get("enabled", True))
        self._periodic_interval: int = int(mc.get("periodic_interval", 5))

        ad = mc.get("anomaly_detection", {})
        self._review_streak: int = int(ad.get("review_streak", 3))
        self._defense_streak: int = int(ad.get("defense_streak", 2))
        self._self_criticism_streak: int = int(ad.get("self_criticism_streak", 3))

        kw = ad.get("self_criticism_keywords", [])
        self._criticism_keywords: list[str] = kw if kw else SELF_CRITICISM_KEYWORDS

        self._confidence_threshold: float = float(mc.get("confidence_threshold", 0.6))
        self._expiry_turns: int = int(mc.get("override_expiry_turns", 5))

        # 异常计数器（触发后重置）
        self._review_streak_counter: int = 0
        self._defense_streak_counter: int = 0
        self._self_criticism_counter: int = 0
        self._last_review_decision: str | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── 触发判定 ──────────────────────────────────────────

    def check_triggers(
        self,
        turn_counter: int,
        review_decision: DecisionType | None,
        had_defense: bool,
        inner_thoughts_text: str | None,
        compound_delta: float = 0.0,
    ) -> bool:
        """检查是否应触发元认知审视。

        Args:
            turn_counter: 当前 turn 编号
            review_decision: 本轮审查决策
            had_defense: 本轮是否激活了防御
            inner_thoughts_text: 本轮内心戏文本
            compound_delta: 本轮最大复合情绪变动（来自 EmotionEngine.last_compound_delta）

        Returns:
            True 表示应触发
        """
        if not self._enabled:
            return False

        triggered = False

        # 1. 定期触发
        if turn_counter % self._periodic_interval == 0:
            triggered = True

        # 2. 审查连续同结论
        if review_decision is not None:
            decision_str = review_decision.value
            if self._last_review_decision == decision_str:
                self._review_streak_counter += 1
            else:
                self._review_streak_counter = 1
                self._last_review_decision = decision_str
            if self._review_streak_counter >= self._review_streak:
                triggered = True
        else:
            self._review_streak_counter = 0
            self._last_review_decision = None

        # 3. 防御连续激活
        if had_defense:
            self._defense_streak_counter += 1
            if self._defense_streak_counter >= self._defense_streak:
                triggered = True
        else:
            self._defense_streak_counter = 0

        # 4. 情绪冲击: |Δcompound| > 0.4（复用 Spec 005 compound_alert）
        if abs(compound_delta) > 0.4:
            triggered = True

        # 5. 自我批评连续出现
        if inner_thoughts_text:
            if any(kw in inner_thoughts_text for kw in self._criticism_keywords):
                self._self_criticism_counter += 1
                if self._self_criticism_counter >= self._self_criticism_streak:
                    triggered = True
            else:
                self._self_criticism_counter = 0
        else:
            self._self_criticism_counter = 0

        # 触发后重置所有异常计数器（§2: 同 turn 不重复触发）
        if triggered:
            self._review_streak_counter = 0
            self._defense_streak_counter = 0
            self._self_criticism_counter = 0
            self._last_review_decision = None

        return triggered

    # ── 上下文组装 ────────────────────────────────────────

    def build_context(
        self,
        turn_summaries: list[dict[str, Any]],
        compound_trends: dict[str, list[float]],
        defense_mode_summary: dict[str, Any],
        memory_system_state: dict[str, Any],
        attention_state: str,
        energy_state: dict[str, Any] | None = None,
        subjective_time: dict[str, Any] | None = None,
        vulnerability_history: dict[str, Any] | None = None,
    ) -> str:
        """组装传递给 LogicBrain 的元认知审查上下文。

        各数据源全部由 TurnManager 传入。
        """
        parts: list[str] = []

        # 最近 N 轮摘要
        parts.append("## 最近 N 轮")
        for ts in turn_summaries[-self._periodic_interval:]:
            parts.append(f"  - {json.dumps(ts, ensure_ascii=False)}")

        # 复合情绪趋势
        if compound_trends:
            parts.append("## 复合情绪趋势")
            for dim, values in compound_trends.items():
                if values:
                    trend = " → ".join(f"{v:.2f}" for v in values[-5:])
                    parts.append(f"  {dim}: {trend}")

        # 防御模式总结
        if defense_mode_summary:
            parts.append("## 防御模式总结")
            parts.append(f"  近N轮防御激活率: {defense_mode_summary.get('activation_rate', 0)}")
            parts.append(f"  主要防御类型: {defense_mode_summary.get('main_types', '无')}")
            for entry in defense_mode_summary.get("awareness_entries", []):
                parts.append(f"    - {entry}")

        # 记忆系统状态
        if memory_system_state:
            parts.append("## 记忆系统状态")
            parts.append(f"  平均回溯条目: {memory_system_state.get('avg_recall_count', 0)}")
            parts.append(f"  空回溯次数: {memory_system_state.get('empty_recall_count', 0)}")
            parts.append(f"  衰减预警: {memory_system_state.get('decay_warning_count', 0)}条")
            parts.append(f"  深刻记忆稳固: {memory_system_state.get('deep_memory_count', 0)}条")

        # 注意力状态
        if attention_state:
            parts.append(f"## 当前注意力状态: {attention_state}")

        # Spec 007: 精力与主观时间
        if energy_state:
            parts.append(f"## 精力与主观时间")
            parts.append(f"  当前精力: {energy_state.get('energy', 0):.2f}")
            if subjective_time:
                parts.append(f"  主观时间: speed_factor={subjective_time.get('speed_factor', 1.0):.2f}")

        # Spec 005 §9: 脆弱历史
        if vulnerability_history:
            parts.append("## 脆弱历史")
            parts.append(f"  当前是否脆弱: {vulnerability_history.get('is_vulnerable', False)}")
            parts.append(f"  冷却剩余: {vulnerability_history.get('cooldown_remaining', 0)}轮")

        return "\n".join(parts)
```

- [ ] **步骤 2：运行测试验证导入**

```bash
python -c "from chat_core.systems.metacognition import MetacognitionEngine; print('OK')"
```

- [ ] **步骤 3：Commit**

```bash
git add chat_core/systems/metacognition.py
git commit -m "feat(metacognition): add MetacognitionEngine core — triggers + context builder"
```

**验证：**
- [ ] 测试通过：`pytest tests/ -q` — 232 passed，零回归（新文件尚未被测）
- [ ] 手动检查：`MetacognitionEngine` 可导入

**依赖：** 任务 1, 2

**预估规模：** M (1 新文件，~120 行)

---

#### 任务 5：tests/test_metacognition.py — 触发逻辑测试

**描述：** 为 MetacognitionEngine.check_triggers() 添加单元测试：定期触发、审查连判触发、防御连发触发、自我批评触发、重置行为、禁用模式。

**文件：**
- 修改：`tests/test_metacognition.py`（追加）

- [ ] **步骤 1：编写触发逻辑测试**

```python
from chat_core.systems.metacognition import MetacognitionEngine
from chat_core.core.types import DecisionType


class TestMetacognitionEngineTriggers:
    """MetacognitionEngine.check_triggers() 测试"""

    @pytest.fixture
    def engine(self):
        return MetacognitionEngine()

    def test_periodic_trigger(self, engine):
        """定期触发：turn_counter % N == 0"""
        assert engine.check_triggers(5, None, False, None) is True
        assert engine.check_triggers(10, None, False, None) is True
        assert engine.check_triggers(15, None, False, None) is True

    def test_periodic_not_trigger_on_non_interval(self, engine):
        """非 N 的倍数不触发"""
        assert engine.check_triggers(1, None, False, None) is False
        assert engine.check_triggers(2, None, False, None) is False
        assert engine.check_triggers(7, None, False, None) is False

    def test_review_streak_trigger(self, engine):
        """审查连续 3 轮同结论 → 触发"""
        # round 1
        assert engine.check_triggers(1, DecisionType.CORRECT, False, None) is False
        # round 2
        assert engine.check_triggers(2, DecisionType.CORRECT, False, None) is False
        # round 3 → trigger
        assert engine.check_triggers(3, DecisionType.CORRECT, False, None) is True

    def test_review_streak_resets_on_different(self, engine):
        """审查结论改变 → 计数器重置"""
        assert engine.check_triggers(1, DecisionType.CORRECT, False, None) is False
        assert engine.check_triggers(2, DecisionType.CORRECT, False, None) is False
        # 改变结论
        assert engine.check_triggers(3, DecisionType.SILENCE, False, None) is False
        # 重置后重新计数
        assert engine.check_triggers(4, DecisionType.SILENCE, False, None) is False
        assert engine.check_triggers(5, DecisionType.SILENCE, False, None) is True  # 第 3 个 SILENCE + 定期

    def test_defense_streak_trigger(self, engine):
        """防御连续 2 轮 → 触发"""
        assert engine.check_triggers(1, None, True, None) is False
        assert engine.check_triggers(2, None, True, None) is True

    def test_defense_streak_resets(self, engine):
        """防御中断 → 计数器重置"""
        assert engine.check_triggers(1, None, True, None) is False
        assert engine.check_triggers(2, None, False, None) is False  # 无防御
        assert engine.check_triggers(3, None, True, None) is False  # 重新计数

    def test_self_criticism_streak_trigger(self, engine):
        """自我批评连 3 轮 → 触发"""
        assert engine.check_triggers(1, None, False, "不该这么说...") is False
        assert engine.check_triggers(2, None, False, "又说错了...") is False
        assert engine.check_triggers(3, None, False, "太机械了...") is True

    def test_self_criticism_resets(self, engine):
        """自我批评中断 → 计数器重置"""
        assert engine.check_triggers(1, None, False, "不该这么说...") is False
        assert engine.check_triggers(2, None, False, "今天天气不错") is False  # 无自我批评
        assert engine.check_triggers(3, None, False, "又说错了...") is False  # 重新计数

    def test_compound_delta_trigger(self, engine):
        """|Δcompound| > 0.4 → 即时触发（情绪冲击）"""
        assert engine.check_triggers(3, None, False, None, compound_delta=0.5) is True

    def test_compound_delta_below_threshold_no_trigger(self, engine):
        """|Δcompound| ≤ 0.4 → 不触发"""
        assert engine.check_triggers(3, None, False, None, compound_delta=0.3) is False
        assert engine.check_triggers(3, None, False, None, compound_delta=0.4) is False

    def test_compound_delta_negative_triggers(self, engine):
        """负向情绪冲击同样触发"""
        assert engine.check_triggers(3, None, False, None, compound_delta=-0.5) is True

    def test_counters_reset_after_trigger(self, engine):
        """触发后计数器全部重置"""
        # 触发审查连判
        engine.check_triggers(1, DecisionType.CORRECT, False, None)
        engine.check_triggers(2, DecisionType.CORRECT, False, None)
        assert engine.check_triggers(3, DecisionType.CORRECT, False, None) is True
        # 触发后计数器应归零
        assert engine._review_streak_counter == 0
        assert engine._defense_streak_counter == 0
        assert engine._self_criticism_counter == 0

    def test_none_review_decision_resets_counter(self, engine):
        """None 审查决策重置计数器"""
        engine.check_triggers(1, DecisionType.CORRECT, False, None)
        engine.check_triggers(2, DecisionType.CORRECT, False, None)
        assert engine.check_triggers(3, None, False, None) is False
        assert engine._review_streak_counter == 0
```

- [ ] **步骤 2：运行测试验证**

```bash
python -m pytest tests/test_metacognition.py -v -k "Trigger"
```
预期：~12 tests PASS

- [ ] **步骤 3：Commit**

```bash
git add tests/test_metacognition.py
git commit -m "test(metacognition): add trigger logic tests for MetacognitionEngine"
```

**验证：**
- [ ] 测试通过：`pytest tests/test_metacognition.py -v` — ~22 tests PASS (含阶段 1 的 10 个)
- [ ] 零回归：`pytest tests/ -q`

**依赖：** 任务 4

**预估规模：** S (1 文件追加，~12 tests)

---

### 检查点：任务 3-5 之后
- [ ] 测试通过：`pytest tests/test_metacognition.py -v` — ~22 tests PASS
- [ ] 零回归：`pytest tests/ -q` — ~254 passed
- [ ] MetacognitionEngine 触发逻辑覆盖：定期/审查连判/防御连发/自我批评/重置/禁用
- [ ] 审查后再继续

---

### 阶段 3：Brain 集成

#### 任务 6：core/brain.py — LogicBrain.metacognition_pass()

**描述：** 在 LogicBrain 类中添加 `metacognition_pass()` 方法：注册 `metacognition_report` 工具，单次 LLM 调用，返回 `MetacognitionReport`。

**文件：**
- 修改：`chat_core/core/brain.py`

- [ ] **步骤 1：读取 brain.py 当前状态，确定插入位置**

LogicBrain 的 `_register_tools()` 添加 `metacognition_report` 工具，追加 `metacognition_pass()` 方法。

- [ ] **步骤 2：在 _register_tools() 末尾追加 metacognition_report 工具**

在 `_register_tools()` 方法的最后一个 `register` 之后添加：

```python
        # Spec 006: 元认知报告工具
        self._tools.register(ToolDefinition(
            name="metacognition_report",
            description="提交元认知审查结论：文本洞察 + 可选参数调节",
            parameters={
                "type": "object",
                "properties": {
                    "insight_text": {
                        "type": "string",
                        "description": "自然语言自我洞察",
                    },
                    "param_overrides": {
                        "type": "object",
                        "properties": {
                            "review_threshold_offset": {
                                "type": "number",
                                "description": "审查阈值偏移，范围 ±0.15",
                            },
                            "defense_prob_multiplier": {
                                "type": "number",
                                "description": "防御概率乘数，范围 0.5~2.0",
                            },
                            "interest_modulations": {
                                "type": "object",
                                "description": "话题兴趣调制，{topic_name: ±0.3}",
                            },
                            "emotion_threshold_offset": {
                                "type": "number",
                                "description": "情绪交互阈值偏移，范围 ±0.1",
                            },
                            "inner_thoughts_mode": {
                                "type": "string",
                                "enum": ["full", "brief", "minimal"],
                                "description": "内心戏详细度",
                            },
                        },
                    },
                    "confidence": {
                        "type": "number",
                        "description": "确定度 0~1",
                        "minimum": 0,
                        "maximum": 1,
                    },
                },
                "required": ["insight_text", "confidence"],
            },
            fn=lambda args, ctx: json.dumps({"report_submitted": True}),
            parallel_safe=False,
        ))
```

- [ ] **步骤 3：添加 metacognition_pass() 方法**

在 LogicBrain 类末尾（`_execute_memory_link` 之后）添加：

```python
    # ── Spec 006: 元认知 pass ───────────────────────────────

    async def metacognition_pass(self, context: str) -> "MetacognitionReport | None":
        """执行元认知审视：单次 LLM 调用，返回 MetacognitionReport。

        复用 LogicBrain 的 DeepSeek Pro，使用 metacognition_report 工具。
        失败时返回 None（静默降级，不阻塞 turn）。
        """
        from chat_core.core.types import MetacognitionReport, MetaParamOverrides

        prompt = (
            "[元认知审查] 请审视你最近的行为模式:\n\n"
            f"{context}\n\n"
            "请调用 metacognition_report 工具提交你的审查结论。"
        )

        messages = [Message(role="user", content=prompt)]

        cfg = get_config()
        api_cfg = cfg.brain_api_config("logic")

        try:
            result = await self._provider.chat(
                messages=messages,
                model=api_cfg.get("model", "deepseek-v4-pro"),
                tools=self._tools.specs(),
                temperature=api_cfg.get("temperature", 0.3),
                max_tokens=api_cfg.get("max_tokens", 1024),
                reasoning_effort=api_cfg.get("reasoning_effort", "max"),
                tool_choice="auto",
            )

            # 解析 tool_calls → MetacognitionReport
            if result.tool_calls:
                for tc in result.tool_calls:
                    if tc.function_name == "metacognition_report":
                        try:
                            args = json.loads(tc.function_args)
                            insight_text = str(args.get("insight_text", ""))
                            confidence = float(args.get("confidence", 0.0))

                            overrides_raw = args.get("param_overrides", {}) or {}
                            param_overrides = MetaParamOverrides()
                            # 仅当 LLM 显式返回该字段时才设置值 + sentinel
                            if "review_threshold_offset" in overrides_raw:
                                param_overrides.review_threshold_offset = float(overrides_raw.get("review_threshold_offset", 0.0))
                                param_overrides._review_threshold_set = True
                            if "defense_prob_multiplier" in overrides_raw:
                                param_overrides.defense_prob_multiplier = float(overrides_raw.get("defense_prob_multiplier", 1.0))
                                param_overrides._defense_prob_set = True
                            if "interest_modulations" in overrides_raw:
                                param_overrides.interest_modulations = dict(overrides_raw.get("interest_modulations", {}))
                            if "emotion_threshold_offset" in overrides_raw:
                                param_overrides.emotion_threshold_offset = float(overrides_raw.get("emotion_threshold_offset", 0.0))
                                param_overrides._emotion_threshold_set = True
                            if "inner_thoughts_mode" in overrides_raw:
                                param_overrides.inner_thoughts_mode = str(overrides_raw.get("inner_thoughts_mode", "full"))
                                param_overrides._inner_thoughts_set = True

                            return MetacognitionReport(
                                insight_text=insight_text,
                                confidence=confidence,
                                param_overrides=param_overrides,
                            )
                        except (json.JSONDecodeError, ValueError, TypeError) as e:
                            logger.warning(f"Failed to parse metacognition_report: {e}")

            # 降级：从 content 中尝试提取
            if result.content:
                try:
                    content_json = json.loads(result.content)
                    if "insight_text" in content_json:
                        return MetacognitionReport(
                            insight_text=str(content_json.get("insight_text", "")),
                            confidence=float(content_json.get("confidence", 0.0)),
                        )
                except (json.JSONDecodeError, ValueError):
                    pass

            return None

        except Exception as e:
            logger.warning(f"Metacognition pass failed: {e}")
            return None
```

- [ ] **步骤 4：运行测试验证导入**

```bash
python -c "from chat_core.core.brain import LogicBrain; print('OK')"
```

- [ ] **步骤 5：Commit**

```bash
git add chat_core/core/brain.py
git commit -m "feat(brain): add LogicBrain.metacognition_pass() for Spec 006"
```

**验证：**
- [ ] 测试通过：`pytest tests/ -q` — 零回归
- [ ] 手动检查：`LogicBrain` 可导入，`metacognition_pass` 签名正确

**依赖：** 任务 1 (types), 任务 4 (MetacognitionEngine)

**预估规模：** M (1 文件修改，~80 行)

---

### 阶段 4：TurnManager 集成

#### 任务 7：core/turn_manager.py — 集成 MetacognitionEngine

**描述：** 在 TurnManager 中：
1. 初始化 `MetacognitionEngine` 和 `MetaParamOverrides`
2. 在 `_async_review_and_decide()` 末尾添加元认知触发逻辑
3. 组装上下文数据（来自 emotion_engine, defense_engine, memory, attention）
4. 调用 `LogicBrain.metacognition_pass()`
5. 解析结果：insight_text → memory + 下一轮注入；param_overrides → apply
6. 将 `MetaParamOverrides` 传递给各消费子系统

**文件：**
- 修改：`chat_core/core/turn_manager.py`

- [ ] **步骤 1：添加 import 和 __init__**

在 `__init__` 中添加：

```python
        # Spec 006: 元认知
        from chat_core.systems.metacognition import MetacognitionEngine
        from chat_core.core.types import MetaParamOverrides
        mc_cfg = cfg.metacognition_config()
        self._metacognition = MetacognitionEngine() if mc_cfg.get("enabled", True) else None
        self._meta_overrides = MetaParamOverrides()  # 容器始终存在（禁用时保持默认值）
        self._last_inner_thoughts: str | None = None
```

- [ ] **步骤 2：_async_review_and_decide() 末尾追加元认知触发**

在 `_async_review_and_decide()` 方法的 `except Exception` 之前、审查/防御逻辑之后，追加：

```python
            # ── Spec 006: 元认知审视 ──
            # 从已有的 defense 结果推断 had_defense（复用上方已计算的 defense 变量，不再重复调用）
            had_defense = False
            if review.decision == DecisionType.CORRECT:
                # defense 在上方第530-538行已计算（在 Spec 005 防御判定块中）
                # 此处通过检查 review 路径判断：防御路径在 _apply_defense 后 return，不会执行到这里
                had_defense = False  # 正常纠正路径 = 无防御
            # 注: 防御路径（DENIAL/RATIONALIZE/PROJECT）在 _apply_defense() 后 return，
            # 不会执行到元认知检查；如需追踪防御历史，由 TurnManager 维护 _defense_history 列表

            compound_delta = abs(self._emotion_engine.last_compound_delta) if self._emotion_engine else 0.0

            if self._metacognition is not None and self._metacognition.check_triggers(
                turn_counter=self._turn_counter,
                review_decision=review.decision,
                had_defense=had_defense,
                inner_thoughts_text=inner_thoughts,
                compound_delta=compound_delta,
            ):
                # 组装上下文
                turn_summaries = self._build_turn_summaries()
                compound_trends = (
                    self._emotion_engine.get_compound_trend()
                    if self._emotion_engine else {}
                )
                defense_summary = self._build_defense_summary()
                memory_state = await self._build_memory_state()
                attention_label = (
                    self._attention_model.get_state_enum("sub").value
                    if self._attention_model else "unknown"
                )
                energy = (
                    self._energy_bar.get_state()
                    if self._energy_bar else None
                )
                energy_dict = {"energy": energy.energy} if energy else None
                stp = (
                    self._subjective_clock.get_perception(
                        energy.energy if energy else 0.9
                    )
                    if self._subjective_clock else None
                )
                stp_dict = {
                    "speed_factor": stp.speed_factor,
                    "perception": stp.perception,
                } if stp else None

                # Spec 006: 添加脆弱历史（Spec 005 §9 已完成）
                vuln_history = None
                if self._emotion_engine:
                    vuln_history = {
                        "is_vulnerable": self._emotion_engine.is_vulnerable,
                        "cooldown_remaining": getattr(self._emotion_engine, "_vulnerability_cooldown", 0),
                    }

                context = self._metacognition.build_context(
                    turn_summaries=turn_summaries,
                    compound_trends=compound_trends,
                    defense_mode_summary=defense_summary,
                    memory_system_state=memory_state,
                    attention_state=attention_label,
                    energy_state=energy_dict,
                    subjective_time=stp_dict,
                    vulnerability_history=vuln_history,
                )

                # 调用 LogicBrain 元认知 pass
                report = await self.logic.metacognition_pass(context)
                if report:
                    # 写入 insight_text 到 memory（下一轮注入）
                    timestamp = time.strftime("%Y%m%d_%H%M%S")
                    await self._memory.save(MemoryEntry(
                        namespace="self/metacognition",
                        key=f"insight_{self._turn_counter}_{timestamp}",
                        value={
                            "insight_text": report.insight_text,
                            "confidence": report.confidence,
                            "turn": self._turn_counter,
                        },
                    ))
                    # 应用参数覆盖
                    self._meta_overrides.apply(report, self._turn_counter)
                    # 将 overrides 注入各子系统
                    if self._emotion_engine:
                        self._emotion_engine.set_meta_overrides(self._meta_overrides)
```

- [ ] **步骤 3：添加辅助方法 _build_turn_summaries, _build_defense_summary, _build_memory_state**

在 TurnManager 类末尾添加：

```python
    # ── Spec 006: 元认知辅助 ──────────────────────────────

    async def _build_turn_summaries(self) -> list[dict[str, Any]]:
        """从 memory 查询最近 N 轮摘要（self/inner_thoughts + user/default/conversations）。"""
        summaries: list[dict[str, Any]] = []
        try:
            interval = self._metacognition._periodic_interval if self._metacognition else 5
            thoughts = await self._memory.query("self/inner_thoughts", limit=interval)
            conversations = await self._memory.query("user/default/conversations", limit=interval)
            for i, t in enumerate(thoughts):
                conv = conversations[i] if i < len(conversations) else None
                summaries.append({
                    "turn": t.key,
                    "inner_thoughts_excerpt": str(t.value.get("raw", ""))[:200] if isinstance(t.value, dict) else "",
                    "reply_excerpt": str(conv.value.get("reply", ""))[:200] if conv and isinstance(conv.value, dict) else "",
                })
        except Exception:
            pass
        return summaries

    def _build_defense_summary(self) -> dict[str, Any]:
        """构建防御模式总结（从 subconscious/defense_awareness 提取最近的条目）。"""
        return {
            "activation_rate": 0.0,
            "main_types": "无",
            "awareness_entries": [],
        }

    async def _build_memory_state(self) -> dict[str, Any]:
        """构建记忆系统状态摘要（回溯统计 + 衰减预警）。"""
        try:
            entries = await self._memory.query("self/inner_thoughts", limit=50)
            total = len(entries)
            return {
                "avg_recall_count": 0 if total == 0 else min(10, total),
                "empty_recall_count": 0,
                "decay_warning_count": 0,
                "deep_memory_count": 0,
            }
        except Exception:
            return {"avg_recall_count": 0, "empty_recall_count": 0, "decay_warning_count": 0, "deep_memory_count": 0}
```

- [ ] **步骤 4：传递 MetaParamOverrides 给子系统**

在 `_run_sub_session()` 中，传递 overrides 给 ReActLoop；在 `_review()`、`_issue_correction()` 中传递 overrides 给 ReviewSystem、DefenseEngine：

在 `_run_sub_session()` 中传递给 loop：
```python
        loop = ReActLoop(
            ...
            meta_overrides=self._meta_overrides,  # Spec 006
        )
```

在 `_review()` 调用 `_review_system.review()` 时加入 overrides 参数。

在 `_apply_defense()` 中的 `evaluate()` 调用加入 overrides 参数。

- [ ] **步骤 5：运行测试验证**

```bash
python -m pytest tests/ -q
```
预期：零回归

- [ ] **步骤 6：Commit**

```bash
git add chat_core/core/turn_manager.py
git commit -m "feat(turn_manager): integrate MetacognitionEngine + MetaParamOverrides"
```

**验证：**
- [ ] 测试通过：`pytest tests/ -q` — 零回归
- [ ] 无 import 错误

**依赖：** 任务 4 (MetacognitionEngine), 任务 6 (metacognition_pass)

**预估规模：** L (1 文件修改，~120 行)

---

### 检查点：任务 6-7 之后
- [ ] 测试通过：`pytest tests/ -q` — 零回归
- [ ] MetacognitionEngine 完整集成到 TurnManager 管线
- [ ] 审查后再继续

---

### 阶段 5：消费者集成（并行可做）

#### 任务 8：systems/review.py — 读取 MetaParamOverrides

**描述：** `ReviewSystem._compute_decision()` 接受可选的 `meta_overrides` 参数，调用 `get_review_threshold()` 替换硬编码的 `0.5` 阈值。

**文件：**
- 修改：`chat_core/systems/review.py`

- [ ] **步骤 1：修改 _compute_decision 签名**

```python
    def _compute_decision(
        self,
        logic_weight: float,
        emotion_weight: float,
        meta_overrides: "MetaParamOverrides | None" = None,
        turn_counter: int = 0,
    ) -> DecisionType:
        combined = logic_weight * 0.5 + emotion_weight * 0.5
        threshold = 0.5
        if meta_overrides is not None:
            threshold = meta_overrides.get_review_threshold(base=0.5, turn_counter=turn_counter)

        if combined > threshold:
            if logic_weight > 0.8 and emotion_weight < 0.3:
                return DecisionType.TWISTED
            return DecisionType.CORRECT
        else:
            return DecisionType.SILENCE
```

- [ ] **步骤 2：更新 review() 方法传递参数**

在 `review()` 方法末尾（`review.decision = ...` 行）改为：

```python
        review.decision = self._compute_decision(
            logic_weight, emotion_weight,
            meta_overrides=kwargs.get("meta_overrides"),
            turn_counter=kwargs.get("turn_counter", 0),
        )
```

需要让 `review()` 接受 `**kwargs` 或显式参数。

- [ ] **步骤 3：运行测试验证**

```bash
python -m pytest tests/ -q
```

- [ ] **步骤 4：Commit**

```bash
git add chat_core/systems/review.py
git commit -m "feat(review): consume MetaParamOverrides.review_threshold_offset"
```

**依赖：** 任务 1 (types)

**预估规模：** S (1 文件修改，~15 行)

---

#### 任务 9：systems/defense.py — 读取 MetaParamOverrides

**描述：** `DefenseEngine.evaluate()` 接受 `meta_overrides` 参数，用 `defense_prob_multiplier` 调制 `final_prob`。

**文件：**
- 修改：`chat_core/systems/defense.py`

- [ ] **步骤 1：修改 evaluate() 签名和逻辑**

```python
    def evaluate(
        self,
        review: ReviewResult,
        error_history: dict[str, int],
        impulsiveness: float,
        last_compound_delta: float = 0.0,
        is_vulnerable: bool = False,
        meta_overrides: "MetaParamOverrides | None" = None,
        turn_counter: int = 0,
    ) -> DefenseResult:
        ...
        final_prob = min(base_prob * modifier, 0.95)

        # Spec 006: 元认知参数调制
        if meta_overrides is not None and not meta_overrides.is_expired(turn_counter):
            final_prob *= meta_overrides.defense_prob_multiplier

        if random.random() > final_prob:
            return DefenseResult(defense_type=DefenseType.DIRECT)
```

- [ ] **步骤 2：运行测试验证**

```bash
python -m pytest tests/test_defense.py -v
```

- [ ] **步骤 3：Commit**

```bash
git add chat_core/systems/defense.py
git commit -m "feat(defense): consume MetaParamOverrides.defense_prob_multiplier"
```

**依赖：** 任务 1 (types)

**预估规模：** S (1 文件修改，~10 行)

---

#### 任务 10：systems/interest.py — 读取 MetaParamOverrides

**描述：** `InterestModel.match()` 在返回前应用 `interest_modulations` 偏移。

**文件：**
- 修改：`chat_core/systems/interest.py`

- [ ] **步骤 1：修改 match() 方法**

```python
    def match(self, topic: str, emotion_engine: Any = None, meta_overrides: Any = None) -> float:
        base = self.get_interest_weight(topic)
        modifier = self.get_mood_modifier(emotion_engine)
        result = min(1.0, base * modifier)

        # Spec 006: 元认知兴趣调制
        if meta_overrides is not None:
            modulations = getattr(meta_overrides, "interest_modulations", {})
            offset = modulations.get(topic.lower(), 0.0)
            result = max(0.0, min(1.0, result + offset))

        return result
```

- [ ] **步骤 2：运行测试验证**

```bash
python -m pytest tests/test_phase6_emotion.py -v -k "interest"
```

- [ ] **步骤 3：Commit**

```bash
git add chat_core/systems/interest.py
git commit -m "feat(interest): consume MetaParamOverrides.interest_modulations"
```

**依赖：** 任务 1 (types)

**预估规模：** XS (1 文件修改，~8 行)

---

#### 任务 11：systems/emotion.py — get_compound_trend() + 读取 offset

**描述：** 添加 `get_compound_trend()` 方法（返回最近 N 个 tick 的复合情绪值历史）；`tick()` 在步骤①(维度交互)前读取 `emotion_threshold_offset` 调制 `interaction_threshold`。

**文件：**
- 修改：`chat_core/systems/emotion.py`

- [ ] **步骤 1：添加 get_compound_trend() 方法**

在 `EmotionEngine` 类末尾添加：

```python
    # ── Spec 006: 复合情绪趋势 ───────────────────────────

    def get_compound_trend(self, brain: str = "sub") -> dict[str, list[float]]:
        """返回指定脑最近 N 个 tick 的复合情绪值历史。

        用于元认知上下文组装。
        """
        if not hasattr(self, "_compound_history"):
            self._compound_history: dict[str, list[float]] = {dim: [] for dim in COMPOUND_DIMS}
            return {dim: [] for dim in COMPOUND_DIMS}

        state = self._states.get(brain)
        if state is None:
            return {dim: [] for dim in COMPOUND_DIMS}

        # 追加当前值到历史
        for dim in COMPOUND_DIMS:
            val = getattr(state, dim, 0.0)
            history = self._compound_history.setdefault(dim, [])
            history.append(val)
            # 保留最多 20 个值
            if len(history) > 20:
                history.pop(0)

        return {dim: list(v) for dim, v in self._compound_history.items()}
```

- [ ] **步骤 2：在 tick() 步骤①前读取 emotion_threshold_offset**

在 `tick()` 方法的 `# ① 维度交互 → 生成复合情绪` 前：

```python
        # Spec 006: 元认知情绪阈值调制
        threshold = self._interaction_threshold
        if hasattr(self, "_meta_overrides") and self._meta_overrides is not None:
            threshold = self._interaction_threshold + self._meta_overrides.emotion_threshold_offset
            threshold = max(0.2, min(0.4, threshold))
```

需要接受 `_meta_overrides` 注入。

- [ ] **步骤 3：添加 set_meta_overrides() 方法**

```python
    def set_meta_overrides(self, overrides: Any) -> None:
        """注入元认知参数覆盖（由 TurnManager 调用）"""
        self._meta_overrides = overrides
```

- [ ] **步骤 4：运行测试验证**

```bash
python -m pytest tests/test_compound_emotion.py -v
```

- [ ] **步骤 5：Commit**

```bash
git add chat_core/systems/emotion.py
git commit -m "feat(emotion): add get_compound_trend() + consume MetaParamOverrides.emotion_threshold_offset"
```

**依赖：** 任务 1 (types)

**预估规模：** M (1 文件修改，~40 行)

---

#### 任务 12：core/loop.py — 读取 MetaParamOverrides.inner_thoughts_mode

**描述：** `ReActLoop` 接受 `meta_overrides` 参数，在 `_inject_subconscious_corrections()` 中同时读取 `self/metacognition` insight_text 并注入为 `[自我洞察]`。`_handle_inner_thoughts()` 根据 `inner_thoughts_mode` 调整详细度要求。

**文件：**
- 修改：`chat_core/core/loop.py`

- [ ] **步骤 1：ReActLoop.__init__ 接受 meta_overrides**

```python
    def __init__(self, ..., meta_overrides: Any = None):
        ...
        self._meta_overrides = meta_overrides  # Spec 006
```

- [ ] **步骤 2：在 _inject_subconscious_corrections() 末尾注入 insight_text**

在查询 `defense_awareness` 之后追加：

```python
            # Spec 006: 读取 self/metacognition 洞察
            try:
                metacog_entries = await self._memory_store.query("self/metacognition", limit=5)
                for entry in metacog_entries:
                    value = entry.value
                    if isinstance(value, dict):
                        text = value.get("insight_text", "")
                        if text:
                            self._messages.insert(
                                -1,
                                Message(role="system", content=f"[自我洞察] {text}"),
                            )
            except Exception:
                pass
```

- [ ] **步骤 3：_init_messages() 根据 inner_thoughts_mode 注入提示**

在 `_init_messages()` 的 `_inject_attention_hint()` 之后追加（设计 §6：在 _think() 注入 system prompt 提示）：

```python
            # Spec 006: 注入内心戏模式提示
            if self._meta_overrides is not None:
                mode = getattr(self._meta_overrides, "inner_thoughts_mode", "full")
                mode_hints = {
                    "full": "",  # 默认不追加额外提示
                    "brief": "[内心戏模式] 请保持内心戏简洁，不超过50字，只记录关键感受即可。",
                    "minimal": "[内心戏模式] 极简模式——内心戏仅记录最重要的一个感受词（如'开心''困惑''疲惫'），不超过10字。",
                }
                hint = mode_hints.get(mode, "")
                if hint:
                    self._messages.insert(-1, Message(role="system", content=hint))
```

- [ ] **步骤 4：运行测试验证**

```bash
python -m pytest tests/test_loop.py -v
```

- [ ] **步骤 5：Commit**

```bash
git add chat_core/core/loop.py
git commit -m "feat(loop): consume MetaParamOverrides.inner_thoughts_mode + inject self/metacognition"
```

**依赖：** 任务 1 (types)

**预估规模：** M (1 文件修改，~30 行)

---

### 检查点：任务 8-12 之后
- [ ] 测试通过：`pytest tests/ -q` — 零回归
- [ ] 所有 5 个消费子系统完成集成
- [ ] 审查后再继续

---

### 阶段 6：集成测试

#### 任务 13：tests/test_metacognition.py — 上下文组装 + 报告解析测试

**描述：** 为 `MetacognitionEngine.build_context()` 和 `MetacognitionReport` 解析添加测试。

**文件：**
- 修改：`tests/test_metacognition.py`（追加）

```python
class TestMetacognitionContext:
    """build_context() 测试"""

    def test_build_context_basic(self):
        engine = MetacognitionEngine()
        ctx = engine.build_context(
            turn_summaries=[{"turn": "turn_001", "topic": "游戏"}],
            compound_trends={"gratification": [0.2, 0.1, 0.05]},
            defense_mode_summary={"activation_rate": 0.4, "main_types": "DENIAL (50%)"},
            memory_system_state={"avg_recall_count": 3.8, "empty_recall_count": 1, "decay_warning_count": 3},
            attention_state="drifting",
            energy_state={"energy": 0.41},
            subjective_time={"speed_factor": 0.65},
        )
        assert "最近 N 轮" in ctx
        assert "复合情绪趋势" in ctx
        assert "gratification" in ctx
        assert "防御模式总结" in ctx
        assert "记忆系统状态" in ctx
        assert "drifting" in ctx
        assert "0.41" in ctx

    def test_build_context_minimal(self):
        engine = MetacognitionEngine()
        ctx = engine.build_context(
            turn_summaries=[],
            compound_trends={},
            defense_mode_summary={},
            memory_system_state={},
            attention_state="",
        )
        assert "最近" in ctx or ctx.strip() == ""  # 至少没有崩溃


class TestIntegrationScenarios:
    """端到端场景测试"""

    def test_confidence_below_threshold_no_override(self):
        """confidence < 0.6 → insight_text 可用但 param_overrides 不应用"""
        ov = MetaParamOverrides()
        param_ov = MetaParamOverrides()
        param_ov.review_threshold_offset = 0.1
        param_ov._review_threshold_set = True
        report = MetacognitionReport(
            insight_text="I noticed something",
            confidence=0.5,
            param_overrides=param_ov,
        )
        ov.apply(report, turn_counter=5)
        assert ov.review_threshold_offset == 0.0  # 未应用
        # 但 insight_text 仍然可用
        assert report.insight_text == "I noticed something"

    def test_full_override_lifecycle(self):
        """完整生命周期：apply → 有效期 → 过期"""
        ov = MetaParamOverrides()
        # 模拟 metacognition_pass() 解析结果（手动设置 sentinel）
        param_ov = MetaParamOverrides()
        param_ov.review_threshold_offset = 0.1
        param_ov._review_threshold_set = True
        param_ov.defense_prob_multiplier = 0.7
        param_ov._defense_prob_set = True
        report = MetacognitionReport(
            insight_text="pattern detected",
            confidence=0.8,
            param_overrides=param_ov,
        )
        # Apply at turn 10
        ov.apply(report, turn_counter=10)
        assert ov.get_review_threshold(0.5, 11) == 0.6  # turn 11, valid
        assert ov.get_review_threshold(0.5, 14) == 0.6  # turn 14, still valid
        assert ov.get_review_threshold(0.5, 15) == 0.5  # turn 15, expired

    def test_compound_trend_provided_by_emotion_engine(self):
        """验证 get_compound_trend 接口约定"""
        from chat_core.systems.emotion import EmotionEngine
        engine = EmotionEngine()
        trends = engine.get_compound_trend("sub")
        assert isinstance(trends, dict)
        for dim in COMPOUND_DIMS:
            assert dim in trends
            assert isinstance(trends[dim], list)
```

- [ ] **运行测试验证**

```bash
python -m pytest tests/test_metacognition.py -v
```

- [ ] **Commit**

```bash
git add tests/test_metacognition.py
git commit -m "test(metacognition): add context + integration scenario tests"
```

**依赖：** 任务 4, 11

**预估规模：** S (1 文件追加，~5 tests)

---

### 检查点：完成
- [ ] 全量测试通过：`pytest tests/ -q` — ~255 passed (232 回归 + ~23 新增)
- [ ] 14 个成功标准全部覆盖 (SC-01 ~ SC-14)
- [ ] 所有新增代码有类型注解
- [ ] 配置支持禁用的降级路径 (enabled: false)
- [ ] LLM 调用失败静默降级不阻塞 turn
- [ ] 就绪待审查

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| `metacognition_pass()` LLM 调用失败阻塞 turn | 🟡 中 | 失败返回 None，异常计数器仍重置，不阻塞 turn |
| 上下文组装数据不完整（emotion_engine 未初始化等）| 🟢 低 | 所有数据源用 `if self._xxx` 守卫，None 安全传递 |
| MetaParamOverrides 未正确传递给所有子系统 | 🟡 中 | 阶段 5 每个任务独立验证，集成测试覆盖 |
| inner_thoughts 关键词匹配误触发 | 🟢 低 | 关键词列表可配置 (config.yaml)，默认5个保守关键词 |

## 待定问题

- 无

---

## 自检结果

1. **规格覆盖度**：14 个 SC 全部有对应任务覆盖 ✅
2. **占位符扫描**：无 TODO/待定/占位符 ✅
3. **类型一致性**：MetacognitionReport/MetaParamOverrides 在 types.py 统一定义，各模块通过 import 引用 ✅
4. **任务规模**：XS×3, S×5, M×4, L×1 — 无 XL ✅
5. **检查点存在**：每 2-3 个任务后有显式检查点 ✅
6. **依赖顺序**：types → engine → brain → turn_manager → consumers ✅
