# AGENTS.md — chat-core 项目文档

> 本文档供 AI Agent 读取，建立项目理解基线。人类开发者同样适用。

---

## 项目概述

**chat-core** 是一个终端 AI 聊天 CLI，实现"四脑模型"人格模拟系统。核心理念：AI 不是被动 Q&A 机器，而是有独立判断力、情绪、记忆、会主动社交的聊天伙伴。

- **语言**: Python 3.12+
- **范式**: asyncio 异步，四脑并发
- **入口**: `chat_core/cli.py` → `chat-core` 命令
- **定位**: 本地单用户 CLI，零外部编排依赖

---

## 架构：四脑模型

```
用户消息
  │
  ├── 逻辑主脑 (LogicBrain)     → 事实检索、结构化记忆、事实审查
  ├── 情感主脑 (EmotionBrain)   → 情感检索、情感标签、语气审查
  │        ↓ 合并注入 (inject_to_sub)
  ├── 子Session (ReActLoop)     → 唯一发言者，think→act→observe 循环
  │        ↓ 发言文本 + 内心戏
  ├── 审查系统 (ReviewSystem)   → 三层错误检测
  └── 权重决策                   → logic×0.6 + emotion×0.4 → 纠正/沉默/拧巴

+ 行为脑 (ActionBrain)           → 临时创建，执行搜索/抓取，用完销毁
+ 主动系统 (ProactiveSystem)     → 无聊检测 → 主动发起对话
```

**核心铁律**: 主脑不发言，子Session 是唯一的嘴。inner_thoughts 调用即结束（无需单独 done）。

---

## 文件索引

### 入口 & 配置

| 文件 | 职责 | 关键类/函数 |
|------|------|------------|
| `chat_core/cli.py` | prompt_toolkit + rich TUI 入口 | `_chat_loop()`, `InterruptHandler`, `_show_mood()`, `_show_interests()` |
| `chat_core/config.py` | YAML 配置 + .env 加载 + schema 校验 | `Config`, `get_config()`, `ConfigError` |
| `chat_core/config.yaml` | 默认配置：模型、脑参数、系统参数 | brains.logic/emotion/sub_session/action |
| `chat_core/prompts/persona.yaml` | 人设定义 (~400 tokens) | identity, voice, style_guide |
| `chat_core/prompts/rules.yaml` | 行为规范 (~200 tokens) | speech_protocol, reply_rules, safety |
| `chat_core/prompts/tools.yaml` | 工具使用说明 (~300 tokens) | sub-session 五项工具 |

### 核心引擎 (`chat_core/core/`)

| 文件 | 职责 | 关键类/函数 |
|------|------|------------|
| `types.py` | 全部共享数据类型 (40+ dataclass/enum) | `Message`, `ConversationTurn`, `EmotionState`, `MemoryEntry`, `ReviewResult`, `Intent`, `StreamEvent`, `ActionResult` |
| `provider.py` | AsyncOpenAI 封装 (流式+非流式) | `ModelProvider.chat()`, `ModelProvider.stream_chat()` |
| `tools.py` | 工具注册与执行 (并行safe+串行) | `ToolRegistry`, `ToolDefinition` |
| `prompt_engine.py` | 三层 Prompt 编译 (persona+rules+tools) | `PromptEngine.build_sub_session_prompt()`, `build_logic_brain_prompt()`, `build_emotion_brain_prompt()` |
| `loop.py` | 子Session ReAct 循环引擎 | `ReActLoop.run()`, `_think()`, `_act()`, `SubSessionConfig`, `register_sub_session_tools()`, `_handle_send_reply()`, `_handle_recall()` |
| `brain.py` | 四脑实现 + 并发池 | `LogicBrain`, `EmotionBrain`, `ActionBrain`, `ActionBrainPool`, `_RateLimiter` |
| `turn_manager.py` | Turn 编排 + EventBus | `TurnManager.process_turn()`, `EventBus` |
| `safety.py` | 内容安全过滤 | `ContentFilter.check_safety()` |
| `history.py` | 对话历史 JSONL 持久化 | `HistoryManager` |

### 子系统 (`chat_core/systems/`)

| 文件 | 职责 | 关键类 |
|------|------|--------|
| `memory.py` | SQLite FTS5 + jieba 分词记忆存储 | `MemoryStore`, `_segment_chinese()` |
| `emotion.py` | 10维×3脑情绪引擎 (衰减+传染) | `EmotionEngine` |
| `personality.py` | 8维人格权重 → 行为参数映射 | `PersonalityEngine` |
| `attention.py` | Focus/Dominance 注意力模型 | `AttentionModel` |
| `boredom.py` | 无聊检测器 (指数衰减) | `BoredomDetector` |
| `interest.py` | 话题追踪 + FuzzyParam + 沉默累积器 | `InterestModel`, `FuzzyParam`, `SilenceAccumulator` |
| `review.py` | 三层错误检测 + 意图提取 | `ReviewSystem`, `extract_intent()` |
| `proactive.py` | 主动行为系统 (initiative/intent/deferred) | `ProactiveSystem`, `_enhance_recall()`, `_recall_with_memory()` |
| `multimodal.py` | 图片检测 + 降级链 | `MultimodalHandler` |

---

## 关键数据流

### 一个 Turn 的完整流程

```
TurnManager.process_turn(user_message)
  │
  ├─ 1. DUAL_RECALL: LogicBrain.think_pre() ∥ EmotionBrain.think_pre()
  │      → recall 工具执行 → 返回记忆列表 + 方向判断文本
  │
  ├─ 2. INJECTING: LogicBrain.think_inject() ∥ EmotionBrain.think_inject()
  │      → inject_to_sub 工具调用 → 返回注入字典
  │
  ├─ 3. SUB_SESSION: ReActLoop.run(user_message)
  │      ┌─ 初始化: system_prompt + user message + injection context
  │      └─ while _should_continue():
  │           ├─ _think()  → LLM 调用 (function calling)
  │           ├─ _act()    → 执行工具 (send_reply/wait/recall/inner_thoughts/done)
  │           │    └─ 工具结果注入 _messages → 下一轮 _think() 可见
  │           └─ inner_thoughts 调用 → _done=True → 终止
  │
  ├─ 4. REVIEWING: ReviewSystem.review(replies, memories)
  │      → 三层检测 → ReviewResult {logic_weight, emotion_weight, combined}
  │
  ├─ 5. DECIDING:
  │      combined > 0.5 → CORRECTING (纠正子Session)
  │      logic>0.8 & emotion<0.3 → TWISTED (拧巴)
  │      combined ≤ 0.5 → SILENCE (沉默归档 + 累积器)
  │
  └─ 6. ARCHIVING: 写入 user/default/conversations + self/inner_thoughts
```

### 记忆检索管线

```
MemoryStore.search(query)
  ├─ 1. _segment_chinese(query) → jieba 分词
  ├─ 2. FTS5 MATCH (OR'd tokens)
  ├─ 3. LIKE 降级 (FTS5 无结果时)
  └─ 4. TTL 过期过滤
```

### 子Session 工具执行流程

```
LLM 返回 NonStreamResult {content, tool_calls}
  ├─ 有 tool_calls:
  │   ├─ send_reply → _handle_send_reply() → replies.append() + _emit_reply()
  │   ├─ wait → asyncio.sleep()
  │   ├─ recall → _handle_recall() → MemoryStore.search()
  │   ├─ inner_thoughts → _inner_thoughts_raw = text, _done = True
  │   └─ done → _done = True
  └─ 无 tool_calls (纯文本):
      ├─ content → replies.append() + _emit_reply()
      └─ 首次纯文本且无 inner_thoughts → 注入 system prompt 要求补写 → 再试一轮
```

---

## 编码约定

### 异步模式
- 所有 I/O 操作必须 `async/await`
- `EmotionEngine.tick()` 是**同步**方法（被 asyncio task 调用）
- 信号处理器中不能 `await`，用 `call_soon_threadsafe()` 调度异步操作

### 类型注解
- 所有函数签名必须有类型注解
- 回调类型用 `Any` 仅在无法确定时
- 使用 `from __future__ import annotations` 延迟求值

### 错误处理
- LLM 调用失败 → `[系统错误: {e}]` 推入 replies，不抛异常
- 工具执行失败 → `json.dumps({"error": ...})` 返回
- 配置校验失败 → `ConfigError` 阻断启动

### 命名空间隔离
- 子Session recall: 只能读 `user/{uid}/*` + `short_term/*`
- 主脑 recall: 全权限
- 潜意识区: corrections > nudges > short_term (优先级)

---

## 配置关键值

| 参数 | 值 | 位置 |
|------|-----|------|
| 逻辑主脑模型 | deepseek-v4-pro | config.yaml brains.logic.model |
| 情感主脑模型 | deepseek-v4-pro | config.yaml brains.emotion.model |
| 子Session模型 | deepseek-v4-flash | config.yaml brains.sub_session.model |
| 行为脑模型 | deepseek-v4-flash | config.yaml brains.action.model |
| 主脑上下文 | 700K tokens | config.yaml brains.logic.max_context_tokens |
| 子Session上下文 | 500K tokens | config.yaml brains.sub_session.max_context_tokens |
| 行为脑上下文 | 500K tokens | config.yaml brains.action.max_context_tokens |
| 子Session max_iter | 5 | config.yaml brains.sub_session.max_iter |
| 思考模式 | max (主脑+子), medium (行为) | config.yaml brains.*.reasoning_effort |
| 审查阈值 | combined > 0.5 | hardcoded in turn_manager.py |
| 无聊触发 | B(t) < 0.30 | config.yaml systems.boredom.trigger_threshold |
| 情绪维度 | 10 (含半衰期) | config.yaml systems.emotion.decay |

---

## 测试

```bash
# 单元测试 (84 tests)
python -m pytest tests/ -v

# Spec E2E 测试 (19 scenarios)
python tests/spec_e2e_test.py

# 单文件测试
python -m pytest tests/test_memory.py -v

# 覆盖率
python -m pytest tests/ --cov=chat_core --cov-report=term
```

| 测试文件 | 覆盖模块 |
|----------|---------|
| `tests/test_memory.py` | MemoryStore CRUD/FTS5/关联/TTL |
| `tests/test_loop.py` | ReActLoop 终止条件/压缩/工具处理 |
| `tests/test_config.py` | Config 加载/校验/环境变量 |
| `tests/test_brain.py` | Brain 创建/池并发/限速器 |
| `tests/test_phase6_emotion.py` | EmotionEngine/PersonalityEngine/AttentionModel |
| `tests/spec_e2e_test.py` | 全量 Spec 19 场景 (需要 API key) |

---

## 常见修改指南

### 添加新工具
1. 在对应 brain 的 `_register_tools()` 中注册 `ToolDefinition`
2. 更新 `prompts/tools.yaml` 中的工具说明
3. 如果是子Session 工具，在 `loop.py:_act()` 中处理工具结果

### 调整人格
1. 修改 `config.yaml` → `systems.personality.initial` 的权重值
2. 修改 `systems/personality.py` 中的行为映射公式

### 添加新情绪维度
1. `core/types.py` → `EmotionState` dataclass 加字段
2. `config.yaml` → `systems.emotion.decay` 加半衰期
3. `systems/emotion.py` → tick() 中加衰减逻辑

### 修改系统提示词
1. 编辑 `prompts/persona.yaml` / `rules.yaml` / `tools.yaml`
2. 如果改结构，更新 `core/prompt_engine.py` 中的编译逻辑

---

## 设计文档

完整规格、计划、数据模型见 `specs/001-chat-core-cli/`：

| 文档 | 路径 |
|------|------|
| 功能规格 (37 FR) | `specs/001-chat-core-cli/spec.md` |
| 实施计划 | `specs/001-chat-core-cli/plan.md` |
| 任务列表 (74 tasks) | `specs/001-chat-core-cli/tasks.md` |
| 数据模型 + 状态机 | `specs/001-chat-core-cli/data-model.md` |
| 技术决策 | `specs/001-chat-core-cli/research.md` |
| CLI 接口契约 | `specs/001-chat-core-cli/contracts/cli-contract.md` |
| 原始架构设计 | `chat-core-design.md` |
