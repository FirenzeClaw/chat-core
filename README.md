# chat-core — 四脑模型 AI 聊天 CLI

一个有记忆、有情绪、有判断力的终端 AI 伙伴。

[![Python](https://img.shields.io/badge/python-3.12+-blue)](https://python.org)
[![Tests](https://img.shields.io/badge/tests-84%20passed-green)](#)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

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

```
用户消息
  │
  ├─ 逻辑主脑 (recall) ──→ 事实检索 ──┐
  ├─ 情感主脑 (recall) ──→ 情感关联 ──┤
  │         ↓ 合并注入                ↓
  │     ┌──────────────────────────────┐
  │     │  子Session (唯一发言者)       │
  │     │  ① recall → 查记忆           │
  │     │  ② send_reply → 说一段       │
  │     │  ③ wait → 停顿              │
  │     │  ④ send_reply → 接着说      │
  │     │  ⑤ inner_thoughts → 结束     │
  │     └──────────────────────────────┘
  │         ↓ 双脑审查
  ├─ 逻辑主脑: 事实审查 ──→ 权重投票
  └─ 情感主脑: 语气审查 ──→ 纠正/沉默/拧巴
```

## 特性

- **四脑架构**：逻辑主脑 + 情感主脑 + 子Session（嘴）+ 行为脑，各司其职
- **人格系统**：8 维个性权重（好奇心、社交性、共情力...）+ 10 维情绪引擎
- **记忆系统**：SQLite FTS5 + jieba 中文分词，跨 session 持久化
- **自我审查**：三层错误检测 → 权重投票 → 纠正或沉默
- **主动发起**：无聊检测 + 兴趣追踪 → 空闲时主动开聊
- **内心戏**：每轮发言后有私密反思（用户不可见）

## 安装

```bash
# Python 3.12+
git clone <repo-url>
cd chat-core
pip install -e .
```

## 配置

创建 `chat_core/.env`：

```
DEEPSEEK_API_KEY=sk-your-key-here
```

支持所有 OpenAI-compatible API，改 `chat_core/config.yaml` 中的 `base_url` 即可。

## 使用

```bash
chat-core
```

| 命令 | 说明 |
|------|------|
| `/quit` `/exit` | 退出 |
| `/help` | 帮助 |
| `/mood` | 查看 AI 情绪向量、人格权重、注意力水平 |
| `/memories <关键词>` | 搜索记忆 |
| `/interests` | 查看 AI 当前关注的话题 |
| Ctrl+C | 请求停止（再按一次强制退出并保存状态） |

## 项目结构

```
chat_core/
├── cli.py              # CLI 入口 (prompt_toolkit + rich)
├── config.py           # 配置加载 (YAML + .env + schema 校验)
├── config.yaml         # 默认配置（模型、脑参数、系统参数）
├── .env                # API key（gitignore）
├── prompts/            # persona / rules / tools 三层提示词
├── core/
│   ├── types.py        # 共享数据类型 (40+ dataclass/enum)
│   ├── provider.py     # LLM Provider (AsyncOpenAI)
│   ├── tools.py        # ToolRegistry (并行/串行执行)
│   ├── prompt_engine.py# 三层 Prompt 编译
│   ├── loop.py         # ReAct Loop 引擎 (think→act→observe)
│   ├── brain.py        # 四脑实现 + ActionBrainPool
│   ├── turn_manager.py # Turn 编排 (双脑→审查→决策)
│   ├── safety.py       # 内容安全过滤
│   └── history.py      # 对话历史 (JSONL)
└── systems/
    ├── memory.py       # MemoryStore (SQLite FTS5 + jieba)
    ├── emotion.py      # EmotionEngine (10维×3脑)
    ├── personality.py  # PersonalityEngine (8维→行为映射)
    ├── attention.py    # AttentionModel (focus + dominance)
    ├── boredom.py      # BoredomDetector (指数衰减)
    ├── interest.py     # InterestModel + FuzzyParam
    ├── review.py       # ReviewSystem (3层错误检测)
    ├── proactive.py    # ProactiveSystem (主动发起/意图)
    └── multimodal.py   # MultimodalHandler (图片降级)
```

## 技术栈

| 组件 | 选型 |
|------|------|
| 语言 | Python 3.12+ |
| LLM | OpenAI-compatible API (DeepSeek V4) |
| 数据库 | SQLite + FTS5 + jieba 分词 |
| CLI | prompt_toolkit + rich |
| 搜索 | duckduckgo-search (可选) |
| 测试 | pytest + pytest-asyncio (84 tests) |

## 开发

```bash
# 运行全部测试
python -m pytest tests/ -v

# 运行 Spec E2E 测试
python tests/spec_e2e_test.py

# 安装开发依赖
pip install -e ".[dev]"
```

## 设计原则

- **四脑分离**：主脑不发言，子Session 是唯一的嘴
- **零外部依赖**：不依赖任何 MCP 服务器或编排工具
- **本地优先**：SQLite 单文件部署，单用户模式
- **渐进式**：从基础对话到完整人格，4 个 Phase 逐步实现

## 设计文档

完整规格说明见 [`specs/001-chat-core-cli/`](specs/001-chat-core-cli/)：

| 文档 | 内容 |
|------|------|
| [spec.md](specs/001-chat-core-cli/spec.md) | 功能规格 (37 FR, 10 SC) |
| [plan.md](specs/001-chat-core-cli/plan.md) | 实施计划 (4 Phase) |
| [tasks.md](specs/001-chat-core-cli/tasks.md) | 任务列表 (74 tasks) |
| [data-model.md](specs/001-chat-core-cli/data-model.md) | 数据模型 + 状态机 |

## Troubleshooting

| 问题 | 解决 |
|------|------|
| `Connection error` | 检查 `.env` 中的 API key 是否正确，网络是否能访问 `api.deepseek.com` |
| `ModuleNotFoundError` | 运行 `pip install -e .` |
| 记忆不工作 | 确认 `data/` 目录存在，`memory.db` 已创建 |
| 回复为空 | 检查 `max_tokens` 是否过小（config.yaml 中每个 brain 的配置） |

## License

MIT

