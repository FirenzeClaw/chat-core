# QQ Bot adapter.py Spec 子系统集成计划 v2 (修正版)

> **面向 AI 代理的工作者：** 使用 superpowers:subagent-driven-development 逐任务实现此计划。
> **v2 修正**: 9 个致命 API 错误 + 3 个逻辑偏差，新增 ValueEngine 初始化

**目标：** 将 Spec 005/008/009/010/011 的 14 个子系统集成到 QQ Bot 管线（`adapter.py`），使 CLI 与 QQ Bot 双模式行为一致。

**架构：** 参照 `turn_manager.py` 已完成的集成模式，在 `adapter.py` 的 `__init__` → `_process` → `_async_review_and_decide` → `_archive` 链路上逐阶段追加。

**技术栈：** Python 3.12+, asyncio

**依赖图：**
```
ValueEngine (Spec 010) ── 独立基础
Spec 005 (DefenseEngine) ── 独立
Spec 008 (RelationshipEngine/PatternDetector/GroupDynamics补充) ── 独立
    │
    ├─→ Spec 009 HumorDetector (需要关系阶段做安全门)
    ├─→ Spec 011 LonelinessDetector (需要关系列表)
    └─→ Spec 011 MotivationEngine (需要孤独值)
Spec 009 (Intuition/Creativity/Moral) ── 部分独立
Spec 011 (SilenceClassifier) ── 独立
```

---

## 任务列表

### 阶段 1: Spec 005 + Spec 010 基础 (S: 1 文件, ~35 行)

#### 任务 1: adapter.py — 添加 DefenseEngine + ValueEngine + 脆弱感检测

**文件：**
- 修改：`chat_core/qq/adapter.py`

**import 追加：**
```python
from chat_core.core.types import DecisionType, DefenseType
from chat_core.systems.defense import DefenseEngine
from chat_core.systems.values import ValueEngine  # Spec 010
```

- [ ] **步骤 1: `__init__` 新增 ValueEngine + DefenseEngine**

在 `self._review_system = ReviewSystem(...)` 之后追加：
```python
        # Spec 010: 价值观引擎（审查阈值/防御概率/道德偏差调制）
        self._value_engine = ValueEngine()
        # Spec 005: 防御机制
        self._defense_engine = DefenseEngine()
        self._error_history: dict[str, int] = {}
        # Spec 008+: 运行时状态（供关系调制等跨阶段数据共享）
        self.runtime_state: dict[str, Any] = {}
```

- [ ] **步骤 2: `_async_review_and_decide` 追加防御判定**

在 `combined = review.combined_weight` 之后、`if combined > 0.5:` 之前插入：
```python
            # Spec 005: 防御判定（仅 CORRECT 决策且 combined > 0.5）
            if review.decision == DecisionType.CORRECT and review.combined_weight > 0.5:
                impulsiveness = (
                    self._personality_engine.weights.impulsiveness
                    if self._personality_engine else 0.2
                )
                for e in review.logic_errors:
                    et = e.error_type.value if hasattr(e.error_type, 'value') else str(e.error_type)
                    self._error_history[et] = self._error_history.get(et, 0) + 1
                compound_delta = (
                    self._emotion_engine.last_compound_delta
                    if self._emotion_engine else 0.0
                )
                defense = self._defense_engine.evaluate(
                    review, self._error_history,
                    impulsiveness=impulsiveness,
                    last_compound_delta=compound_delta,
                    is_vulnerable=(
                        self._emotion_engine.is_vulnerable
                        if self._emotion_engine else False
                    ),
                    value_engine=self._value_engine,
                )
                if defense.defense_type != DefenseType.DIRECT:
                    await self._memory.save(MemoryEntry(
                        namespace="subconscious/nudges",
                        key=f"defense_{ctx.session_key}_{int(time.time())}",
                        value={
                            "defense_type": defense.defense_type.value,
                            "correction_text": defense.correction_text,
                            "combined_weight": review.combined_weight,
                        },
                    ))
                    return  # 防御路径短路正常纠正流
```

> **注意**: `review.combined_weight` 替代 v1 中未定义的 `combined` 变量。

- [ ] **步骤 3: `_process` 末尾追加脆弱感检测**

在 `self._on_conversation_ended()` 之前追加：
```python
        # Spec 005: 脆弱感检测 → 写入 memory 供后续 turn 参考
        if self._emotion_engine and self._emotion_engine.is_vulnerable:
            await self._memory.save(MemoryEntry(
                namespace=f"user/{ctx.user_id}/c2c/conversations",
                key=f"vulnerability_{int(time.time())}",
                value={
                    "type": "vulnerability_exposed",
                    "turn": user_session.turn_counter,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            ))
```

**验证：**
```bash
python -m pytest tests/test_defense.py tests/test_compound_emotion.py tests/test_values.py -v -x
python -m pytest tests/ -q
```

---

### 阶段 2: Spec 008 → RelationshipEngine + PatternDetector + GroupDynamics 补充 (S: 1 文件, ~60 行)

#### 任务 2: adapter.py — 添加社交关系 + 习惯模式 + 群统计补充

**文件：**
- 修改：`chat_core/qq/adapter.py`

**import 追加：**
```python
from chat_core.systems.relationship import RelationshipEngine
from chat_core.systems.patterns import PatternDetector
```

- [ ] **步骤 1: `__init__` 新增引擎**

在 `self._group_dynamics.set_memory(memory_store)` 之后追加：
```python
        # Spec 008: 社交关系 + 习惯模式
        self._relationship_engine = RelationshipEngine()
        self._pattern_detector = PatternDetector()
        self._pattern_detector.set_memory(memory_store)
```

- [ ] **步骤 2: `_process` 开头追加群聊 reply/active_day 统计**

在 `user_session = self._sessions.get_or_create(...)` 之后追加：
```python
        # Spec 008: 群聊统计补充 (reply + active_day)
        if ctx.is_group:
            if ctx.is_at:
                self._group_dynamics.record_reply(ctx.group_id)
            self._group_dynamics.record_active_day(ctx.group_id)
```

- [ ] **步骤 3: `_process` 中 loop.run() 前追加 PersonalityEngine 关系调制**

在 `loop.set_reply_callback(_on_reply)` 之后、`loop.run(ctx.content)` 之前追加：
```python
        # Spec 008: 应用关系阶段人格调制
        if self._personality_engine and self._relationship_engine:
            stage = self._relationship_engine.get_stage(ctx.user_id)
            modulation = self._relationship_engine.get_modulation(ctx.user_id)
            mod_params = self._personality_engine.apply_relationship_modulation(
                stage=stage, modulation=modulation
            )
            self.runtime_state["relationship_modulation"] = mod_params
```

- [ ] **步骤 4: `_process` 末尾（审核注入完成后）追加关系更新 + 模式检测 + 脆弱安全门**

在 `memories` 已赋值之后（L247 `_context, memories = await inject_task` 之后）、脆弱感检测之前追加：
```python
        # Spec 008: 更新用户关系梯度
        if self._relationship_engine.enabled:
            ai_valence = 0.0
            if self._emotion_engine:
                ai_state = self._emotion_engine.get_state("sub")
                ai_valence = getattr(ai_state, "valence", 0.0)
            self._relationship_engine.update(
                user_id=ctx.user_id,
                recall_hit_count=0,  # adapter 无法可靠获取子Session recall 计数
                combined_review_weight=0.5,
                inner_thoughts_text=loop.inner_thoughts or "",
                user_message=ctx.content,
                correction_accepted=False,
                memory_entry_count=len(memories) if memories else 0,
                user_emotion_valence=0.0,
                ai_emotion_valence=ai_valence,
            )

        # Spec 008: 检测用户习惯模式 (async detect)
        if self._pattern_detector.enabled:
            await self._pattern_detector.detect(
                user_id=ctx.user_id,
                user_message=ctx.content,
                inner_thoughts_text=loop.inner_thoughts or "",
            )

        # Spec 005/008: 通知 EmotionEngine 当前关系阶段（脆弱安全门）
        if self._emotion_engine and self._relationship_engine:
            stage_enum = self._relationship_engine.get_stage(ctx.user_id)
            self._emotion_engine.set_relationship_stage(
                stage_enum.value if stage_enum else None
            )
```

- [ ] **步骤 5: `_get_or_create_sub_session` 传递关系阶段 + 社交模式**

在 `register_sub_session_tools(...)` 之后追加：
```python
        # Spec 008: 设置关系上下文
        if self._relationship_engine:
            stage = self._relationship_engine.get_stage(user_id)
            desc = self._relationship_engine.get_stage_description(stage)
            loop.set_relationship_context(user_id, stage.value, desc)
        # Spec 008: 设置社交模式
        if self._pattern_detector:
            pattern_hint = self._pattern_detector.get_pattern_injection(user_id)
            if pattern_hint:
                loop.set_social_patterns(pattern_hint)
```

- [ ] **步骤 6: `_async_review_and_decide` 防御调用传入 relationship_modulation**

更新步骤 1 中的 `defense.evaluate()` 调用，追加参数：
```python
                defense = self._defense_engine.evaluate(
                    ...,
                    value_engine=self._value_engine,
                    relationship_modulation=(
                        self._relationship_engine.get_modulation(ctx.user_id)
                        if self._relationship_engine else None
                    ),
                )
```

**验证：**
```bash
python -m pytest tests/test_relationship.py tests/test_group_dynamics.py tests/test_patterns.py tests/test_defense.py -v -x
python -m pytest tests/ -q
```

---

### 阶段 3: Spec 009 + Spec 011 (M: 1 文件, ~110 行)

#### 任务 3: adapter.py — 添加沉默语义 + 动机 + 孤独

**文件：**
- 修改：`chat_core/qq/adapter.py`

**import 追加：**
```python
from chat_core.systems.silence import SilenceClassifier, SilenceType
from chat_core.systems.motivation import MotivationEngine
from chat_core.systems.loneliness import LonelinessDetector
from chat_core.core.types import SilenceType as SilenceTypeEnum  # 如与 silence.py 冲突则别名
```

- [ ] **步骤 1: `__init__` 新增三个引擎**

在 `self._pattern_detector = PatternDetector()` 之后追加：
```python
        # Spec 011: 沉默语义 + 动机 + 孤独
        self._silence_classifier = SilenceClassifier()
        self._motivation_engine = MotivationEngine()
        self._loneliness_detector = LonelinessDetector()
```

- [ ] **步骤 2: `_async_review_and_decide` combined≤0.5 分支追加 SilenceClassifier**

替换原有的 else 分支内容。当前 L457-475 为：
```python
        else:
            # combined ≤ 0.5: 归档到 self/noticed/
            try:
                await self._memory.save(MemoryEntry(
                    namespace="self/noticed", ...
```

替换为：
```python
        else:
            # Spec 011: 沉默语义化判定
            try:
                silence_record = self._silence_classifier.classify(
                    review=review,
                    emotion=self._emotion_engine.get_state("sub") if self._emotion_engine else None,
                    energy=self._energy_bar._state.energy,
                    relationship_stage=(
                        self._relationship_engine.get_stage(ctx.user_id)
                        if self._relationship_engine else None
                    ),
                    silence_streak=0,  # adapter 无跨 turn 沉默计数
                    active_turns=0,
                )
                if silence_record.silence_type == SilenceType.OVERLOAD:
                    self._energy_bar.boost_recovery(
                        self._silence_classifier.get_recovery_boost()
                    )
                if (silence_record.silence_type == SilenceType.ANGRY
                        and self._emotion_engine):
                    self._emotion_engine.accelerate("sub", "resentment", 0.05)
            except Exception:
                logger.debug("沉默分类失败: user=%s", ctx.user_id[:12])

            # combined ≤ 0.5: 归档到 self/noticed/
            try:
                await self._memory.save(MemoryEntry(
                    namespace="self/noticed",
                    key=f"noticed_{ctx.session_key}_{int(time.time())}",
                    value={
                        "combined_weight": combined,
                        "logic_weight": review.logic_weight,
                        "emotion_weight": review.emotion_weight,
                        "logic_errors": [e.description for e in review.logic_errors],
                        "tone_issues": [i.description for i in review.emotion_issues],
                        "original_replies": replies,
                        "user_id": ctx.user_id,
                    },
                ))
                logger.debug("审查沉默归档: user=%s combined=%.2f", ctx.user_id[:12], combined)
            except Exception:
                logger.debug("沉默归档失败: user=%s", ctx.user_id[:12])
```

- [ ] **步骤 3: `_process` 末尾追加 LonelinessDetector.tick()**

在 `_on_conversation_ended()` 之前追加：
```python
        # Spec 011: 孤独感更新（每 turn 约 60s 墙钟）
        if self._loneliness_detector and self._relationship_engine:
            stage = self._relationship_engine.get_stage(ctx.user_id)
            rel_list = [(ctx.user_id, stage.value)] if stage else []
            speed = self._subjective_clock.speed_factor if self._subjective_clock else 1.0
            self._loneliness_detector.tick(60.0, rel_list, subjective_speed=speed)
```

- [ ] **步骤 4: `_process` 追加 MotivationEngine 评估 + 注入**

在 LonelinessDetector.tick() 之后追加：
```python
        # Spec 011: 动机评估 → 注入子 Session 下一轮
        if self._motivation_engine.enabled:
            loneliness_val = self._loneliness_detector.level
            ms = self._motivation_engine.evaluate(
                boredom=self._boredom_detector.get_boredom(),
                energy=self._energy_bar._state.energy,
                loneliness=loneliness_val,
            )
            hint = self._motivation_engine.build_injection(ms)
            if hint:
                loop.set_motivation_hint(hint)
```

**验证：**
```bash
python -m pytest tests/test_silence.py tests/test_motivation.py -v -x
python -m pytest tests/ -q
```

---

#### 任务 4: adapter.py — 添加 Spec 009 (Intuition + Creativity + Humor + Moral)

**文件：**
- 修改：`chat_core/qq/adapter.py`

**import 追加：**
```python
from chat_core.systems.intuition import IntuitionEngine
from chat_core.systems.creativity import CreativityEngine
from chat_core.systems.humor import HumorDetector
from chat_core.systems.moral import MoralConflictDetector, ProConAssessor
```

- [ ] **步骤 1: `__init__` 新增四个引擎**

在 Spec 011 引擎之后追加：
```python
        # Spec 009: 认知增强
        self._intuition_engine = IntuitionEngine()
        self._creativity_engine = CreativityEngine()
        self._humor_detector = HumorDetector()
        self._moral_detector = MoralConflictDetector()
        self._pro_con_assessor = ProConAssessor()
```

- [ ] **步骤 2: `_get_or_create_sub_session` 传递 IntuitionEngine**

在 `register_sub_session_tools(...)` 之后追加：
```python
        # Spec 009: 直觉引擎
        loop.set_intuition_engine(self._intuition_engine)
```

- [ ] **步骤 3: `_process` 中 loop.run() 前追加 Creativity + Humor**

在 `loop.run(ctx.content)` 之前追加：
```python
        # Spec 009: 创造力上下文
        if self._creativity_engine.enabled:
            creativity_ctx = await self._run_creativity_dual_path(ctx.content)
            if creativity_ctx:
                loop.set_creativity_context(creativity_ctx)
        # Spec 009: 幽默检测
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

- [ ] **步骤 4: 新增辅助方法 `_run_creativity_dual_path`**

在 `_on_conversation_started` 方法之前追加：
```python
    async def _run_creativity_dual_path(self, user_message: str) -> str | None:
        """Spec 009: 创造力双路径执行。
        
        CreativityEngine 不负责 LLM 调用——adapter 自行编排。
        Path A: Flash LLM 概念发散
        Path B: 联锁检索放大
        """
        playfulness = (
            self._personality_engine.weights.playfulness
            if self._personality_engine else 0.5
        )
        if not self._creativity_engine.should_trigger(playfulness, user_message):
            return None
        # Path A: 生成 prompt → Flash LLM → 解析
        prompt = self._creativity_engine.build_path_a_prompt(user_message)
        try:
            response = await self._provider.chat(
                messages=[Message(role="user", content=prompt)],
                model="deepseek-v4-flash",
                max_tokens=256,
                temperature=0.9,
            )
            path_a_mappings = self._creativity_engine.parse_path_a_result(response.content)
        except Exception:
            logger.debug("Path A Flash 调用失败")
            path_a_mappings = []
        # Path B: 联锁检索放大
        try:
            chain_config = self._creativity_engine.get_extended_chain_config()
            results = await self._memory.search_chained(user_message, chain_config)
            path_b_summaries = self._creativity_engine.filter_path_b_memories(results)
        except Exception:
            logger.debug("Path B 联锁检索失败")
            path_b_summaries = []
        return self._creativity_engine.build_injection(path_a_mappings, path_b_summaries)
```

- [ ] **步骤 5: `_process` 末尾追加 MoralConflictDetector**

在 `_on_conversation_ended()` 之前追加（作为 fire-and-forget 避免阻塞主流程）：
```python
        # Spec 009: 道德困境检测 (fire-and-forget)
        if self._moral_detector.enabled:
            asyncio.create_task(self._moral_check(ctx, loop, user_session))
```

- [ ] **步骤 6: 新增辅助方法 `_moral_check`**

在 `_run_creativity_dual_path` 之后追加：
```python
    async def _moral_check(
        self, ctx: MessageContext, loop: ReActLoop, user_session: UserSession,
    ) -> None:
        """Spec 009: 道德困境检测 + Pro/Con 双脑 + 归档 (fire-and-forget)。"""
        try:
            stage = (
                self._relationship_engine.get_stage(ctx.user_id)
                if self._relationship_engine else None
            )
            moral_conflict = self._moral_detector.detect(
                user_message=ctx.content,
                inner_thoughts=loop.inner_thoughts,
                relationship_stage=stage,
                energy=self._energy_bar._state.energy,
            )
            if not moral_conflict or not self._logic_brain or not self._emotion_brain:
                return
            conflict_ctx = f"困境: {moral_conflict.trigger_description}\n用户消息: {ctx.content}"
            logic_score, logic_reason = await self._logic_brain.pro_con(conflict_ctx)
            emotion_score, emotion_reason = await self._emotion_brain.pro_con(conflict_ctx)
            # Spec 010: 价值观调制 moral_bias
            moral_bias = (
                self._value_engine.get_modulation("moral_bias")
                if self._value_engine else None
            )
            assessment = self._pro_con_assessor.assess(
                logic_score, logic_reason, emotion_score, emotion_reason,
                moral_bias=moral_bias,
            )
            await self._memory.save(MemoryEntry(
                namespace=f"self/moral/{user_session.turn_counter}",
                key="assessment",
                value={
                    "conflict_type": moral_conflict.conflict_type.value,
                    "logic_score": assessment.logic_score,
                    "emotion_score": assessment.emotion_score,
                    "recommended_path": assessment.recommended_path,
                    "deadlock": assessment.deadlock,
                    "escalation": assessment.escalation,
                },
            ))
            if assessment.deadlock:
                await self._memory.save(MemoryEntry(
                    namespace="subconscious/moral_conflict",
                    key=str(user_session.turn_counter),
                    value={
                        "path": "deadlock",
                        "logic": assessment.logic_reasoning,
                        "emotion": assessment.emotion_reasoning,
                    },
                ))
        except Exception:
            logger.debug("道德检测失败: user=%s", ctx.user_id[:12])
```

**验证：**
```bash
python -m pytest tests/test_intuition.py tests/test_creativity.py tests/test_humor.py tests/test_moral.py -v -x
python -m pytest tests/ -q
```

---

### 最终检查点
```bash
python -m pytest tests/test_defense.py tests/test_compound_emotion.py tests/test_values.py \
      tests/test_relationship.py tests/test_group_dynamics.py tests/test_patterns.py \
      tests/test_silence.py tests/test_motivation.py \
      tests/test_intuition.py tests/test_creativity.py tests/test_humor.py tests/test_moral.py \
      -v -x
python -m pytest tests/ -q  # 467+ tests
```

---

## 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| CreativityEngine Path A Flash 调用依赖 `self._provider.chat()` 支持动态 model 参数 | 🟡 | 已验证 `ModelProvider.chat(model=...)` 支持动态切换 |
| MoralConflictDetector fire-and-forget 中双脑 Pro/Con 调用可能失败（LLM 超时等） | 🟢 | try/except 静默降级，不影响主回复流程 |
| `_process` 方法增长至 ~180 行 | 🟢 | 分段注释保持可读性，新增逻辑以辅助方法 (`_run_creativity_dual_path`, `_moral_check`) 抽离 |
| `_pattern_detector.set_memory()` 需在 `__init__` 中调用 | 🟢 | `memory_store` 在 `__init__` 中可用 |
| `SilenceClassifier` 的 `silence_streak` 参数 adapter 无法提供跨 turn 计数 | 🟢 | 固定传 0，语义分类仍有效（基于当前审查结果 + 情绪/精力状态） |

---

## 文件变更总览

| 文件 | 阶段 1 | 阶段 2 | 阶段 3 | 总行数 |
|------|:---:|:---:|:---:|:---:|
| `chat_core/qq/adapter.py` | +40 | +65 | +120 | **+225** |
| `chat_core/qq_bot.py` | 0 | 0 | 0 | **0** |

> **qq_bot.py 无需变更**：所有新引擎均为零外部依赖，adapter.py 内部独立初始化。

---

## v1→v2 变更清单

| v1 缺陷 | v2 修正 |
|---------|---------|
| `combined` 变量未定义 | 改用 `review.combined_weight` |
| `PatternDetector.detect_and_update()` 不存在 | 改用 `detect(user_id, message, inner_thoughts)` |
| `PatternDetector.get_system_injection()` 不存在 | 改用 `get_pattern_injection(user_id)` |
| `LonelinessDetector.current` 不存在 | 改用 `level` 属性 |
| `RelationshipEngine.get_all_relationships()` 不存在 | 改用 `[(user_id, stage.value)]` 手动构造 |
| `HumorDetector.detect_and_build()` 不存在 | 分两步: `detect()` + `build_injection()` |
| `CreativityEngine.should_trigger()` 参数颠倒 | 修正为 `should_trigger(playfulness, user_message)` |
| `CreativityEngine.run_path_a/b()` 不存在 | 重写为 adapter 自行编排 LLM 调用 |
| `loop._recall_history` 不存在 | `recall_hit_count=0` |
| ValueEngine 缺失 | 新增 `ValueEngine()` 初始化 |
| moral_bias 未传入 assess() | 传入 `moral_bias` 参数 |
| PersonalityEngine 调制结果丢弃 | 存入 `self.runtime_state` |
