# Spec 实施完整度修复计划 v2 (修正版)

> **基于**: [审计报告](../specs/2026-07-10-spec-completeness-audit.md) + [v1 审查](2026-07-10-spec-completeness-fix-review.md)
> **v2 修正**: 修复 v1 的 7 个致命 API 调用错误 + 6 个逻辑偏差，新增 3 个遗漏任务的覆盖
> **预估总改动**: ~180 行，8 个文件

---

## 阶段 A: P0 运行时逻辑断裂 (4 文件, ~50 行)

### 任务 A1: SILENCE 决策路径连接

**文件**: `chat_core/core/turn_manager.py:670-672`

```python
# 当前:
            if review.decision == DecisionType.CORRECT:
                await self._issue_correction(review, replies)
            elif review.decision == DecisionType.TWISTED:
                await self._issue_correction(review, replies)

# 替换为:
            if review.decision == DecisionType.CORRECT:
                await self._issue_correction(review, replies)
            elif review.decision == DecisionType.TWISTED:
                await self._issue_correction(review, replies)
            elif review.decision == DecisionType.SILENCE:
                await self._silent_archive(review, replies)
```

`_silent_archive` 签名 `(self, review: ReviewResult, replies: list[str])` — `replies` 在 `_async_review_and_decide` 上下文中可用（L343 传入）。

**验证**: `pytest tests/test_silence.py -v -x`

---

### 任务 A2: PersonalityEngine 关系调制连接

**文件**: `chat_core/core/turn_manager.py` — `_run_sub_session` 调用前

`personality.py:159` 的 `apply_relationship_modulation()` **不修改内部状态**，只返回 `dict[str, float]`。需要将返回值存入 RuntimeState 供 sub-session 使用。

```python
            # Spec 008: 应用关系阶段人格调制
            if self._personality_engine and self._relationship_engine:
                stage = self._relationship_engine.get_stage(self._current_user_id)
                modulation = self._relationship_engine.get_modulation(self._current_user_id)
                mod_params = self._personality_engine.apply_relationship_modulation(
                    stage=stage, modulation=modulation
                )
                # 将调制结果写入 RuntimeState（子 Session 通过 prompt 消费）
                self.runtime_state["relationship_modulation"] = mod_params
```

**验证**: `pytest tests/test_relationship.py -v -x`

---

### 任务 A3: DefenseEngine 关系调制参数传入

**文件**: `chat_core/core/turn_manager.py:654`

```python
# 当前:
                defense = self._defense_engine.evaluate(
                    review, self._error_history,
                    impulsiveness=impulsiveness,
                    last_compound_delta=compound_delta,
                    is_vulnerable=(...),
                    meta_overrides=self._meta_overrides,
                    turn_counter=self._turn_counter,
                    value_engine=self._value_engine,
                )

# 替换为:
                defense = self._defense_engine.evaluate(
                    review, self._error_history,
                    impulsiveness=impulsiveness,
                    last_compound_delta=compound_delta,
                    is_vulnerable=(...),
                    meta_overrides=self._meta_overrides,
                    turn_counter=self._turn_counter,
                    value_engine=self._value_engine,
                    relationship_modulation=(
                        self._relationship_engine.get_modulation(self._current_user_id)
                        if self._relationship_engine else None
                    ),
                )
```

**验证**: `pytest tests/test_defense.py tests/test_relationship.py -v -x`

---

### 任务 A4: insight_text → system prompt 注入

**文件 1**: `chat_core/core/turn_manager.py:753-761` — 存储时同步写入 subconscious

```python
# 当前:
                    await self._memory.save(MemoryEntry(
                        namespace="self/metacognition",
                        key=f"insight_{self._turn_counter}_{timestamp}",
                        value={"insight_text": report.insight_text, ...},
                    ))

# 替换为:
                    await self._memory.save(MemoryEntry(
                        namespace="self/metacognition",
                        key=f"insight_{self._turn_counter}_{timestamp}",
                        value={"insight_text": report.insight_text, ...},
                    ))
                    if report.insight_text:
                        await self._memory.save(MemoryEntry(
                            namespace="subconscious/metacognition_insight",
                            key="latest",
                            value={
                                "insight_text": report.insight_text,
                                "confidence": report.confidence,
                            },
                        ))
```

**文件 2**: `chat_core/core/loop.py` — 新增注入方法

在 `_inject_motivation()` (L365) 之后新增:

```python
    def _inject_metacognition_insight(self) -> None:
        """Spec 006: 注入元认知洞察到 system prompt。"""
        hint = getattr(self, '_metacognition_insight_hint', None)
        if hint:
            self._messages.insert(-1, Message(role="system", content=hint))

    def set_metacognition_insight(self, insight_text: str) -> None:
        if insight_text:
            self._metacognition_insight_hint = f"[自我洞察] {insight_text}"
        else:
            self._metacognition_insight_hint = None
```

**文件 2 续**: `_init_messages()` 首次启动 (L273) 和复用分支 (L284) 各加一行:

```python
            self._inject_metacognition_insight()  # Spec 006
```

**验证**: `pytest tests/test_metacognition.py -v -x`

---

### 任务 A5: ValueEngine → MoralConflictDetector 集成

**文件 1**: `chat_core/core/turn_manager.py:795`

```python
# 当前:
                assessment = self._pro_con_assessor.assess(
                    logic_score, logic_reason, emotion_score, emotion_reason,
                )

# 替换为:
                moral_bias = (
                    self._value_engine.get_modulation("moral_bias")
                    if self._value_engine else None
                )
                assessment = self._pro_con_assessor.assess(
                    logic_score, logic_reason, emotion_score, emotion_reason,
                    moral_bias=moral_bias,
                )
```

**文件 2**: `chat_core/systems/moral.py:135` — `assess()` 签名扩展

```python
    def assess(
        self,
        logic_score: float,
        logic_reasoning: str,
        emotion_score: float,
        emotion_reasoning: str,
        moral_bias: float | None = None,
    ) -> ProConAssessment:
        diff = abs(logic_score - emotion_score)

        # Spec 010: 价值观调制 bias — care↑ → 情感侧更倾向保护
        if moral_bias is not None:
            emotion_score += moral_bias * 0.1
            logic_score += (1.0 - moral_bias) * 0.1
            diff = abs(logic_score - emotion_score)

        deadlock = diff < self._deadlock_threshold
        escalation = diff > self._escalate_threshold
        # ... rest unchanged
```

> **注意**: `ValueEngine.get_modulation("moral_bias")` 返回 `float`（非 dict），`values.py:101` 返回单个调制值。原 v1 假设返回 dict 是错误的。

**验证**: `pytest tests/test_moral.py tests/test_values.py -v -x`

---

### 任务 A6: build_context 扩展参数桥接

**文件**: `chat_core/core/turn_manager.py:738-747`

```python
# 当前:
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

# 替换为:
                # ── Spec 008/009/010/011: 扩展上下文 ──
                if self._relationship_engine:
                    stage_enum = self._relationship_engine.get_stage(self._current_user_id)
                    rel_stage = stage_enum.value if stage_enum else None
                else:
                    rel_stage = None

                if self._group_dynamics:
                    metrics = self._group_dynamics.get_metrics(self._current_user_id)
                    group_summary = vars(metrics) if metrics else None
                else:
                    group_summary = None

                moral_ctx = None
                if getattr(self._metacognition, "moral_escalation_pending", False):
                    moral_ctx = "道德冲突已升级到元认知审查"

                silence_pattern = None
                silence_total = sum(self._silence_counters.values())
                if silence_total > 0:
                    silence_pattern = f"连续沉默{silence_total}次"

                active_motivations = None
                if self._motivation_engine:
                    ms = self._motivation_engine.evaluate(
                        boredom=self._boredom_detector.get_boredom(),
                        energy=self._energy_bar._state.energy,
                        loneliness=(
                            self._loneliness_detector.current
                            if self._loneliness_detector else 0.0
                        ),
                        value_weights=(
                            vars(self._value_engine.values)
                            if self._value_engine else None
                        ),
                    )
                    active_motivations = self._motivation_engine.build_injection(ms)

                value_state = (
                    vars(self._value_engine.values)
                    if self._value_engine else None
                )
                narrative_text = (
                    self._narrative_engine.state.latest
                    if self._narrative_engine else None
                )

                context = self._metacognition.build_context(
                    turn_summaries=turn_summaries,
                    compound_trends=compound_trends,
                    defense_mode_summary=defense_summary,
                    memory_system_state=memory_state,
                    attention_state=attention_label,
                    energy_state=energy_dict,
                    subjective_time=stp_dict,
                    vulnerability_history=vuln_history,
                    value_state=value_state,
                    narrative_text=narrative_text,
                    relationship_stage=rel_stage,
                    group_role_summary=group_summary,
                    moral_conflict_context=moral_ctx,
                    silence_pattern=silence_pattern,
                    active_motivations=active_motivations,
                )
```

**验证**: `pytest tests/test_metacognition.py -v -x`

---

### 阶段 A 检查点

```bash
pytest tests/test_silence.py tests/test_relationship.py tests/test_defense.py \
      tests/test_metacognition.py tests/test_moral.py tests/test_values.py -v -x
pytest tests/ -q  # 466+ tests, 零新增回归
```

---

## 阶段 B: P1 数据桥接 (1 文件, ~10 行)

### 任务 B1: 情感共鸣 — valence 参数传入

**文件**: `chat_core/core/turn_manager.py:831-839`

```python
# 当前:
                self._relationship_engine.update(
                    user_id=user_id,
                    recall_hit_count=recall_hit_count,
                    ...
                )

# 替换为:
                ai_valence = 0.0
                if self._emotion_engine:
                    ai_state = self._emotion_engine.get_state("sub")
                    ai_valence = getattr(ai_state, "valence", 0.0)
                # user_valence 留 0.0 — 无独立用户情绪追踪时的安全默认
                # 情感共鸣规则 (|diff| < threshold) 在 AI 中性时触发

                self._relationship_engine.update(
                    user_id=user_id,
                    recall_hit_count=recall_hit_count,
                    combined_review_weight=review.combined_weight if review else 1.0,
                    inner_thoughts_text=self._last_inner_thoughts or "",
                    user_message=user_message,
                    correction_accepted=(review.decision == DecisionType.CORRECT),
                    memory_entry_count=len(memories),
                    user_emotion_valence=0.0,
                    ai_emotion_valence=ai_valence,
                )
```

> **注意**: v1 将 AI 自身 valence 同时赋值给 user_valence 和 ai_valence，导致 `|diff| ≡ 0`，情感共鸣每轮必定触发。修正后 user_valence 为 0.0（无用户情绪追踪的安全默认）。

**验证**: `pytest tests/test_relationship.py -v -x`

---

### 阶段 B 检查点

```bash
pytest tests/test_relationship.py tests/test_phase6_emotion.py -v -x
pytest tests/ -q
```

---

## 阶段 C: P2 局部遗漏 (4 文件, ~60 行)

### 任务 C1: context stub 方法实现

**文件**: `chat_core/core/turn_manager.py`

**C1a**: `__init__` 中初始化防御历史（L117 附近）:

```python
        self._defense_engine = DefenseEngine()
        self._defense_history: list[str] = []  # Spec 006: 防御类型历史
```

**C1b**: `_apply_defense` 中记录防御类型（L1230 `self._had_defense_this_turn = True` 之前）:

```python
        self._defense_history.append(defense.defense_type.value)
        self._had_defense_this_turn = True
```

**C1c**: `_build_defense_summary()` 替换 stub (L1363-1369):

```python
    def _build_defense_summary(self) -> dict[str, Any]:
        """构建防御模式总结。"""
        try:
            recent = self._defense_history[-10:] if self._defense_history else []
            unique = list(dict.fromkeys(recent))  # 保持顺序去重
            return {
                "activation_rate": round(len(self._defense_history) / max(1, self._turn_counter), 2),
                "main_types": ", ".join(unique[-3:]) if unique else "无",
                "error_counts": dict(self._error_history),
            }
        except Exception:
            return {"activation_rate": 0.0, "main_types": "无", "awareness_entries": []}
```

**C1d**: `_build_memory_state()` 替换 stub (L1371-1383):

```python
    async def _build_memory_state(self) -> dict[str, Any]:
        """构建记忆系统状态摘要。"""
        try:
            deep_count = 0
            decay_warnings = 0
            entries = await self._memory.query("self/inner_thoughts", limit=50)
            for e in entries:
                curve = getattr(e, "decay_curve", "standard")
                sal = getattr(e, "salience", 10.0)
                if curve == "deep":
                    deep_count += 1
                if sal < 3.0:
                    decay_warnings += 1
            return {
                "total_entries": len(entries),
                "deep_memory_count": deep_count,
                "decay_warning_count": decay_warnings,
            }
        except Exception:
            return {"total_entries": 0, "deep_memory_count": 0, "decay_warning_count": 0}
```

**验证**: `pytest tests/test_metacognition.py -v -x`

---

### 任务 C2: SubjectiveClock 情绪/兴趣参数传入 + 主观时间用于 boredom

**文件 1**: `chat_core/systems/boredom.py:36` — `__init__` 追加参数

```python
    def __init__(self, attention_model: Any = None, subjective_clock: Any = None,
                 energy_bar: Any = None, emotion_engine: Any = None,
                 interest_model: Any = None) -> None:
        ...
        self._emotion_engine = emotion_engine      # Spec 007
        self._interest_model = interest_model      # Spec 007
```

**文件 2**: `chat_core/systems/boredom.py:176-179` — `_tick_loop` 传入完整参数

```python
            if self._subjective_clock and self._attention_model:
                try:
                    attn_state = self._attention_model.get_state_enum("sub")
                    emotion_state = (
                        self._emotion_engine.get_state("sub")
                        if self._emotion_engine else None
                    )
                    # InterestModel 无 get_last_match() — 使用默认 0.0
                    interest_match = 0.0
                    self._subjective_clock.tick(
                        interval,
                        attention_state_enum=attn_state,
                        emotion_state=emotion_state,
                        interest_match=interest_match,
                    )
```

**文件 3**: `chat_core/systems/boredom.py:116` — `get_boredom()` 使用主观时间

```python
    def get_boredom(self) -> float:
        if not self._active:
            return 0.0
        # Spec 007: 使用主观时钟累计时间替代墙钟时间
        if self._subjective_clock and self._subjective_clock.accumulated > 0:
            elapsed = self._subjective_clock.accumulated
        else:
            elapsed = time.time() - self._start_time
        if elapsed <= 0:
            return 0.0
        decay_halflife = self.DEFAULT_DECAY_HALFLIFE
        boredom = 1.0 - self._eval_param * math.exp(-elapsed / decay_halflife)
        return max(0.0, min(1.0, boredom))
```

**文件 4**: `chat_core/core/turn_manager.py:161-165` — 实例化传入新参数

```python
        self._boredom_detector = BoredomDetector(
            attention_model=attention_model,
            subjective_clock=self._subjective_clock,
            energy_bar=self._energy_bar,
            emotion_engine=self._emotion_engine,
            interest_model=self._interest_model,
        )
```

**验证**: `pytest tests/test_subjective_time.py -v -x`

---

### 任务 C3: 脆弱感关系安全门

**文件 1**: `chat_core/systems/emotion.py:350` — `_check_vulnerability()` 追加关系检查

在 L356 (`if not self._vulnerability_enabled:`) 之后、L359 (`if self._vulnerability_cooldown > 0:`) 之前插入:

```python
        if not self._is_relationship_safe():
            self.is_vulnerable = False
            return False
```

在 `EmotionEngine` 类中新增两个方法:

```python
    def set_relationship_stage(self, stage: str | None) -> None:
        """Spec 005: 设置当前用户的关系阶段（用于脆弱安全门）。"""
        self._current_relationship_stage = stage

    def _is_relationship_safe(self) -> bool:
        """仅 friend / close_friend 允许脆弱暴露。"""
        stage = getattr(self, "_current_relationship_stage", None)
        if stage is None:
            return True  # 无关系数据时默认允许（CLI 单用户模式）
        return stage in ("friend", "close_friend")
```

**文件 2**: `chat_core/core/turn_manager.py` — 关系更新后同步通知

在 `_relationship_engine.update()` (L839) 之后追加:

```python
            if self._emotion_engine and self._relationship_engine:
                stage_enum = self._relationship_engine.get_stage(user_id)
                self._emotion_engine.set_relationship_stage(
                    stage_enum.value if stage_enum else None
                )
```

**验证**: `pytest tests/test_compound_emotion.py -v -x`

---

### 任务 C4: metacognition moral_escalation 触发 + compound_alert 订阅

**C4a**: `chat_core/systems/metacognition.py` — `check_triggers()` 新增独立触发条件

在 L117 (`triggered = True` 情绪冲击判定) 之后、L119 (`# 5. 自我批评`) 之前插入:

```python
        # 6. 道德冲突升级 (Spec 009) — 独立触发，不依赖 compound_delta
        if getattr(self, "moral_escalation_pending", False):
            triggered = True
```

在 L130 (`if triggered:`) 重置块中追加:

```python
            self.moral_escalation_pending = False
```

**C4b**: `chat_core/core/turn_manager.py:_ensure_listeners` (L218-224) — 订阅 compound_alert

在现有 `emotion_alert` 和 `logic_conflict` 订阅后追加:

```python
        if not self._listeners_ready:
            self._event_bus.subscribe("compound_alert", self._on_compound_alert)
            self._listeners_ready = True
```

新增回调方法:

```python
    async def _on_compound_alert(self, event: dict[str, Any]) -> None:
        """Spec 005: compound_alert → 注意力冲击。"""
        if self._attention_model:
            self._attention_model.apply_event(
                "sub",
                AttentionEvent.EMOTION_SHOCK,
                boost=0.30,
            )
```

**验证**: `pytest tests/test_moral.py tests/test_metacognition.py tests/test_compound_emotion.py -v -x`

---

### 任务 C5: ANGRY 沉默 resentment delta + memory._time_annotate dragging

**C5a**: `chat_core/core/turn_manager.py:1253-1256` — `_silent_archive` 中追加 ANGRY 处理

```python
        if silence_record.silence_type == SilenceType.OVERLOAD:
            self._energy_bar.boost_recovery(self._silence_classifier.get_recovery_boost())
        if (silence_record.silence_type == SilenceType.ANGRY
                and self._emotion_engine):
            self._emotion_engine.accelerate("sub", "resentment", 0.05)
```

**C5b**: `chat_core/systems/memory.py:740-748` — `_time_annotate` 追加 dragging

当前只处理 "immersed"。在现有 if/elif 块中追加:

```python
        elif perception == "dragging":
            parts.append("时间过得特别慢，有点煎熬")
```

**验证**: `pytest tests/test_silence.py tests/test_memory.py -v -x`

---

### 任务 C6: 编码约定修复

**文件**: `chat_core/systems/silence.py`, `chat_core/systems/motivation.py`, `chat_core/systems/loneliness.py`

在三个文件顶部（模块 docstring 之后、首个 import 之前）添加:

```python
from __future__ import annotations
```

**验证**: `pytest tests/test_silence.py tests/test_motivation.py -v -x`

---

### 阶段 C 检查点

```bash
pytest tests/test_metacognition.py tests/test_subjective_time.py \
      tests/test_compound_emotion.py tests/test_moral.py \
      tests/test_silence.py tests/test_memory.py tests/test_motivation.py -v -x
pytest tests/ -q  # 466 tests, 零新增回归
```

---

## 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| `vars(GroupRoleMetrics)` 在 Python 3.12+ 对 dataclass 返回完整字段 | 🟢 | dataclass 默认支持 `vars()`，无需额外处理 |
| BoredomDetector 主观时间替换可能导致 boredom 上升曲线变化 | 🟡 | `accumulated` 初始为 0，与 `time.time() - self._start_time` 行为一致 |
| `_on_compound_alert` 需 `AttentionEvent` import | 🟢 | `turn_manager.py` 已 import `AttentionEvent` |
| `_value_engine.get_modulation("moral_bias")` 返回 float，非 dict | ✅ | v2 已修正 — `moral_bias: float \| None` |

---

## 测试策略

每阶段完成后运行阶段测试 + 全量回归:

```bash
# 阶段 A
pytest tests/test_silence.py tests/test_relationship.py tests/test_defense.py \
      tests/test_metacognition.py tests/test_moral.py tests/test_values.py -v -x

# 阶段 B  
pytest tests/test_relationship.py tests/test_phase6_emotion.py -v -x

# 阶段 C
pytest tests/test_metacognition.py tests/test_subjective_time.py \
      tests/test_compound_emotion.py tests/test_moral.py \
      tests/test_silence.py tests/test_memory.py tests/test_motivation.py -v -x

# 全量回归
pytest tests/ -q
```

**基线**: 466 passed, 1 预存 flaky (`test_tick_updates_prev_valence`)
**目标**: ≤ 1 flaky，零新增失败

---

## 未纳入 (有意延后)

| 问题 | 原因 |
|------|------|
| QQ Bot adapter.py 集成 | ~80 行独立任务，需 QQ 环境 |
| NarrativeEngine 时间线存储 | `latest` 满足当前需求 |
| FR-10 硬约束 (max 段数) | 软实现足够 |
| `_build_turn_summaries` 扩展字段 | 需 LLM 解析，属于增强 |
| `InterestModel._last_match_score` | 现有 API 不支持零参查询，需要额外字段设计 |

---

## v1→v2 变更清单

| v1 缺陷 | v2 修正 |
|---------|---------|
| A2 丢弃 `apply_relationship_modulation` 返回值 | 存入 `runtime_state["relationship_modulation"]` |
| A6 `InterestModel.get_last_match()` 不存在 | 使用 `0.0` 默认值 |
| A6 `MotivationEngine.build_injection()` 缺参 | 先调 `evaluate()` 获取 `MotivationState` |
| A6 `ValueEngine.snapshot()` 不存在 | 使用 `vars(self._value_engine.values)` |
| A6 `NarrativeEngine.get_latest()` 不存在 | 使用 `self._narrative_engine.state.latest` |
| A6 `GroupDynamics.get_group_summary()` 不存在 | 使用 `get_metrics()` + `vars()` |
| A6 `moral_ctx` 类型 bool→str 不匹配 | 显式转为描述字符串 |
| A6 `get_stage()` 枚举未转字符串 | `.value` 转换 |
| B1 valence 永远相等 → 情感共鸣必触发 | user_valence=0.0, ai_valence=AI 实际值 |
| C1 `_defense_history` 不存在 | `__init__` 初始化 + `_apply_defense` 记录 |
| C4 moral_escalation 嵌套在 compound 块内 | 独立触发条件 |
| C4 compound_alert 未订阅 | 新增 `_on_compound_alert` + 事件订阅 |
| C2 boredom 未用主观时间 | `get_boredom()` 改用 `subjective_clock.accumulated` |
| C5 memory dragging 注解缺失 | 追加 `_time_annotate` dragging 分支 |
