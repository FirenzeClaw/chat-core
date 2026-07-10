# chat-core — 四脑模型 AI 聊天 CLI

一个有记忆、有情绪、有判断力的终端 AI 伙伴。

[![Python](https://img.shields.io/badge/python-3.12+-blue)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-467%20passed-green)](#)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 目录

- [架构](#架构)
- [安装](#安装)
- [配置](#配置)
- [使用](#使用)
- [项目结构](#项目结构)
- [技术栈](#技术栈)
- [测试](#测试)
- [设计文档](#设计文档)
- [贡献](#贡献)
- [License](#license)

```
╭─────────────────────────────────────────╮
│ chat-core — 四脑模型 AI 伙伴             │
│ 输入消息开始对话 | /quit 退出             │
╰─────────────────────────────────────────╯
你: 你好，我叫小明，在北京做程序员
╭─ 小深 ──────────────────────────────────╮
│ 小明你好呀！北京的程序员——是在哪个方向？  │
╰─────────────────────────────────────────╯
你: 你还记得我叫什么、在哪工作吗？
╭─ 小深 ──────────────────────────────────╮
│ 小明，在北京做程序员。刚才你说过的～      │
╰─────────────────────────────────────────╯
```

## 架构

### 四脑模型

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

**核心铁律**: 主脑不发言，子Session 是唯一的嘴。

### 子系统全景 (18 个)

| 类别 | 子系统 | 职责 |
|------|--------|------|
| 记忆 | MemoryStore (FTS5 + jieba) | 联锁检索 + 幂律遗忘 + 三级分级 + 双向迁移 |
| 情绪 | EmotionEngine (10维×3脑) | 复合情绪 + 衰减 + 传染 + 脆弱检测 |
| 人格 | PersonalityEngine (8维) | 关系阶段调制 + 行为参数映射 |
| 注意力 | AttentionModel | 三态状态机 (FOCUSED/DRIFTING/DULL) + 疲劳因子 |
| 无聊 | BoredomDetector | 指数衰减 + 注意力状态感知 tick |
| 兴趣 | InterestModel + FuzzyParam | 话题追踪 + 沉默累积器 |
| 审查 | ReviewSystem | 三层错误检测 + 意图提取 |
| 主动 | ProactiveSystem | 无聊触发 + 意图/延迟动作 |
| 防御 | DefenseEngine | DENIAL/RATIONALIZE/PROJECT + 脆弱联动 |
| 精力 | EnergyBar | 消耗 + 恢复 + 防御联动 + OVERLOAD 加速 |
| 时间 | SubjectiveClock | 注意力/情绪/兴趣三维调制 |
| 元认知 | MetacognitionEngine | 定期+异常双触发审视 + 参数覆盖 |
| 关系 | RelationshipEngine | 4维向量 + 阶段判定 + 人格调制 |
| 群组 | GroupDynamics | 群角色统计 + 氛围快照 |
| 模式 | PatternDetector | 问候/时间规律/内部梗检测 + 中间态持久化 |
| 认知 | Intuition/Creativity/Humor/Moral | L1-L3 直觉 + 双路径创造力 + 幽默安全门 + 道德双脑评估 |
| 动机 | Motivation/Silence/Loneliness | 双层动机 + 5类沉默语义 + 孤独驱动 |
| 价值 | ValueEngine + NarrativeEngine | 三层价值观树 + 自我叙事 |

### QQ Bot 模式（多用户）

```
QQ WebSocket 消息到达
  │
  ├─ 全局双主脑 (LogicBrain + EmotionBrain) ─ 共享，异步后台注入
  ├─ 竞态追踪 (RaceTracker) ─ 活跃子 Session 计数 → 烦躁加速
  ├─ 潜意识注入 (SubconsciousInjector) ─ 按竞态度截断上下文
  │
  └─ 每对话者独立子 Session (ReActLoop)
       │  立即启动回复 (2-5s)，不等双脑
       ├─ ① send_reply → 直接发 QQ
       ├─ ② wait → 停顿
       ├─ ③ send_reply → 接着说
       ├─ ④ recall → 查 MemoryStore
       └─ ⑤ inner_thoughts → 结束
            │
            └─ 归档 + 异步提取用户事实 → MemoryStore
```

QQ Bot 模式下完整集成了全部 18 个子系统（Spec 005/008/009/010/011），CLI 与 QQ Bot 双模式行为一致。

## 安装

```bash
# Python 3.12+
git clone <repo-url>
cd chat-core
pip install -e .
```

## 配置

创建 `chat_core/.env`：

```env
DEEPSEEK_API_KEY=sk-your-key-here
```

支持所有 OpenAI-compatible API，改 `chat_core/config.yaml` 中的 `base_url` 即可。

### QQ Bot 配置

在 `chat_core/.env` 中追加：

```env
QQ_BOT_APPID=你的AppID
QQ_BOT_SECRET=你的AppSecret
```

## 使用

```bash
chat-core              # CLI 模式：默认四脑管线
chat-core --direct     # CLI 降级模式：跳过双脑+审查，直接 ReActLoop
chat-core-qq           # QQ Bot 模式
```

| 命令 | 说明 |
|------|------|
| `/quit` `/exit` | 退出 |
| `/help` | 帮助 |
| `/mood` | 查看 AI 情绪向量、人格权重、注意力水平 |
| `/memories <关键词>` | 搜索记忆 |
| `/interests` | 查看 AI 当前关注的话题 |
| Ctrl+C | 请求停止（再按一次强制退出并保存状态） |

QQ Bot 健康检查: `http://localhost:18090/health`

## 项目结构

```
chat_core/
├── cli.py              # CLI 入口 (prompt_toolkit + rich)
├── config.py           # 配置加载 (YAML + .env + schema 校验)
├── config.yaml         # 默认配置
├── .env                # API key (gitignore)
├── prompts/            # persona / rules / tools 三层提示词
├── core/
│   ├── types.py        # 共享数据类型 (60+ dataclass/enum)
│   ├── provider.py     # LLM Provider (AsyncOpenAI, 流式+非流式)
│   ├── tools.py        # ToolRegistry (并行/串行执行)
│   ├── prompt_engine.py# 三层 Prompt 编译
│   ├── loop.py         # ReAct Loop 引擎 (think→act→observe)
│   ├── brain.py        # 四脑实现 + ActionBrainPool + Pro/Con 评估
│   ├── turn_manager.py # Turn 编排 (双脑→审查→防御→决策→元认知)
│   ├── safety.py       # 内容安全过滤
│   └── history.py      # 对话历史 (JSONL)
├── systems/            # 18 个子系统
│   ├── memory.py       # MemoryStore (SQLite FTS5 + jieba + 联锁检索)
│   ├── emotion.py      # EmotionEngine (10维×3脑 + 复合情绪)
│   ├── personality.py  # PersonalityEngine (8维→行为映射)
│   ├── attention.py    # AttentionModel (三态状态机)
│   ├── boredom.py      # BoredomDetector (指数衰减 + 主观时间)
│   ├── interest.py     # InterestModel + FuzzyParam
│   ├── review.py       # ReviewSystem (三层错误检测)
│   ├── proactive.py    # ProactiveSystem (主动发起)
│   ├── multimodal.py   # MultimodalHandler (图片降级)
│   ├── defense.py      # DefenseEngine (DENIAL/RATIONALIZE/PROJECT)
│   ├── energy.py       # EnergyBar (消耗+恢复+防御联动)
│   ├── subjective_time.py # SubjectiveClock (三维调制)
│   ├── metacognition.py   # MetacognitionEngine (审视+参数覆盖)
│   ├── relationship.py    # RelationshipEngine (4维+阶段+人格调制)
│   ├── group_dynamics.py  # GroupDynamics (群角色+氛围)
│   ├── patterns.py        # PatternDetector (习惯模式+中间态)
│   ├── intuition.py       # IntuitionEngine (L1/L2/L3 三级降级)
│   ├── creativity.py      # CreativityEngine (双路径概念发散)
│   ├── humor.py           # HumorDetector (预期违背+双关语)
│   ├── moral.py           # MoralConflictDetector + ProConAssessor
│   ├── silence.py         # SilenceClassifier (5类沉默语义)
│   ├── motivation.py      # MotivationEngine (双层动机)
│   ├── loneliness.py      # LonelinessDetector (孤独驱动)
│   ├── values.py          # ValueEngine (三层价值观树)
│   └── narrative.py       # NarrativeEngine (自我叙事)
└── qq/                 # QQ Bot 集成
    ├── protocol.py     # QQ WebSocket + REST API
    ├── sessions.py     # 用户会话 TTL
    ├── adapter.py      # QQ→双主脑+子Session (集成 18 子系统)
    ├── race_tracker.py # 竞态追踪
    ├── subconscious.py # 潜意识注入
    └── qq_bot.py       # QQ Bot 入口
```

## 技术栈

| 组件 | 选型 |
|------|------|
| 语言 | Python 3.12+ |
| LLM | DeepSeek V4 (Pro 主脑 + Flash 子Session) |
| 数据库 | SQLite + FTS5 + jieba 分词 |
| CLI | prompt_toolkit + rich |
| QQ Protocol | aiohttp + WebSocket |
| 测试 | pytest + pytest-asyncio (467 tests) |

## 测试

```bash
python -m pytest tests/ -v       # 467 tests
python tests/spec_e2e_test.py    # Spec E2E (19 scenarios)
python -m pytest tests/ --cov=chat_core --cov-report=term
```

## 设计文档

完整规格、计划、数据模型见 `specs/` 和 `docs/superpowers/`：

| Spec | 内容 | 状态 |
|------|------|:---:|
| [001-chat-core-cli](specs/001-chat-core-cli/spec.md) | CLI 功能规格 (37 FR) | ✅ |
| [002-qq-bot-integration](specs/002-qq-bot-integration/spec.md) | QQ Bot 功能规格 (25 FR) | ✅ |
| [003-memory-chain-recall](specs/003-memory-chain-recall/spec.md) | 记忆联锁 + 幂律遗忘 (24 FR) | ✅ |
| [004-design-alignment](specs/004-design-alignment/spec.md) | 设计对齐 (10 FR) | ✅ |
| Spec 005 | 复合情绪 + 防御机制 (26 FR) | ✅ |
| Spec 006 | 元认知深度 (14 FR) | ✅ |
| Spec 007 | 具身感知: 疲劳 + 主观时间 (16 FR) | ✅ |
| Spec 008 | 社交与关系 (16 FR) | ✅ |
| Spec 009 | 认知增强: 直觉+创造力+幽默+道德 (18 FR) | ✅ |
| Spec 010 | 价值体系 + 自我叙事 (14 FR) | ✅ |
| Spec 011 | 沉默语义 + 动机系统 (18 FR) | ✅ |
| [审计报告](docs/superpowers/specs/2026-07-10-spec-completeness-audit.md) | 8 Spec 实施完整度审计 | ✅ |
| [CLI 修复计划](docs/superpowers/plans/2026-07-10-spec-completeness-fix.md) | 管线断裂修复 (17 任务) | ✅ |
| [QQ Bot 集成计划](docs/superpowers/plans/2026-07-10-qq-bot-spec-integration.md) | adapter.py 子系统集成 | ✅ |
| [实施路线图](docs/superpowers/specs/IMPLEMENTATION-ROADMAP.md) | 全量 Spec 总览 + 分阶段计划 | ✅ |

## 设计原则

- **四脑分离**: 主脑不发言，子Session 是唯一的嘴
- **零外部依赖**: 不依赖任何编排工具
- **本地优先**: SQLite 单文件部署
- **CLI + QQ 双模式**: 同一人格，共享记忆和情绪

## Troubleshooting

| 问题 | 解决 |
|------|------|
| `Connection error` | 检查 `.env` 中的 API key 是否正确 |
| `ModuleNotFoundError` | 运行 `pip install -e .` |
| 记忆不工作 | 确认 `data/` 目录存在 |
| 回复为空 | 检查 `max_tokens` 是否过小 (config.yaml) |
| QQ Bot 不启动 | 确认 `QQ_BOT_APPID` / `QQ_BOT_SECRET` 已配置 |

## 贡献

本项目为个人实验项目，暂不接受外部 PR。Bug 报告和改进建议欢迎提 Issue。

## License

MIT
