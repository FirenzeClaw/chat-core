# Data Model: Chat Core CLI

**Feature**: `001-chat-core-cli`
**Created**: 2026-07-09

---

## Entity Overview

```
┌──────────┐     ┌─────────────────┐     ┌──────────────────┐
│  User    │────→│ ConversationTurn │←────│ ReviewResult     │
└──────────┘     └─────────────────┘     └──────────────────┘
                        │                        │
                        ↓                        ↓
                 ┌──────────────┐        ┌───────────────┐
                 │ InnerThought │        │ CorrectionCmd │
                 └──────────────┘        └───────────────┘

┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│ MemoryEntry  │←───→│ MemoryLink   │     │ EmotionState │
└──────────────┘     └──────────────┘     └──────────────┘

┌──────────────┐     ┌──────────────┐
│ Personality  │     │ Intent       │
└──────────────┘     └──────────────┘
```

---

## Core Entities

### ConversationTurn

Represents one round of user message → AI response.

| Field | Type | Description |
|-------|------|-------------|
| turn_id | string | Unique ID (e.g., "turn_042") |
| timestamp | datetime | When the turn started |
| user_message | string | Raw user input |
| logic_injection | dict | Logic brain's inject_to_sub output |
| emotion_injection | dict | Emotion brain's inject_to_sub output |
| sub_session_id | string | Sub-session that handled this turn |
| reply_segments | list[ReplySegment] | Array of send_reply outputs |
| inner_thoughts_raw | string \| null | Sub-session's raw inner_thoughts text |
| inner_thoughts_parsed | InnerThought \| null | Logic brain's parsed result |
| review | ReviewResult \| null | Post-hoc review from both brains |
| correction | CorrectionCmd \| null | If a correction was issued |
| status | TurnStatus | "active" → "sub_session" → "review" → "correcting" → "done" |

### ReplySegment

| Field | Type | Description |
|-------|------|-------------|
| text | string | Message text (≤ 500 chars) |
| wait_before | float \| null | Pause duration before this segment (seconds) |
| timestamp | datetime | When sent |

### InnerThought

| Field | Type | Description |
|-------|------|-------------|
| raw | string | Original text from sub-session |
| feeling | FeelingLabel | Primary emotion + valence (-1 to 1) |
| reflection | string | Self-reflection text |
| summary | string | 1-line summary of thought |
| topics | list[string] | Extracted topic tags |
| user_read | UserMoodRead | AI's reading of user's mood |
| self_assessment | string | Self-evaluation (e.g., "真诚度: 9/10") |
| intent | Intent \| null | Extracted action intent |

### FeelingLabel

| Field | Type | Description |
|-------|------|-------------|
| primary | string | Dominant emotion (共情/好奇/担忧/...) |
| valence | float | -1 (negative) to 1 (positive) |

### UserMoodRead

| Field | Type | Description |
|-------|------|-------------|
| mood | string | Inferred user mood (低落/兴奋/平静/...) |
| need | string | Inferred user need (被理解/获取信息/...） |

### ReviewResult

| Field | Type | Description |
|-------|------|-------------|
| logic_verdict | Verdict | "ok" \| "error_found" \| "minor_issue" |
| logic_weight | float | Error severity (0-1) |
| logic_errors | list[FactError] | Specific errors found |
| emotion_verdict | Verdict | "ok" \| "tone_issue" \| "minor_tone" |
| emotion_weight | float | Tone issue severity (0-1) |
| emotion_issues | list[ToneIssue] | Specific tone issues |
| combined_weight | float | logic_weight × 0.6 + emotion_weight × 0.4 |
| decision | DecisionType | "correct" \| "silence" \| "twisted" |

### FactError

| Field | Type | Description |
|-------|------|-------------|
| error_type | ErrorType | identity_error / contradiction / fact_error / minor_detail |
| description | string | What was wrong |
| conflicting_memory_key | string | Memory key that contradicts |
| weight | float | 0.3 ~ 0.9 based on error_type |

### ToneIssue

| Field | Type | Description |
|-------|------|-------------|
| issue_type | ToneErrorType | hurtful / insensitive / tone_harsh / tone_cold / minor_tone |
| description | string | What was tonally problematic |
| weight | float | 0.3 ~ 0.95 based on issue_type |

### CorrectionCmd

| Field | Type | Description |
|-------|------|-------------|
| source | string | "logic" \| "emotion" \| "combined" |
| message | string | The corrective follow-up message |
| written_to | string | Memory key where correction was stored in subconscious |
| is_twisted | bool | Whether this was a "拧巴" (logic vs emotion conflict) |

---

## Memory Entities

### MemoryEntry

Core storage unit in SQLite.

| Field | Type | Description |
|-------|------|-------------|
| namespace | string | Logical grouping (user/facts, self/inner_thoughts, ...) |
| key | string | Unique key within namespace |
| value | JSON string | Arbitrary structured data |
| layer | "gist" \| "detail" | Detail auto-migrates to gist after 60 days |
| salience | float (1-10) | Importance score, affects decay and retrieval boost |
| entity_type | string | Type tag for entity-based retrieval |
| topic_tags | list[string] | Topic keywords for FTS5 indexing |
| emotional_tags | JSON \| null | Emotion labels (added by emotion brain) |
| created_at | datetime | Creation timestamp |
| updated_at | datetime | Last modification |
| expires_at | datetime \| null | TTL for short-term memories |
| ttl | int \| null | Time-to-live in seconds |

**Namespaces**:
```
user/{uid}/facts/          # Structured user facts
user/{uid}/profile/        # Inferred user profile
user/{uid}/emotions/       # Emotion labels
user/{uid}/conversations/  # Conversation summaries

self/inner_thoughts/       # Parsed inner thoughts
self/reflections/          # Long-term reflections
self/noticed/              # Things noticed but not said
self/feelings/             # Emotional memories (incl. twisted)
self/intentions/           # "What I want to do"

short_term/action_results/ # Action brain results (auto-expire)
short_term/recent_topics/  # Recent topics (decay)
short_term/active_nudges/  # Current directional nudges

subconscious/corrections/  # Correction directives (high priority)
subconscious/nudges/       # Proactive topic suggestions
subconscious/deferred_actions/ # Actions to revisit later

global/persona/            # Persona definition
global/knowledge/          # Searched knowledge
global/boredom/            # Boredom state
```

### MemoryLink

| Field | Type | Description |
|-------|------|-------------|
| from_key | string | Source memory entry (`"namespace/key"` — see note below) |
| to_key | string | Target memory entry |
| relation | RelationType | "extends" \| "contradicts" \| "related_to" |

> **Implementation note**: `from_key`/`to_key` use `"{namespace}/{key}"` composite format.
> Since namespace itself may contain `/` (e.g. `"user/default"`), this format is ambiguous
> for parsing. Internal code (`_spread_activate`) queries `memory_links` table directly with
> separate namespace+key columns to avoid this issue.

---

## Emotion & Personality Entities

### EmotionState

Per-brain emotional vector.

| Field | Type | Description |
|-------|------|-------------|
| brain | string | "logic" \| "emotion" \| "sub" |
| surprise | float (0-1) | Surprise level |
| confusion | float (0-1) | Confusion level |
| joy | float (0-1) | Joy level |
| sadness | float (0-1) | Sadness level |
| anticipation | float (0-1) | Anticipation level |
| trust | float (0-1) | Trust level |
| fear | float (0-1) | Fear level |
| anger | float (0-1) | Anger level |
| disgust | float (0-1) | Disgust level |
| interest | float (0-1) | Interest/curiosity level |
| half_lives | dict[str, int] | Per-dimension decay half-lives (seconds) |
| last_tick | datetime | Last decay tick timestamp |

### Personality

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| curiosity | float (0-1) | 0.7 | Drives autonomous exploration |
| sociability | float (0-1) | 0.8 | Drives proactive conversation |
| playfulness | float (0-1) | 0.6 | Modulates response temperature |
| empathy | float (0-1) | 0.5 | Drives empathetic response mode |
| assertiveness | float (0-1) | 0.3 | Drives correction willingness |
| creativity | float (0-1) | 0.6 | Modulates response diversity |
| impulsiveness | float (0-1) | 0.2 | Drives immediate vs deferred correction |
| loyalty | float (0-1) | 0.75 | Boosts memory retrieval relevance |

### Intent

| Field | Type | Description |
|-------|------|-------------|
| action | IntentType | SEARCH \| SPEAK \| REMEMBER \| NONE |
| detail | string | Specific action description |
| confidence | float | Extraction confidence |
| assessed_weight | float \| null | Post-evaluation weight (≥ 0.5 → execute) |
| status | IntentStatus | "pending" \| "executing" \| "deferred" \| "resolved" \| "expired" |
| revisit_condition | string \| null | When to re-evaluate (for deferred) |

---

## State Machine: TurnManager

```
IDLE
  │ user message arrives
  ▼
DUAL_RECALL (logic + emotion brains parallel)
  │ both complete
  ▼
INJECTING (brains call inject_to_sub)
  │ injection ready
  ▼
SUB_SESSION (ReAct loop: think → act → think → ...)
  │ inner_thoughts + done
  ▼
REVIEWING (logic + emotion review parallel)
  │ both complete
  ▼
DECIDING (weight calculation)
  ├─ combined > 0.5 → CORRECTING (correction sub-session)
  │   └─ correction done → ARCHIVING → IDLE
  ├─ logic > 0.8 AND emotion < 0.3 → twisted → CORRECTING (logic wins)
  │   └─ correction done + twisted record → ARCHIVING → IDLE
  └─ combined ≤ 0.5 → ARCHIVING (silent) → IDLE
```

---

## State Machine: SubSession ReAct Loop

```
CREATED
  │ receive injection
  ▼
THINKING
  │ LLM returns tool_call(s)
  ▼
ACTING
  │ execute tool(s)
  ▼
OBSERVING (results injected into context)
  │
  ├─ tool was send_reply → THINKING (may send more)
  ├─ tool was wait → after delay → THINKING
  ├─ tool was recall → THINKING
  ├─ tool was inner_thoughts → WAITING_DONE
  │   └─ tool was done → COMPLETED
  └─ any termination condition met → COMPLETED

Termination conditions:
  - done tool called
  - hit_count ≥ max_iter (default 5)
  - sadness > 0.8
  - focus < 0.15
  - boredom > 0.7
```
