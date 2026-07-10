# Changelog

## [Unreleased] — 2026-07-10 (Phase 1+2: 注意力 + 幂律 + 复合情绪 + 具身感知)

### Added

- **注意力状态机完整实施** (Phase 1): 三态模型 (FOCUSED/DRIFTING/DULL) + `apply_event()` 13事件转移矩阵 + 0.3s 平滑过渡 + 状态感知分级衰减 (0.001/0.002/0.0005/s) + 疲劳因子 (50 turns 满疲劳, 1.5× 加速) + DULL 态不沉默
- **注意力系统集成**: `emotion.py` Δvalence 检测→`emotion_alert` 事件发布；`loop.py` focus 状态注入 system prompt + recall→注意力回调 (salience≥7→MEMORY_STRONG_HIT)；`turn_manager.py` 延迟启动事件监听器 (`emotion_alert`+`logic_conflict`) + 发言段数惩罚；`adapter.py` RaceTracker 竞态阈值检测 (2→3 RACE_MILD, 4→5 RACE_SEVERE)
- **§5 昏沉联动**: `boredom.py` 状态感知 tick 间隔 (30/20/15s) + DULL 触发阈值 0.40；`interest.py` DULL 态情绪调制兴趣触发 (×0.5~×2.0)；`proactive.py` `_should_initiate()` DULL→禁发, DRIFTING→×0.3
- **配置全外化**: `config.yaml` `systems.attention` 段扩展为 `state_machine` + `drift` + `fatigue` + `boredom_link`，所有参数可调
- **14 新增测试**: `TestAttentionStateMachine` (8), `TestEmotionAttentionLink` (3), `TestAttentionInjection` (3)
- **实施计划文档** (`docs/superpowers/plans/2026-07-10-attention-state-machine.md`): 4 阶段 10 任务详细计划
- **Spec 003 §12 幂律遗忘曲线**: `effective_salience()` 幂律公式 `S/(1+β×t^α)` (standard β=0.01, deep β=0.001)，`_apply_salience_boost()` 先衰减后 boost，`_downgrade_long_to_short()` 降级迁移 (salience<3)，`_unmark_deep_memory()` deep 回退 (salience<5)，`created_at_epoch` REAL 列，`decay.enabled` 配置开关
- **7 新增测试**: `TestPowerLawDecay` (4) + `TestBidirectionalMigration` (3)，全量 175 tests
- **幂律遗忘实施计划** (`docs/superpowers/plans/2026-07-10-memory-power-law-decay.md`): 7 任务详细计划
- **Spec 005 复合情绪+防御+脆弱感** (Phase 2): 12 维复合情绪 (INTERACTION_MATRIX 10键19条目 + tick() 七步流) + DefenseEngine 三种防御 (DENIAL/RATIONALIZE/PROJECT) + 脆弱感系统 (极端情绪检测 + self_disclosure ×2.0 + 防御 ×0.3) + 42 新增测试
- **Spec 007 具身感知** (Phase 2): EnergyBar 精力管理 (事件消耗 + idle 恢复 + 防御联动) + SubjectiveClock 主观时钟 (注意力/情绪/兴趣三维调制) + 记忆时间注解 + 15 新增测试
- **Spec 006 元认知深度** (Phase 2): MetacognitionEngine 定期+异常双触发审视 (5种触发)、LogicBrain.metacognition_pass() 单次 LLM 调用 + metacognition_report 工具、MetaParamOverrides 参数覆盖容器 (审查阈值/防御概率/情绪阈值/兴趣/内心戏)、5 子系统消费集成、29 新增测试
- **复合情绪实施计划** (`docs/superpowers/plans/2026-07-10-compound-emotion-defense.md`): 11 任务、4 阶段
- **具身感知实施计划** (`docs/superpowers/plans/2026-07-10-embodied-perception.md`): 9 任务、4 阶段
- **审查异步化** (specs/004-design-alignment): CLI 审查从同步 `await` 改为 `asyncio.create_task` 异步执行，子Session `send_reply` 立即输出不等待审查。QQ Bot 新增完整审查纠正管线 (`_async_review_and_decide`)
- **纠正延迟生效**: `_issue_correction()` 不再立即运行纠正子Session，改为写入 `subconscious/corrections`，下一轮子Session `_init_messages` 自动读取并注入
- **子Session 读潜意识区**: `ReActLoop._inject_subconscious_corrections()` 在 `_init_messages` 后查询 `subconscious/corrections` 并注入 system message
- **纠正子Session 恢复完整能力**: max_iter 2→5，重新开启 inner_thoughts/wait 工具，递归深度计数器 `_correction_depth ≤ 2`
- **双脑权重平等**: `combined = logic × 0.5 + emotion × 0.5`（原 0.6/0.4）
- **双脑持续情绪通知通道**: `emotion_alert` 事件通道，情感主脑检测情绪变化实时通知逻辑主脑
- **注意力状态机设计文档** (`docs/superpowers/specs/2026-07-10-attention-state-machine-design.md`): 三态模型 (FOCUSED/DRIFTING/DULL) + 平滑过渡 + 多脑协调 + 情绪/记忆/竞态/兴趣联动
- **send_reply 工具返回格式优化**: `【QQ消息发送成功】<潜意识>{提示}` 格式，避免 LLM 误解为对话对象发言
- **specs/004-design-alignment**: spec.md (10 FR), plan.md (4 Phase), tasks.md (17 tasks)

### Fixed

- **注意力衰减过快**: `drift_decay_rate 0.01→0.001`，解决跨 turn 100s 后 focus 归零导致 `should_exit_sub` 阻断子Session
- **`_replies` 不重置**: `ReActLoop.run()` 新增 `self._replies.clear()`，修复跨 turn segment 计数虚高
- **send_reply 停顿移除**: 删除 `asyncio.sleep(1-3s)` 内嵌停顿，分段回复更连贯
- **400 错误修复**: `NonStreamResult` + `provider.chat()` + `brain.py` 补全 `reasoning_content` 回传链路
- **自我介绍 prompt 增强**: `rules.yaml` 新增身份询问触发规则，`tools.yaml` 新增分段引导
- **QQ Bot session/去重/token 修复**: 过期 session 清理旧 ReActLoop, deque 替代 set 去重, token 失败短过期重试

### Changed

- `chat-core-design.md`: 审查异步非阻塞、纠正延迟生效、多副脑、双脑权重平等、持续情绪通道
- `config.yaml`: `drift_decay_rate: 0.001`

### Tests

- `tests/test_design_alignment.py` 新建: 25 tests 覆盖审查异步/subconscious注入/递归深度/权重/拧巴记录/异常降级/情绪通道
- 全量回归: 154 tests passed

---

## [Unreleased] — 2026-07-10 (记忆联锁 + Recall 深刻化)

### Added

- **记忆联锁检索** (`MemoryStore.search_chained()`): FTS5 主检索 → 4 级关联链延伸 (显式 links → topic_tags → entity_type → namespace)，每次 recall 自然联想关联记忆
- **自然语言回溯** (`MemoryStore._format_recall_result()`): recall 返回自然语言文本（"【记忆回溯】\n我记得：..."），随机连接词、情绪注解、情绪推演，替换旧 JSON 格式
- **Recall 深刻化**: 每次 recall 命中后 salience 递增 (direct +0.5, links +0.3, tags +0.2, entity +0.15, namespace +0.1)，access_count 自动 +1
- **记忆分级**: 三级记忆 — 短期记忆 (short_term/*, 最多 10 条裁剪) → 长期记忆 (salience≥5 + access_count≥3, 自动迁移到 user/*) → 深刻记忆 (salience≥7, decay_curve='deep')
- **主脑/子Session recall 权限隔离**: 主脑全量访问，子Session 仅可读 user/{uid}/* + short_term/*
- **联锁配置**: `RecallChainConfig` 包含 top_n、extensions、max_per_level、namespace_prefix；`LOGIC_BRAIN_CHAIN_CONFIG` (5+3+2+2+1+0) 和 `SUB_SESSION_CHAIN_CONFIG` (3+2+1+0) 预置常量
- **ChainedMemory 数据类型**: 含 entry、chain_level、chain_parent_key、relevance_score 元数据
- **Schema 迁移**: access_count、last_access、decay_curve 列 (idempotent ALTER TABLE)

### Changed

- `LogicBrain._execute_recall()`: 切换到 `search_chained()` 联锁检索
- `LogicBrain.think_inject()`: 使用 `_format_recall_result()` 自然语言回溯注入子 Session
- `register_sub_session_tools()`: 新增 `chain_config: RecallChainConfig` 参数，recall 工具使用联锁检索
- `BotAdapter._get_or_create_sub_session()`: 为每用户构造带 namespace_prefix 的 `RecallChainConfig`

### Tests

- 新增 16 测试 (Spec 003 WP2): 联锁检索/延伸链/去重/断链跳过/自然语言输出/深刻化/分级迁移/搜索/权限隔离
- 全量回归: 124 tests passed

---

## [Unreleased] — 2026-07-09 (QQ Bot 集成)

### Added

- **QQ Bot 接入**: 新增 `chat-core-qq` 命令，chat-core 作为 QQ 机器人运行
- **QQ WebSocket 协议层** (`qq/protocol.py`): 状态机 (hello→identify→running)、Token 管理、消息去重、REST API 发送（含错误码分类重试）
- **全局双主脑 + 多子 Session** (`qq/adapter.py`): LogicBrain + EmotionBrain 全局共享，每对话者独立 ReActLoop
- **竞态追踪** (`qq/race_tracker.py`): 活跃子 Session 计数 → severity (low/medium/high) → EmotionEngine 烦躁加速
- **潜意识注入调节** (`qq/subconscious.py`): 按竞态程度截断注入上下文（顾此薄彼效应）
- **双脑异步注入**: 子 Session 立即启动回复，双脑 recall+inject 后台异步运行，首段回复 2-5s
- **用户画像自动构建**: 每 turn 后异步 LLM 提取用户事实 → `user/{uid}/facts`
- **群聊旁听记忆**: 非 @ 消息写入 `user/{uid}/group/{gid}/observations`，不触发回复
- **用户会话管理** (`qq/sessions.py`): TTL 过期，per-user asyncio.Lock 串行
- **消息分段发送**: send_reply 回调直接调 QQ API，LLM 多段回复逐条发送
- **健康检查端点**: `:18090/health`
- QQ Bot 配置节: `config.yaml` → `qq_bot`

### Changed

- `config.yaml`: 新增 `qq_bot` 配置节
- `config.py`: 新增 `qq_config()` 方法
- `pyproject.toml`: 新增 `aiohttp` 依赖、`chat-core-qq` 入口、`chat_core.qq` 包
- `qq_bot.py`: 日志同时输出到 `data/qq_bot.log` 文件

### Tests

- `tests/test_qq_protocol.py` (12 tests): MessageContext/事件解析/去重
- `tests/test_qq_sessions.py` (12 tests): UserSession/SessionManager TTL
- 全量回归: 95 tests passed

---

## [Unreleased] — 2026-07-09 (streaming + memory pipeline session)

### Added

- **流式工具调用**: `ReActLoop._think()` 切到 `stream_chat()`，LLM 推理过程实时可见
- **流式 UI**: `rich.Live` 面板渲染 LLM 输出 + 工具调用状态（💭 推理中 / 🔧 调用工具 / ✅ 完成）
- **T022 检索管线补全**:
  - `_spread_activate()` — 沿 `memory_links` 扩散激活关联记忆 (depth=2)
  - `_cluster_boost()` — 按共享 `topic_tags` 排序提升聚类条目
  - `_rerank_memories()` — LogicBrain LLM 重排 top 5 (1s timeout + 降级保护)
- **Proactive 回调链路**: CLI → TurnManager → ProactiveSystem → 主动子Session ReActLoop，无聊触发主动发言可流式显示
- **纠正子Session 流式回调**: `_run_correction_sub_session()` 注入 `stream_callback`
- **jieba 预加载**: 启动时 `_preload_jieba()` 避免首次 recall 阻塞
- `Message.reasoning_content` 字段 — 存储 DeepSeek 推理链
- `StreamEvent.reasoning_content` 字段 — 流式 DONE 事件携带推理链
- `TurnManager.set_reply_callback()` / `ProactiveSystem` 回调参数

### Fixed

- **DeepSeek reasoning_content 多轮回传**: `stream_chat()` 捕获推理链 → DONE event → `_think()` 存入 Message → `_serialize_messages()` 回传。修复 `_act()` 纯文本覆写时丢失 reasoning_content 导致 400 错误
- **FTS5 列名不匹配**: `value_text` → `value`（对齐主表），含自动迁移 `_migrate_fts_columns()`
- **`get_links()` 双向追踪 bug**: 之前只追踪 to 方向，修复为无条件追加 link + 两端加入 BFS
- **`memory_link` prompt 增强**: `build_logic_brain_prompt()` 新增 `【memory_link 使用指南】` 含 3 种关系示例和触发条件

### Changed

- `brain.py`: `import re` 移到模块顶部
- `_rerank_memories()`: 内联 `import re` 移除

---

## [Unreleased] — 2026-07-09 (audit + integration session)

### Added

- `ToolRegistry.unregister()` 方法，支持工具运行时替换
- `cli.py --direct` 降级模式，跳过四脑管线直接运行 ReActLoop

### Fixed

- **TurnManager 接入 CLI**：CLI 默认使用完整四脑管线（双脑 recall→inject→ReAct→审查→纠正→归档），此前 TurnManager 虽完整实现但从未实例化，导致 21 项 FR 管线未生效
- **FR-23 无聊公式**：从递减改为递增 `boredom = 1 - eval_param × e^(-t/600)`，阈值 0.70/0.90，对齐 spec
- **Brain tool_call 协议**：`think_pre`/`think_inject` 追加 tool result 消息，修复 OpenAI API 400 错误（assistant tool_calls 后缺少 tool 响应）
- **ReActLoop 纯文本兜底**：LLM 输出纯文本时自动合成为 `send_reply` 工具调用记录，保留 `reasoning_effort=max` + `tool_choice=auto`
- **recall 重复注册**：`_enhance_recall` 先 unregister 再注册，解决 TurnManager 子Session 中 recall 工具冲突
- **provider.py reasoning_effort 传递**：允许显式传递 `"off"` 值给 API（之前被条件过滤）

### Changed

- `chat-cli-research.md` 新增"Agent 状态机核心模式"章节，对照 Claude Code / aichat / chat-core 三种实现

---

## [0.1.0] — 2026-07-09

### Added

- 四脑架构：逻辑主脑、情感主脑、子Session（嘴）、行为脑
- ReAct Loop 引擎：think → act → observe 循环，支持 function calling
- 子Session 工具：send_reply、wait、recall、inner_thoughts（调用即结束）、done
- MemoryStore：SQLite + FTS5 全文检索 + jieba 中文分词
- 三层记忆：清醒记忆、短期记忆、潜意识区
- EmotionEngine：10 维情绪 × 3 脑，独立衰减半衰期 + 脑间传染
- PersonalityEngine：8 维人格权重 → 行为参数映射
- BoredomDetector：指数衰减无聊检测 + 主动发起
- InterestModel：话题追踪 + FuzzyParam 模糊化触发
- ReviewSystem：三层错误检测（关键词 → 缓存 → LLM）
- 权重决策：logic × 0.6 + emotion × 0.4，阈值 0.5
- 沉默累积器：FuzzyParam 非机械"忍无可忍"触发
- 拧巴记录：逻辑与情感分歧时按逻辑执行并记录
- 意图提取：regex → 关键词 → LLM fallback
- 行为脑并发池：Semaphore(2) + Token Bucket 限速
- 上下文压缩：三级策略（70%/85% 阈值）
- 子Session 退役机制
- Rich TUI：prompt_toolkit + rich，情绪状态栏、打字动画
- 多模态降级链：图片 → vision model → fallback → 文本描述
- 安全过滤：ContentFilter 内容检测 + 长度截断 + URL 白名单
- 对话历史 JSONL 持久化
- 配置 schema 校验 + .env 自动加载
- 84 单元测试 + 19 Spec E2E 测试
