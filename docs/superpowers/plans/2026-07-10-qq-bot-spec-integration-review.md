# Plan Review: QQ Bot Spec 子系统集成计划

> 审查目标: `docs/superpowers/plans/2026-07-10-qq-bot-spec-integration.md`
> 审查方法: 逐 API 交叉验证实际代码库方法签名

---

## 一、致命缺陷 (9 个 — 代码将运行时失败)

### 🔴 DEFECT-1: Task 1 Step 2 — `combined` 变量未定义即使用

**位置**: Plan L55
**问题**: 代码写在 `combined = review.combined_weight` 之前，但条件使用了 `combined > 0.5`。`combined` 此时未赋值。
**修复**: 使用 `review.combined_weight > 0.5` 替代 `combined > 0.5`。L84 同样改为 `review.combined_weight`。

---

### 🔴 DEFECT-2: Task 2 Step 4 — `PatternDetector.detect_and_update()` 不存在

**位置**: Plan L170
**实际 API**: `detect(user_id, user_message, inner_thoughts_text)` — async，返回 `list[InteractionPattern]`
**修复**:
```python
        if self._pattern_detector.enabled:
            await self._pattern_detector.detect(
                user_id=ctx.user_id,
                user_message=ctx.content,
                inner_thoughts_text=loop.inner_thoughts or "",
            )
```

---

### 🔴 DEFECT-3: Task 2 Step 6 — `PatternDetector.get_system_injection()` 不存在

**位置**: Plan L200
**实际 API**: `get_pattern_injection(user_id) -> str | None`
**修复**:
```python
        if self._pattern_detector:
            pattern_hint = self._pattern_detector.get_pattern_injection(user_id)
```

---

### 🔴 DEFECT-4: Task 4 Step 4 — `LonelinessDetector.current` 不存在

**位置**: Plan L340
**实际 API**: `level` (property, returns `float`)
**修复**: `self._loneliness_detector.level if self._loneliness_detector else 0.0`

---

### 🔴 DEFECT-5: Task 4 Step 5 — `RelationshipEngine.get_all_relationships()` 不存在

**位置**: Plan L359
**实际做法** (见 turn_manager.py:976):
```python
relationships=[(ctx.user_id, stage.value)] if stage else []
```
**修复**:
```python
        if self._loneliness_detector and self._relationship_engine:
            stage = self._relationship_engine.get_stage(ctx.user_id)
            rel_list = [(ctx.user_id, stage.value)] if stage else []
            speed = self._subjective_clock.speed_factor if self._subjective_clock else 1.0
            self._loneliness_detector.tick(60.0, rel_list, subjective_speed=speed)
```

---

### 🔴 DEFECT-6: Task 5 Step 4 — `HumorDetector.detect_and_build()` 不存在

**位置**: Plan L425
**实际 API**: `detect(user_message, relationship_stage)` + `build_injection(opportunities)` — 两个独立方法
**修复**:
```python
        if self._humor_detector.enabled:
            stage = (
                self._relationship_engine.get_stage(ctx.user_id)
                if self._relationship_engine else None
            )
            opportunities = self._humor_detector.detect(ctx.content, stage)
            humor_hint = self._humor_detector.build_injection(opportunities)
            if humor_hint:
                loop.set_humor_hint(humor_hint)
```

---

### 🔴 DEFECT-7: Task 5 Step 4 — `CreativityEngine.should_trigger()` 参数顺序颠倒

**位置**: Plan L434
**实际签名**: `should_trigger(playfulness: float, user_message: str) -> bool`
**Plan 调用**: `should_trigger(user_message, playfulness)` — 参数顺序错误
**修复**: `self._creativity_engine.should_trigger(playfulness, user_message)`

---

### 🔴 DEFECT-8: Task 5 Step 4 — `CreativityEngine.run_path_a()` / `run_path_b()` 不存在

**位置**: Plan L439-444
**实际 API**: 
- `build_path_a_prompt(user_message)` — 生成 prompt 字符串
- `parse_path_a_result(text)` — 解析 LLM 响应
- `get_extended_chain_config()` — 返回 RecallChainConfig
- `filter_path_b_memories(results)` — 过滤记忆结果

**CreativityEngine 不负责 LLM 调用** — 调用方（adapter/turn_manager）自己调 LLM。
**修复**: 重写 `_run_creativity_dual_path`:
```python
    async def _run_creativity_dual_path(self, user_message: str) -> str | None:
        playfulness = (
            self._personality_engine.weights.playfulness
            if self._personality_engine else 0.5
        )
        if not self._creativity_engine.should_trigger(playfulness, user_message):
            return None
        # Path A: Flash LLM 概念发散
        prompt = self._creativity_engine.build_path_a_prompt(user_message)
        response = await self._provider.chat(
            messages=[Message(role="user", content=prompt)],
            model="deepseek-v4-flash",
            max_tokens=256,
            temperature=0.9,
        )
        path_a_mappings = self._creativity_engine.parse_path_a_result(response.content)
        # Path B: 联锁检索放大
        chain_config = self._creativity_engine.get_extended_chain_config()
        results = await self._memory.search_chained(user_message, chain_config)
        path_b_summaries = self._creativity_engine.filter_path_b_memories(results)
        return self._creativity_engine.build_injection(path_a_mappings, path_b_summaries)
```

---

### 🔴 DEFECT-9: Task 2 Step 3 — `loop._recall_history` 不存在

**位置**: Plan L151
**ReActLoop**: 无 `_recall_history` 属性
**修复**: 直接使用 `recall_hit_count=0`（adapter 上下文无法可靠获取子 Session 的 recall 计数）

---

## 二、逻辑偏差 (3 个)

### 🟡 DEVIATION-1: Task 3 Step 2 — PersonalityEngine 调制结果未消费

**位置**: Plan L244-246
**问题**: `apply_relationship_modulation()` 仅计算并返回 dict，不修改内部状态。adapter 调用后丢弃返回值 = 无效果。且 adapter 无 `runtime_state` dict。
**修复**: 在 adapter `__init__` 中初始化 `self.runtime_state: dict[str, Any] = {}`，存储结果：
```python
            mod_params = self._personality_engine.apply_relationship_modulation(
                stage=stage, modulation=modulation
            )
            self.runtime_state["relationship_modulation"] = mod_params
```

---

### 🟡 DEVIATION-2: 遗漏 — ValueEngine 未传入 MoralConflictDetector.assess()

**位置**: Plan L468
**问题**: CLI 修复中已将 `moral_bias` 传入 `ProConAssessor.assess()`，但 QQ Bot 计划中未包含此修复。
**修复**: 在 `assess()` 调用前获取并传入:
```python
                moral_bias = (
                    self._value_engine.get_modulation("moral_bias")
                    if self._value_engine else None
                )
                assessment = self._pro_con_assessor.assess(
                    logic_score, logic_reason, emotion_score, emotion_reason,
                    moral_bias=moral_bias,
                )
```
注意：需要先初始化 `ValueEngine`（当前 adapter 无此实例）。

---

### 🟡 DEVIATION-3: 遗漏 — adapter 未初始化 ValueEngine

**位置**: adapter `__init__`
**问题**: CLI 修复中 ValueEngine 是关键依赖（调制审查阈值、防御概率、道德偏差），QQ Bot 计划完全未引入。
**修复**: 在 `__init__` 中追加:
```python
        from chat_core.systems.values import ValueEngine
        self._value_engine = ValueEngine()
```

---

## 三、小问题 (3 个)

### ⚠️ MINOR-1: Task 2 Step 3 — `memories` 变量在 adapter 上下文中不可靠

Plan L159 使用 `len(memories) if memories else 0`，但 `memories` 来自 `inject_task` 返回值 (L247: `_context, memories = await inject_task`)。此赋值在 `_process` 末尾，而 Task 2 Step 3 代码在 `inject_task` 等待之后（L247）、`loop.run()` 之后。此时 `memories` 确实已赋值。✅ 此点无问题。但代码放置位置需确认在 `memories` 赋值之后。

### ⚠️ MINOR-2: Task 1 Step 2 — `DecisionType` 需从 types.py import

`adapter.py` 当前仅 import `AttentionEvent, MemoryEntry, Message, RecallChainConfig, SUB_SESSION_CHAIN_CONFIG`。需追加 `DecisionType`。Plan L92 已提及但未列出完整 import 行。

### ⚠️ MINOR-3: Stage 1 文件数标注错误

Plan L27: "S: 2 文件" 但 qq_bot.py 零变更。应标注为 "S: 1 文件"。

---

## 四、汇总

| 类别 | 数量 | 涉及任务 |
|------|:---:|------|
| 🔴 致命 (运行时失败) | 9 | Task 1, 2, 4, 5 |
| 🟡 逻辑偏差 | 3 | Task 3, Task 5, init |
| ⚠️ 小问题 | 3 | 标注 |

### 新增依赖

Plan 声称 "qq_bot.py 无需变更"，但实际上：
- **ValueEngine** 未初始化 → 需要在 adapter `__init__` 或 qq_bot.py 中初始化
- **CreativityEngine Path A** 需要 LLM 调用 → adapter 已有 `self._provider`，可用

### 遗漏的子系统

Plan 覆盖了 13 个子系统，但以下 CLI 修复内容未包含在 QQ Bot 计划中：
- `ValueEngine` 初始化（Spec 010）
- `ANGRY → resentment +0.05`（Spec 011，_silent_archive 中）
- `compound_alert → 注意力事件`（Spec 005，仅在 CLI turn_manager 中实现）
- EmotionEngine `set_relationship_stage` 在 `_process` 中调用（已在 Task 3 Step 4 覆盖）✅

---

## 五、结论

Plan 结构合理（三阶段、依赖图正确），但 **8 个 API 调用引用不存在的方法** + **1 个变量未定义bug** 需要在实施前修正。CreativityEngine 的 Path A/B 设计误解最严重——该引擎不负责 LLM 调用，adapter 需自行编排。

**建议**: 修正上述 12 个缺陷后重新发布 plan v2。
