# Plan Review: Spec 实施完整度修复计划

> 审查目标: `docs/superpowers/plans/2026-07-10-spec-completeness-fix.md`
> 审查方法: 逐任务交叉验证实际代码库中的方法签名、属性存在性、参数类型

---

## 一、致命缺陷 (7 个 — 代码将运行时失败)

### 🔴 DEFECT-1: A2 — `apply_relationship_modulation()` 返回值被丢弃

**位置**: Plan Line 44-53

**问题**: `personality.py:159` 的 `apply_relationship_modulation()` **不修改内部状态**，仅计算并返回 `dict[str, float]`（文档注释明确: "返回关系阶段调制后的行为参数（不修改内部权重）"）。Plan 调用后丢弃返回值，调制结果未被消费。

**后果**: 关系阶段的 empathy/self_disclosure/proactive/playfulness 调制系数计算了但从未被使用。

**修复**: 存储返回值到 `self._runtime_state` 或注入到 sub-session 的 system prompt 中。至少需要调用 `self._personality_engine.set_runtime_modulation(result)` 或等效方式。

---

### 🔴 DEFECT-2: A6 — `InterestModel.get_last_match()` 不存在

**位置**: Plan Line 253 `self._motivation_engine.build_injection()` — 实际是 Plan C2 Line 422

**实际 API**: `InterestModel` 仅有 `match(topic: str, ...)` 方法（需要 topic 参数），无 `get_last_match()`。

**后果**: C2 任务运行时 `AttributeError`。

**修复**: 使用 `interest_match=0.0`（默认值），或添加 `_last_match_score` 字段到 `InterestModel`。

---

### 🔴 DEFECT-3: A6 — `MotivationEngine.build_injection()` 签名错误

**位置**: Plan Line 252-253

**问题**: Plan 调用 `self._motivation_engine.build_injection()` 不传参。
**实际签名**: `build_injection(self, state: MotivationState) -> str | None`，需要 `MotivationState` 作为第一个参数。

**后果**: `TypeError: build_injection() missing 1 required positional argument: 'state'`

**修复**: 
```python
if self._motivation_engine:
    state = self._motivation_engine.evaluate(
        boredom=self._boredom_detector.get_boredom(),
        energy=self._energy_bar._state.energy,
        loneliness=self._loneliness_detector.current if self._loneliness_detector else 0.0,
        value_weights=self._value_engine.values if self._value_engine else None,
    )
    active_motivations = self._motivation_engine.build_injection(state)
```

---

### 🔴 DEFECT-4: A6 — `ValueEngine.snapshot()` 不存在

**位置**: Plan Line 254

**实际 API**: `ValueEngine` 有 `values` property 返回 `ValueSystem` dataclass，无 `snapshot()` 方法。

**修复**: 使用 `self._value_engine.values`（返回 `ValueSystem` dataclass 或用 `vars()` 转 dict）。

```python
value_state = vars(self._value_engine.values) if self._value_engine else None
```

---

### 🔴 DEFECT-5: A6 — `NarrativeEngine.get_latest()` 不存在

**位置**: Plan Line 255

**实际 API**: `NarrativeEngine` 有 `state` property，其 `.latest` 属性存放最新叙述文本。另有 `get_system_injection()` 返回完整格式化文本。

**修复**: 使用 `self._narrative_engine.state.latest`

---

### 🔴 DEFECT-6: A6 — `GroupDynamics.get_group_summary()` 不存在

**位置**: Plan Line 246

**实际 API**: `GroupDynamics` 有 `get_metrics(group_id)` 返回 `GroupRoleMetrics` dataclass。

**修复**: 使用 `self._group_dynamics.get_metrics(self._current_user_id)`，或将 `GroupRoleMetrics` dataclass 转为 dict。

---

### 🔴 DEFECT-7: C1 — `self._defense_history` 属性不存在

**位置**: Plan Line 350

**实际状态**: TurnManager 仅有 `self._had_defense_this_turn: bool` 标志（每轮重置），没有任何累加防御历史列表。

**修复**: 在 `_apply_defense()` 中追加 `self._defense_history.append(defense.defense_type.value)`。或在 `__init__` 中初始化 `self._defense_history: list[str] = []`。

---

## 二、逻辑偏差 (6 个)

### 🟡 DEVIATION-1: A6 — `moral_conflict_context` 类型不匹配

**位置**: Plan Line 247, 270

**问题**: `getattr(self._metacognition, "moral_escalation_pending", None)` 返回 `bool | None`，但 `build_context` 参数 `moral_conflict_context: str | None` 期望字符串。

**后果**: 元认知上下文收到 `True`/`False` 而非描述性文本。

**修复**: 
```python
moral_ctx = "道德冲突已升级到元认知审查" if getattr(
    self._metacognition, "moral_escalation_pending", False
) else None
```

---

### 🟡 DEVIATION-2: B1 — 情感共鸣 valence 永远相等

**位置**: Plan Line 313-314

**问题**: `user_valence = getattr(user_state, "valence", 0.0)` 和 `ai_valence = user_valence` 使用同一个 `EmotionState`（sub 脑的 AI 自身情绪）作为用户情绪。导致 `|user_valence - ai_valence|` 永远为 0，情感共鸣规则**每轮必定触发**（而非设计意图的"情绪相似时偶尔触发"）。

**后果**: closeness 每轮无条件 +0.03，关系升级加速约 3 倍。

**修复**: 
```python
ai_valence = getattr(self._emotion_engine.get_state("sub"), "valence", 0.0)
user_valence = 0.0  # 无独立用户情绪追踪时的安全默认值
# 备选: 从用户消息情感分析获取，或设为与 AI valence 的加权镜像
```

---

### 🟡 DEVIATION-3: C4 — moral_escalation 触发条件插入位置错误

**位置**: Plan Line 494-497

**问题**: Plan 说"在 L117 之后插入"。L117 是 `triggered = True` 在 `if abs(compound_delta) > 0.4:` 块内。这意味着 moral_escalation 检查仅在 compound_delta > 0.4 时执行，而非独立触发条件。

**后果**: 若本轮没有情绪冲击但同时存在 moral_escalation，元认知不会触发。

**修复**: 将 moral_escalation 检查放在 compound_delta 块之外（L117 之后、L119 之前），作为独立的第 6 触发条件。

---

### 🟡 DEVIATION-4: 遗漏 — BoredomDetector 主观时间未替换

**审计报告 Spec 007**: "主观时间未用于 boredom 计算"
**Plan 覆盖**: 仅 C2 修复 SubjectiveClock 参数传入，但未修改 `get_boredom()` 方法。

**当前**: `boredom.py:116` — `elapsed = time.time() - self._start_time`
**应有**: `elapsed = self._subjective_clock.accumulated`（若启用主观时钟）
或 `elapsed = self._subjective_clock.accumulated if self._subjective_clock else (time.time() - self._start_time)`

**影响**: 设计文档要求 B(t) 基于主观时间衰减，但实际仍用墙钟。

---

### 🟡 DEVIATION-5: 遗漏 — `memory.py._time_annotate` "dragging" 未处理

**审计报告 Spec 007 SC-14**: 记忆回溯注解仅处理 "immersed"，未处理 "dragging"
**Plan 覆盖**: 无对应任务

**修复**: 在 `memory.py:740-748` 的 `_time_annotate` 中追加 dragging 分支。

---

### 🟡 DEVIATION-6: 遗漏 — compound_alert 注意力事件订阅

**审计报告 Spec 005**: `turn_manager.py:_ensure_listeners` 未订阅 `compound_alert` 事件
**Plan 覆盖**: 无对应任务

**当前**: 仅订阅 `emotion_alert` 和 `logic_conflict`
**应有**: 订阅 `compound_alert` → 映射到 `AttentionEvent.EMOTION_SHOCK`

---

## 三、类型/签名小问题 (2 个)

### ⚠️ MINOR-1: A6 — `get_stage()` 返回枚举，非字符串

**位置**: Plan Line 244, 268

`self._relationship_engine.get_stage()` 返回 `RelationshipStage` 枚举。传给 `build_context(relationship_stage=rel_stage)` 时，`build_context` 期望 `str | None`。需要 `.value` 转换。

**修复**: `rel_stage = stage.value if (stage := self._relationship_engine.get_stage(...)) else None`

---

### ⚠️ MINOR-2: A6 — `get_metrics()` 返回 dataclass，非 dict

**位置**: Plan Line 246, 269

`get_metrics()` 返回 `GroupRoleMetrics` dataclass。`build_context` 期望 `dict[str, Any] | None`。

**修复**: `group_summary = vars(self._group_dynamics.get_metrics(...))`

---

## 四、汇总

| 类别 | 数量 | 任务 |
|------|:---:|------|
| 🔴 致命 (运行时失败) | 7 | A2, A6×5, C1 |
| 🟡 逻辑偏差 | 6 | A6, B1, C4, +3 遗漏 |
| ⚠️ 类型小问题 | 2 | A6×2 |
| **合计** | **15** | |

### 遗漏的审计问题 (有报告但无修复)

| 审计编号 | 内容 | 
|----------|------|
| Spec 005-2 | compound_alert → 注意力状态机事件订阅 |
| Spec 007 §3.2 | BoredomDetector 主观时间替换 |
| Spec 007 SC-14 | memory._time_annotate "dragging" 处理 |

---

## 五、结论

Plan 的结构（三阶段、任务粒度）和组织合理，但 **A6 任务有 6 个方法调用引用不存在的 API**，需要在实施前修正。A2 任务的 `apply_relationship_modulation()` 返回值未消费是设计级别的疏漏。3 个审计问题在 Plan 中无对应修复任务。

**建议**: 修正上述 15 个缺陷后重新发布 plan v2，或直接在实施阶段按修正后的代码执行。
