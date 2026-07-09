# Quickstart: Chat Core CLI

**Feature**: `001-chat-core-cli`

---

## Prerequisites

- Python 3.12 or later
- API key for an OpenAI-compatible LLM provider (DeepSeek recommended)

---

## Setup (5 minutes)

### 1. Install

```bash
cd chat-core
pip install -e .
```

### 2. Configure

Create `chat_core/.env` with your API key:

```
DEEPSEEK_API_KEY=sk-your-key-here
```

The bundled `config.yaml` has sensible defaults. To customize, edit `chat_core/config.yaml`:

```yaml
brains:
  logic:
    api: deepseek
    model: deepseek-v4-pro
    temperature: 0.3
    max_tokens: 512
    max_context_tokens: 700000
    reasoning_effort: max
  emotion:
    api: deepseek
    model: deepseek-v4-pro
    temperature: 0.3
    max_tokens: 512
    max_context_tokens: 700000
    reasoning_effort: max
  sub_session:
    api: deepseek
    model: deepseek-v4-flash
    temperature: 0.8
    max_tokens: 512
    max_iter: 5
    max_context_tokens: 500000
    reasoning_effort: max
  action:
    api: deepseek
    model: deepseek-v4-flash
    temperature: 0.5
    max_tokens: 256
    max_concurrent: 2
    max_context_tokens: 500000
    reasoning_effort: medium
```

### 3. Run

```bash
chat-core
```

The `.env` is auto-loaded — no need to export environment variables.

---

## First Conversation

```
你: 你好，我叫小明，在北京做程序员

小深: 小明你好呀！北京的程序员——是在哪个方向？前端还是后端？

你: 还记得我的名字和工作吗？

小深: 小明，在北京做程序员。刚才你说过的～最近在忙什么项目？
```

## Key Commands

| Command | What it does |
|---------|-------------|
| `/mood` | See AI's emotion vectors, personality weights, attention levels |
| `/memories <keyword>` | Search stored memories |
| `/interests` | See topics the AI is tracking |
| `/quit` | Graceful exit (saves emotion state + closes memory) |
| Ctrl+C | Request stop (×2 for force quit with state save) |

---

## What to Expect

### The AI will:
- Remember facts about you across sessions
- Sometimes correct itself if it says something wrong
- Occasionally stay silent about things it notices but chooses not to mention
- Proactively bring up topics it finds interesting (especially after idle time)
- Adjust its tone based on your emotional state

### The AI won't:
- Respond instantly like a chatbot — pauses are intentional
- Always be "right" — it values natural conversation over perfect accuracy
- Share its inner thoughts with you (those are private)
- Need you to manage its "state" — memory and emotions are automatic

---

## Data Storage

Everything is stored locally in `./data/`:

```
data/
├── memory.db          # SQLite database (all memories, FTS5 indexed)
└── history/
    └── default.jsonl  # Full conversation history (append-only)
```

To start fresh: delete `./data/` directory.
