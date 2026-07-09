# Research & Decisions: Chat Core CLI

**Feature**: `001-chat-core-cli`
**Created**: 2026-07-09

---

## 1. LLM Provider Selection

**Decision**: OpenAI-compatible API via `openai` SDK >= 1.0 with `AsyncOpenAI`

**Rationale**:
- DeepSeek API is OpenAI-compatible → single client for all providers
- Native function calling support → no manual tool-use prompt engineering
- Async by default → fits asyncio architecture
- `reasoning_effort` parameter for thinking-level control

**Alternatives considered**:
- `langchain`: 过重，引入不必要的抽象层
- Raw `httpx` + manual tool parsing: 重复造轮子
- `litellm`: 多了一层路由，chat-core 只需单 provider

---

## 2. Database Choice

**Decision**: SQLite with FTS5 extension via `aiosqlite`

**Rationale**:
- 零配置、单文件、无需独立数据库进程
- FTS5 全文检索满足记忆搜索需求
- `aiosqlite` 提供原生 asyncio 支持
- WAL 模式支持并发读，配合 namespace 隔离避免写冲突

**Alternatives considered**:
- `chromadb` / vector DBs: 对纯文本检索 overkill，引入运维复杂度
- `tinydb`: 无全文检索，大数据量性能差
- PostgreSQL + pgvector: 需要外部服务，违反"零外部依赖"原则

**Chinese text handling**: FTS5 内置 `unicode61` tokenizer 对中文分词效果有限。检索管线已设计降级路径：FTS5 → LIKE 模糊匹配 → LLM 精排。

---

## 3. CLI Framework

**Decision**: `prompt_toolkit` + `rich`

**Rationale**:
- `prompt_toolkit`: 成熟的终端输入框架，支持语法高亮、自动补全、多行输入
- `rich`: 终端富文本渲染，支持 Markdown、面板、表格、进度条
- 两者都是纯 Python，无原生依赖

**Alternatives considered**:
- `textual`: 全屏 TUI 框架，过重。chat-core 是行式对话，非全屏应用
- `click` + `colorama`: 功能不足，无实时输入处理
- Raw `input()` + ANSI: 太简陋，缺少 Ctrl+C 优雅处理

---

## 4. Function Calling Strategy

**Decision**: 原生 OpenAI function calling（tool_choice="auto"）

**Rationale**:
- 设计文档要求所有脑通过 function calling 执行工具
- 子Session 工具集：`send_reply`, `wait`, `recall`, `inner_thoughts`, `done`
- 主脑工具集：`recall`, `memory_save`, `memory_link`, `memory_tag`, `inject_to_sub`
- `tool_choice="auto"` 让模型自行决定何时调用工具（ReAct 循环的核心）

**inner_thoughts + done 协议强制**：通过 system prompt 明确要求两步协议（先 inner_thoughts 后 done），TurnManager 检测降级。

---

## 5. Concurrency Model

**Decision**: asyncio with explicit serialization gates

**Rules derived from design doc §11.4**:
- 子Session: 串行（`asyncio.Lock`）— "用户的嘴只有一张"
- 双主脑: 可并行（各自的 recall 互不依赖）
- 行为脑: Semaphore(2) 并发池
- 主脑审查 → 阻塞子Session
- 用户新消息 → 排队不打断

**Event bus**: 内部 `asyncio.Queue` 实现三窗口交换（§11.5）

---

## 6. Memory Retrieval Pipeline

**Decision**: 三级检索：FTS5 → LIKE 降级 → LLM 精排

**Flow**:
1. FTS5: `MATCH` 查询，top_n=20
2. LIKE 降级: FTS5 无结果时 `LIKE '%keyword%'` 兜底
3. LLM 精排: top 5 送入 LLM（max_tokens=64, timeout=1s）排序相关性
4. 扩散激活: 高相关记忆的关联记忆一同返回
5. 集群 boost: 同 topic 记忆加权

---

## 7. Emotion Decay Model

**Decision**: 指数衰减 + 独立半衰期

**Formula**: `V(t) = V₀ × 2^(-t / half_life)`

| Dimension | Half-life | Rationale |
|-----------|-----------|-----------|
| surprise | 30s | 惊讶快速消退 |
| confusion | 120s | 困惑稍持久 |
| fear | 300s (5min) | 恐惧中等消退 |
| anger | 600s (10min) | 愤怒中等持久 |
| disgust | 600s (10min) | 厌恶与愤怒同步 |
| joy | 600s (10min) | 愉悦中等持久 |
| sadness | 900s (15min) | 悲伤较持久 |
| interest | 1200s (20min) | 兴趣缓慢消退 |
| anticipation | 1800s (30min) | 期待缓慢消退 |
| trust | 3600s (60min) | 信任最持久 |

**Contagion**: 脑间传染系数 0.1，每个 tick 传播一次。

---

## 8. Boredom Model

**Decision**: 基于对话质量 + 指数衰减

**Formula**: `B(t) = eval_param × e^(-t / τ)` where τ = 600s

**Trigger**: `B(t) < 0.30` → 无聊触发
**End conversation**: `B(t) > 0.70 AND impulsiveness > 0.5` → 子Session done

---

## 9. Silent Accumulator (FuzzyParam)

**Decision**: 非确定性的"忍无可忍"触发

**Formula**:
```python
base = min(0.3, silence_count × 0.05)
corrected = FuzzyParam(
    base=base,
    amplitude=0.1,
    noise=random() × 0.05
).sample()
```

每次采样结果不同 → 非机械的"有时忍得住，有时忍不住"。

---

## 10. Context Compression & Retirement

**Decision**: 三级策略（§11.7）

| Level | Threshold | Action |
|-------|-----------|--------|
| Normal | < 70% tokens | 保留全部历史 |
| Compress | 70-85% | 旧工具结果截断至 100 字，短期记忆降为 1 条 |
| Retire | > 85% | 子Session 退役 → 提取摘要 → 新 session 接续 |

主脑退役阈值不同：> 90% tokens 或 wire.jsonl > 500 行。

---

## 11. Chinese Text Tokenization

**Decision**: jieba 分词 + FTS5 OR'd token matching

**Rationale**:
- FTS5 内置 `unicode61` tokenizer 对中文逐字分词，搜索"建筑师"无法匹配"建筑设计师"
- jieba 将查询分词为语义单元 → FTS5 用 OR 连接多词 MATCH → LIKE 多词降级
- E2E 实测：无 jieba 时召回率 ~0%，集成后 > 90%
- jieba 为纯 Python 库，pip install 即用，无 C 扩展依赖

**Alternatives considered**:
- 自定义 FTS5 tokenizer: 需 C 扩展，跨平台部署困难
- jieba_fast: C 扩展版，速度更快但 pip 安装可能失败（需编译器）

---

## 12. tool_choice Strategy

**Decision**: `tool_choice="auto"` for all brains, with strong prompt enforcement for sub-session

**Rationale**:
- DeepSeek API **不支持** `tool_choice="required"`（返回 400: "Thinking mode does not support this tool_choice"）
- 子Session 通过 prompt 强制声明 + 工作流引导 + 纯文本降级路径确保工具使用
- 主脑和行为脑天然需要 `auto`（灵活选择是否调用工具）

**E2E Verification**: 子Session 在 19/19 Spec tests 中正确使用 send_reply/recall/wait/inner_thoughts

---

## 13. inner_thoughts + done Consolidation

**Decision**: inner_thoughts 调用即结束，done 保留为备份

**Rationale**:
- DeepSeek Flash 偶有调用 inner_thoughts 后漏调 done 的行为
- 合并为"inner_thoughts → 自动 done"消除依赖模型调用两个连续工具
- done 保留在工具集中作为等价选项。提示词改为 "调用 inner_thoughts 即结束，无需 done"

**Impact**: 消除 `done ❌` 降级路径，内心戏完成率从 ~60% 提升到 ~95%

---

## Summary

All technical unknowns from the design document have been resolved with concrete decisions. No remaining NEEDS CLARIFICATION items.
