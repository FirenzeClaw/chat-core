# CLI Contract: Chat Core

**Feature**: `001-chat-core-cli`
**Created**: 2026-07-09

---

## Entry Point

```bash
chat-core [COMMAND] [OPTIONS]
```

### Commands

| Command | Description |
|---------|-------------|
| `chat-core chat` | Start interactive chat session (default) |
| `chat-core --help` | Show help |

### Options

| Flag | Description | Default |
|------|-------------|---------|
| `-c, --config PATH` | Path to config file | `./config.yaml` |
| `-m, --model MODEL` | Override LLM model | Config value |
| `--no-color` | Disable colored output | false |
| `-v, --verbose` | Enable debug logging | false |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Normal exit (`/quit` or Ctrl+C ×2) |
| 1 | Configuration error (missing API key, invalid config) |
| 2 | Runtime error (unhandled exception) |

---

## Interactive Commands

Within a chat session, the following slash commands are available:

| Command | Description |
|---------|-------------|
| `/quit` or `/exit` | Gracefully exit (saves state) |
| `/clear` | Clear current session context |
| `/mood` | Display AI's current emotional state |
| `/memories [query]` | Search stored memories |
| `/help` | Show available commands |

---

## Configuration Contract

File: `config.yaml` (see `chat-core-design.md` §11.3, Gap 15 for full schema)

### Required Fields

```yaml
apis:
  <provider_name>:
    provider: string        # Provider identifier
    base_url: string        # API endpoint URL
    api_key: string         # API key (supports ${ENV_VAR} substitution)

brains:
  logic:    { api, model, temperature, max_tokens }
  emotion:  { api, model, temperature, max_tokens }
  sub_session: { api, model, temperature, max_tokens, max_iter }
  action:   { api, model, temperature, max_tokens, max_concurrent }
```

### Environment Variable Substitution

Any config value containing `${VAR_NAME}` is replaced at load time:

```yaml
api_key: ${DEEPSEEK_API_KEY}
```

Priority: environment variable > config file > default value.

---

## Tool Contracts (Function Calling Schemas)

### Sub-Session Tools

#### send_reply
```json
{
  "name": "send_reply",
  "description": "Send a message to the chat. Returns {'sent': true}. Can call multiple times.",
  "parameters": {
    "text": "string (≤ 500 chars) — natural conversational text"
  }
}
```

#### wait
```json
{
  "name": "wait",
  "description": "Natural pause simulating thinking/typing gap.",
  "parameters": {
    "seconds": "number (0.5 - 5.0)"
  }
}
```

#### inner_thoughts
```json
{
  "name": "inner_thoughts",
  "description": "Post-reply inner reflection. Call ONCE after all send_reply. NOT visible to user.",
  "parameters": {
    "text": "string — inner thoughts, feelings, self-reflection, and optional '我是否想要做什么: ...'"
  }
}
```

#### done
```json
{
  "name": "done",
  "description": "Confirm end of turn. MUST call after inner_thoughts.",
  "parameters": {}
}
```

#### recall (sub read-only)
```json
{
  "name": "recall",
  "description": "Search memories. Read-only. Restricted namespaces.",
  "parameters": {
    "query": "string — search keywords or question"
  }
}
```

### Logic Brain Tools

#### recall
```json
{
  "name": "recall",
  "description": "Deep memory search for facts, user profile, conversation history.",
  "parameters": {
    "query": "string"
  }
}
```

#### memory_save
```json
{
  "name": "memory_save",
  "description": "Write structured memory entry.",
  "parameters": {
    "namespace": "string (e.g., user/facts)",
    "key": "string",
    "value": "object (JSON)",
    "layer": "\"gist\" | \"detail\" (default: gist)"
  }
}
```

#### memory_link
```json
{
  "name": "memory_link",
  "description": "Create relation between memory entries.",
  "parameters": {
    "from_key": "string",
    "to_key": "string",
    "relation": "\"extends\" | \"contradicts\" | \"related_to\""
  }
}
```

#### inject_to_sub
```json
{
  "name": "inject_to_sub",
  "description": "Inject context and direction into sub-session.",
  "parameters": {
    "context": "string — conversation context summary",
    "direction": "string — reply direction guidance",
    "relevant_memories": "string[] (optional)",
    "avoid_phrases": "string[] (optional)"
  }
}
```

### Emotion Brain Tools

Same as logic brain, except:
- `memory_save` → `memory_tag` (adds emotional labels to existing entries)
- `memory_link` not available

#### memory_tag
```json
{
  "name": "memory_tag",
  "description": "Append emotional tags to existing memory. Does NOT create new entries.",
  "parameters": {
    "namespace": "string",
    "key": "string",
    "tags": "object — emotion label key-value pairs"
  }
}
```

### Action Brain Tools

#### search
```json
{
  "name": "search",
  "description": "Search the internet. Rate limited: 5/60s, 2s cooldown.",
  "parameters": {
    "query": "string"
  }
}
```

#### web_fetch
```json
{
  "name": "web_fetch",
  "description": "Fetch web page content. http/https only, ≤ 100KB, 10s timeout.",
  "parameters": {
    "url": "string (uri)"
  }
}
```

#### recall (action)
```json
{
  "name": "recall",
  "description": "Read-only memory search.",
  "parameters": {
    "query": "string"
  }
}
```

---

## Internal Event Bus Contract

Events published on the internal message bus during turn processing.

| Event | Publisher | Payload | Consumer |
|-------|-----------|---------|----------|
| `logic_recall` | LogicBrain | `{facts, entities}` | EmotionBrain (cross-reference) |
| `emotion_recall` | EmotionBrain | `{feelings, tags}` | LogicBrain (cross-reference) |
| `logic_review` | LogicBrain | `{errors, corrections}` | TurnManager (weight calc) |
| `emotion_review` | EmotionBrain | `{tone_issues, tags}` | TurnManager (weight calc) |
| `logic_inspiration` | LogicBrain (idle) | `{topic_ideas}` | BoredomDetector |
| `emotion_inspiration` | EmotionBrain (idle) | `{mood_read, suggestion}` | BoredomDetector |
| `conversation_ended` | TurnManager | `{turn_id, logic_eval, emotion_weight}` | BoredomDetector, EmotionTick |
| `correction_issued` | TurnManager | `{turn_id, correction}` | MemoryStore (archive) |
| `sub_session_retired` | TurnManager | `{old_id, new_id, summary}` | — (log) |
