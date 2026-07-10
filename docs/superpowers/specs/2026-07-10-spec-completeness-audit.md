# 8 个 Spec 实施完整度审计报告

> **审计时间**: 2026-07-10
> **审计范围**: 注意力状态机 + Spec 003/005/006/007/008/009/010/011（共 9 套系统）
> **基线**: 466 tests, 18 新系统文件
> **方法**: 逐 FR 交叉审核 (设计文档 × 实施计划 × 源代码 × 测试)

---

## 一、汇总总表

| # | Spec | FR 覆盖 | 集成完整度 | 测试 | 综合评分 | 关键问题 |
|:---:|------|:---:|:---:|:---:|:---:|------|
| — | 注意力状态机 | 20/20 | 20/20 | 13 | ✅ 完整 | FR-10 为软实现 (prompt 提示而非硬约束) |
| 003 | 记忆联锁+遗忘 | 15/15 | 16/16 | 35 | ✅ 完整 | 零遗漏 |
| 005 | 复合情绪+防御 | 14/18 | 12/16 | 38 | 🟡 有遗漏 | QQ Bot 缺 DefenseEngine/脆弱感; 脆弱感缺关系安全门; compound_alert 无消费者 |
| 006 | 元认知深度 | 12/14 | 14/15 | 29 | 🟡 有遗漏 | insight_text 未注入 system prompt; 3 个 context stub |
| 007 | 具身感知 | 13/16 | 13/14 | 16 | 🟢 基本完整 | SubjectiveClock 情绪/兴趣调制未连接; 主观时间未用于 boredom 计算 |
| 008 | 社交与关系 | 20/20 | 6/12 | 43 | 🔴 有遗漏 | PersonalityEngine/DefenseEngine 调制链断裂; QQ Bot 未集成; 情感共鸣链路断裂 |
| 009 | 认知增强 | 21/21 | 10/12 | 53 | 🟢 基本完整 | moral_escalation 标记未消费; QQ Bot 未集成 |
| 010 | 价值体系+叙事 | 8/13 | 7/12 | 17 | 🔴 有遗漏 | MoralConflictDetector 未读 ValueEngine; build_context 参数缺失; 时间线存储不完整 |
| 011 | 沉默语义+动机 | 14/18 | 7/12 | 92 | 🔴 有遗漏 | SILENCE 决策路径不可达; build_context 参数缺失; ANGRY resentment 未应用 |

**FR 总览**: ~148 FR 中 ~137 完整实现 (93%), ~11 有遗漏 (7%)

---

## 二、P0 级问题 — 运行时逻辑断裂 (6 项)

### P0-1 [Spec 011] SILENCE 决策路径不可达

**位置**: `chat_core/core/turn_manager.py:_async_review_and_decide` (L670-672)

**现象**: `_async_review_and_decide` 仅处理 `DecisionType.CORRECT` 和 `DecisionType.TWISTED`，`DecisionType.SILENCE` 直接跳过不处理。`_silent_archive()` (含 SilenceClassifier 语义分类 + EnergyBar.boost_recovery + NarrativeEngine.append_chapter + 沉默历史持久化) 仅在 `_issue_correction` 的 `combined ≤ 0.5` 分支被调用，对 SILENCE 决策不可达。

**影响**: 整个 Spec 011 的核心运行时功能 (5 类沉默语义判定、OVERLOAD 恢复加速、ANGRY 情绪影响、沉默驱动叙事事件、沉默历史持久化) 在真实的 SILENCE 决策中**永不触发**。

**修复方案**: 在 `_async_review_and_decide` 中增加 SILENCE 分支，调用 `_silent_archive()`。

---

### P0-2 [Spec 008] PersonalityEngine 关系调制链路未连接

**位置**: `chat_core/systems/personality.py:159-201` (apply_relationship_modulation 已定义) vs `chat_core/core/turn_manager.py` (未调用)

**现象**: `PersonalityEngine.apply_relationship_modulation()` 实现了关系阶段对 empathy/self_disclosure/proactive/playfulness 四个参数的调制系数 (stranger ×0.7, close_friend ×1.3 等)，但 `turn_manager.py` 在 process_turn 中从未调用此方法。

**影响**: 关系阶段对人格参数的调制完全无效。设计意图中的"越熟越放得开"行为在运行时不会体现。

**修复方案**: 在 `turn_manager.py:process_turn` 的 SUB_SESSION 阶段前，调用 `self._personality.apply_relationship_modulation(stage)`。

---

### P0-3 [Spec 008] DefenseEngine 关系调制参数未传入

**位置**: `chat_core/systems/defense.py:59,101-103` (参数已接受) vs `chat_core/core/turn_manager.py:664` (未传入)

**现象**: `DefenseEngine.evaluate()` 接受 `relationship_modulation` 参数用于调制防御概率 (stranger ×1.5, close_friend ×0.5)，但 `turn_manager.py` 在审查阶段调用时未传入此参数。

**影响**: 关系阶段对防御概率的调制不生效。设计意图"对陌生人更有防御，对密友更坦诚"无法在运行时体现。

**修复方案**: 在 `turn_manager.py:664` 调用 `defense_engine.evaluate()` 时，从 `relationship_engine.get_modulation()` 获取 `defense_prob_mult` 并传入。

---

### P0-4 [Spec 006] 元认知 insight_text 未注入 system prompt

**位置**: `chat_core/core/turn_manager.py:753-761` (insight_text → memory) vs `chat_core/core/loop.py` (缺少 `_inject_metacognition_insight()`)

**现象**: 元认知审查生成的洞察文本 (`insight_text`) 仅存入 `self/metacognition/insight_{turn}` 记忆，但未注入子 Session 的 `_init_messages` system prompt。设计 §7 明确要求 `"[自我洞察] {insight_text}"` 格式注入。

**影响**: 元认知的"自我反思"无法影响后续子 Session 行为。LLM 看不到自己的反思结论。

**修复方案**: 在 `loop.py` 新增 `_inject_metacognition_insight()` 方法，从 `subconscious/metacognition_insight` 读取并注入 system prompt。

---

### P0-5 [Spec 010] ValueEngine → MoralConflictDetector 集成缺失

**位置**: `chat_core/systems/values.py:127-132` (moral_bias 计算已就绪) vs `chat_core/systems/moral.py` (无 ValueEngine 引用)

**现象**: ValueEngine 的 `get_modulation("moral_bias")` 返回当前价值观对道德困境的倾向调制，但 `MoralConflictDetector` 和 `ProConAssessor` 的代码中无任何对 ValueEngine 的引用。

**影响**: 价值观体系对道德判断的调制链路完全断链。例如，"care"权重高的 AI 在诚实-vs-保护困境中应更倾向保护，但此调制永远不会发生。

**修复方案**: 在 `turn_manager.py` 道德检测流程中，将 `value_engine.get_modulation("moral_bias")` 传入 `ProConAssessor.assess()`。

---

### P0-6 [跨 Spec] QQ Bot adapter.py 缺失 Spec 005/008/009/011 集成

**位置**: `chat_core/qq/adapter.py`

**现象**: `BotAdapter.process_message()` 直接运行 ReActLoop，但缺少以下系统的初始化和调用:

| Spec | 缺失系统 | 影响 |
|------|---------|------|
| Spec 005 | DefenseEngine, 脆弱感检测 | QQ 多用户模式下无防御机制和脆弱暴露 |
| Spec 008 | RelationshipEngine, PatternDetector | QQ 模式下无关系梯度和习惯检测 |
| Spec 008 | GroupDynamics (部分) | 缺少 record_reply/record_member_reply/record_active_day |
| Spec 009 | 全部四个系统 (直觉/创造力/幽默/道德) | QQ 模式下无认知增强能力 |
| Spec 011 | SilenceClassifier/MotivationEngine/LonelinessDetector | QQ 模式下无沉默语义和动机系统 |

**修复方案**: 参照 `turn_manager.py` 的管线，在 `adapter.py` 中逐系统集成。建议作为一个独立集成任务处理。

---

## 三、P1 级问题 — 数据桥接缺失 (1 项)

### P1-1 [跨 Spec] turn_manager.py build_context 调用缺少扩展参数

**位置**: `chat_core/core/turn_manager.py:738`

**现象**: `MetacognitionEngine.build_context()` 签名已扩展支持 10+ 个参数，但 `turn_manager.py` 调用时仅传入基础参数。缺失参数:

| 参数 | 所属 Spec | 数据源 | 状态 |
|------|----------|--------|------|
| `relationship_stage` | Spec 008 | `self._relationship_engine.stage` | ❌ 未传 |
| `group_role_summary` | Spec 008 | `self._group_dynamics.get_summary()` | ❌ 未传 |
| `moral_conflict_context` | Spec 009 | `self._last_moral_conflict` | ❌ 未传 |
| `value_state` | Spec 010 | `self._value_engine.snapshot()` | ❌ 未传 |
| `narrative_text` | Spec 010 | `self._narrative_engine.get_latest()` | ❌ 未传 |
| `silence_pattern` | Spec 011 | `self._silence_classifier.last_classification` | ❌ 未传 |
| `active_motivations` | Spec 011 | `self._motivation_engine.get_active()` | ❌ 未传 |

**影响**: 元认知审查上下文缺失社会关系、群角色、道德冲突、价值观变化、沉默模式、活跃动机等关键信息，审视质量严重降级。

**修复方案**: 在 `turn_manager.py:738` 调用处逐个传入缺失参数。各数据源在 `turn_manager.py` 中均已有引用，无需新增访问器。

---

## 四、P2 级问题 — 其他局部遗漏 (8 项)

### 4.1 Spec 005

| 编号 | 问题 | 位置 | 修复方案 |
|:---:|------|------|------|
| 005-1 | 脆弱感缺少关系安全门 (仅 friend+ 应触发) | `emotion.py:_check_vulnerability()` | 检查 `relationship_engine.stage >= friend` |
| 005-2 | compound_alert 事件无消费者 (注意力状态机未订阅) | `turn_manager.py:_ensure_listeners` | 订阅 `compound_alert` 事件 → `AttentionEvent.EMOTION_SHOCK` |

### 4.2 Spec 006

| 编号 | 问题 | 位置 | 修复方案 |
|:---:|------|------|------|
| 006-1 | `_build_defense_summary()` stub (硬编码返回空值) | `turn_manager.py:1363-1369` | 从 `_defense_engine` 获取真实统计 |
| 006-2 | `_build_memory_state()` stub (硬编码返回 0) | `turn_manager.py:1371-1383` | 从 `_memory_store` 查询真实统计 |
| 006-3 | `_build_turn_summaries()` 数据不完整 (缺话题/情绪/审查) | `turn_manager.py:1345-1361` | 追加完整字段 |

### 4.3 Spec 007

| 编号 | 问题 | 位置 | 修复方案 |
|:---:|------|------|------|
| 007-1 | SubjectiveClock 情绪/兴趣调制运行时未连接 | `boredom.py:_tick_loop` | 传入 `emotion_state` 和 `interest_match` |
| 007-2 | 主观时间未用于 boredom 计算 | `boredom.py:get_boredom()` | 用 `subjective_clock.total_subjective_time` 替代 `time.time()` |

### 4.4 Spec 008

| 编号 | 问题 | 位置 | 修复方案 |
|:---:|------|------|------|
| 008-1 | 情感共鸣链路断裂 (valence 参数未传入) | `turn_manager.py:831` | 传入 `user_emotion_valence` 和 `ai_emotion_valence` |

### 4.5 Spec 009

| 编号 | 问题 | 位置 | 修复方案 |
|:---:|------|------|------|
| 009-1 | metacognition moral_escalation_pending 标记未消费 | `metacognition.py:check_triggers()` | 新增 moral_escalation 触发条件 |

### 4.6 Spec 010

| 编号 | 问题 | 位置 | 修复方案 |
|:---:|------|------|------|
| 010-1 | NarrativeEngine 时间线存储不完整 (仅 latest，无 timeline/{date}) | `narrative.py` | 每次 append_chapter 同时写入 `self/narrative/timeline/{date}` |
| 010-2 | 子 Session recall 无法访问 self/narrative/* 命名空间 | 权限配置 | 扩展子 Session recall scope，或通过 system prompt 注入间接解决 |

### 4.7 Spec 011

| 编号 | 问题 | 位置 | 修复方案 |
|:---:|------|------|------|
| 011-1 | ANGRY 沉默 resentment +0.05 未应用 | `turn_manager.py:_silent_archive` | 调用 `emotion.accelerate("sub", "resentment", 0.05)` |
| 011-2 | 3 个新文件缺少 `from __future__ import annotations` | silence.py, motivation.py, loneliness.py | 文件头部添加 |

---

## 五、各 Spec 详细审查

### 注意力状态机 (基础系统)

**FR 覆盖**: 20/20 ✅
**测试**: 13 new tests

| FR | 描述 | 证据 |
|----|------|------|
| 三态模型 | FOCUSED/DRIFTING/DULL | `types.py:385-389`, `attention.py:19-26` |
| 13 事件转移矩阵 | 全部 13 种事件 | `attention.py:141-225` |
| 0.3s 平滑过渡 | transition_duration | `attention.py:121,228-259` |
| 多脑协调 | emotion_alert + logic_conflict | `turn_manager.py:226-245` |
| DULL 态一定回复 | should_exit 对 DULL 返回 False | `attention.py:311-313` |
| 疲劳因子 | 50 turns → 1.5× | `attention.py:88-90,266-267` |
| 三态分级衰减 | 0.001/0.002/0.0005 | `attention.py:79-82,262-263` |
| 配置外化 | config.yaml 完整段 | `config.yaml:159-202` |
| System Prompt 注入 | 三态不同提示 | `loop.py:286-302` |
| 行为参数调制 | 仅 prompt 提示（软实现） | `loop.py:295-297` ⚠️ |
| Recall→注意力回调 | MEMORY_STRONG_HIT/MISS | `loop.py:847-865` |
| RaceTracker→注意力 | MILD/SEVERE | `adapter.py:188-198` |
| 无聊联动 | 状态感知 tick 间隔 | `boredom.py:128-155` |
| 兴趣联动 | DULL mood_modifier | `interest.py:216-252` |
| ProactiveSystem | DULL 禁止, DRIFTING ×0.3 | `proactive.py:120-164` |
| EmotionEngine→alert | Δvalence>0.5 | `emotion.py:324-344` |
| EventBus 延迟启动 | lazy init | `turn_manager.py:218-254` |
| per-segment 惩罚 | 三态不同 | `turn_manager.py:307-315` |
| QQ Bot 独立 AttentionModel | per session | `adapter.py:308` |
| 初始状态 FOCUSED(0.9) | config 配置 | `config.yaml:161` |

**唯一可改进点**: FR-10 行为参数调制（max 段数/recall 概率）仅通过 system prompt 文本提示，无代码级硬约束。

---

### Spec 003: 记忆联锁 + 幂律遗忘

**FR 覆盖**: 15/15 ✅
**测试**: 7 new + 28 existing

| FR | 描述 | 证据 |
|----|------|------|
| Schema 迁移 (4 列) | access_count/last_access/decay_curve/created_at_epoch | `memory.py:191-228` |
| RecallChainConfig | 两套预设 (主脑/子Session) | `types.py:293-316` |
| search_chained() | 4级 fallback 联锁链 | `memory.py:844-893` |
| 全局去重 | 同 key 保留最低 chain_level | `memory.py:657-665` |
| Recall 深刻化 boost | L0=0.50, L1=0.30, L2=0.20, L3=0.15, L4=0.10 | `memory.py:897-970` |
| 自然语言回溯 | 连接词+情绪注解+推演+跨群注解 | `memory.py:777-840` |
| 记忆三级分级 | short/long/deep | `memory.py:972-1076` |
| 双向迁移 | 晋升(5→long,7→deep) + 降级(<3→short) | `memory.py:972-1076` |
| short_term 10 条裁剪 | trim | `memory.py:1078-1096` |
| 幂律衰减 S/(1+β×t^α) | β=0.01/0.001, α=0.5 | `memory.py:906-934` |
| 衰减→boost 顺序 | 先衰减后 boost→cap 10.0 | `memory.py:947-968` |
| 滞后带防抖 | salience∈[3,5) 不迁移 | `memory.py:1019` |
| 配置开关 | decay.enabled | `config.yaml:131` |
| created_at_epoch 存量回填 | UPDATE unixepoch | `memory.py:224-227` |
| loop.py 集成 | search_chained + 注意力回调 | `loop.py:831-865` |

**结论**: 零遗漏，完整。

---

### Spec 005: 复合情绪 + 防御机制

**FR 覆盖**: 14/18 (4 个遗漏)
**测试**: 28 (compound) + 10 (defense) = 38

#### 已实现的 FR

| FR | 描述 | 证据 |
|----|------|------|
| SC-01 | 交互矩阵生成复合情绪 (12 复合维度) | `emotion.py:50-98,231-245` |
| SC-02 | 复合衰减 (半衰期 = 构成维均值 × ratio) | `emotion.py:374-389,248-261` |
| SC-03 | 复合跨脑传染 (22 维全部传播) | `emotion.py:281-289` |
| SC-04 | compound_alert 发布 (|Δcompound| > 0.4) | `emotion.py:292-322` |
| SC-05 | DENIAL 路径不写 correction | `defense.py:168-176` |
| SC-06 | RATIONALIZE 路径 correction 含解释 | `defense.py:177-185` |
| SC-07 | PROJECT 路径情绪偏移 | `defense.py:187-194` |
| SC-08 | 基线概率公式 (1.0 - impulsiveness) | `defense.py:80` |
| SC-09 | 条件修饰叠加 | `defense.py:83-91` |
| SC-10 | 沉默累积器联动 | `defense.py:175/183/193` |
| SC-12 | 配置开关 | `config.yaml:77-94` |
| SC-13 | 新增测试 ≥10 | 38 tests ✅ |
| SC-14 | 脆弱感触发 (compound ≥ 0.7) | `emotion.py:350-372` |
| SC-16 | 脆弱→防御骤降 (×0.3) | `defense.py:92-93` |

#### 遗漏的 FR

| FR | 描述 | 状态 | 详情 |
|----|------|:---:|------|
| SC-15 | 脆弱感关系安全门 (仅 friend+) | ❌ | `_check_vulnerability()` 未检查关系阶段 |
| — | QQ Bot DefenseEngine 集成 | ❌ | `adapter.py` 完全跳过防御判定 |
| — | QQ Bot 脆弱感集成 | ❌ | `adapter.py` 无脆弱检测和注入 |
| — | compound_alert 事件无消费者 | ❌ | 注意力状态机未订阅此事件 |

---

### Spec 006: 元认知深度

**FR 覆盖**: 12/14 (2 个遗漏)
**测试**: 29

#### 已实现的 FR

| FR | 描述 | 证据 |
|----|------|------|
| SC-01 | 定期触发 (每 N 轮) | `metacognition.py:89-91` |
| SC-02 | 异常触发 — 审查连判 ≥3 | `metacognition.py:93-105` |
| SC-03 | 异常触发 — 防御连发 ≥2 | `metacognition.py:108-113` |
| SC-05 | 参数调节 — 审查阈值偏移 | `types.py:521-524` |
| SC-06 | confidence < 0.6 只写不调 | `types.py:500-501` |
| SC-07 | 参数覆盖 N 轮后过期 | `types.py:518-519` |
| SC-08 | inner_thoughts 模式切换 | `loop.py:306-321` |
| SC-09 | 自我批评关键词触发 | `metacognition.py:120-128` |
| SC-10 | 复合情绪趋势→上下文 | `emotion.py:476-500→metacognition.py:171-176` |
| SC-13 | 新增测试 ≥10 | 29 tests ✅ |
| — | 情绪冲击触发 | `metacognition.py:116-117` |

#### 遗漏的 FR

| FR | 描述 | 状态 | 详情 |
|----|------|:---:|------|
| SC-04 | insight_text 注入 system prompt | ❌ | 仅写入 memory，未注入 `_init_messages` |
| SC-11 | 防御意识历史→上下文 | ⚠️ | `_build_defense_summary()` 为 stub |

---

### Spec 007: 具身感知

**FR 覆盖**: 13/16 (3 个遗漏)
**测试**: 10 (energy) + 6 (subjective_time) = 16

#### 已实现的 FR

| FR | 描述 | 证据 |
|----|------|------|
| SC-01 | 正常 turn 消耗 0.03 | `energy.py:50-52` |
| SC-02 | 长回复消耗 0.06 | `energy.py:51-52` |
| SC-03 | 情绪冲击消耗 0.10 | `energy.py:53-54` |
| SC-04 | PROJECT 防御解脱 +0.02 | `energy.py:60` |
| SC-05 | DENIAL 防御内耗 -0.02 | `energy.py:58` |
| SC-06 | 高分位恢复 +0.02/min | `energy.py:73-74` |
| SC-07 | 低分位恢复 +0.005/min | `energy.py:77-78` |
| SC-08 | Exit 阈值 <0.15 | `energy.py:94, loop.py:483` |
| SC-09 | FOCUSED → speed_factor=0.3 | `subjective_time.py:64-65` |
| SC-10 | DULL → speed_factor=2.0 | `subjective_time.py:68-69` |
| SC-13 | 记忆标记写入 | `turn_manager.py:1318-1326` |
| SC-16 | 新增测试 ≥8 | 16 tests ✅ |

#### 遗漏的 FR

| FR | 描述 | 状态 | 详情 |
|----|------|:---:|------|
| SC-11 | 情绪调制 (joy/sadness) | ⚠️ | 逻辑存在但 `boredom.tick` 调用时未传 `emotion_state` |
| SC-12 | 兴趣调制 | ⚠️ | 逻辑存在但 `boredom.tick` 调用时未传 `interest_match` |
| SC-14 | 记忆回溯注解 (含 "dragging") | ⚠️ | `_time_annotate` 仅处理 "immersed" |

---

### Spec 008: 社交与关系

**FR 覆盖**: 20/20 (核心算法) — 但集成链路断裂严重
**测试**: 22 (relationship) + 12 (group_dynamics) + 9 (patterns) = 43

#### 已实现的核心算法

| FR | 描述 | 证据 |
|----|------|------|
| 4维关系向量 | trust/closeness/respect/familiarity | `types.py:587-596` |
| 10条增长规则 | recall/对话/情感/自我暴露/话题/纠正/记忆 | `relationship.py:163-194` |
| 衰减规则 | 4维 0.001-0.003/day | `relationship.py:208-218` |
| 4阶段判定 | stranger/acquaintance/friend/close_friend | `relationship.py:220-237` |
| 人格调制系数 | 4阶段 × 4参数 | `relationship.py:78-93` |
| 群角色统计 | GroupRoleMetrics | `group_dynamics.py:49-99` |
| 群氛围快照 | GroupAtmosphere 持久化 | `group_dynamics.py:103-166` |
| 跨群社交注解 | `_format_recall_result` | `memory.py:816-838` |
| 问候检测 | ≥3次相同 | `patterns.py:101-135` |
| 时间规律 | 同 bucket >60% | `patterns.py:137-168` |
| 话题循环 | 同 topic ≥3次 | `patterns.py:171-201` |
| 内部梗 | inner_thoughts 关键词+重复 | `patterns.py:203-238` |
| 中间态持久化 | _pending→patterns | `patterns.py:243-324` |
| 模式系统注入 | `_init_messages` | `loop.py:347-351` |

#### 集成断裂 (P0)

| 断裂点 | 位置 | 详情 |
|--------|------|------|
| PersonalityEngine 调制未调用 | `turn_manager.py` 未调 | apply_relationship_modulation 定义但未连接 |
| DefenseEngine 调制参数未传 | `turn_manager.py:664` | relationship_modulation 参数未传入 |
| 情感共鸣链路断裂 | `turn_manager.py:831` | valence 参数未传入 update() |
| QQ Bot 完全未集成 | `adapter.py` | 无 RelationshipEngine/PatternDetector |

---

### Spec 009: 认知增强

**FR 覆盖**: 21/21 (核心算法完整)
**测试**: 10 (intuition) + 12 (creativity) + 12 (humor) + 19 (moral) = 53

#### 已实现

| 模块 | 关键 FR | 证据 |
|------|---------|------|
| IntuitionEngine L1 | ≥5 hits + salience≥7 → 直接回复 | `intuition.py:84-110` |
| IntuitionEngine L2 | Flash 单次调用 confidence>0.7 | `loop.py:504-547` |
| IntuitionEngine L3 | 完整 ReAct 降级兜底 | `intuition.py:80` |
| 直觉状态调制 | FOCUSED ×1.5, DULL ×0.5, 低精力 ×1.3 | `intuition.py:120-127` |
| CreativityEngine 触发 | playfulness>0.5 或开放问题 | `creativity.py:53-67` |
| CreativityEngine Path A | Flash LLM 概念发散 | `creativity.py:71-85` |
| CreativityEngine Path B | 联锁放大 (extended chain_config) | `creativity.py:89-108` |
| CreativityEngine 合并注入 | system prompt | `creativity.py:112-127` |
| HumorDetector 预期违背 | 反问句式 | `humor.py:81-90` |
| HumorDetector 双关语 | 歧义词词典 | `humor.py:94-104` |
| HumorDetector 关系安全门 | stranger/acquaintance 不触发 | `humor.py:61-63` |
| MoralConflict 三类型检测 | 诚实vs保护/忠诚/自我vs他人 | `moral.py:75-123` |
| Pro/Con 双脑评估 | LogicBrain + EmotionBrain | `brain.py:521-549,779-806` |
| deadlock/escalation | |diff|<0.2 / >0.4 | `moral.py:144-145` |
| 道德归档 + subconscious | self/moral + deadlock→subconscious | `turn_manager.py:799-823` |

#### 遗漏

| 问题 | 详情 |
|------|------|
| metacognition moral_escalation 标记未消费 | `metacognition.py:check_triggers()` 未检查此标记 |
| QQ Bot 全部四个系统未集成 | `adapter.py` 无 Intuition/Creativity/Humor/Moral |

---

### Spec 010: 价值体系 + 自我叙事

**FR 覆盖**: 8/13 (5 个遗漏/部分)
**测试**: 10 (values) + 7 (narrative) = 17

#### 已实现

| FR | 描述 | 证据 |
|----|------|------|
| SC-01 | 三层树 (3 美德 × 9 子价值观) | `values.py:18-43` |
| SC-02 | 诚实话伤关系→honesty↓ care↑ | `values.py:82-83` |
| SC-03 | 元认知发现防御→self_honesty↑ | `turn_manager.py:766-767` |
| SC-04 | 价值观→审查阈值 (honesty factor) | `review.py:362-364` |
| SC-05 | 价值观→防御概率 (self_honesty) | `defense.py:98-99` |
| SC-07 | 定期叙事生成 | `turn_manager.py:770-781, brain.py:557-585` |
| SC-09 | 脆弱暴露→叙事事件 | `turn_manager.py:403-408` |
| SC-10 | 叙事注入 system prompt | `loop.py:323-332` |

#### 遗漏

| FR | 描述 | 状态 | 详情 |
|----|------|:---:|------|
| SC-06 | 价值观→道德困境 (moral_bias) | ❌ | moral.py 无 ValueEngine 引用 |
| SC-08 | 关系升级→叙事事件 | ⚠️ | 钩子就绪但未连接触发 |
| SC-11 | 叙事参与记忆检索 | ⚠️ | self/narrative/* 不可达子 Session recall |
| — | NarrativeEngine 时间线存储 | ⚠️ | 仅 latest，无 timeline/{date} |
| — | build_context 参数未传递 | ❌ | value_state/narrative_text 未传入 |

---

### Spec 011: 沉默语义 + 动机系统

**FR 覆盖**: 14/18 (4 个遗漏)
**测试**: 44 (silence) + 48 (motivation) = 92

#### 已实现

| FR | 描述 | 证据 |
|----|------|------|
| SC-01 | 5 类沉默判定 | `silence.py:45-75` |
| SC-02 | TACIT 不算沉默 (increment=0) | `silence.py:36-39` |
| SC-04 | OVERLOAD→recovery_boost=2.0 | `energy.py:83-90, turn_manager.py:1255-1256` |
| SC-05 | 沉默历史持久化 (self/silences) | `turn_manager.py:1257-1263` |
| SC-06 | Drive — socialize | `motivation.py:43-53` |
| SC-07 | Drive — rest | `motivation.py:48` |
| SC-08 | Drive — seek_close | `motivation.py:48-51` |
| SC-09 | Value — explore | `motivation.py:55-65` |
| SC-10 | Value — check_on | `motivation.py:55-65` |
| SC-11 | 冲突解决 — 体力优先 | `motivation.py:67-77` |
| SC-12 | Loneliness 依赖亲近关系 | `loneliness.py:38-40` |
| SC-13 | Loneliness 主观时钟 | `loneliness.py:42` |
| SC-14 | 动机注入 system prompt | `motivation.py:82-94, loop.py:365-369` |
| SC-16 | ProactiveSystem 读动机 | `proactive.py:142-153` |

#### 遗漏

| FR | 描述 | 状态 | 详情 |
|----|------|:---:|------|
| SC-03 | ANGRY→resentment +0.05 | ⚠️ | 判定正确但情绪 delta 未应用 |
| — | SILENCE 决策路径不可达 | ❌ | `_async_review_and_decide` 跳过 SILENCE |
| — | build_context 参数未传递 | ❌ | silence_pattern/active_motivations 未传入 |
| — | 3 文件缺少 `from __future__ import annotations` | ⚠️ | 编码约定未遵循 |

---

## 六、跨 Spec 共享问题汇总

### 问题 1: `turn_manager.py` build_context 参数桥接缺失

影响 Spec: **006, 008, 009, 010, 011** (5 个)

`metacognition.py:build_context()` 签名已扩展但 `turn_manager.py:738` 调用未传入。

### 问题 2: QQ Bot `adapter.py` 未集成 Spec 005/008/009/011

影响 Spec: **005, 008, 009, 011** (4 个)

CLI 管线 (`turn_manager.py`) 已正确集成，但 QQ Bot 管线 (`adapter.py`) 严重滞后。

### 问题 3: 多个 Stub 方法

影响 Spec: **006, 010** (2 个)

`_build_defense_summary()`, `_build_memory_state()`, `_build_turn_summaries()`, NarrativeEngine 时间线存储不完整。

### 问题 4: 钩子已定义但未连接

影响 Spec: **005, 007, 008, 009, 010** (5 个)

多个已实现的 API (apply_relationship_modulation, compound_alert listener, moral_escalation_pending, ValueEngine moral_bias) 处于"已定义但调用方未连接"状态。

---

## 七、修复优先级路线图

### 阶段 A: P0 修复 (预计 ~30 行改动, 3 文件)

| 序号 | 修复 | 文件 | 行数 |
|:---:|------|------|:---:|
| A1 | SILENCE 决策路径 → `_silent_archive()` | `turn_manager.py` | ~5 |
| A2 | PersonalityEngine 关系调制连接 | `turn_manager.py` | ~3 |
| A3 | DefenseEngine 关系调制参数传入 | `turn_manager.py` | ~3 |
| A4 | insight_text → system prompt 注入 | `loop.py` + `turn_manager.py` | ~10 |
| A5 | ValueEngine → MoralConflictDetector 集成 | `turn_manager.py` | ~5 |
| A6 | build_context 扩展参数传入 (7 个参数) | `turn_manager.py` | ~7 |

### 阶段 B: QQ Bot 集成 (预计 ~80 行改动, 1 文件)

| 序号 | 修复 | 文件 | 行数 |
|:---:|------|------|:---:|
| B1 | DefenseEngine + 脆弱感集成 | `adapter.py` | ~20 |
| B2 | RelationshipEngine + PatternDetector 集成 | `adapter.py` | ~20 |
| B3 | Intuition/Creativity/Humor/Moral 集成 | `adapter.py` | ~30 |
| B4 | SilenceClassifier/MotivationEngine/LonelinessDetector 集成 | `adapter.py` | ~15 |

### 阶段 C: P2 局部修复 (预计 ~40 行改动, 6 文件)

| 序号 | 修复 | 文件 | 行数 |
|:---:|------|------|:---:|
| C1 | 3 个 context stub 方法实现 | `turn_manager.py` | ~15 |
| C2 | SubjectiveClock 情绪/兴趣参数传入 | `boredom.py` | ~5 |
| C3 | 主观时间用于 boredom 计算 | `boredom.py` | ~3 |
| C4 | 情感共鸣 valence 参数传入 | `turn_manager.py` | ~3 |
| C5 | metacognition moral_escalation 触发 | `metacognition.py` | ~5 |
| C6 | 脆弱感关系安全门 | `emotion.py` | ~5 |
| C7 | ANGRY resentment delta | `turn_manager.py` | ~2 |
| C8 | `from __future__ import annotations` | silence.py, motivation.py, loneliness.py | ~3 |

---

## 八、统计数据

| 维度 | 数值 |
|------|:---:|
| 审查系统数 | 9 |
| 总 FR 数 | ~148 |
| 完整实现 | ~137 (93%) |
| 有遗漏 | ~11 (7%) |
| P0 级问题 | 6 |
| P1 级问题 | 1 |
| P2 级问题 | 8 |
| 跨 Spec 共享问题 | 4 |
| 新系统文件 | 18 (全部就位) |
| 总测试数 | 466 (1 预存 flaky) |
| 预估修复总改动量 | ~150 行 |
| 核心引擎代码质量 | 优秀 (算法完整、测试充分) |
| 主要短板 | 管线集成层 (QQ Bot 缺失、参数桥接缺失) |
