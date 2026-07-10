# chat-core CLI 架构设计文档

> 设计时间：2026-07-09
> 状态：设计完成，待实现

---

## 一、项目定位

chat-core 是一个独立运行的终端 AI 聊天 CLI。不依赖 Kimi Code CLI、kimi-debug-tunnel 或任何外部编排工具。核心体验是"和一个有完整人格的人在终端聊天"。

**架构哲学**：与人类沟通思维对齐——快速反应但有自己的想法，并行判断对错，决策"说还是不说"，会主动发起话题。

---

## 二、大脑分工

### 2.1 多脑模型

```
┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌──────────┐
│  逻辑主脑      │  │  情感主脑      │  │ 子Session × N  │  │ 行为脑   │
│  Logic Keeper │  │  Emotion Tagger│  │  Speakers      │  │  Action  │
├───────────────┤  ├───────────────┤  ├───────────────┤  ├──────────┤
│ recall(事实)  │  │ recall(情感)  │  │ send_reply     │  │ search   │
│ 方向判断      │  │ 方向判断      │  │ wait           │  │ recall   │
│ 解析内心戏    │  │ 打标签        │  │ inner_thoughts │  │ web_fetch│
│ 结构化归档    │  │ 情感归档      │  │ recall(只读)   │  │          │
│ 事实审查(异步)│  │ 语气审查(异步)│  │ done           │  │          │
│ 纠错权重协商  │  │ 纠错权重协商  │  │                │  │          │
│ 评估→无聊     │  │ 兴趣→无聊     │  │                │  │          │
├───────────────┤  ├───────────────┤  ├───────────────┤  ├──────────┤
│ 说话: ❌      │  │ 说话: ❌      │  │ 说话: ✅       │  │ 说话: ❌ │
│ 写记忆: ✅    │  │ 写记忆: ✅    │  │ 写记忆: ❌     │  │ 写记忆:❌│
│ 决策: ✅      │  │ 决策: ✅      │  │ 决策: ❌       │  │ 决策: ❌ │
└───────────────┘  └───────────────┘  └───────────────┘  └──────────┘
```

**核心原则**：主脑不发言。子Session 是唯一的嘴，可以有多个（多副脑）。行为脑无情感状态。

### 2.2 各脑工具集

**逻辑主脑**：`recall`, `memory_save`, `memory_link`, `inject_to_sub`, `write_correction`

**情感主脑**：`recall`, `memory_tag`, `inject_to_sub`, `write_correction`

**子Session（每个实例）**：`send_reply`, `wait`, `recall`(只读), `inner_thoughts`(纯文本), `done`

**行为脑**：`search`, `recall`, `web_fetch`（无 `send_reply`）

### 2.3 子Session 数量

主脑根据对话复杂度决定激活几个子Session：

| 场景 | 数量 | 分工 |
|------|:---:|------|
| 简单应答 | 1 | 主发言人 |
| 情感对话 | 2 | 主发言人 + 语气调节者 |
| 信息密集型 | 2 | 主发言人 + 信息补充者 |
| 复杂/冲突 | 3 | 主发言人 + 调节者 + 补充者 |

多个子Session 的 `send_reply` 按时间戳交错输出到 CLI，标注来源（如 `[夏柠]` vs 无标注以保持人格统一）。

---

## 三、一个 Turn 的完整流程

**核心原则**：主脑审查**不阻塞**子Session 的消息发送。子Session 发送→用户立即看到。主脑异步审查→发现错误→通过潜意识区在**下一轮**注入纠正。

### 3.1 消息到达 → 双脑并行检索

```
用户消息 → 
  逻辑主脑: recall("关键词") → 事实记忆
  情感主脑: recall("情感词") → 情感关联
→ 各自方向判断 → 通过消息总线交换 (见 11.5) → 合并注入子Session
```

### 3.2 子Session ReAct 循环（可多个并行）

```
每个子Session 收到各自注入 → while _should_continue():
  ① 读短期记忆 (近3轮行为脑结果)
  ② 匹配潜意识区 (corrections/nudges — 主脑在上一轮写入的)
  ③ _think() → LLM function calling
  ④ _act() → send_reply 立即输出到 CLI, 不等待审查
  ⑤ 工具结果注入上下文 → 回到 ①
→ inner_thoughts (纯文本)
→ done
```

**关键**: 用户看到子Session 的第一条 `send_reply` 时，主脑的审查可能还没开始。消息流式到达，不被阻塞。

**终止条件**：`done` | hit_count ≥ max_iter | sadness > 0.8 | focus < 0.15 | boredom > 0.7

### 3.3 双脑异步审查 + 权重协商

```
子Session 完成 → TurnManager 收到 inner_thoughts:

  [异步启动 — 不阻塞用户下一轮消息]
  
  ┌─ 逻辑主脑 ──────────────────┐  ┌─ 情感主脑 ──────────────────┐
  │ 事实审查 + 解析内心戏         │  │ 语气审查 + 打标签            │
  │ memory.save (结构化归档)      │  │ memory_tag (情感标签)        │
  │                              │  │                              │
  └──────────┬───────────────────┘  └──────────┬───────────────────┘
             │                                 │
             └──── 消息总线交换审查结论 ────────┘
                           │
                    联合协商权重:
                    combined = logic_weight × 0.5 + emotion_weight × 0.5
```

### 3.4 权重分流（延迟生效）

| 条件 | 行动 | 何时生效 |
|------|------|------|
| logic > 0.8 AND emotion < 0.3 | 拧巴：记录情感不适，执行逻辑 | 写入 subconscious |
| combined > 0.5 | 写潜意识区 → 下轮注入自动触发纠正 | **下一轮** |
| combined ≤ 0.5 | 沉默归档 → "我知道但选择没说" | 仅归档 |

**关键设计**：纠正永远不打断当前轮。它写在潜意识区，等**下一轮**子Session think 时自动匹配并触发纠正。这模拟了"我刚才想了想，其实..."的人类对话节奏。

---

## 四、记忆力系统

### 4.1 记忆三层

```
MemoryStore (SQLite FTS5)
├── 清醒记忆: user/*, self/*, global/*
│   结构: {key, value(JSON), salience, decay_curve, entity_type, topic_tags}
│   检索: FTS5→LIKE降级→LLM精排→扩散激活→多跳→集群boost
│
├── 短期记忆: short_term/*
│   保留: 近10条, 自动衰减
│   读取: 子Session 每次 _think() 自动拉取
│
└── 潜意识区: subconscious/*
    写入: 双脑高权重共识
    读取: 子Session 每次 think 按话题匹配
```

### 4.2 完整命名空间

```
user/{uid}/
├── facts/            逻辑主脑: 结构化事实
├── profile/          逻辑主脑: 推断画像
├── emotions/         情感主脑: 情绪标签
└── conversations/    双脑: 对话摘要

self/
├── inner_thoughts/   逻辑主脑: 解析后的内心戏
├── reflections/      逻辑主脑: 长期反思
├── noticed/          逻辑主脑: 沉默归档
├── feelings/         情感主脑: 情感记忆(含twisted拧巴)
└── intentions/       双脑: "我想要做什么"

short_term/
├── action_results/   行为脑返回(近10条)
├── recent_topics/    最近话题(衰减)
└── active_nudges/    当前方向微调

subconscious/
├── corrections/      纠正指示(高权重)
├── nudges/           主动话题
└── deferred_actions/  "现在不能做，想了想..."

global/
├── persona/          人设定义
├── knowledge/        搜索知识
└── boredom/          无聊状态
```

---

## 五、人格系统

### 5.1 情绪引擎

```
10维 × 3脑 (主脑逻辑/主脑情感/子Session)
(行为脑无状态，不参与情绪系统)
各维度独立衰减半衰期 (30s ~ 3600s)
脑间传染 (contagion_strength = 0.1)
个性调制: 外显情绪 = 真实情绪 × 人格过滤器
```

### 5.2 个性权重 (8维)

| 权重 | 默认 | 决策影响 |
|------|:---:|------|
| curiosity | 0.7 | 自主搜索、兴趣触发 |
| sociability | 0.8 | 主动发起意愿 |
| playfulness | 0.6 | 回复 temperature |
| empathy | 0.5 | 共情模式选择 |
| assertiveness | 0.3 | 纠正发言的魄力 |
| creativity | 0.6 | 回复多样性 |
| impulsiveness | 0.2 | 立刻纠正 vs 沉默倾向 |
| loyalty | 0.75 | 记忆检索 boost |

### 5.3 无聊 + 兴趣联动

```
对话结束时:
  逻辑主脑 → 评估参数 (对话深度/满意度)
  情感主脑 → 兴趣权重 (立刻/稍后)

无聊系统:
  BoredomParam = 评估参数 × e^(-t/600s)
  触发无聊 → 检查兴趣权重 → 主动发言 或 信息增强

对话中兴趣触发:
  话题累计提及 > 阈值 → 主动发言

提前结束:
  boredom > 0.7 AND impulsiveness > 0.5 → done
```

### 5.4 沉默累积器

```
同类型错误每次选择沉默 → 基础权重 +0.05
FuzzyParam 模糊化采样:
  corrected_weight = FuzzyParam(
    base = min(0.3, count × 0.05),
    amplitude = 0.1,
    noise = random() × 0.05
  ).sample()

→ 非机械的"忍无可忍"触发纠正
```

---

## 六、权重决策系统

### 6.1 纠正权重

| 事实错误类型 | 权重 | 情感错误类型 | 权重 |
|------|:---:|------|:---:|
| identity_error | 0.9 | hurtful | 0.95 |
| contradiction | 0.8 | insensitive | 0.7 |
| fact_error | 0.7 | tone_harsh | 0.6 |
| minor_detail | 0.3 | tone_cold | 0.5 |
| no_error | 0.0 | minor_tone | 0.3 |

```
combined = logic_weight × 0.5 + emotion_weight × 0.5
阈值: 0.5
```

### 6.2 意图执行权重

```
inner_thoughts 中的 "我想做X":
  logic_assess: 是否相关/有价值/时机合适
  emotion_assess: 是否自然/不突兀/对气氛好

combined > 0.5 → 激活行为脑
combined ≤ 0.5 → deferred_actions: "现在不能做，想了想..."
```

---

## 七、Prompt 系统

### 7.1 三层编译

```yaml
persona.yaml:  身份 + 性格 + 说话风格  (~400 tokens)
rules.yaml:    行为规范 + 平台约束    (~200 tokens)
tools.yaml:    工具使用说明           (~300 tokens)
```

编译为每脑独立的 system prompt，注入情绪/注意力/兴趣/记忆状态。

### 7.2 子Session 的每次 think 上下文

```
[System Prompt]                    编译后的 persona+rules+tools
[情绪状态]                         当前维度的显著值
[注意力]                           focus + dominance
[近期动态]                         行为脑近3条结果
[注意]                             匹配的潜意识纠正指示
[用户消息]                         用户原始消息
[历史消息]                         session 的 message history
```

---

## 八、技术选型

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.12+ |
| LLM SDK | openai >= 1.0 (AsyncOpenAI) |
| 函数调用 | 原生 OpenAI function calling |
| 数据库 | SQLite + FTS5 (aiosqlite) |
| CLI | prompt_toolkit + rich |
| 网页搜索 | duckduckgo-search |
| 配置 | YAML + 环境变量 |

**不依赖**：Kimi Code CLI、kimi-debug-tunnel、任何 MCP 服务器、任何外部 session 管理服务

---

## 九、项目结构

```
chat-core/
├── cli.py                    # CLI 入口
├── config.py                 # 配置加载
├── config.yaml               # 默认配置
├── prompts/
│   ├── persona.yaml          # 人设定义
│   ├── rules.yaml            # 行为规范
│   └── tools.yaml            # 工具说明模板
├── core/
│   ├── loop.py               # ReAct Loop 引擎
│   ├── brain.py              # 四脑实现 (逻辑/情感/子Session/行为)
│   ├── turn_manager.py       # Turn 协调
│   ├── tools.py              # ToolRegistry + 工具实现
│   ├── prompt_engine.py      # Prompt 编译
│   └── provider.py           # LLM Provider 抽象
├── systems/
│   ├── memory.py             # MemoryStore (SQLite FTS5)
│   ├── emotion.py            # EmotionEngine (10维×3脑)
│   ├── personality.py        # Personality (8维权重)
│   ├── boredom.py            # BoredomDetector
│   ├── interest.py           # InterestModel (FuzzyParam)
│   └── attention.py          # AttentionModel
├── data/
│   └── memory.db
└── tests/
```

---

## 十、实现顺序

```
Phase 1: 骨架 CLI                   (~5天)
  □ CLI 入口 + config 加载
  □ LLM Provider 抽象
  □ ReAct Loop 引擎 (原生 function calling)
  □ 子Session 基础工具: send_reply, wait, done
  □ System Prompt 三层编译
  → 交付: 能聊天的 CLI

Phase 2: 双脑 + 记忆                (~8天)
  □ MemoryStore (SQLite FTS5 + 检索)
  □ 双主脑 (逻辑 + 情感)
  □ 子Session inner_thoughts
  □ 权重决策 + 纠正/沉默分流
  □ 短期记忆 + 潜意识区
  □ 行为脑 (search/recall/web_fetch)
  → 交付: 有记忆、会审查、会纠错的聊天

Phase 3: 完整人格                    (~8天)
  □ EmotionEngine
  □ Personality 权重系统
  □ BoredomDetector + InterestModel
  □ 沉默累积器 (FuzzyParam)
  □ 拧巴记录
  □ 主动发起 (无聊+兴趣联动)
  □ Session 退役机制
  → 交付: 有完整"活"人格的聊天

Phase 4: 打磨                        (~5天)
  □ CLI TUI 美化 (rich)
  □ 多模态降级
  □ 历史持久化
  □ 测试覆盖
  → 交付: 产品级 CLI 聊天工具
```


## 十一、疏漏补充

### 11.1 inner_thoughts 协议

**问题**：模型可能跳过 inner_thoughts 直接 done，或中途调用。

**方案**：System prompt 明确约束为强制两步协议：

```
【发言结束协议】
当你认为发言结束后，必须按顺序调用两个工具：
1. inner_thoughts  — 输出你的内心戏（纯文本）
2. done            — 结束

inner_thoughts 只能在发言全部结束后调用一次。
如果你还在说话（还有 send_reply 要发），不要调用 inner_thoughts。
调用 done 之前必须先调用 inner_thoughts。

如果模型跳过了 inner_thoughts 直接 done → TurnManager 检测到无 inner_thoughts 
→ 记录 warning → 本次内心戏为空 → 双脑只能基于发言文本做审查（降级模式）
```

### 11.2 主脑的 _think() 循环

**方案**：主脑分为两个阶段（详见 12.1），总计 ~2 次 LLM 调用。

```
逻辑主脑 Phase 1 (recall + 方向判断):
  _think() → LLM function calling
  可用工具: recall, memory_link
  → 模型调用 recall → 获取记忆
  → 模型输出方向判断文本

逻辑主脑 Phase 2 (注入):
  _think() → LLM function calling (上下文含 Phase 1 结果)
  可用工具: inject_to_sub
  → 模型调用 inject_to_sub → 注入完成
```

### 11.3 错误检测机制

**问题**：逻辑主脑如何"发现"子Session 说错了？

**方案**：分层检测，逐步升级成本：

```
Layer 1: 关键词实体快速比对 (零 LLM 成本)
  提取子Session 发言中的实体 (人名/地名/时间/数字)
  对比 recall 结果中同类型实体
  精确匹配 → 通过 | 不匹配 → 标记为候选错误

Layer 2: 规则判定 (零 LLM 成本)
  如果候选错误实体在潜意识区已有纠正记录
  → 直接确认错误 (无需 LLM)

Layer 3: LLM 审查 (仅在 Layer 1 发现候选 且 Layer 2 无记录时)
  prompt: "子Session 说'用户是校队的'。记忆显示'用户是院队的'。这是错误吗？"
  → 返回: {is_error: bool, severity: float}

优先级: Layer 3 有 1s 超时 → 超时则跳过该候选 (不阻塞)
```

### 11.4 并发模型

**问题**：四脑 + tick 的并发关系未定义。

**方案**：异步非阻塞，审查在后台运行。

```
并发规则:
  ┌─ 子Session 发送消息 = 立即输出到 CLI (不等待任何人)
  ├─ 同一时刻最多 3 个子Session 并行发言 (Semaphore(3))
  ├─ 主脑审查在子Session 完成后异步启动 (不阻塞子Session)
  ├─ 审查期间的纠正 → 写入潜意识区 → 下一轮子Session think 自动读取
  ├─ 行为脑与子Session 可并行 (行为脑结果进短期记忆)
  ├─ 行为脑上限 2 个并发 (Semaphore(2))
  ├─ 用户新消息到达时:
  │   若子Session 正在运行 → 排队 (不打断当前发言)
  │   若主脑正在审查 → 不等待 (审查是异步的，不影响)
  ├─ idle tick 仅在全局 idle 状态触发
  └─ 双主脑之间可并行 (各自的 recall/phases 互不依赖)

时序示意:
  用户消息
    → 主脑 Phase 1+2 (recall + inject) [~2s]
    → 子Session ReAct [~3s, 消息流式输出]
        ├── send_reply(1) → CLI 立即显示 ← 用户开始阅读
        ├── send_reply(2) → CLI 立即显示
        └── send_reply(3) → CLI 立即显示 → inner_thoughts → done
    → 主脑异步审查 [~1s, 后台运行, 不阻塞]
        └── 写 subconscious → 下一轮生效
```

### 11.5 双脑相互启发

**方案**：通过内部消息总线，三个交换窗口 + 一个持续通道：

```
交换窗口 1: recall 完成后 (双脑交换各自检索到的记忆)
  逻辑主脑 → bus.publish("logic_recall", {facts: [...], entities: [...]})
  情感主脑 → bus.publish("emotion_recall", {feelings: [...], tags: [...]})
  各自的注入构建时读取对方的发布

交换窗口 2: 审查完成后 (双脑交换各自的审查结论)
  逻辑主脑 → bus.publish("logic_review", {errors: [...], corrections: [...]})
  情感主脑 → bus.publish("emotion_review", {tone_issues: [...], tags: [...]})
  用于联合协商权重

交换窗口 3: 空闲 tick 时 (双脑交换各自的主动发起想法)
  逻辑主脑 → bus.publish("logic_inspiration", {topic_ideas: [...]})
  情感主脑 → bus.publish("emotion_inspiration", {mood_read: "...", suggestion: "..."})
  用于合并启发

持续通道: 情感→逻辑的实时情绪通知
  情感主脑在用户消息分析后, 若检测到显著情绪变化:
    bus.publish("emotion_alert", {mood_shift: "happy→sad", intensity: 0.7})
  逻辑主脑收到后, 在 Phase 2 注入时调整方向:
    "用户情绪急转直下, 当前方向需要更谨慎"
  (不需要等待审查窗口 — 实时调整)
```

### 11.6 意图提取

**问题**：inner_thoughts 纯文本中的"我想做X"如何可靠提取？

**方案**：两层提取 + 可选 System Prompt 引导：

```
System Prompt 中建议子Session 用显式格式 (但不强制):
  "如果有什么想做的，可以用 '我是否想要做什么: ...' 开头写一段"

提取逻辑:
  ① 正则匹配: /我是否想要做什么[:：]\s*(.+?)(?:\n\n|$)/
     匹配到 → 提取为意图文本
  ② 意图分类 (规则):
     - 含 "搜索/查/找" → ACTION_SEARCH
     - 含 "告诉/说/提醒" → ACTION_SPEAK
     - 含 "记住/记录" → ACTION_REMEMBER
     - 无匹配 → "无明确意图"
  ③ 如果 ① 没匹配到但有 "想/要/该" 关键词:
     → 用 LLM 轻量提取 (max_tokens=64, timeout=1s)
     → 超时则放弃 (不阻塞)
```

### 11.7 子Session 上下文压缩

**问题**：每次 think 上下文膨胀。

**方案**：三级压缩策略：

```
< 70% token 容量 → 正常运行 (保留全部历史)
70-85% → 压缩模式:
  - 旧工具结果: 保留 action + result 摘要 (截断至 100 字)
  - 短期记忆注入: 从 3 条降为 1 条
  - 潜意识匹配: 只匹配 severity ≥ medium 的条目
> 85% → 退役模式:
  - 触发子Session 退役 → 提取摘要 → 新子Session 接续
  - 交接注入: {"最近的对话摘要": "...", "未完的话题": "..."}
```

### 11.8 记忆源优先级

**问题**：短期记忆与潜意识区冲突。

**方案**：显式优先级：

```
子Session 读取记忆源时的合并规则:
  ① 潜意识区 (corrections) — 最高优先 (这是主脑的显式纠正)
  ② 潜意识区 (nudges/deferred) — 第二优先
  ③ 短期记忆 (recent_topics) — 第三优先
  ④ 短期记忆 (action_results) — 第四优先

冲突示例:
  short_term 说 "用户喜欢抽卡游戏"
  subconscious/corrections 说 "用户不喜欢抽卡游戏"
  → corrections 覆盖 short_term
  → 注入子Session 时标注: "根据之前的纠正，用户不喜欢抽卡游戏"
```

### 11.9 行为脑并发池

**方案**：

```python
class ActionBrainPool:
    def __init__(self, max_concurrent=2):
        self._sem = asyncio.Semaphore(max_concurrent)
        self._queue = asyncio.Queue()  # 排队等待的任务
    
    async def submit(self, task: ActionTask) -> ActionResult:
        async with self._sem:
            brain = ActionBrain(task)
            return await brain.run()
```

行为脑生命周期：创建 → run → 返回结果 → 销毁。无复用。

### 11.10 工具安全

**方案**：

```
send_reply:
  - 内容过滤: 检测到明确的自伤/暴力关键词 → 阻断 + 记录
  - 长度限制: 单条 ≤ 500 字 (超过自动截断)

search:
  - 频率限制: 每 60s 最多 5 次搜索 (token bucket)
  - 冷却: 每次搜索间隔 ≥ 2s

recall:
  - 命名空间隔离: 子Session 的 recall 只能读 user/{uid}/* 和 short_term/*
  - 不能读 self/* (自己的内心戏归档) 或 subconscious/* (除非主脑显式注入)

web_fetch:
  - URL 白名单: 仅允许 http/https
  - 内容大小限制: ≤ 100KB
  - 超时: 10s
```

### 11.11 无聊系统的 "wake session"

**问题**："wake 静态 session" 不明确。

**方案**：不是唤醒一个已有的 session，而是**临时创建一个行为脑**：

```
无聊触发 → 检查兴趣权重:
  > 0.5: "需要信息增强"
    → 创建行为脑 (临时, 单次任务)
    → task: "基于用户兴趣 [topic1, topic2]，搜索最近相关新闻"
    → 结果返回给双脑
    → 双脑决定: 是否写入 subconscious/nudges
    → 行为脑销毁
  
  ≤ 0.5: "直接发起沟通"
    → 跳过行为脑
    → 写入 subconscious/nudges → 注入子Session
```

### 11.12 多模态降级

**方案**：

```
图片检测:
  CLI 输入层拦截: 检测消息中的图片路径/URL
  (prompt_toolkit 支持粘贴图片路径)

路由:
  ① 检查主脑的 LLM Provider 是否 supports_vision
     是 → 图片直接作为 user message 的 image_url 传入
     否 → 进入降级链
  
  ② 降级链 (config.yaml 中定义):
     multimodal_chain: [stepfun/step-3.7-flash, openai/gpt-4o]
     
     依次尝试:
       创建临时 行为脑(describe_image, image_url)
       → 返回图片描述文本
       → 注入主脑: "[系统] 用户发了一张图片，内容: {描述}"
  
  ③ 所有降级 Provider 都不可用:
     → 主脑收到提示: "[系统] 用户发了一张图片，但当前没有可用的视觉模型"
     → 子Session 回复: "啊，我现在看不到图片..."
```

### 11.13 用户中断

**方案**：

```
SIGINT (Ctrl+C) 处理:
  ① 第一次 Ctrl+C:
     若子Session 正在 ReAct → 发送 cancel 信号 → 子Session 立即 done
     若行为脑正在运行 → 等当前行为脑完成 (不中断外部 API 调用)
     若主脑正在审查 → 等审查完成
     CLI 输出: "⏸ 已请求停止..."
  
  ② 第二次 Ctrl+C (2s 内):
     强制退出 → 保存当前状态 → sys.exit(0)
  
  ③ 正常 /quit:
     等待当前 turn 完成 → 保存情绪状态 → 关闭记忆库 → 退出
```

### 11.14 对话历史持久化

**方案**：

```
格式: JSONL (每行一条消息)
路径: data/history/{user_id}.jsonl

每条记录:
{
  "timestamp": "2026-07-09T08:30:00Z",
  "role": "user" | "assistant",
  "content": "...",
  "turn_id": "turn_042",
  "brain_metadata": {            // 仅 assistant 消息
    "speaker": "sub_session",
    "inner_thoughts": "...",    // 归档的内心戏
    "review": {
      "logic_verdict": "ok",
      "emotion_verdict": "ok",
      "corrections_made": null
    }
  }
}

与记忆系统的关系:
  - 历史 JSONL = 原始记录 (只追加)
  - MemoryStore = 结构化提取 (FTS5 索引)
  - 每日批处理: JSONL → 提取摘要 → 写入 MemoryStore
```

### 11.15 情绪 Tick 生命周期

**方案**：

```python
class EmotionTick:
    """后台情绪衰减任务"""
    
    def __init__(self, interval=10.0):
        self._interval = interval
        self._task: asyncio.Task | None = None
        self._paused = False
    
    async def start(self):
        self._task = asyncio.create_task(self._run())
    
    async def _run(self):
        while True:
            await asyncio.sleep(self._interval)
            if not self._paused:
                await self.emotion_engine.tick()  # 所有脑的衰减 + 传染
    
    def pause(self):
        """活跃对话中暂停衰减 (情绪在对话中由消息驱动变化)"""
        self._paused = True
    
    def resume(self):
        """对话结束后恢复衰减"""
        self._paused = False
```

**Tick 状态切换**：
```
对话开始 → pause()  (对话中的情绪变化由 on_message() 驱动)
对话结束 → resume() (自然衰减恢复)
空闲期间 → 正常运行 (每 10s 衰减一次)
```


## 十二、跨章节对接缺口修复

### 12.1 主脑执行流（Gap 1-3）

**Gap 1: 单 pass 矛盾 → 两阶段 LLM 调用**

主脑实际需要 2 次 LLM 调用，不是 1 次：

```
逻辑主脑 Phase 1 (recall + 方向):
  ① _think_pre() → LLM function calling
     可用工具: recall, memory_link
     模型基于用户消息调用 recall → 获取记忆
     模型输出 final_text → 这是"初步方向判断"
     (纯文本，如 "用户情绪低落，需要共情。记忆显示他是院队的。")

逻辑主脑 Phase 2 (注入):
  ② _think_inject() → LLM function calling
     上下文 = 用户消息 + Phase 1 的 recall 结果 + Phase 1 的方向判断
     可用工具: inject_to_sub
     模型调用 inject_to_sub(context="...", direction="...", memories=[...])
     → 注入子Session
```

**性能优化**：Phase 1 和 Phase 2 之间共享相同的 system prompt 前缀缓存。Phase 2 增量仅 ~200 tokens（recall 结果）。

**Gap 2: inject_to_sub 明确定义为工具**

```python
# inject_to_sub 的 OpenAI tool schema
{
    "type": "function",
    "function": {
        "name": "inject_to_sub",
        "description": "向子Session注入对话上下文、记忆和方向指导。子Session将基于这些信息进行回复。",
        "parameters": {
            "type": "object",
            "properties": {
                "context": {
                    "type": "string",
                    "description": "当前对话上下文摘要，包含用户消息的要点"
                },
                "direction": {
                    "type": "string",
                    "description": "回复方向指导，如'真诚共情，不敷衍安慰，自然过渡'"
                },
                "relevant_memories": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "相关记忆摘要列表"
                },
                "avoid_phrases": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "需要避免的表述"
                }
            },
            "required": ["context", "direction"]
        }
    }
}
```

**Gap 3: 主脑退役机制**

```
主脑退役阈值 (与子Session 不同):
  上下文 tokens > 90% → 退役 (主脑上下文增长慢，阈值更高)
  wire.jsonl > 500 行 → 退役
  注意力衰减信号 ≥ 2 个 → 退役

主脑退役交接格式:
{
  "brain_type": "logic" | "emotion",
  "session_summary": "最近10轮对话的关键事实/情感走向",
  "pending_corrections": [...],     // 未处理的纠正指示
  "active_intentions": [...],       // 未完成的意图
  "user_profile_snapshot": {...},   // 当前用户画像快照
  "recent_topics": [...],           // 最近话题列表
}
```

---

### 12.2 inner_thoughts 生命周期（Gap 4-5）

**Gap 4: 归档格式**

```python
# self/inner_thoughts/{timestamp} 的存储格式
{
    "raw": "子Session的原始纯文本内心戏",         # 保留原始文本
    "parsed": {                                   # 逻辑主脑解析结果
        "feeling": {"primary": "共情", "valence": 0.3},
        "reflection": "回避了套路安慰...",
        "summary": "共情比赛失利→认可努力→转移注意力",
        "topics": ["比赛", "篮球"],
        "user_read": {"mood": "低落", "need": "被理解"},
        "self_assessment": "真诚度: 9/10",
        "intent": {"action": "SPEAK", "detail": "下次聊轻松话题"}  # 如果提取到意图
    },
    "tags": {                                     # 情感主脑打的标签
        "emotional_valence": "supportive",
        "tone_quality": "genuine",
        "effectiveness": 0.85
    }
}
```

**Gap 5: memory_save vs memory_tag 分离**

```
memory_save (逻辑主脑):  写入结构化事实
  → user/facts/, user/profile/, self/inner_thoughts/
  
memory_tag (情感主脑):   给已有 entry 追加情感标签
  → 不创建新 entry，而是 update 现有 entry 的 tags 字段
  
示例:
  ① 逻辑主脑: memory_save("user/conversations", "turn_042", {
       "summary": "聊了比赛失利",
       "topic": "篮球"
     })
  ② 情感主脑: memory_tag("user/conversations", "turn_042", {
       "emotional_valence": "supportive",
       "user_mood_shift": "sad→accepting"
     })
  → 结果: 同一个 entry 有逻辑字段 + 情感标签，不冲突
```

---

### 12.3 纠正循环（Gap 6-7）

**Gap 6: 纠正不是单次模式，是完整的子Session 发言**

```
纠正发言的子Session:
  ┌─ 完整子Session 能力 (和正常发言一样)
  ├─ 工具: send_reply, wait, recall(只读subconscious), inner_thoughts, done
  ├─ 触发: 下一轮子Session think 时自动匹配到 subconscious/corrections
  ├─ 流程: think → read subconscious → "啊等等, 我记得是..." → send_reply → wait
  │        → "上次听你说的" → send_reply → inner_thoughts → done
  ├─ inner_thoughts 存在 (归档纠正的内心戏, 如 "纠正了身份信息，语气自然")
  ├─ 纠正的 send_reply 也可以多段 (像正常说话一样自然)
  │
  ├─ 递归防护:
  │   纠正发言产生的 inner_thoughts 被审查时:
  │   ✅ 如果纠正正确 → 记录 → 结束
  │   ❌ 如果纠正本身又错了 → 再次写入 subconscious
  │      → 但标记 recursion_depth += 1
  │      → depth > 2 → 不再纠正 (放弃, 记录 "纠正失败")
  └─ 最多递归 2 层
```

**Gap 7: 拧巴记录明确归属**

```
TurnManager 在异步审查完成后统一处理:

  if logic_weight > 0.8 and emotion_weight < 0.3:
      ① 写入 subconscious/corrections (按逻辑执行纠正)
      ② TurnManager 写入拧巴记录:
         memory.save("self/feelings/twisted", f"twisted_{turn_id}", {
             "context": "纠正 vs 沉默分歧",
             "logic_decision": "纠正 (将写入 subconscious)",
             "emotion_dissent": "觉得不该说 (weight: 0.2)",
             "resolution": "按逻辑执行。下一轮子Session 会自动触发纠正。",
             "emotion_aftermath": "轻微不适，记住了",
         })
```

---

### 12.4 行为脑→短期记忆（Gap 8-10）

**Gap 8: ActionResult 格式**

```python
@dataclass
class ActionResult:
    task: str                    # 原始任务
    task_type: str               # "search" | "recall" | "web_fetch" | "describe_image"
    output: str                  # 结果摘要文本 (≤200 字)
    raw: dict | None             # 完整原始结果 (不注入子Session，供双脑参考)
    sources: list[str]           # search 结果来源
    session_id: str              # 行为脑 session ID (用于日志)
    elapsed_ms: int              # 执行耗时
    success: bool
    error: str | None
```

**Gap 9: 短期记忆写入者**

```
流程: 行为脑完成 → TurnManager 接收 → TurnManager 写入 short_term

  # TurnManager._on_action_brain_complete():
  result = await action_brain.run()
  memory.save("short_term/action_results", f"action_{now}", {
      "task": result.task,
      "summary": result.output,
      "timestamp": now(),
      "ttl": 300,  # 5 分钟后自动过期
  })
```

**Gap 10: 短期记忆衰减**

```
在每次读取时按时间过滤 (不跑后台任务):
  
  def get_short_term(limit=3):
      entries = memory.query("short_term/action_results", 
                             order_by="created_at DESC")
      now = time.time()
      return [
          e for e in entries[:limit]
          if now - e["timestamp"] < e.get("ttl", 600)  # 默认 10min TTL
      ]
```

---

### 12.5 主动发起流（Gap 11-13）

**Gap 11: 对话结束事件**

```
TurnManager.process_turn() 的最后一步:

  async def process_turn(self, user_message):
      # ... 主脑 Phase 1+2, 子Session ReAct, 双脑 review ...
      
      # 对话结束
      logic_eval = await self.logic_brain.evaluate_conversation()
      emotion_weight = await self.emotion_brain.get_interest_weight()
      
      self.event_bus.emit("conversation_ended", {
          "turn_id": self.current_turn_id,
          "logic_eval": logic_eval,        # {depth: 0.6, satisfaction: 0.7}
          "emotion_weight": emotion_weight, # 0.4
          "timestamp": time.time(),
      })
      
      # 无聊系统收到事件 → 启动计时器
      # 情绪系统收到事件 → resume tick
```

**Gap 12: 无聊 tick 周期**

```
BoredomTicker: 独立 asyncio 任务, 每 30s 检查

  async def _run(self):
      while True:
          await asyncio.sleep(30)
          if self._active:  # 仅在 idle 状态
              self._boredom_level = self._eval * exp(-self._elapsed / 600)
              
              if self._boredom_level < 0.30:  # 触发阈值
                  await self._on_boredom_trigger()

  def start(self, eval_param: float, interest_weight: float):
      self._eval = eval_param
      self._interest_weight = interest_weight
      self._elapsed = 0
      self._active = True

  def stop(self):
      self._active = False  # 对话开始时停止
```

**Gap 13: 主动发言时子Session 的创建**

```
TurnManager._on_proactive_trigger():
  
  ① 检查是否有活跃的子Session:
     if self.sub_session and self.sub_session.is_alive():
         → 直接注入 (复用现有 session)
     else:
         → 创建新子Session (从主脑的 system prompt 模板)
  
  ② 注入:
     inject_to_sub({
         "initiative": "主动发起",
         "nudge_ref": "subconscious/nudges/主动话题_游戏",
         "direction": "用关心的语气开聊"
     })
  
  ③ 子Session ReAct → send_reply → inner_thoughts
  ④ 双脑审查 (主动发起的审查流程与被动相同)
```

---

### 12.6 实现细节（Gap 14-17）

**Gap 14: 完整工具 Schema 定义**

```python
# 子Session 工具
TOOLS_SUB_SESSION = [
    {
        "type": "function",
        "function": {
            "name": "send_reply",
            "description": "发送一条消息到聊天窗口。每次调用后你会收到 {'sent': True} 的反馈。可以多次调用来分段表达。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "消息内容，自然口语，≤500字"}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "自然停顿。模拟思考和打字的间隙。",
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {"type": "number", "description": "0.5-5.0秒", "minimum": 0.5, "maximum": 5.0}
                },
                "required": ["seconds"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "从记忆中检索相关信息。只读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索关键词或问题"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inner_thoughts",
            "description": "发言结束后的内心戏。仅在全部发言完成后调用一次。纯文本，不展示给用户。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "你的内心想法、当前感受、自我反思、以及'我是否想要做什么'"
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "确认本轮发言结束。必须在 inner_thoughts 之后调用。",
            "parameters": {"type": "object", "properties": {}}
        }
    },
]

# 逻辑主脑工具
TOOLS_LOGIC_BRAIN = [
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "深度记忆检索。搜索事实、用户画像、历史对话。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索查询"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_save",
            "description": "写入结构化记忆。",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string", "description": "命名空间，如 user/facts"},
                    "key": {"type": "string", "description": "条目键名"},
                    "value": {"type": "object", "description": "JSON 值"},
                    "layer": {"type": "string", "enum": ["gist", "detail"], "default": "gist"}
                },
                "required": ["namespace", "key", "value"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "memory_link",
            "description": "建立记忆之间的关联。",
            "parameters": {
                "type": "object",
                "properties": {
                    "from_key": {"type": "string"},
                    "to_key": {"type": "string"},
                    "relation": {"type": "string", "enum": ["extends", "contradicts", "related_to"]}
                },
                "required": ["from_key", "to_key", "relation"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "inject_to_sub",
            "description": "向子Session注入上下文和方向指导。",
            "parameters": {
                "type": "object",
                "properties": {
                    "context": {"type": "string", "description": "对话上下文"},
                    "direction": {"type": "string", "description": "回复方向"},
                    "relevant_memories": {"type": "array", "items": {"type": "string"}},
                    "avoid_phrases": {"type": "array", "items": {"type": "string"}}
                },
                "required": ["context", "direction"]
            }
        }
    },
]

# 情感主脑工具 (与逻辑主脑类似，但 memory_save → memory_tag)
TOOLS_EMOTION_BRAIN = [
    # recall (同上)
    # memory_tag: 给已有记忆追加情感标签
    {
        "type": "function",
        "function": {
            "name": "memory_tag",
            "description": "给已有记忆追加情感标签，不创建新条目。",
            "parameters": {
                "type": "object",
                "properties": {
                    "namespace": {"type": "string"},
                    "key": {"type": "string"},
                    "tags": {"type": "object", "description": "情感标签键值对"}
                },
                "required": ["namespace", "key", "tags"]
            }
        }
    },
    # inject_to_sub (同上)
]

# 行为脑工具
TOOLS_ACTION_BRAIN = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "搜索互联网获取实时信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索查询"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": "从记忆中检索信息。只读。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "抓取网页内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "format": "uri"}
                },
                "required": ["url"]
            }
        }
    },
]
```

**Gap 15: config.yaml 完整结构**

```yaml
# chat-core 配置
version: 1

# LLM API 配置
apis:
  deepseek:
    provider: deepseek
    base_url: https://api.deepseek.com/v1
    api_key: ${DEEPSEEK_API_KEY}
    capabilities: []           # [vision, function_calling]
  stepfun:
    provider: stepfun
    base_url: https://api.stepfun.com/v1
    api_key: ${STEPFUN_API_KEY}
    capabilities: [vision]

# 各脑模型配置
brains:
  logic:
    api: deepseek
    model: deepseek-v4-pro
    temperature: 0.3
    max_tokens: 512
    
  emotion:
    api: deepseek
    model: deepseek-v4-flash
    temperature: 0.3
    max_tokens: 512
    
  sub_session:
    api: deepseek
    model: deepseek-v4-flash
    temperature: 0.8         # 运行时由 personality.playfulness 调制
    max_tokens: 512
    max_iter: 5
    
  action:
    api: deepseek
    model: deepseek-v4-flash
    temperature: 0.5
    max_tokens: 256
    max_concurrent: 2

# 多模态降级链
multimodal:
  enabled: true
  chain: [stepfun, deepseek]  # 按顺序尝试

# 内化系统参数
systems:
  emotion:
    tick_interval: 10
    decay:
      surprise: 30
      confusion: 120
      joy: 600
      sadness: 900
      anticipation: 1800
      trust: 3600
      # ... 其余维度
    contagion_strength: 0.1
    introspect_threshold: 0.3   # 自省触发：双脑余弦距离

  personality:
    initial:
      curiosity: 0.7
      sociability: 0.8
      playfulness: 0.6
      empathy: 0.5
      assertiveness: 0.3
      creativity: 0.6
      impulsiveness: 0.2
      loyalty: 0.75

  memory:
    db_path: ./data/memory.db
    retrieval:
      fts5_top_n: 20
      llm_rerank_top_n: 5
      llm_rerank_timeout: 1.0
      spread_activation_depth: 2
    decay:
      detail_auto_migrate_days: 60
      gist_expire_salience_5: 90
      gist_expire_salience_7: 135
      gist_expire_salience_10: 180

  boredom:
    tick_interval: 30
    decay_halflife: 600         # 半衰期(秒)
    trigger_threshold: 0.30     # 触发无聊的 boredom 水平
    end_conversation_threshold: 0.70

  interest:
    topic_trigger_threshold: 3  # 同一话题提及次数
    topic_weight_increment: 0.1
    decay_per_hour: 0.05

  attention:
    baseline:
      logic: {focus: 0.8, dominance: 0.7}
      emotion: {focus: 0.7, dominance: 0.5}
      sub: {focus: 0.9, dominance: 0.6}
    drift_decay_rate: 0.01      # 每秒衰减

# Prompt 文件路径
prompts:
  persona: ./prompts/persona.yaml
  rules: ./prompts/rules.yaml
  tools: ./prompts/tools.yaml

# 历史记录
history:
  path: ./data/history/
  format: jsonl

# 安全
safety:
  send_reply_max_length: 500
  search_rate_limit: [5, 60]    # 5次/60秒
  search_cooldown: 2            # 每次搜索间隔(秒)
  web_fetch_timeout: 10
  web_fetch_max_size: 102400    # 100KB
```

**Gap 16: Personality → 子Session 行为的传递路径**

```python
class PersonalityEngine:
    """8维个性权重 → 运行时参数映射"""
    
    def get_llm_temperature(self, brain_type: str) -> float:
        """playfulness 调制 temperature"""
        base_temp = self.config.brains[brain_type].temperature
        playfulness = self.weights.playfulness
        return base_temp + playfulness * 0.3  # 0.6 → +0.18, 范围 0.5-1.1
    
    def get_response_mode(self) -> str:
        """empathy 决定是否启用共情模式"""
        if self.weights.empathy > 0.5:
            return "empathetic"  # → 注入子Session 的 direction
        return "normal"
    
    def get_creativity_bias(self) -> float:
        """creativity 影响回复多样性"""
        return self.weights.creativity * 0.5

# 传递路径:
# PersonalityEngine → TurnManager._build_injection() → inject_to_sub.direction
# 例如 direction = f"{mode}模式: {base_direction}"
# PersonalityEngine → SubSession._think() → llm.temperature = get_llm_temperature()
```

**Gap 17: deferred_actions 激活路径**

```
两个激活窗口:

窗口 1: 每次新 turn 开始时
  TurnManager.process_turn() → 开始前检查:
    pending = memory.query("subconscious/deferred_actions", 
                           filter=lambda a: not a.get("resolved"))
    for action in pending:
        # 重新评估: 现在能做吗？
        new_weight = reassess(action)
        if new_weight > 0.5:
            execute(action)
            memory.update(action.key, {"resolved": True, "resolved_at": now()})

窗口 2: 无聊 tick 触发时
  BoredomTicker._on_trigger():
    # 在准备主动发言前，检查是否有 deferred action 可以顺便处理
    pending = memory.query("subconscious/deferred_actions")
    for action in pending:
        if context_matches(action.revisit_condition):
            execute(action)  # "现在时机合适了"

窗口 3: 被动清理
  deferred action 创建后 24h 未被激活 → 自动标记 resolved (过期)
```
