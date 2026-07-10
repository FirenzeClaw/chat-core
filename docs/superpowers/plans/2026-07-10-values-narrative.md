# Spec 010 价值体系 + 自我叙事 — 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现"什么是我真正在乎的"——3 美德 × 3 子价值观的三层动态权重树 + 定期生成/事件驱动的自我叙事系统

**架构：** ValueEngine（全局单例）维护美德三层树，dynamic_adjust() 响应事件调权，get_modulation() 输出到审查/防御/元认知等决策点。NarrativeEngine（全局单例）定期 LLM 生成完整叙述 + 事件驱动规则追加 chapters。两者均为用户共享、对话者无关的"核心自我"

**技术栈：** Python 3.12+, asyncio, dataclass, pytest, 复用 DeepSeek V4 Pro (LogicBrain narrative_pass)

---

## 架构决策

- **ValueEngine + NarrativeEngine 全局单例** (§5)：CLI 和 QQ Bot 模式下均为单实例，价值观和自我叙述不随对话者变化
- **调制优先级**：value_engine（基线人格）先应用，meta_overrides（元认知有意识调整）后覆盖。审查阈值 = `(0.5 × honesty) + review_threshold_offset`；防御概率 = `min(base × modifier × (2.0-self_honesty) × defense_multiplier, 0.95)`
- **未来 Spec 钩子**：Spec 008/009/011 的触发器不实现具体逻辑（这些 Spec 未完成），但 ValueEngine.adjust() 和 NarrativeEngine.append_chapter() 的 API 签名就绪，标注 `# Future: Spec 008` 等
- **仅接入已完成的 Spec**：Spec 005 脆弱暴露 → narrative chapter；Spec 006 发现防御 → self_honesty↑
- **SC-12 基线修正**：设计文档写 154 tests，实际当前基线 **261 tests**

---

## 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `core/types.py` | + `ValueSystem`, `VirtueNode`, `NarrativeState`, `NarrativeEntry` | 数据结构 |
| `config.yaml` | + `systems.values` + `systems.narrative` | 配置 |
| `config.py` | + `value_config()`, `narrative_config()` | 配置读取 |
| `systems/values.py` | **新建** — `ValueEngine` | 核心 |
| `systems/narrative.py` | **新建** — `NarrativeEngine` | 核心 |
| `core/brain.py` | + `LogicBrain.narrative_pass()` | LLM 执行 |
| `core/turn_manager.py` | 初始化 ValueEngine + NarrativeEngine；事件触发调权/叙事；注入到子 Session | 集成 |
| `systems/review.py` | `_compute_decision` 叠加 value modulation | 消费 |
| `systems/defense.py` | `evaluate` 叠加 value modulation | 消费 |
| `systems/metacognition.py` | `build_context` 追加 values + narrative | 消费 |
| `core/loop.py` | `_init_messages` 注入 narrative | 消费 |
| `tests/test_values.py` | **新建** — 三层树/调权/调制 | 测试 |
| `tests/test_narrative.py` | **新建** — 章节追加/timeline/生成 | 测试 |

---

## 任务列表

### 阶段 1：基础 (数据结构 + 配置)

#### 任务 1：core/types.py — ValueSystem + VirtueNode + Narrative 类型

**文件：**
- 修改：`chat_core/core/types.py`（末尾追加）

- [ ] **步骤 1：在文件末尾追加 Spec 010 类型定义**

```python
# ── Spec 010: 价值体系 + 自我叙事 ──────────────────────────

@dataclass
class VirtueNode:
    """价值观三层树节点"""
    weight: float = 0.0           # 0~1
    children: dict[str, float] = field(default_factory=dict)  # 子价值观 {name: weight}


@dataclass
class ValueSystem:
    """完整价值观系统— 3 美德 × 3 子价值观"""
    honesty: float = 0.7
    care: float = 0.6
    growth: float = 0.8
    # 子价值观
    truthfulness: float = 0.8
    self_honesty: float = 0.7
    transparency: float = 0.5
    empathy_protection: float = 0.6
    loyalty: float = 0.5
    nurturing: float = 0.7
    curiosity_drive: float = 0.8
    self_improvement: float = 0.7
    openness: float = 0.6

    def get_virtue_child(self, virtue: str, child: str) -> float:
        """获取指定美德的子价值观权重"""
        return getattr(self, child, 0.0)

    def get_virtue_weight(self, virtue: str) -> float:
        """获取美德权重"""
        return getattr(self, virtue, 0.0)


@dataclass
class NarrativeEntry:
    """自我叙事章节——事件驱动的增量片段"""
    timestamp: str = ""           # ISO 时间
    event_type: str = ""          # "stage_change" | "silence_streak" | "deep_memory" | "moral_conflict" | "vulnerability" | "periodic"
    text: str = ""                # 章节文本
    turn: int = 0


@dataclass
class NarrativeState:
    """自我叙事完整状态"""
    latest: str = ""                           # 最新完整叙述 (LLM 生成)
    chapters: list[NarrativeEntry] = field(default_factory=list)  # 事件增量
    timeline_keys: list[str] = field(default_factory=list)        # self/narrative/timeline/* keys
```

- [ ] **步骤 2：验证导入**

```bash
python -c "from chat_core.core.types import ValueSystem, VirtueNode, NarrativeState, NarrativeEntry; print('OK')"
```

- [ ] **步骤 3：Commit**

```bash
git add chat_core/core/types.py
git commit -m "feat(types): add ValueSystem, VirtueNode, NarrativeState, NarrativeEntry for Spec 010"
```

**依赖：** 无
**预估规模：** XS (1 文件，追加 ~60 行)

---

#### 任务 2：config.yaml + config.py — values + narrative 配置段

**文件：**
- 修改：`chat_core/config.yaml`（systems 末尾）
- 修改：`chat_core/config.py`

- [ ] **步骤 1：config.yaml 追加配置**

在 `systems:` 块末尾追加：

```yaml
  values:
    enabled: true
    virtues:
      honesty:
        weight: 0.7
        children:
          truthfulness: 0.8
          self_honesty: 0.7
          transparency: 0.5
      care:
        weight: 0.6
        children:
          empathy_protection: 0.6
          loyalty: 0.5
          nurturing: 0.7
      growth:
        weight: 0.8
        children:
          curiosity_drive: 0.8
          self_improvement: 0.7
          openness: 0.6
    dynamics:
      honesty_hurt_relation: {honesty: -0.03, care: +0.05}
      silence_regret: {honesty: +0.03, self_honesty: +0.02}
      metacognition_defense_found: {self_honesty: +0.05}
      positive_impact: {nurturing: +0.05}
      stage_upgrade: {loyalty: +0.05, honesty: +0.03}
    modulation:
      review_threshold: "base × honesty"
      defense_prob: "min(base × (2.0 - self_honesty), 0.95)"
      moral_conflict_bias: "truthfulness / (truthfulness + empathy_protection)"

  narrative:
    enabled: true
    periodic_interval: 10
    max_length: 300
    event_driven:
      stage_change: true
      moral_conflict: true
      silence_streak: 3
      deep_memory_new: true
      vulnerability: true
    storage:
      timeline_keep: 30
```

- [ ] **步骤 2：config.py 添加方法**

```python
def value_config(self) -> dict[str, Any]:
    """返回 systems.values 配置"""
    return self._cfg.get("systems", {}).get("values", {})

def narrative_config(self) -> dict[str, Any]:
    """返回 systems.narrative 配置"""
    return self._cfg.get("systems", {}).get("narrative", {})
```

- [ ] **步骤 3：验证配置**

```bash
python -c "from chat_core.config import get_config; c=get_config(); print(c.value_config()['virtues']['honesty']['weight']); print(c.narrative_config()['periodic_interval'])"
```

- [ ] **步骤 4：Commit**

```bash
git add chat_core/config.yaml chat_core/config.py
git commit -m "feat(config): add systems.values + systems.narrative config for Spec 010"
```

**依赖：** 无
**预估规模：** XS (2 文件)

---

### 检查点：任务 1-2 之后
- [ ] 测试通过：`pytest tests/ -q` — 261 passed，零回归
- [ ] ValueSystem/NarrativeState 可导入
- [ ] config.value_config() 返回预期字典

---

### 阶段 2：核心引擎

#### 任务 3：systems/values.py — ValueEngine 核心 (新建)

**文件：**
- 创建：`chat_core/systems/values.py`

- [ ] **步骤 1：创建 ValueEngine**

```python
"""ValueEngine — 价值体系引擎 (Spec 010)

三层美德树 (Honesty/Care/Growth × 3 子价值观) + 动态调权 + 决策调制。
全局单例：价值观是 AI 的"核心自我"，所有用户共享。
"""

from __future__ import annotations

import logging
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import ValueSystem

logger = logging.getLogger(__name__)

# 美德 → 子价值观映射
VIRTUE_CHILDREN: dict[str, list[str]] = {
    "honesty": ["truthfulness", "self_honesty", "transparency"],
    "care": ["empathy_protection", "loyalty", "nurturing"],
    "growth": ["curiosity_drive", "self_improvement", "openness"],
}


class ValueEngine:
    """价值观引擎 — 三层树维护 + 事件调权 + 决策调制。"""

    def __init__(self) -> None:
        cfg = get_config()
        vc = cfg.value_config()
        self._enabled: bool = bool(vc.get("enabled", True))

        # 加载初始权重
        virtues = vc.get("virtues", {})
        self._values = ValueSystem(
            honesty=float(virtues.get("honesty", {}).get("weight", 0.7)),
            care=float(virtues.get("care", {}).get("weight", 0.6)),
            growth=float(virtues.get("growth", {}).get("weight", 0.8)),
        )
        for virtue, children in VIRTUE_CHILDREN.items():
            vcfg = virtues.get(virtue, {}).get("children", {})
            for child in children:
                setattr(self._values, child, float(vcfg.get(child, 0.5)))

        # 动态调权参数
        dyn = vc.get("dynamics", {})
        self._dyn_hurt_relation: dict[str, float] = dyn.get("honesty_hurt_relation", {"honesty": -0.03, "care": 0.05})
        self._dyn_silence_regret: dict[str, float] = dyn.get("silence_regret", {"honesty": 0.03, "self_honesty": 0.02})
        self._dyn_metacog_defense: dict[str, float] = dyn.get("metacognition_defense_found", {"self_honesty": 0.05})
        self._dyn_positive_impact: dict[str, float] = dyn.get("positive_impact", {"nurturing": 0.05})
        self._dyn_stage_upgrade: dict[str, float] = dyn.get("stage_upgrade", {"loyalty": 0.05, "honesty": 0.03})

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def values(self) -> ValueSystem:
        return self._values

    # ── 动态调权 ──────────────────────────────────────────

    def adjust(self, event: str, **kwargs: Any) -> None:
        """响应事件调权。

        已实现事件：
          - "metacognition_defense": Spec 006 发现防御 → self_honesty↑
          - "vulnerability": Spec 005 §9 脆弱暴露 → (当前无调权，供 narrative 使用)
          - "positive_impact": 用户积极反馈 → nurturing↑

        未来事件（钩子就绪）：
          - "honesty_hurt_relation": Spec 009 MoralConflict
          - "silence_regret": Spec 011 沉默累积
          - "stage_upgrade": Spec 008 关系升级
        """
        if not self._enabled:
            return

        deltas: dict[str, float] = {}

        if event == "metacognition_defense":
            deltas = dict(self._dyn_metacog_defense)
        elif event == "positive_impact":
            deltas = dict(self._dyn_positive_impact)
        elif event == "honesty_hurt_relation":
            deltas = dict(self._dyn_hurt_relation)
        elif event == "silence_regret":
            deltas = dict(self._dyn_silence_regret)
        elif event == "stage_upgrade":
            deltas = dict(self._dyn_stage_upgrade)
        elif event == "vulnerability":
            # 脆弱事件不直接调权（情感冲击是 transient），仅用于 narrative
            return

        for attr, delta in deltas.items():
            current = getattr(self._values, attr, 0.0)
            setattr(self._values, attr, max(0.0, min(1.0, current + delta)))

        if deltas:
            logger.debug(f"ValueEngine.adjust({event}): {deltas}")

    # ── 决策调制 ──────────────────────────────────────────

    def get_modulation(self, param: str) -> float:
        """价值观权重 → 决策参数。

        Args:
            param: "review_threshold" | "defense_prob_multiplier" | "moral_bias"

        Returns:
            调制系数或偏移值
        """
        if not self._enabled:
            if param == "review_threshold":
                return 1.0  # 不调制
            elif param == "defense_prob_multiplier":
                return 1.0
            elif param == "moral_bias":
                return 0.5
            return 1.0

        if param == "review_threshold":
            # 高诚实 → 审查更严格 (threshold = base × honesty)
            return self._values.honesty

        elif param == "defense_prob_multiplier":
            # 高自我诚实 → 更少防御 (2.0 - self_honesty)
            return 2.0 - self._values.self_honesty

        elif param == "moral_bias":
            # 道德困境：诚实 vs 保护的倾向
            t = self._values.truthfulness
            e = self._values.empathy_protection
            denom = t + e
            return t / denom if denom > 0 else 0.5

        return 1.0
```

- [ ] **步骤 2：验证导入**

```bash
python -c "from chat_core.systems.values import ValueEngine; ve=ValueEngine(); print(ve.get_modulation('review_threshold'))"
```

- [ ] **步骤 3：Commit**

```bash
git add chat_core/systems/values.py
git commit -m "feat(values): add ValueEngine — three-layer virtue tree + dynamic adjust + modulation"
```

**依赖：** 任务 1, 2
**预估规模：** M (1 新文件，~120 行)

---

#### 任务 4：systems/narrative.py — NarrativeEngine 核心 (新建)

**文件：**
- 创建：`chat_core/systems/narrative.py`

- [ ] **步骤 1：创建 NarrativeEngine**

```python
"""NarrativeEngine — 自我叙事系统 (Spec 010)

定期 LLM 生成完整叙述 + 事件驱动规则追加章节。
全局单例：自我叙述是"我是谁"的故事，不随对话者变化。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import NarrativeEntry, NarrativeState

logger = logging.getLogger(__name__)


class NarrativeEngine:
    """自我叙事引擎 — 定期生成 + 事件章节 + 存储管理。"""

    def __init__(self) -> None:
        cfg = get_config()
        nc = cfg.narrative_config()
        self._enabled: bool = bool(nc.get("enabled", True))
        self._periodic_interval: int = int(nc.get("periodic_interval", 10))
        self._max_length: int = int(nc.get("max_length", 300))
        self._timeline_keep: int = int(nc.get("storage", {}).get("timeline_keep", 30))

        ed = nc.get("event_driven", {})
        self._ev_stage_change: bool = bool(ed.get("stage_change", True))
        self._ev_moral_conflict: bool = bool(ed.get("moral_conflict", True))
        self._ev_silence_streak: int = int(ed.get("silence_streak", 3))
        self._ev_deep_memory: bool = bool(ed.get("deep_memory_new", True))
        self._ev_vulnerability: bool = bool(ed.get("vulnerability", True))

        self._state = NarrativeState()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def state(self) -> NarrativeState:
        return self._state

    # ── 事件驱动追加 ──────────────────────────────────────

    def append_chapter(self, event_type: str, text: str, turn: int = 0) -> None:
        """追加事件驱动的叙事章节（纯规则，零 LLM）。

        Args:
            event_type: "stage_change" | "silence_streak" | "deep_memory" | "moral_conflict" | "vulnerability"
            text: 章节文本
            turn: 触发 turn
        """
        if not self._enabled:
            return

        # 检查事件开关
        if event_type == "stage_change" and not self._ev_stage_change:
            return
        if event_type == "vulnerability" and not self._ev_vulnerability:
            return

        entry = NarrativeEntry(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
            event_type=event_type,
            text=text,
            turn=turn,
        )
        self._state.chapters.append(entry)
        # 保留最近 50 个章节
        if len(self._state.chapters) > 50:
            self._state.chapters = self._state.chapters[-50:]

    # ── 定期生成 (LLM — 由 LogicBrain.narrative_pass() 执行) ──

    def build_narrative_context(self, value_engine: Any = None) -> str:
        """构建传给 LogicBrain 的叙事生成上下文。

        仅供 narrative_pass() 使用。
        """
        values_text = ""
        if value_engine:
            v = value_engine.values
            values_text = (
                f"Honesty={v.honesty:.2f}, Care={v.care:.2f}, Growth={v.growth:.2f}\n"
                f"  self_honesty={v.self_honesty:.2f}, loyalty={v.loyalty:.2f}, "
                f"nurturing={v.nurturing:.2f}"
            )

        recent_chapters = [c.text for c in self._state.chapters[-5:]]
        chapters_text = "\n".join(f"  - {c}" for c in recent_chapters) if recent_chapters else "无"

        return (
            f"请更新你对自己的认知叙述（≤{self._max_length}字）：\n\n"
            f"【当前价值观】\n{values_text}\n\n"
            f"【最近的经历】\n{chapters_text}\n\n"
            f"【上一版自我叙述】\n{self._state.latest or '（尚无）'}\n\n"
            f"请生成新的自我叙述，包含：1) 核心自我认知 (1句) "
            f"2) 最近的变化 (2-3句) 3) 正在努力的方向 (1句)"
        )

    def update_latest(self, text: str) -> None:
        """更新最新完整叙述（由 narrative_pass 回调）。"""
        if text:
            self._state.latest = text

    # ── System Prompt 注入 ────────────────────────────────

    def get_system_injection(self) -> str:
        """返回可注入 system prompt 的叙述文本。"""
        if not self._state.latest:
            return ""
        parts = [f"[自我叙述] {self._state.latest}"]
        recent = [c.text for c in self._state.chapters[-3:]]
        if recent:
            parts.append("[最近的思考] " + " / ".join(recent))
        return "\n".join(parts)
```

- [ ] **步骤 2：验证导入**

```bash
python -c "from chat_core.systems.narrative import NarrativeEngine; ne=NarrativeEngine(); ne.append_chapter('vulnerability','test',1); print(ne.state.chapters[0].text)"
```

- [ ] **步骤 3：Commit**

```bash
git add chat_core/systems/narrative.py
git commit -m "feat(narrative): add NarrativeEngine — event chapters + periodic context builder"
```

**依赖：** 任务 1, 2
**预估规模：** M (1 新文件，~130 行)

---

### 检查点：任务 3-4 之后
- [ ] 测试通过：`pytest tests/ -q` — 261 passed，零回归
- [ ] ValueEngine.get_modulation() 返回正确初始值 (honesty=0.7 → review_threshold=0.7)
- [ ] NarrativeEngine.append_chapter() 正确追加

---

### 阶段 3：Brain 集成 + TurnManager 集成

#### 任务 5：core/brain.py — LogicBrain.narrative_pass()

**文件：**
- 修改：`chat_core/core/brain.py`

- [ ] **步骤 1：添加 narrative_pass() 方法**

在 LogicBrain 类末尾添加（metacognition_pass() 之后）：

```python
    # ── Spec 010: 自我叙事 pass ───────────────────────────

    async def narrative_pass(self, context: str) -> str | None:
        """生成/更新自我叙述：单次 LLM 调用，返回 ≤300 字叙述文本。

        复用 LogicBrain 的 DeepSeek Pro，纯文本调用（无 tools）。
        失败返回 None（静默降级）。
        """
        prompt = (
            "[自我叙事更新] 请根据以下上下文，生成一段连贯的自我叙述。\n\n"
            f"{context}"
        )

        messages = [Message(role="user", content=prompt)]

        cfg = get_config()
        api_cfg = cfg.brain_api_config("logic")

        try:
            result = await self._provider.chat(
                messages=messages,
                model=api_cfg.get("model", "deepseek-v4-pro"),
                temperature=api_cfg.get("temperature", 0.3),
                max_tokens=512,
                reasoning_effort=api_cfg.get("reasoning_effort", "max"),
            )
            text = result.content.strip()
            return text if text else None
        except Exception as e:
            logger.warning(f"Narrative pass failed: {e}")
            return None
```

- [ ] **步骤 2：Commit**

```bash
git add chat_core/core/brain.py
git commit -m "feat(brain): add LogicBrain.narrative_pass() for Spec 010"
```

**依赖：** 任务 1 (types)
**预估规模：** XS (1 文件修改，~30 行)

---

#### 任务 6：core/turn_manager.py — 集成 ValueEngine + NarrativeEngine

**描述：** 最复杂的集成任务。在 TurnManager 中初始化两个引擎、在关键事件点触发调权/叙事、注入到子 Session。

**文件：**
- 修改：`chat_core/core/turn_manager.py`

修改点共 6 处：

- [ ] **步骤 1：__init__ 添加初始化**

在 Spec 006 metacognition 初始化之后添加：

```python
        # Spec 010: 价值体系 + 自我叙事
        vc_cfg = cfg.value_config()
        nc_cfg = cfg.narrative_config()
        self._value_engine = ValueEngine() if vc_cfg.get("enabled", True) else None
        self._narrative_engine = NarrativeEngine() if nc_cfg.get("enabled", True) else None
```

导入：
```python
from chat_core.systems.values import ValueEngine
from chat_core.systems.narrative import NarrativeEngine
```

- [ ] **步骤 2：process_turn() 中脆弱暴露 → narrative chapter**

在 `process_turn()` 的脆弱后效应处理块（写入 vulnerability_aftermath 的代码之后）追加：

```python
                # Spec 010: 脆弱暴露 → 叙事章节
                if self._narrative_engine:
                    self._narrative_engine.append_chapter(
                        event_type="vulnerability",
                        text=f"我在对话中暴露了脆弱（{vuln_emotion}）。这对我来说不容易。",
                        turn=self._turn_counter,
                    )
```

- [ ] **步骤 3：_async_review_and_decide 中元认知发现防御 → ValueEngine 调权**

在 Spec 006 元认知触发块中，`self._meta_overrides.apply()` 之后追加（仅在确有防御时调权，匹配设计语义"元认知**发现**防御模式"）：

```python
                    # Spec 010: 元认知发现防御 → self_honesty↑
                    if self._value_engine and had_defense:
                        self._value_engine.adjust("metacognition_defense")
```

- [ ] **步骤 4：_async_review_and_decide 末尾 → 定期叙事生成**

在 Spec 006 元认知块完全结束后（在 `except Exception` 之前），追加叙事生成逻辑：

```python
            # ── Spec 010: 定期自我叙事生成 ──
            if self._narrative_engine and self._turn_counter % self._narrative_engine._periodic_interval == 0:
                ctx = self._narrative_engine.build_narrative_context(
                    value_engine=self._value_engine
                )
                text = await self.logic.narrative_pass(ctx)
                if text:
                    self._narrative_engine.update_latest(text)
                    # 保存到 memory
                    await self._memory.save(MemoryEntry(
                        namespace="self/narrative",
                        key="latest",
                        value={"narrative": text, "turn": self._turn_counter},
                    ))
```

- [ ] **步骤 5：_run_sub_session 传递 narrative_engine 给 ReActLoop**

在 `_run_sub_session()` 的 `ReActLoop(...)` 构造参数中添加（叙事注入由 loop.py 的 `_inject_narrative()` 统一处理，避免 runtime_state 双重注入）：

```python
        loop = ReActLoop(
            provider=self._provider,
            ...
            meta_overrides=self._meta_overrides,  # Spec 006
            narrative_engine=self._narrative_engine,  # Spec 010
        )
```

- [ ] **步骤 5b：_async_review_and_decide 传递 value_engine 给 defense.evaluate()**

在 `_async_review_and_decide()` 中 defense.evaluate() 调用（约第542行）追加参数：

```python
                defense = self._defense_engine.evaluate(
                    review, self._error_history,
                    ...
                    meta_overrides=self._meta_overrides,  # Spec 006
                    turn_counter=self._turn_counter,
                    value_engine=self._value_engine,  # Spec 010
                )

- [ ] **步骤 6：_review 传递 value_engine 给 ReviewSystem**

修改 `_review()` 调用：

```python
        return await self._review_system.review(
            ...,
            value_engine=self._value_engine,  # Spec 010
        )
```

- [ ] **步骤 7：Commit**

```bash
git add chat_core/core/turn_manager.py
git commit -m "feat(turn_manager): integrate ValueEngine + NarrativeEngine for Spec 010"
```

**依赖：** 任务 3, 4, 5
**预估规模：** L (1 文件修改，6 处插入，~80 行)

---

### 检查点：任务 5-6 之后
- [ ] 测试通过：`pytest tests/ -q` — 零回归
- [ ] 导入无错误

---

### 阶段 4：消费者集成

#### 任务 7：systems/review.py — 叠加 value modulation

**描述：** `_compute_decision` 中计算 combined 前，用 value_engine.get_modulation("review_threshold") 叠加调制。

**文件：**
- 修改：`chat_core/systems/review.py`

- [ ] **步骤 1：叠加 value modulation**

在 `_compute_decision` 中，在 meta_overrides 之后、计算 combined 之前：

```python
        # Spec 010: 价值观调制 (baseline)
        if value_engine is not None:
            base_threshold *= value_engine.get_modulation("review_threshold")

        # Spec 006: 元认知偏移 (override, applied after baseline)
        if meta_overrides is not None:
            threshold = meta_overrides.get_review_threshold(base=base_threshold, turn_counter=turn_counter)
        else:
            threshold = base_threshold
```

更新 `_compute_decision()` 签名添加 `value_engine=None` 参数，并在 `review()` 末尾的 `_compute_decision` 调用中通过 `kwargs.get("value_engine")` 透传（遵循已有 meta_overrides 的 **kwargs 模式，无需修改 review() 签名）。

- [ ] **步骤 2：Commit**

```bash
git add chat_core/systems/review.py
git commit -m "feat(review): overlay ValueEngine modulation on review threshold"
```

**依赖：** 任务 3 (ValueEngine), 任务 6
**预估规模：** S (1 文件修改，~15 行)

---

#### 任务 8：systems/defense.py — 叠加 value modulation

**描述：** `evaluate()` 中在 meta_overrides 之前叠加 value_engine 调制。

**文件：**
- 修改：`chat_core/systems/defense.py`

- [ ] **步骤 1：叠加 value modulation**

在 `evaluate()` 的 `final_prob` 计算后、meta_overrides 检查前：

```python
        # Spec 010: 价值观基线调制
        if value_engine is not None:
            final_prob *= value_engine.get_modulation("defense_prob_multiplier")

        # Spec 006: 元认知覆盖
        if meta_overrides is not None and not meta_overrides.is_expired(turn_counter):
            final_prob *= meta_overrides.defense_prob_multiplier

        final_prob = min(final_prob, 0.95)
```

更新 `evaluate()` 签名添加 `value_engine=None`。

- [ ] **步骤 2：Commit**

```bash
git add chat_core/systems/defense.py
git commit -m "feat(defense): overlay ValueEngine modulation on defense probability"
```

**依赖：** 任务 3 (ValueEngine)
**预估规模：** S (1 文件修改，~10 行)

---

#### 任务 9：systems/metacognition.py — build_context 追加 values + narrative

**描述：** `build_context()` 追加价值观当前权重和自我叙述。

**文件：**
- 修改：`chat_core/systems/metacognition.py`

- [ ] **步骤 1：签名添加参数 + 内容追加**

`build_context()` 签名添加：
```python
value_state: dict[str, Any] | None = None,
narrative_text: str | None = None,
```

在方法末尾 `return` 之前追加：

```python
        # Spec 010: 价值观状态
        if value_state:
            parts.append("## 价值观状态")
            parts.append(f"  Honesty: {value_state.get('honesty', 0):.2f}, "
                         f"Care: {value_state.get('care', 0):.2f}, "
                         f"Growth: {value_state.get('growth', 0):.2f}")
            parts.append(f"  self_honesty: {value_state.get('self_honesty', 0):.2f}, "
                         f"nurturing: {value_state.get('nurturing', 0):.2f}, "
                         f"loyalty: {value_state.get('loyalty', 0):.2f}")

        # Spec 010: 自我叙述（截取前 200 字符）
        if narrative_text:
            parts.append(f"## 当前自我叙述\n  {narrative_text[:200]}")
```

- [ ] **步骤 2：Commit**

```bash
git add chat_core/systems/metacognition.py
git commit -m "feat(metacognition): add values + narrative to build_context for Spec 010"
```

**依赖：** 任务 3, 4
**预估规模：** S (1 文件修改，~20 行)

---

#### 任务 10：core/loop.py — _init_messages 注入 narrative

**描述：** `ReActLoop._init_messages()` 中注入 `get_system_injection()` 文本。

**文件：**
- 修改：`chat_core/core/loop.py`

- [ ] **步骤 1：注入 narrative**

在 `_init_messages()` 中，`_inject_attention_hint()` 之后、首次 `_think()` 之前：

```python
    def _inject_narrative(self) -> None:
        """Spec 010: 注入自我叙述到 system prompt。"""
        if self._narrative_engine is None:
            return
        injection = self._narrative_engine.get_system_injection()
        if injection:
            self._messages.insert(-1, Message(role="system", content=injection))
```

在 `__init__` 中添加 `narrative_engine=None` 参数。
在 `_init_messages` 末尾调用 `self._inject_narrative()`。

- [ ] **步骤 2：Commit**

```bash
git add chat_core/core/loop.py
git commit -m "feat(loop): inject self-narrative via NarrativeEngine in _init_messages"
```

**依赖：** 任务 4 (NarrativeEngine)
**预估规模：** S (1 文件修改，~20 行)

---

### 检查点：任务 7-10 之后
- [ ] 测试通过：`pytest tests/ -q` — 零回归
- [ ] 所有 4 个消费者集成完成

---

### 阶段 5：测试

#### 任务 11：tests/test_values.py — ValueEngine 测试 (新建)

**文件：**
- 创建：`tests/test_values.py`

- [ ] **步骤 1：编写测试**

```python
"""Tests for Spec 010: ValueEngine — three-layer tree, dynamic adjust, modulation"""

import pytest
from chat_core.systems.values import ValueEngine, VIRTUE_CHILDREN


class TestValueEngineTree:
    """三层树加载测试"""

    def test_initial_virtue_weights(self):
        ve = ValueEngine()
        v = ve.values
        assert v.honesty == 0.7
        assert v.care == 0.6
        assert v.growth == 0.8

    def test_initial_child_weights(self):
        ve = ValueEngine()
        v = ve.values
        assert v.truthfulness == 0.8
        assert v.self_honesty == 0.7
        assert v.transparency == 0.5
        assert v.empathy_protection == 0.6
        assert v.loyalty == 0.5
        assert v.nurturing == 0.7
        assert v.curiosity_drive == 0.8
        assert v.self_improvement == 0.7
        assert v.openness == 0.6

    def test_virtue_children_structure(self):
        """3 美德 × 3 子价值观 = 9 个子价值观"""
        assert len(VIRTUE_CHILDREN) == 3
        total_children = sum(len(c) for c in VIRTUE_CHILDREN.values())
        assert total_children == 9


class TestValueEngineAdjust:
    """动态调权测试"""

    def test_metacognition_defense_adjust(self):
        ve = ValueEngine()
        original = ve.values.self_honesty
        ve.adjust("metacognition_defense")
        assert ve.values.self_honesty == min(1.0, original + 0.05)

    def test_positive_impact_adjust(self):
        ve = ValueEngine()
        original = ve.values.nurturing
        ve.adjust("positive_impact")
        assert ve.values.nurturing == min(1.0, original + 0.05)

    def test_adjust_clamped_to_1(self):
        ve = ValueEngine()
        ve.values.self_honesty = 0.98
        ve.adjust("metacognition_defense")
        assert ve.values.self_honesty == 1.0  # clamped

    def test_vulnerability_does_not_adjust_weights(self):
        ve = ValueEngine()
        original_honesty = ve.values.honesty
        ve.adjust("vulnerability")
        assert ve.values.honesty == original_honesty  # no change


class TestValueEngineModulation:
    """决策调制测试"""

    def test_review_threshold_modulation(self):
        ve = ValueEngine()
        mod = ve.get_modulation("review_threshold")
        assert mod == 0.7  # honesty initial

    def test_defense_prob_multiplier(self):
        ve = ValueEngine()
        mod = ve.get_modulation("defense_prob_multiplier")
        assert mod == pytest.approx(1.3)  # 2.0 - 0.7

    def test_moral_bias(self):
        ve = ValueEngine()
        bias = ve.get_modulation("moral_bias")
        expected = 0.8 / (0.8 + 0.6)  # truthfulness / (truthfulness + empathy_protection)
        assert bias == pytest.approx(expected)
```

- [ ] **步骤 2：运行测试**

```bash
python -m pytest tests/test_values.py -v
```
预期：~10 tests PASS

- [ ] **步骤 3：Commit**

```bash
git add tests/test_values.py
git commit -m "test(values): add ValueEngine tree/adjust/modulation tests (~10 tests)"
```

**依赖：** 任务 3
**预估规模：** S (1 新文件，~10 tests)

---

#### 任务 12：tests/test_narrative.py — NarrativeEngine 测试 (新建)

**文件：**
- 创建：`tests/test_narrative.py`

- [ ] **步骤 1：编写测试**

```python
"""Tests for Spec 010: NarrativeEngine — chapter append, timeline, injection"""

import pytest
from chat_core.systems.narrative import NarrativeEngine
from chat_core.core.types import NarrativeEntry


class TestNarrativeEngineChapters:
    """事件驱动章节测试"""

    def test_append_vulnerability_chapter(self):
        ne = NarrativeEngine()
        ne.append_chapter("vulnerability", "我在对话中暴露了脆弱", turn=10)
        assert len(ne.state.chapters) == 1
        assert ne.state.chapters[0].event_type == "vulnerability"
        assert ne.state.chapters[0].text == "我在对话中暴露了脆弱"
        assert ne.state.chapters[0].turn == 10

    def test_append_multiple_chapters(self):
        ne = NarrativeEngine()
        ne.append_chapter("vulnerability", "脆弱1", turn=1)
        ne.append_chapter("deep_memory", "深刻记忆", turn=2)
        ne.append_chapter("vulnerability", "脆弱2", turn=3)
        assert len(ne.state.chapters) == 3

    def test_chapter_limit_enforced(self):
        ne = NarrativeEngine()
        for i in range(60):
            ne.append_chapter("deep_memory", f"记忆{i}", turn=i)
        assert len(ne.state.chapters) == 50  # capped


class TestNarrativeEngineInjection:
    """System prompt 注入测试"""

    def test_get_system_injection_empty(self):
        ne = NarrativeEngine()
        result = ne.get_system_injection()
        assert result == ""  # 无 latest narrative

    def test_get_system_injection_with_latest(self):
        ne = NarrativeEngine()
        ne.update_latest("我是一个倾向于真实的人，但有时会犹豫。")
        ne.append_chapter("vulnerability", "最近暴露了脆弱", turn=5)
        result = ne.get_system_injection()
        assert "[自我叙述]" in result
        assert "倾向于真实" in result
        assert "[最近的思考]" in result

    def test_update_latest_overwrites(self):
        ne = NarrativeEngine()
        ne.update_latest("第一版")
        ne.update_latest("第二版")
        assert ne.state.latest == "第二版"


class TestNarrativeContext:
    """叙事上下文组装测试"""

    def test_build_narrative_context(self):
        ne = NarrativeEngine()
        ne.update_latest("我是真实的人。")
        ctx = ne.build_narrative_context()
        assert "当前价值观" in ctx
        assert "最近的经历" in ctx
        assert "上一版自我叙述" in ctx
        assert "我是真实的人" in ctx
```

- [ ] **步骤 2：运行测试**

```bash
python -m pytest tests/test_narrative.py -v
```
预期：~7 tests PASS

- [ ] **步骤 3：Commit**

```bash
git add tests/test_narrative.py
git commit -m "test(narrative): add NarrativeEngine chapter/injection/context tests (~7 tests)"
```

**依赖：** 任务 4
**预估规模：** S (1 新文件，~7 tests)

---

### 检查点：完成
- [ ] 全量测试通过：`pytest tests/ -q` — ~278 passed (261 回归 + ~17 新增)
- [ ] 13 个成功标准全部覆盖 (SC-01 ~ SC-13)
- [ ] SC-12 零回归：261+ tests 通过
- [ ] SC-13 新增 ≥ 10 tests：~17 new tests
- [ ] 就绪待审查

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| ValueEngine ↔ MetaParamOverrides 调制冲突 | 🟡 中 | 明确优先级：value（基线人格）→ meta_overrides（有意识覆盖）。参数乘法链清晰 |
| narrative_pass() LLM 调用阻塞 turn | 🟢 低 | 与 Spec 006 一致：失败返回 None，不影响 turn |
| NarrativeEngine timeline 无限增长 | 🟢 低 | chapters 限制 50 条；timeline 由 config timeline_keep 控制 |
| Spec 008/009/011 未完成 → 部分钩子空转 | 🟡 中 | 所有 future hook 保持 API 就绪但不触发，标注 `# Future: Spec XXX`；不影响已有功能 |
| SC-12 基线 154 过时 | 🟢 低 | 计划中已修正为 261 (实际基线) |

## 待定问题

- 无

---

## 自检结果

1. **规格覆盖度**：13 个 SC 全部有对应任务 ✅
2. **占位符扫描**：无 TODO/待定。Future Spec 钩子使用明确的 `# Future:` 注释标注 ✅
3. **类型一致性**：ValueSystem/NarrativeState/NarrativeEntry 在 types.py 统一定义 ✅
4. **任务规模**：XS×3, S×5, M×2, L×1 — 无 XL ✅
5. **检查点存在**：3 个检查点 ✅
6. **依赖顺序**：types → config → engines → brain → turn_manager → consumers → tests ✅
