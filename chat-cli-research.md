# GitHub 成熟 Chat CLI 工具调研

> 调研时间：2026-07-09

---

## 概览

| 工具 | Stars | 语言 | 定位 |
|------|-------|------|------|
| [aichat](https://github.com/sigoden/aichat) | 10.2k | Rust | 通用 LLM CLI 全家桶 |
| [ShellGPT](https://github.com/ther1d/shell_gpt) | 12.2k | Python | Shell 命令生成专用 |
| [DesktopCommanderMCP](https://github.com/wonderwhy-er/DesktopCommanderMCP) | 6.4k | TypeScript | MCP 终端中间件 |
| [CarbonCode](https://github.com/Yapie0/carboncode) | 159 | TypeScript | DeepSeek 编码 Agent |

---

## 1. AIChat — 通用 LLM CLI 全家桶

**仓库**: https://github.com/sigoden/aichat  
**语言**: Rust 81.9% / HTML 13.3% / Shell 3.2%  
**许可证**: MIT + Apache 2.0  
**版本**: v0.30.0（2025-07-06）

### 交互模式
- **CMD 模式**: 一次性问答，`aichat "hello"`
- **REPL 模式**: 交互式对话，支持 tab 补全、多行输入、历史搜索、自定义 keybinding 和 prompt

### 核心对话
- **Session 管理**: 上下文感知的多轮对话，持久化存储，可随时恢复
- **Role 自定义**: 为不同场景创建角色（prompt + 模型配置），一键切换
- **Macro 宏**: 将一系列 REPL 命令组合成可重用的宏脚本

### Shell 增强
- **Shell Assistant**: 自然语言 → shell 命令，感知当前 OS 和 Shell 类型，自动适配
- 输出后可选择 Execute / Describe / Abort

### 输入方式
- stdin 管道
- 本地文件 `-f data.txt`
- 本地目录 `-f dir/`
- 远程 URL `-f https://example.com`
- 外部命令输出 `` -f `git diff` ``
- 组合多输入源

### RAG（检索增强生成）
- 导入外部文档，通过 embeddings 按需检索相关片段注入对话

### 工具与 Agent
- **Function Calling**: LLM 调用外部工具（通过 [llm-functions](https://github.com/sigoden/llm-functions) 仓库扩展）
- **MCP 协议**: 接入 Model Context Protocol 生态
- **AI Agent**: Agent = 指令(Prompt) + 工具(Functions) + 文档(RAG)，类似 CLI 版 OpenAI GPTs

### 多 Provider 支持（20+）
OpenAI / Claude / Gemini / Ollama / Groq / Azure-OpenAI / VertexAI / Bedrock / GitHub Models / Mistral / DeepSeek / AI21 / XAI Grok / Cohere / Perplexity / Cloudflare / OpenRouter / Ernie / Qianwen / Moonshot / ZhipuAI / MiniMax / Deepinfra / VoyageAI / 任何 OpenAI-Compatible API

### 本地 HTTP 服务
```bash
aichat --serve
# Chat Completions API: http://127.0.0.1:8000/v1/chat/completions
# Embeddings API:       http://127.0.0.1:8000/v1/embeddings
# LLM Playground:       http://127.0.0.1:8000/playground
# LLM Arena:            http://127.0.0.1:8000/arena?num=2
```

### 自定义主题
- 支持暗色/亮色自定义主题，高亮响应文本和代码块

---

## 2. ShellGPT — Shell 命令生成专用

**仓库**: https://github.com/ther1d/shell_gpt  
**语言**: Python 99.3%  
**许可证**: MIT  
**版本**: v1.5.1（2026-05-06）

### Shell 命令
- `--shell / -s`: 自然语言 → shell 命令，输出后可选择 Execute / Describe / Abort
- 感知 OS 和 `$SHELL`，跨平台适配（Linux/macOS/Windows, Bash/Zsh/PowerShell/CMD）
- `--no-interaction`: 非交互模式，输出命令可管道给 `pbcopy` 等工具
- `--describe-shell / -d`: 解释已有 shell 命令

### Shell 热键集成
- `--install-integration`: 安装到 `.bashrc` / `.zshrc`
- `Ctrl+L` 热键: 直接在终端 buffer 中调出 AI 建议，可编辑后执行

### 对话模式
- **Chat 模式**: `--chat <session_name>` 持续对话，迭代优化
- **REPL 模式**: `--repl` 交互式多轮，可结合 `--shell` / `--code`
- Session 持久化、列表 (`--list-chats`)、回放 (`--show-chat`)

### 代码生成
- `--code / -c`: 纯代码输出，可重定向到文件
- 支持管道输入代码并追加注释

### Role 系统
- 自定义角色 (如 `json_generator`)，控制输出格式和行为
- `--create-role`, `--list-roles`, `--show-role`

### Function Calling
- 安装默认函数 (`--install-functions`) 或自定义 Python 函数
- LLM 可自动调用系统命令，处理错误并重试

### 输入灵活性
- 管道: `git diff | sgpt "generate commit message"`
- 重定向: `sgpt "summarise" < document.txt`
- heredoc: `sgpt << EOF ... EOF`
- 直接参数: `sgpt "what is fibonacci"`

### 请求缓存
- `--cache` / `--no-cache`: 相同 query 相同参数返回本地缓存
- 不同 temperature / top-p 视为不同请求

### 运行时配置 (`~/.config/shell_gpt/.sgptrc`)
```ini
OPENAI_API_KEY=your_api_key
DEFAULT_MODEL=gpt-5.4-mini
CHAT_CACHE_LENGTH=100
CACHE_LENGTH=100
REQUEST_TIMEOUT=60
DEFAULT_COLOR=magenta
CODE_THEME=default
```

---

## 3. DesktopCommanderMCP — MCP 终端中间件

**仓库**: https://github.com/wonderwhy-er/DesktopCommanderMCP  
**语言**: TypeScript  
**许可证**: 自定义  
**版本**: v0.2.x（活跃开发中）

### 核心定位
MCP Server，给 Claude/Gemini/Codex 等 AI 客户端提供：
- 终端命令执行
- 文件系统搜索和读写
- Diff 编辑能力

### MCP 工具集
- `run_terminal_command`: 执行 shell 命令
- `read_file` / `write_file`: 文件读写
- `edit_file`: 基于 diff 的精确编辑
- `search_files`: 文件系统搜索
- `get_prompts`: 获取预设提示词模板

### 跨客户端支持
- Claude Desktop / Claude Code
- Cursor IDE
- Gemini CLI
- Codex CLI
- Warp 终端
- Cline

### 代码编辑
- 基于 diff 的 edit_block，非全量替换
- 支持模糊搜索定位文件
- Markdown 编辑器 round-trip 安全（Tiptap 实现）

### 辅助工具
- PDF 生成（自动检测/下载 Chrome）
- MCP 协议合规性修复（多客户端兼容）
- 可选遥测分析（UUID 匿名）
- Docker 部署支持

---

## 4. CarbonCode — 中国 Claude Code 替代

**仓库**: https://github.com/Yapie0/carboncode  
**语言**: TypeScript 97.3%  
**许可证**: MIT  
**版本**: v0.2.9

### 核心定位
中国第一个基于 DeepSeek 的代码开发工具
- Token 成本节省 90% 以上（相比 Claude）
- 能力接近 Claude Sonnet 4.6

### 子命令

| 命令 | 用途 |
|------|------|
| `carboncode` | 在当前项目启动编码智能体 |
| `carboncode code [dir]` | 在指定目录启动编码智能体 |
| `carboncode chat` | 纯聊天模式（不带文件系统和 shell 工具） |
| `carboncode run "task"` | 非交互式执行一次任务 |
| `carboncode init [dir]` | 分析项目生成 `CARBON.md` 项目指南 |
| `carboncode doctor` | 本地环境健康检查 |
| `carboncode update` | 检查并安装最新 CLI 包 |

### 多 Agent 协作
- 支持 Claude / Codex 等多 Agent 并行协作
- 自动任务拆分、自动开发

### MCP 测试
- 内置 MCP 协议支持

### 模型预设
- `flash`: `deepseek-v4-flash`（快速）
- `pro`: `deepseek-v4-pro`（强力）
- `auto`: 默认 Flash 启动，困难回合自动升级到 Pro

### 配置
- 用户配置: `~/.carboncode/config.json`
- 项目规则: `AGENTS.md` / `CARBON.md`
- 环境变量: `DEEPSEEK_API_KEY`

### 其他特性
- TUI 终端界面（基于 Ink/React）
- Ctrl+R 历史搜索
- PR Review 自动审查
- 桌面端 GUI（独立仓库）

---

## 功能矩阵对比

| 功能 | aichat | ShellGPT | DesktopCommander | CarbonCode |
|------|:---:|:---:|:---:|:---:|
| REPL 交互 | ✅ | ✅ | ❌ | ✅ |
| CMD 一次性 | ✅ | ✅ | — | ✅ (`run`) |
| Session 管理 | ✅ | ✅ | — | ✅ |
| Role 自定义 | ✅ | ✅ | — | ❌ |
| Shell 命令生成 | ✅ | ✅ | — | ✅ |
| Shell 热键集成 | ❌ | ✅ | ❌ | ❌ |
| 代码生成 | ❌ | ✅ | — | ✅ |
| RAG | ✅ | ❌ | ❌ | ❌ |
| Function Calling | ✅ | ✅ | — | ✅ |
| MCP | ✅ | ❌ | ✅ | ✅ |
| AI Agent | ✅ | ❌ | — | ✅ |
| 多 Provider | 20+ | OpenAI/Ollama | 不限 | DeepSeek |
| 本地 HTTP 服务 | ✅ | ❌ | ❌ | ❌ |
| 终端文件编辑 | ❌ | ❌ | ✅ | ✅ |
| 多 Agent 协作 | ❌ | ❌ | ❌ | ✅ |
| Macro 宏 | ✅ | ❌ | ❌ | ❌ |
| 请求缓存 | ❌ | ✅ | ❌ | ❌ |

---

## 关键洞察

1. **aichat 功能最全面**: 覆盖了 REPL、Session、Role、RAG、Tool/Agent、多 Provider、Macro、HTTP 服务等几乎所有维度。Rust 实现，性能好，是架构参考的最佳模板。

2. **ShellGPT 在终端集成最深**: `Ctrl+L` 热键直接将 AI 建议注入终端 buffer，这是其他工具没有的体验。Python 实现，代码量小，适合快速理解 chat CLI 核心逻辑。

3. **DesktopCommander 是基础设施**: 不是面向用户的 chat CLI，而是 MCP Server 中间件，让已有 AI 客户端获得终端控制能力。适用于需要扩展 coding agent 工具链的场景。

4. **CarbonCode 是国内替代方案**: 基于 DeepSeek，成本极低。功能上对标 Claude Code（自动任务拆分、多 Agent 协作、TUI），适合国内开发场景。

---

## 双主脑 + 共享子 Agent CLI 架构研究

> 目标：为 chat-engine 设计 CLI 交互工具，默认两个父 Session 并行（双主脑），共享子 Agent 池（多副脑）
> 研究时间：2026-07-09

### 1. 背景：chat-engine 现有架构

chat-engine 当前是 QQ Bot 引擎，核心架构如下：

```
用户消息
    │
    ▼
 orchestrator → reply_scheduler (优先级队列)
    │
    ▼
 engine.chat() → fast 脑秒回
    │
    ▼
 brain.evaluate() → [rational 脑 + emotional 脑 并行评估] → 融合决策 → 追答
    │
    ▼
 agent_loop.run_agent_loop() (SPEC 007, 实验性)
    ├── fast 脑: think → send_reply → wait → send_reply → done  (主发言人)
    ├── rational 脑: 观察 fast 消息 → 事实审查 → 追加修正  (非阻塞观察者)
    └── emotional 脑: 观察 fast 消息 → 语气审查 → 追加缓和

共享组件:
- ToolPool (工具互斥锁池, 支持 dominance 抢占)
- MessageQueue (消息队列, 按序消费)
- BrainSessionManager (4 脑独立上下文, 跨脑广播)
```

**关键约束**：
- 工具只有 6 个：`send_reply`、`search`、`recall`、`wait`、`classify_image`、`done`
- 不使用原生 function calling（step 模型不支持），LLM 输出 JSON 文本由 `parse_json_block` 解析
- fast 脑负责主动回复，rational/emotional 脑是被动观察者
- 三脑共享同一个 ToolPool，互斥锁防并发冲突
- 消息队列统一消费（防止刷屏），间隔至少 `AGENT_LOOP_SEND_INTERVAL`

### 2. kimi-debug-tunnel：已存在的 Session 编排基础设施

用户本机的 `D:/code/kimi-debug-tunnel/` 项目是一个完整的 **MCP 服务器**（28 个 MCP 工具），用于编排 Kimi Code CLI session。它就是现成的"子 Agent 池管理器"。

#### 2.1 核心架构

```
Kimi Code CLI（统筹 Session）
    │  MCP stdio JSON-RPC（28 个工具）
    ▼
┌─ kimi-debug-tunnel ────────────────────────────────────┐
│                                                          │
│  MCP Server (mcp-server.ts)                              │
│  ├─ create_session(cwd, permission_mode, model,          │
│  │    thinking, policy, memory_level, from_session)      │
│  ├─ execute_prompt(session_id, prompt, auto_mode)        │
│  ├─ poll_session(session_id) → active/swarm/awaiting/done│
│  ├─ watch_session(session_id) → 后台监听完成             │
│  ├─ memory_set/get/list/delete/status/archive            │
│  ├─ run_flow(cwd, steps) → 多步流程自动编排              │
│  └─ execute_workflow(template_name) → 模板驱动           │
│                                                          │
│  WireClient (wire-client.ts)                             │
│  ├─ REST API → Kimi Server (POST /api/v1/sessions/...)  │
│  ├─ WebSocket → 实时状态推送（eliminating polling）      │
│  └─ 心跳探测 + 自动重连（每 10s ping）                   │
│                                                          │
│  MemoryStore (memory-store.ts)                           │
│  ├─ SQLite (node:sqlite, 零依赖)                         │
│  ├─ L1: 项目知识库 (.kimi-tunnel/memory.db)              │
│  ├─ L2: Session 上下文 (session:<id>/*)                  │
│  └─ 自动注入: create_session 时按 memory_level 注入索引  │
│                                                          │
│  PolicyEngine                                            │
│  ├─ read-only / safe-edit / full-access                  │
│  └─ 自定义 YAML 策略                                     │
│                                                          │
│  WorkflowEngine                                          │
│  └─ 自适应工作流：创建 session → 逐步驱动 → 阻塞处理     │
│                                                          │
└─────────────┬────────────────────────────────────────────┘
              │ Bearer Token REST API
              ▼
        Kimi Server (kimi web --port 5494)
```

#### 2.2 关键能力：Session 即子 Agent

kimi-debug-tunnel 的核心抽象是：**每个 Kimi Code session 就是一个子 Agent**。创建 session → 发送 prompt → 等待完成 → 读取结果，这本质上是子 Agent 的完整生命周期。

```python
# 伪代码：用 kimi-debug-tunnel 作为子 Agent 基础设施
async def spawn_subagent(task: str, cwd: str, model: str = "flash") -> str:
    # 1. 创建 Kimi Code session（子 Agent）
    sid = await mcp.create_session(cwd=cwd, permission_mode="auto",
                                    model=model, thinking="high",
                                    policy="safe-edit",
                                    memory_level="standard")
    
    # 2. 发送任务（即发即返）
    await mcp.execute_prompt(session_id=sid, prompt=task, auto_mode=True)
    
    # 3. 后台轮询等待完成
    while True:
        status = await mcp.poll_session(sid)
        if status == "idle" or status == "done":
            break
        await asyncio.sleep(2)
    
    # 4. 读取结果
    records = await mcp.list_io_records(sid)
    return records[-1]["assistant_text"]
```

#### 2.3 对比 CarbonCode：为什么用 session 而非进程内子循环

| 维度 | CarbonCode spawn_subagent | kimi-debug-tunnel session |
|------|------|------|
| **隔离方式** | 同进程 new CacheFirstLoop | 独立 OS 级别 Kimi Code session |
| **工具能力** | fork 父 Registry，有限工具集 | 完整 Kimi Code CLI 工具集（28+ tools） |
| **上下文** | 独立 ImmutablePrefix + Session | 独立 wire.jsonl + 128K 上下文窗口 |
| **注意力管理** | 无（依赖父 loop 取消） | 退役机制（~360K 拐点检测 + 自动重建） |
| **冷启动** | 零上下文，仅 prompt | memory_level 自动注入项目知识库 |
| **模型选择** | flash/pro（硬编码） | 任意 Kimi Code 支持的模型 |
| **权限控制** | 无 | PolicyEngine（read-only/safe-edit/full-access + YAML） |
| **工作流** | 无 | WorkflowEngine（模板驱动多步编排） |
| **共享记忆** | 无（仅结果缓存） | SQLite 三层记忆（L1 项目 / L2 Session / L3 向量） |
| **续接** | resume_session | from_session（自动拉取 handoff） |
| **语言** | TypeScript (Node.js) | MCP stdio（语言无关，Python 可调用） |

**结论**：kimi-debug-tunnel 已经是比 CarbonCode 更强大的子 Agent 基础设施。chat-engine CLI 不需要重新实现子 Agent 池——直接通过 MCP 调用 kimi-debug-tunnel 即可。

### 3. 重构方案：双主脑 + Session 子 Agent

#### 3.1 总体架构

```
┌─ chat-engine CLI (Python) ──────────────────────────────┐
│                                                           │
│  ┌─ 主脑 A (理性/系统视角) ───────────────────────────┐  │
│  │  BrainAgentLoop (复用 agent_loop.py 模式)            │  │
│  │  ├─ 独立 BrainSession (复用 brain_session.py)       │  │
│  │  ├─ LLM: DeepSeek Pro (推理强)                      │  │
│  │  ├─ System Prompt: 系统架构分析师                    │  │
│  │  ├─ 工具: read_file, grep, edit, write, bash,        │  │
│  │  │       spawn_subagent → kimi-debug-tunnel MCP      │  │
│  │  └─ 产出: 代码修改 / 架构分析 / bug 修复              │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                           │
│  ┌─ 主脑 B (感性/用户视角) ───────────────────────────┐  │
│  │  BrainAgentLoop (复用 agent_loop.py 模式)            │  │
│  │  ├─ 独立 BrainSession                               │  │
│  │  ├─ LLM: DeepSeek Flash (便宜，读多写少)             │  │
│  │  ├─ System Prompt: 用户体验审查者                    │  │
│  │  ├─ 工具: 同上 + spawn_subagent                      │  │
│  │  └─ 产出: 文档反馈 / UX 建议 / 边界发现              │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                           │
│  ┌─ SubagentSessionManager (共享) ────────────────────┐  │
│  │  封装 kimi-debug-tunnel MCP 调用                      │  │
│  │  ├─ create_session(cwd, model, policy)                │  │
│  │  ├─ execute_prompt(session_id, task)                  │  │
│  │  ├─ poll_until_done(session_id, timeout)              │  │
│  │  ├─ 并发槽位 (asyncio.Semaphore, max 4)               │  │
│  │  ├─ 文件冲突矩阵 (file_path → session_id)             │  │
│  │  ├─ 结果缓存 (task_hash → result)                     │  │
│  │  └─ 预算追踪 (累计 token/cost)                        │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                           │
│  ┌─ 协调层 ──────────────────────────────────────────┐   │
│  │  TurnManager                                         │  │
│  │  ├─ 用户消息 → 双主脑广播                            │  │
│  │  ├─ 双主脑并行启动 + asyncio.gather                  │  │
│  │  ├─ 跨脑信号通知（observe_event，来自 SPEC 008）      │  │
│  │  ├─ 产出汇聚 + 去重 + 冲突检测                        │  │
│  │  └─ CLI 渲染（流式输出 + 来源标注）                  │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                           │
└─────────────┬─────────────────────────────────────────────┘
              │ MCP stdio (JSON-RPC)
              ▼
    kimi-debug-tunnel (Node.js, 28 MCP tools)
              │
              ▼
    Kimi Server (kimi web --port 5494)
```

#### 3.2 主脑运行时模型（精华版）

每个主脑复用 chat-engine 的 `BrainAgentLoop` 模式，但改造为代码编辑场景：

```python
class BrainAgentLoop:  # 基于 agent_loop.py 改造
    """单脑 Agent 循环 — think → act → observe_peer 循环。"""
    
    async def run_turn(self, user_message: str) -> TurnResult:
        while self._should_continue():
            # 1. think: LLM 决策下一步
            action = await self._think()
            
            # 2. act: 执行工具
            if action.name == "spawn_subagent":
                result = await self.subagent_pool.spawn(
                    parent=self.brain_type,
                    task=action.args["task"],
                    model=action.args.get("model", "flash"),
                )
            else:
                result = await self._execute_tool(action)
            
            # 3. 产出通知协调层（可能触发另一脑的 observe_event）
            self._notify_output(action, result)
            
            # 4. peer_signal: 另一脑有新产出 → 可中断当前动作
            if self._peer_signal.is_set():
                self._peer_signal.clear()
                self._incorporate_peer_output()
        
        return self._compile_result()
```

**System Prompt 差异**（~200 token，注入独立 BrainSession）：

| | 主脑 A | 主脑 B |
|------|------|------|
| 角色 | "你是代码审计者。关注：正确性、安全性、性能、边界条件。" | "你是用户体验审查者。关注：可读性、交互逻辑、边缘场景覆盖。" |
| spawn 偏好 | 探索型（read 多 write 少） | 批判型（找漏洞、找不一致） |
| 冲突策略 | 代码正确性优先 | 用户体验优先（A 可否决） |

#### 3.3 共享子 Agent Session 池

```python
class SubagentSessionPool:
    """跨主脑共享的子 Agent session 管理器。
    
    封装 kimi-debug-tunnel MCP 调用，提供：
    - 并发槽位限制（默认 4）
    - 文件冲突检测
    - 结果缓存去重
    - 注意力监控 + 退役通知
    """
    
    def __init__(self, mcp_client, max_concurrent: int = 4):
        self._mcp = mcp_client
        self._sem = asyncio.Semaphore(max_concurrent)
        self._file_locks: dict[str, str] = {}  # path → session_id
        self._cache: dict[str, SubagentResult] = {}
        self._active: dict[str, SessionHandle] = {}
    
    async def spawn(
        self,
        parent: str,           # "brain_a" | "brain_b"
        task: str,
        model: str = "flash",
        policy: str = "safe-edit",
        allowed_files: list[str] | None = None,
    ) -> SubagentResult:
        async with self._sem:
            # 1. 去重检查
            cache_key = f"{task[:200]}|{model}"
            if cache_key in self._cache:
                return self._cache[cache_key]
            
            # 2. 文件冲突检查
            if allowed_files:
                conflicts = [f for f in allowed_files if f in self._file_locks]
                if conflicts:
                    raise FileConflictError(conflicts)
                for f in allowed_files:
                    self._file_locks[f] = parent
            
            try:
                # 3. 创建 Kimi Code session
                sid = await self._mcp.create_session(
                    cwd=self._project_root,
                    permission_mode="auto",
                    model=f"deepseek/{model}",
                    thinking="high",
                    policy=policy,
                    memory_level="standard",  # 自动注入项目知识
                )
                
                # 4. 发送任务（即发即返）
                await self._mcp.execute_prompt(
                    session_id=sid,
                    prompt=task,
                    auto_mode=True,
                )
                
                # 5. 后台轮询等待完成
                result_text = await self._poll_until_done(sid, timeout=300)
                
                # 6. 缓存结果
                result = SubagentResult(success=True, output=result_text, session_id=sid)
                self._cache[cache_key] = result
                return result
                
            finally:
                if allowed_files:
                    for f in allowed_files:
                        self._file_locks.pop(f, None)
```

#### 3.4 双主脑的"活灵活现"交互——速度、质量、灵活度的三角平衡

这是本方案区别于所有现有工具的**核心创新**。三个维度的拆解：

**速度**：
- 双主脑**并行启动**（`asyncio.gather`），不等 A 跑完再跑 B
- 主脑 A 使用 Pro 模型但有 spawn 子 Agent 的并行加速（4x 子 Agent 并行）
- 主脑 B 使用 Flash 模型（便宜 12x，延迟更低），用于快速审查
- 子 Agent 通过 kimi-debug-tunnel 的 WebSocket 推送实时反馈进度
- CLI 流式输出（先完成的先显示，不等全部）

**质量**：
- A 和 B 的**视角互补**：系统 vs 用户、理性 vs 感性
- B 的批判性审查（类似 chat-engine 的 rational 脑观察 fast 脑）
- 子 Agent session 的注意力管理：~360K 拐点自动退役 + 新建
- 冲突检测机制：代码修改冲突标记、结论矛盾升级
- 共享记忆（memory_set/get）：双脑通过 L1 项目知识库共享上下文

**灵活度**：
- 用户可在 CLI 中**动态指定**哪个脑主导哪个任务
- 单脑模式（简单问题只启动 A，B 休眠）
- 双脑模式（复杂问题 A+B 并行 + 汇聚）
- 子 Agent session **可续接**（`from_session`）：长任务跨 session 不丢上下文
- Policy 动态切换：探索用 `read-only`，修改用 `safe-edit`

```
灵活度调节:

简单问题 ("修复这个 typo")
  → 单脑 A（Flash, read-only 直接编辑）
  → ~3s 完成

中等复杂 ("这个函数的边界条件有问题吗？")
  → 双脑并行: A 审查代码逻辑, B 审查调用方
  → 各 spawn 1-2 个探索子 Agent
  → ~15s 完成，产出带证据链

大型重构 ("重构 user 模块，拆成 3 个文件")
  → 双脑分工: A 负责拆分逻辑, B 负责接口兼容
  → A spawn 3 个 session（各自的 spec 审查）
  → B spawn 2 个 session（调用方兼容性检查）
  → ~60s 完成，产出包含 diff + 审查报告
```

#### 3.5 与 chat-engine 现有模块的重用

| chat-engine 模块 | 重用方式 |
|------|------|
| `brain_session.py` | ✅ 直接复用——每脑独立 BrainSession 管理 |
| `agent_loop.py` (BrainAgentLoop) | ✅ 核心循环框架复用——改造为代码编辑场景 |
| `agent_loop.py` (ToolPool) | ✅ 工具互斥锁机制复用——文件锁升级版 |
| `agent_loop.py` (跨脑广播) | ✅ broadcast_cross_brain → observe_event 信号 |
| `engine.py` (LLM 调用) | ✅ 复用 AsyncOpenAI 客户端 + system prompt 构建 |
| `emotion.py` / `attention.py` / `interest.py` | ❌ CLI 工具不需要情绪系统 |
| `memory_store.py` | ⚠️ 可被 kimi-debug-tunnel 的 MemoryStore 替代 |
| `tools.py` (send_reply/search/recall) | ❌ 替换为代码工具集 + MCP 调用 |

### 4. 实现路线图

```
Phase 1: 单脑 CLI + kimi-debug-tunnel 集成
  ├── Python asyncio MCP client（调用 kimi-debug-tunnel）
  ├── 单脑 BrainAgentLoop（think→act，工具: 直接操作 + spawn subagent）
  ├── SubagentSessionPool（封装 MCP 调用）
  └── 基础 CLI 交互（readline + 流式输出）

Phase 2: 双主脑并行
  ├── TurnManager（双脑启动 + 汇聚）
  ├── 跨脑 observe_event 信号（复用 SPEC 008）
  ├── 文件冲突矩阵 + 结果缓存
  └── CLI 渲染（双脑输出分栏或标注来源）

Phase 3: 智能协调
  ├── 任务复杂度自动分级（单脑 vs 双脑）
  ├── 子 Agent session 注意力监控 + 退役
  ├── 预算共享 + 动态 spawn 限制
  └── Personality 注入（A: 系统视角, B: 用户视角）
```

### 5. 架构对比

| | chat-engine 现状 | CarbonCode | Kimi CLI AgentSwarm | **本方案** |
|------|:---:|:---:|:---:|:---:|
| 父 Agent | 1 (fast 主导) | 1 | 1 | **2 并行** |
| 子 Agent | ❌ | ✅ 进程内 loop | ✅ 同进程 Agent | **✅ Kimi session（OS级隔离）** |
| 脑/Agent 关系 | 并行评估 | 层级委派 | 批量委派 | **并行评估 + 层级委派** |
| 上下文管理 | 4 脑独立 Session | 独立 Prefix | 零上下文 | **独立 Session + memory 注入** |
| 注意力管理 | ❌ | ❌ | ❌ | **✅ ~360K 拐点退役** |
| 权限控制 | ❌ | ❌ | ❌ | **✅ PolicyEngine (YAML)** |
| 共享记忆 | 跨脑广播 | ❌ | ❌ | **✅ SQLite 三层记忆** |
| 工作流 | ❌ | ❌ | ❌ | **✅ WorkflowEngine** |

### 6. 开放问题

1. **Python MCP client 实现？** chat-engine (Python) 如何调用 kimi-debug-tunnel (Node.js MCP server)？方案：Python `asyncio` subprocess 启动 `node dist/index.js` 作为 MCP stdio transport，或直接 HTTP 调用 Kimi Server REST API 绕过 MCP。
2. **双脑人格注入深度？** 仅 System Prompt 差异（~200 token），还是独立 persona 配置？
3. **FLOW 触发条件？** 何时自动启用双脑 vs 单脑？方案：复杂度判定器——文件数 > 5 或用户显式 `--dual` flag。
4. **冲突时用户交互？** diff 冲突时 CLI 如何呈现？方案：类似 `git merge` 的冲突标记 + 用户选择。
