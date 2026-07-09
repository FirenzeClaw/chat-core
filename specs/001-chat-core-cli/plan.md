# Implementation Plan: Chat Core CLI

**Feature**: `001-chat-core-cli`
**Source Spec**: [spec.md](./spec.md)
**Source Design**: [chat-core-design.md](../../chat-core-design.md)
**Created**: 2026-07-09
**Status**: Implemented ✅
**Last Updated**: 2026-07-09

---

## Technical Context

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Language | Python 3.12+ | asyncio 成熟度、aiosqlite 生态、prompt_toolkit 支持 |
| LLM SDK | openai >= 1.0 (AsyncOpenAI) | 原生 function calling 支持，兼容 DeepSeek / OpenAI / 任意兼容 API |
| Database | SQLite + FTS5 via aiosqlite | 零配置、全文检索、异步支持、单文件部署 |
| CLI Framework | prompt_toolkit + rich | 终端交互体验成熟、支持 ANSI 格式化 |
| Web Search | duckduckgo-search | 无需 API key、免费、Python 原生 |
| Config | YAML + 环境变量 | 人类可读、分层覆盖 |
| Async Runtime | asyncio | 原生 Python、四脑并发模型天然适配 |
| Testing | pytest + pytest-asyncio | Python 标准测试框架 |
| Type Checking | mypy (strict mode) | Python 3.12+ 类型注解全覆盖 |

### Performance Target

| Metric | Target | Source |
|--------|--------|--------|
| First substantive reply character (excl. wait pauses) | < 3 seconds | SC-01 |
| Memory recall query latency | < 1 second | US2 acceptance |
| 30-minute continuous conversation stability | 100% no crash/hang | SC-07 |

### External Dependencies

| Dependency | Purpose | Fallback |
|------------|---------|----------|
| DeepSeek API (or compatible) | 所有脑的 LLM 推理 | 任何 OpenAI-compatible endpoint |
| DuckDuckGo | 网页搜索 | 无搜索功能（降级为仅本地记忆） |
| Internet access | web_fetch | 无网页抓取（降级提示） |

---

## Constitution Check

**Status**: N/A — No `.specify/memory/constitution.md` exists. Project governance defaults to design document (`chat-core-design.md`) as authoritative reference.

Key architectural constraints from design doc:
- **四脑分离**：逻辑主脑、情感主脑、子Session、行为脑各司其职，主脑不发言
- **主脑不发言原则**：子Session 是唯一的嘴
- **无外部编排依赖**：不依赖 Kimi Code CLI、kimi-debug-tunnel、任何 MCP 服务器
- **本地优先**：SQLite 本地存储，单用户
- **asyncio 并发模型**：子Session 串行、主脑之间可并行、行为脑 Semaphore(2)

---

## Phase Breakdown

### Phase 0: Project Skeleton (Day 1-2)

**Goal**: Runnable CLI that can complete a single turn of conversation via LLM.

**Files to create**:
```
chat-core/
├── cli.py                    # CLI 入口 (prompt_toolkit)
├── config.py                 # 配置加载 (YAML + env)
├── config.yaml               # 默认配置
├── prompts/
│   ├── persona.yaml          # 人设定义
│   ├── rules.yaml            # 行为规范
│   └── tools.yaml            # 工具说明模板
├── core/
│   ├── loop.py               # ReAct Loop 引擎
│   ├── tools.py              # ToolRegistry + 子Session 工具
│   ├── prompt_engine.py      # Prompt 编译
│   └── provider.py           # LLM Provider (AsyncOpenAI)
├── data/                     # 运行时数据目录
└── tests/
    ├── conftest.py
    ├── test_loop.py
    ├── test_tools.py
    └── test_prompt_engine.py
```

**Key deliverables**:
- [ ] `config.yaml` 加载 + 环境变量覆盖
- [ ] `ModelProvider` 抽象（支持 DeepSeek / OpenAI / 自定义 endpoint）
- [ ] `ToolRegistry` + 子Session 基础工具：`send_reply`, `wait`, `done`, `inner_thoughts`, `recall`
- [ ] `ReActLoop` 引擎（子Session think-act 循环，支持 function calling）
- [ ] `PromptEngine` — 编译 persona/rules/tools 三层为 system prompt
- [ ] `cli.py` — prompt_toolkit 交互式 REPL

**Tests**:
- Provider: mock API response, 验证 streaming/non-streaming
- Tools: 各工具注册、参数校验、错误处理
- Loop: mock LLM → 验证 think-act 循环终止条件
- PromptEngine: 验证三层编译输出格式

**Acceptance**: 启动 CLI → 输入消息 → LLM 回复显示在终端。

---

### Phase 1: Dual Brain + Memory (Day 3-7)

**Goal**: AI has memory and two review brains that check its own output.

**Files to create/modify**:
```
chat-core/
├── core/
│   ├── brain.py              # NEW: 四脑基类 + 逻辑/情感/行为脑
│   ├── turn_manager.py       # NEW: Turn 协调 (流程编排)
├── systems/
│   ├── memory.py             # NEW: MemoryStore (SQLite FTS5)
├── tests/
│   ├── test_memory.py
│   ├── test_brain.py
│   └── test_turn_manager.py
```

**Key deliverables**:
- [ ] `MemoryStore` — SQLite + FTS5，支持 CRUD + 全文检索 + 命名空间隔离
  - 命名空间：`user/`, `self/`, `short_term/`, `subconscious/`, `global/`
  - 检索管线：FTS5 → LIKE 降级 → LLM 精排 → 扩散激活
- [ ] 逻辑主脑 — recall + memory_save + memory_link + inject_to_sub + write_correction
- [ ] 情感主脑 — recall + memory_tag + inject_to_sub + write_correction
- [ ] 行为脑 — search + recall + web_fetch（临时创建，用完销毁）
- [ ] `TurnManager` — 编排完整 turn 流程：
  - 双脑并行 recall → 各自注入 → 子Session ReAct → 双脑审查 → 权重决策
- [ ] 权重决策系统：corrected_weight = logic × 0.6 + emotion × 0.4，阈值 0.5
- [ ] 短期记忆：近 10 条，TTL 自动衰减
- [ ] 潜意识区：corrections / nudges / deferred_actions

**Tests**:
- MemoryStore: CRUD、FTS5 搜索、命名空间隔离、TTL 衰减
- LogicBrain: recall → memory_save → inject_to_sub 完整调用链
- EmotionBrain: recall → memory_tag 标签追加
- TurnManager: mock 双脑 → 验证 turn 流程状态机
- WeightedDecision: 边界值 (0.49/0.51)、拧巴状态 (logic>0.8, emotion<0.3)

**Acceptance**: AI 记住用户说过的事实 → 下次对话引用 → 说错时自动纠正。

---

### Phase 2: Full Personality (Day 8-13)

**Goal**: AI has emotions, personality weights, boredom, and proactive behavior.

**Files to create/modify**:
```
chat-core/
├── systems/
│   ├── emotion.py            # NEW: EmotionEngine (10维×3脑)
│   ├── personality.py        # NEW: Personality (8维权重)
│   ├── boredom.py            # NEW: BoredomDetector
│   ├── interest.py           # NEW: InterestModel (FuzzyParam)
│   └── attention.py          # NEW: AttentionModel
├── tests/
│   ├── test_emotion.py
│   ├── test_personality.py
│   ├── test_boredom.py
│   └── test_interest.py
```

**Key deliverables**:
- [ ] `EmotionEngine` — 10 维 × 3 脑，独立衰减半衰期 (30s ~ 3600s)，脑间传染
- [ ] `PersonalityEngine` — 8 维权重，映射到运行时参数：
  - playfulness → temperature 调制
  - empathy → 共情模式
  - creativity → 回复多样性
  - impulsiveness → 纠正魄力
  - sociability → 主动发起频率
- [ ] `BoredomDetector` — idle 状态下指数衰减，触发阈值 0.30
- [ ] `InterestModel` — 话题提及计数，FuzzyParam 模糊化采样
- [ ] `AttentionModel` — focus + dominance 双参数，每秒衰减
- [ ] 沉默累积器 — 同类型错误 × 0.05 递增，FuzzyParam 非机械触发
- [ ] 拧巴记录 — TurnManager 写入 `self/feelings/twisted`
- [ ] 主动发起 — 无聊 + 兴趣联动 → 行为脑搜索 → 子Session 发言
- [ ] 子Session 退役机制 — 上下文 > 85% token 时提取摘要 → 新 session 接续

**Tests**:
- EmotionEngine: tick 衰减、传染系数、维度边界
- Personality: 各权重对 behavior 参数的调制验证
- BoredomDetector: 指数衰减曲线、触发/不触发边界
- InterestModel: FuzzyParam 多次采样统计分布
- SilentAccumulator: 递增逻辑、多次采样
- ProactiveTrigger: 无聊 + 兴趣联动场景

**Acceptance**: 对话结束后 AI 情绪自然衰减 → 无聊触发主动发言 → 话题自然地关联历史兴趣。

---

### Phase 3: Polish & Ship (Day 14-18)

**Goal**: Production-quality CLI with visual polish, multi-modal support, and comprehensive tests.

**Files to create/modify**:
```
chat-core/
├── cli.py                    # MODIFY: rich TUI 美化
├── core/
│   ├── history.py            # NEW: 对话历史持久化
├── systems/
│   ├── multimodal.py         # NEW: 多模态降级链
├── tests/
│   ├── test_history.py
│   ├── test_multimodal.py
│   └── test_cli.py           # NEW: CLI 集成测试
```

**Key deliverables**:
- [ ] Rich TUI 美化 — 用户/AI 消息分色、打字动画、情绪状态栏
- [ ] 对话历史 JSONL 持久化 — `data/history/{user_id}.jsonl`
- [ ] 多模态降级链 — 图片检测 → vision model → 降级 Provider → 文本描述
- [ ] Ctrl+C 两级中断 — 第一次请求停止，第二次 2s 内强制退出
- [ ] 工具安全 — send_reply 内容过滤、search 限速、web_fetch 超时/大小限制
- [ ] 行为脑并发池 — Semaphore(2) + Queue
- [ ] 测试覆盖 ≥ 80%

**Tests**:
- History: JSONL 追加、读取、与 MemoryStore 一致性
- Multimodal: vision detect、降级链 fallback、all-unavailable 提示
- CLI: 完整 turn 集成测试（mock LLM）
- Safety: 内容过滤边界、限速 token bucket

**Acceptance**: 产品级 CLI，稳定运行 30 分钟对话不崩溃，正常退出保存所有状态。

---

## Dependency Graph

```
Phase 0 (Skeleton)
├── cli.py ← prompt_toolkit + rich
├── config.py ← YAML + env
├── core/provider.py ← openai SDK
├── core/tools.py ← ToolRegistry
├── core/loop.py ← ReActLoop
├── core/prompt_engine.py ← yaml 编译
└── prompts/ (persona + rules + tools)

Phase 1 (Brain + Memory)
├── systems/memory.py ← aiosqlite + FTS5
├── core/brain.py ← ModelProvider ← 继承自 Phase 0
│   ├── LogicBrain
│   ├── EmotionBrain
│   ├── SubSession
│   └── ActionBrain
├── core/turn_manager.py ← 所有 brains + memory
└── 依赖: Phase 0

Phase 2 (Personality)
├── systems/emotion.py ← asyncio tick
├── systems/personality.py ← 8 维权重
├── systems/boredom.py ← EmotionEngine + InterestModel
├── systems/interest.py ← FuzzyParam
├── systems/attention.py
└── 依赖: Phase 1 (需要 MemoryStore + TurnManager)

Phase 3 (Polish)
├── core/history.py ← MemoryStore
├── systems/multimodal.py ← ModelProvider 降级链
├── cli.py (美化)
└── 依赖: Phase 2
```

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM function calling 不稳定（跳过 inner_thoughts） | 中 | TurnManager 检测降级模式（见设计 §11.1） |
| 子Session ReAct 循环不终止 | 高 | max_iter=5 + sadness>0.8 + focus<0.15 + boredom>0.7 多条件终止 |
| SQLite FTS5 中文分词效果差 | 中 | LIKE 降级 + LLM 精排兜底 |
| 上下文 token 膨胀导致 LLM 调用失败 | 高 | 三级压缩 + 退役机制（见设计 §11.7） |
| 纠正递归（纠正又触发纠正） | 中 | 纠正 session 为单次模式，max_iter=2，不触发新纠正循环 |
| 并发竞态（双脑同时写记忆） | 低 | SQLite WAL 模式 + namespace 隔离 |
| 行为脑外部 API 超时 | 低 | 10s 超时 + 写 error 到短期记忆而非崩溃 |

---

## Implementation Notes (Post-Implementation)

### Deviations from Plan

| Item | Plan | Actual | Reason |
|------|------|--------|--------|
| `tool_choice` | `auto` (默认) | `auto` (保持) | DeepSeek API 不支持 `required`，改为强 prompt 约束 |
| inner_thoughts + done | 两步协议 (inner_thoughts → done) | inner_thoughts 即结束，done 备用 | 模型偶有漏调 done，合并为一 |
| jieba 分词 | 未在 plan 中 | 集成 jieba，添加到依赖 | FTS5 unicode61 中文分词精度不足，jieba 显著提升召回率 |
| 主脑跨 turn 上下文 | 原为每次全新调用 | 改为累积 message history，700K 上下文 | 用户要求主脑保持上下文 |
| 情感脑模型 | deepseek-v4-flash | deepseek-v4-pro | 与逻辑脑一致，深度思考 |
| 子Session reasoning_effort | max | max (保持) | 与 tool_choice=auto 兼容 |
| .env 自动加载 | 未在 plan 中 | 实现 `_load_dotenv()` | 用户体验优化 |

### Risk Resolution

| Risk | Status | Notes |
|------|:---:|------|
| LLM function calling 不稳定 | ✅ Resolved | inner_thoughts 即结束 + 纯文本降级路径 + 一次重试 |
| 子Session 循环不终止 | ✅ Resolved | 多条件终止，84 tests 覆盖 |
| FTS5 中文分词 | ✅ Resolved | jieba 集成，中文召回率从 ~0% 提升到 > 90% |
| 上下文 token 膨胀 | ✅ Resolved | 三级压缩 + 退役，主脑 700K / 子 500K 上限 |
| 纠正递归 | ✅ Resolved | `_in_correction` flag guard |
| 并发竞态 | ✅ Resolved | WAL + namespace，84 tests 无竞态 |
