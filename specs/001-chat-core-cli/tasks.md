# Tasks: Chat Core CLI

**Feature**: `001-chat-core-cli`
**Source Plan**: [plan.md](./plan.md)
**Source Spec**: [spec.md](./spec.md)
**Created**: 2026-07-09

---

## Phase Mapping (plan.md → tasks.md)

| Plan Phase | Tasks Phase(s) | Description |
|------------|----------------|-------------|
| Phase 0 (Skeleton) | Phase 1-3 | Setup + Foundational + US1 |
| Phase 1 (Brain+Memory) | Phase 4-5 | US2 + US3 |
| Phase 2 (Personality) | Phase 6-7 | US4 + US5 |
| Phase 3 (Polish) | Phase 8 | Polish & Cross-Cutting |

---

## User Stories

| ID | Priority | Story | Core FRs |
|----|----------|-------|----------|
| US1 | P1 | Basic Conversation — 用户在终端输入消息，AI 以自然语言回复 | FR-01~06 |
| US2 | P1 | Memory & Recall — AI 跨 session 记住用户事实并在对话中引用 | FR-07~12 |
| US3 | P2 | Self-Review & Correction — AI 审查自身发言的事实和语气，必要时纠正 | FR-18~22 |
| US4 | P2 | Emotional Intelligence — AI 感知情绪、展现个性、语气适配 | FR-13~17 |
| US5 | P3 | Proactive & Interest-Driven — AI 在空闲时主动发起话题、探索兴趣 | FR-23~30 |

---

## Phase 1: Setup (Project Scaffold)

**Goal**: Runnable project skeleton with dependency management.

- [x] T001 Create project directory structure per plan.md: `chat-core/cli.py`, `chat-core/config.py`, `chat-core/core/`, `chat-core/systems/`, `chat-core/prompts/`, `chat-core/data/`, `chat-core/tests/`
- [x] T002 [P] Create `pyproject.toml` with Python 3.12+ requirement, dependencies (openai>=1.0, prompt_toolkit, rich, aiosqlite, pyyaml, duckduckgo-search), dev dependencies (pytest, pytest-asyncio, mypy)
- [x] T003 [P] Create `config.yaml` default template with all required sections (apis, brains, systems, prompts, history, safety) per contracts/cli-contract.md and plan.md
- [x] T004 [P] Create prompt templates `prompts/persona.yaml`, `prompts/rules.yaml`, `prompts/tools.yaml` with initial content per design doc §7.1
- [x] T005 [P] Create `.gitignore` with Python ignores (`__pycache__/`, `*.pyc`, `data/`, `.env`)

---

## Phase 2: Foundational (Shared Infrastructure)

**Goal**: Core abstractions all user stories depend on. MUST complete before any user story work.

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete.

- [x] T006 Implement `config.py` — load YAML config with `${ENV_VAR}` substitution, env override, validation of required fields (FR-01 dependency)
- [x] T007 Implement `core/provider.py` — `ModelProvider` class wrapping `AsyncOpenAI`, supporting streaming (`streamChat`) and non-streaming (`chat`) modes, model parameter passthrough (temperature, max_tokens, reasoning_effort) (FR-02 dependency)
- [x] T008 [P] Implement `core/tools.py` — `ToolRegistry` with register, specs(), execute(), executeBatch() (parallel-safe + serial), fork() methods per design doc §12.6 Gap 14 (FR-03 dependency)
- [x] T009 [P] Implement `core/prompt_engine.py` — compile persona.yaml + rules.yaml + tools.yaml into per-brain system prompts, with runtime state injection (emotion, attention, memory) per design doc §7.1 (FR-16 dependency)
- [x] T010 [P] Define shared data types in `core/types.py` — `Message`, `ToolCall`, `ToolSpec`, `StreamEvent`, `ActionResult`, `TurnStatus`, `MemoryEntry`, `EmotionState`, `PersonalityWeights` per data-model.md

**Checkpoint**: Config loads, Provider connects to LLM API, ToolRegistry registers tools, PromptEngine compiles — ready for brain implementation.

---

## Phase 3: US1 — Basic Conversation (P1) 🎯 MVP

**Goal**: User types a message in the terminal, AI replies with natural language through a ReAct loop.

**Independent Test**: Launch CLI → type "Hello" → see AI reply with persona-appropriate response within 5 seconds.

### Implementation

- [x] T011 [US1] Implement `core/loop.py` — `ReActLoop` engine: think (LLM call with function calling) → act (execute tool calls via ToolRegistry) → observe (inject results) → loop until termination condition (done tool hit, max_iter exceeded, sadness>0.8, focus<0.15, boredom>0.7) per design doc §3.2
- [x] T012 [US1] Register sub-session tools in `core/loop.py`: `send_reply` (text ≤ 500 chars, safety filter for self-harm/violence), `wait` (0.5-5.0s asyncio.sleep), `recall` (read-only, restricted namespaces), `inner_thoughts` (store raw text), `done` (signal termination) per contracts/cli-contract.md
- [x] T013 [US1] Implement sub-session protocol enforcement in `core/loop.py`: detect skipped `inner_thoughts` (done without prior inner_thoughts) → log warning → degrade to text-only review per design doc §11.1
- [x] T014 [US1] Implement `cli.py` — prompt_toolkit interactive REPL: accept user input, create sub-session, run ReActLoop, stream `send_reply` output to terminal, handle `/quit`, `/help`, `/mood`, `/memories` slash commands per contracts/cli-contract.md
- [x] T015 [US1] Wire full turn in `cli.py`: config load → provider init → prompt compile → user message → ReActLoop.run() → display replies → loop

### Tests

- [x] T016 [P] [US1] Create `tests/test_provider.py` — mock API responses, verify streaming SSE parsing, non-streaming result extraction, error handling
- [x] T017 [P] [US1] Create `tests/test_tools.py` — tool registration, parameter validation, send_reply length limit, wait timing, parallel/serial execution ordering
- [x] T018 [P] [US1] Create `tests/test_loop.py` — mock LLM returning tool calls, verify think→act→observe cycle, termination on done/max_iter/sadness/focus/boredom, inner_thoughts enforcement
- [x] T019 [P] [US1] Create `tests/test_prompt_engine.py` — verify three-layer compilation, runtime state injection format, per-brain template isolation

**Checkpoint**: `chat-core chat` → type message → see natural language reply. 🎉

---

## Phase 4: US2 — Memory & Recall (P1)

**Goal**: AI stores and retrieves facts across sessions. User says "remember I play basketball" → 3 days later AI references it unprompted.

**Independent Test**: Chat about a personal fact → quit → restart → ask about that fact → AI recalls it correctly.

### Implementation

- [x] T020 [US2] Implement `systems/memory.py` — `MemoryStore` class with SQLite + FTS5 backend: `save(namespace, key, value)`, `get(namespace, key)`, `query(namespace, filter)`, `search(query)` (FTS5), `link(from_key, to_key, relation)`, `tag(namespace, key, tags)` per data-model.md. Auto-create `data/` directory on first run if missing.
- [x] T021 [US2] Implement namespace enforcement in `systems/memory.py`: sub-session recall restricted to `user/{uid}/*` and `short_term/*`; logic brain full access; emotion brain `memory_tag` only (no create); short_term TTL auto-expiry in `query()` per design doc §12.4 Gap 10
- [x] T022 [US2] Implement retrieval pipeline in `systems/memory.py`: FTS5 search (top_n=20) → LIKE fallback (no FTS5 hits) → LLM rerank (top 5, max_tokens=64, 1s timeout) → spread activation (follow memory_links depth=2) → cluster boost (same topic_tags) per research.md §6
- [x] T023 [US2] Implement memory decay in `systems/memory.py`: detail layer auto-migrate to gist after 60 days; gist expiry based on salience (salience 5→90d, 7→135d, 10→180d); short_term entries filtered by TTL at read time per config.yaml defaults
- [x] T024 [US2] Implement `core/brain.py` — `LogicBrain` class: Phase 1 (recall + direction) → `_think_pre()` with tools [recall, memory_link]; Phase 2 (inject) → `_think_inject()` with tool [inject_to_sub]; single-pass (no multi-turn loop) per design doc §12.1 Gap 1
- [x] T025 [US2] Implement `core/brain.py` — `EmotionBrain` class: same two-phase structure as LogicBrain but with tools [recall, memory_tag, inject_to_sub]; memory_tag appends emotional labels to existing entries per design doc §12.2 Gap 5
- [x] T026 [US2] Implement `core/brain.py` — `ActionBrain` class: temporary brain created per task, tools [search, recall, web_fetch], runs once then destroys; `ActionBrainPool` with Semaphore(2) + Queue per design doc §11.9
- [x] T027 [US2] Implement `core/turn_manager.py` — `TurnManager.process_turn()`: dual recall (logic + emotion parallel) → inject_to_sub → sub-session ReAct → archive → emit "conversation_ended" event per design doc §3 and data-model.md state machine
- [x] T028 [US2] Implement internal event bus in `core/turn_manager.py`: three exchange windows (recall results, review results, idle inspiration) using asyncio.Queue per design doc §11.5 and contracts/cli-contract.md

### Tests

- [x] T029 [P] [US2] Create `tests/test_memory.py` — CRUD operations, FTS5 search (English + Chinese), LIKE fallback, namespace enforcement, TTL expiry, memory links, spread activation
- [x] T030 [P] [US2] Create `tests/test_brain.py` — mock LLM for LogicBrain two-phase call, EmotionBrain memory_tag, ActionBrain search/recall/web_fetch, inject_to_sub output format
- [x] T031 [US2] Create `tests/test_turn_manager.py` — mock all brains, verify process_turn flow: dual recall → inject → sub_session → archive, event emissions, state transitions

**Checkpoint**: AI remembers facts across sessions. "What did I tell you about my job?" → correct recall.

---

## Phase 5: US3 — Self-Review & Correction (P2)

**Goal**: AI internally reviews its own replies for factual accuracy and tone. When errors are significant, it follows up with a natural correction. Minor issues are silently noted.

**Independent Test**: Deliberately feed AI contradictory info (say "I'm a designer" then later "I code all day") → AI should correct itself referencing the earlier fact.

### Implementation

- [x] T032 [US3] Implement error detection Layer 1 in `core/turn_manager.py` or new `systems/review.py`: keyword entity extraction from reply + comparison with recall results; exact match → pass, mismatch → flag as candidate per design doc §11.3
- [x] T033 [US3] Implement error detection Layer 2: check candidate errors against subconscious/corrections for pre-existing records → direct confirmation without LLM per design doc §11.3
- [x] T034 [US3] Implement error detection Layer 3 in review module: LLM-based review prompt ("sub said X, memory shows Y, is this an error?") → {is_error, severity}; 1s timeout → skip on timeout per design doc §11.3
- [x] T035 [US3] Implement weighted decision in `core/turn_manager.py`: `combined = logic_weight × 0.6 + emotion_weight × 0.4`; threshold 0.5; error type weights (identity_error=0.9, contradiction=0.8, fact_error=0.7, minor_detail=0.3; hurtful=0.95, insensitive=0.7, tone_harsh=0.6, tone_cold=0.5, minor_tone=0.3) per design doc §6.1
- [x] T036 [US3] Implement correction flow in `core/turn_manager.py`: combined > 0.5 → write to subconscious/corrections → create correction sub-session (single-pass, max_iter=2, no inner_thoughts, no recursive correction trigger) → inject → send_reply(最多1次) → done; anti-recursion guard per design doc §12.3 Gap 6
- [x] T037 [US3] Implement silent archive in `core/turn_manager.py`: combined ≤ 0.5 → write to self/noticed with observation → increment silence accumulator for that error type (base += 0.05, max 0.3) per design doc §5.4
- [x] T038 [US3] Implement twisted state handling in `core/turn_manager.py`: logic_weight > 0.8 AND emotion_weight < 0.3 → execute logic decision → write twisted record to self/feelings/twisted with context, logic_decision, emotion_dissent, resolution per design doc §12.3 Gap 7
- [x] T039 [US3] Implement silence accumulator (FuzzyParam) in `systems/interest.py` or new module: corrected_weight = FuzzyParam(base=min(0.3, count×0.05), amplitude=0.1, noise=random()×0.05).sample(); non-deterministic triggering per research.md §9

### Tests

- [x] T040 [P] [US3] Create `tests/test_review.py` — entity extraction accuracy, Layer 1/2/3 detection, weighted decision edge cases (0.49, 0.51), twisted state trigger
- [x] T041 [US3] Create `tests/test_correction.py` — correction sub-session flow, anti-recursion guard, silent archive write, silence accumulator increment + FuzzyParam sampling distribution

**Checkpoint**: AI says "you're in the varsity team" → detects stored memory says "department team" → corrects itself.

---

## Phase 6: US4 — Emotional Intelligence (P2)

**Goal**: AI has an emotional state that changes based on conversation and decays over time. Personality weights modulate behavior. User feels the AI "has moods."

**Independent Test**: Share sad news → AI responds empathetically → share exciting news → AI responds enthusiastically → check `/mood` shows different emotional vectors.

### Implementation

- [x] T042 [US4] Implement `systems/emotion.py` — `EmotionEngine` with 10 dimensions × 3 brains (logic, emotion, sub): surprise, confusion, joy, sadness, anticipation, trust, fear, anger, disgust, interest; each with float value 0-1 per data-model.md
- [x] T043 [US4] Implement emotion decay in `systems/emotion.py`: per-dimension half-life (surprise=30s, confusion=120s, joy=600s, sadness=900s, anticipation=1800s, trust=3600s); exponential decay formula V(t)=V₀×2^(-t/half_life) per research.md §7
- [x] T044 [US4] Implement emotion contagion in `systems/emotion.py`: cross-brain spread with strength=0.1 per tick; emotion tick asyncio task (10s interval); pause during active conversation, resume on conversation_ended event per design doc §11.15
- [x] T045 [US4] Implement `systems/personality.py` — `PersonalityEngine` with 8 weights (curiosity=0.7, sociability=0.8, playfulness=0.6, empathy=0.5, assertiveness=0.3, creativity=0.6, impulsiveness=0.2, loyalty=0.75) per data-model.md
- [x] T046 [US4] Implement personality → behavior mapping in `systems/personality.py`: playfulness → temperature modulation (base + playfulness×0.3); empathy > 0.5 → "empathetic" mode in inject direction; creativity → response diversity bias; impulsiveness → correction immediacy; sociability → proactive frequency per design doc §12.6 Gap 16
- [x] T047 [US4] Implement `systems/attention.py` — `AttentionModel` with focus + dominance per brain (logic: 0.8/0.7, emotion: 0.7/0.5, sub: 0.9/0.6); drift decay rate 0.01/s; read by sub-session each think() as part of termination condition (focus < 0.15 → exit) per design doc §3.2
- [x] T048 [US4] Integrate emotion + personality into TurnManager and PromptEngine: inject current emotional state into sub-session context block "[情绪状态]"; apply personality temperature to each LLM call; pass empathy mode to inject_to_sub.direction per plan.md Phase 2
- [x] T049 [US4] Implement `/mood` slash command in `cli.py`: display current emotion vector per brain, personality weights summary, attention focus levels

### Tests

- [x] T050 [P] [US4] Create `tests/test_emotion.py` — tick decay math, contagion spread, dimension bounds (0-1 clamping), pause/resume cycle
- [x] T051 [P] [US4] Create `tests/test_personality.py` — temperature modulation formula, empathy mode toggle, loyalty → memory boost, impulsiveness → correction threshold
- [x] T052 [US4] Create `tests/test_attention.py` — drift decay, focus threshold termination, dominance effect on review

**Checkpoint**: AI responds empathetically to sad news, enthusiastically to good news. `/mood` shows dynamic emotional state.

---

## Phase 7: US5 — Proactive & Interest-Driven (P3)

**Goal**: AI gets "bored" when idle and may proactively start conversations. It tracks topic interest and autonomously searches for related information.

**Independent Test**: Chat about a topic → go idle 5-10 minutes → AI proactively messages about that topic → ask about a different topic 3+ times → AI shows heightened interest.

### Implementation

- [x] T053 [US5] Implement `systems/boredom.py` — `BoredomDetector`: receives `conversation_ended` event with {logic_eval, emotion_weight} → starts ticker (30s interval, only in idle state); boredom = eval_param × e^(-t/600s); trigger at < 0.30; end_conversation at > 0.70 AND impulsiveness > 0.5 per design doc §12.5 Gap 12
- [x] T054 [US5] Implement `systems/interest.py` — `InterestModel`: track topic mention counts per conversation; threshold = 3 mentions → trigger; topic weight increment 0.1/mention; decay 0.05/hour; per design doc config defaults
- [x] T055 [US5] Implement boredom → action routing in `core/turn_manager.py`: boredom trigger → check interest weight; > 0.5 → create ActionBrain for "search recent news on [interest topics]" → brain returns results → dual brains decide → write to subconscious/nudges if relevant; ≤ 0.5 → write to subconscious/nudges directly → inject sub-session per design doc §11.11
- [x] T056 [US5] Implement proactive conversation launch in `core/turn_manager.py`: `_on_proactive_trigger()` → check sub-session alive (reuse) or create new → inject_to_sub({initiative: "主动发起", nudge_ref, direction}) → ReActLoop → send_reply → review (same as passive flow) per design doc §12.5 Gap 13
- [x] T057 [US5] Implement intent extraction in review module: regex match `我是否想要做什么[:：]\s*(.+?)(?:\n\n|$)` from inner_thoughts; classify: 搜索/查/找→ACTION_SEARCH, 告诉/说/提醒→ACTION_SPEAK, 记住/记录→ACTION_REMEMBER; LLM fallback if regex misses but "想/要/该" keywords present (max_tokens=64, 1s timeout) per design doc §11.6
- [x] T058 [US5] Implement intent execution in `core/turn_manager.py`: extracted intent → dual brain assess (logic: relevance+value+timing; emotion: naturalness+atmosphere) → combined > 0.5 → execute via ActionBrain or TurnManager; ≤ 0.5 → write to subconscious/deferred_actions per design doc §6.2
- [x] T059 [US5] Implement deferred action lifecycle in `core/turn_manager.py`: check on each new turn (re-assess weight); check on boredom trigger (context match); auto-expire after 24h unresolved per design doc §12.6 Gap 17
- [x] T060 [US5] Implement context compression in `core/loop.py`: < 70% tokens → full history; 70-85% → compress old tool results to 100 chars, reduce short-term to 1 entry; > 85% → sub-session retirement → extract summary → new sub-session with handoff injection per design doc §11.7

### Tests

- [x] T061 [P] [US5] Create `tests/test_boredom.py` — exponential decay curve, trigger/non-trigger boundary, end_conversation condition, idle/active state toggle
- [x] T062 [P] [US5] Create `tests/test_interest.py` — topic counting, threshold trigger, FuzzyParam sampling statistics, hourly decay
- [x] T063 [US5] Create `tests/test_proactive.py` — boredom→action routing, proactive trigger→sub-session creation, intent extraction regex + classification, deferred action activation + expiry

**Checkpoint**: Wait 10 minutes after conversation → AI proactively messages about previously discussed topic.

---

## Phase 8: Polish & Cross-Cutting (P4)

**Goal**: Production-quality CLI, safety enforcement, multi-modal fallback, conversation history persistence.

### Implementation

- [x] T064 Implement rich TUI in `cli.py`: user/AI message color differentiation, typing animation for `wait` pauses, emotion status bar, markdown rendering
- [x] T065 Implement `core/history.py` — conversation history persistence: JSONL format (one line per message) at `data/history/{user_id}.jsonl` with turn_id and brain_metadata per design doc §11.14
- [x] T066 Implement `systems/multimodal.py` — four-step pipeline: (1) image detection in CLI input (file path / URL patterns); (2) primary vision call if provider supports vision (image_url in message); (3) fallback chain from config.yaml (e.g., stepfun → deepseek) if primary lacks vision — each attempt creates ActionBrain for image description; (4) all providers unavailable → inject "[系统] 用户发了一张图片，但当前没有可用的视觉模型" per design doc §11.12
- [x] T067 Implement Ctrl+C handling in `cli.py`: first SIGINT → send cancel signal to sub-session → immediate done; second SIGINT within 2s → force exit with state save; normal /quit → wait for turn completion → save emotion → close memory → exit per design doc §11.13
- [x] T068 Implement tool safety in `core/tools.py` and systems: send_reply content filter (self-harm/violence keyword block); search rate limiter (token bucket: 5 tokens/60s, 2s cooldown); web_fetch restrictions (http/https only, 100KB limit, 10s timeout) per design doc §11.10 and FR-34~37
- [x] T069 Implement context compression token estimation in `core/loop.py`: approximate token count from message content length ÷ 4 (rough estimate); apply compression levels based on percentage thresholds
- [x] T070 Implement SQLite WAL mode in `systems/memory.py`: enable WAL journal mode for concurrent read safety; add connection pool for parallel brain access

### Tests

- [x] T071 [P] Create `tests/test_history.py` — JSONL append/read consistency, turn_id linking, brain_metadata serialization
- [x] T072 [P] Create `tests/test_multimodal.py` — image path detection, vision capability check, fallback chain execution, all-unavailable message
- [x] T073 [P] Create `tests/test_safety.py` — content filter boundary cases, search rate limiter token bucket, web_fetch URL whitelist, size limit enforcement
- [x] T074 [P] Create `tests/test_cli.py` — full turn integration test with mock LLM, slash command handling, Ctrl+C interrupt, quit state preservation, and 30-minute continuous conversation stability test (SC-07)

---

## Dependency Graph

```
Phase 1 (Setup)
  └─ T001-T005 (parallel)

Phase 2 (Foundational) ← BLOCKS all user stories
  ├─ T006 config.py
  ├─ T007 provider.py
  ├─ T008 tools.py
  ├─ T009 prompt_engine.py
  └─ T010 types.py
  ↓
Phase 3: US1 ← depends on Phase 2
  ├─ T011 loop.py → T012 tools → T013 enforcement → T014 cli.py → T015 wire
  └─ T016-T019 tests (parallel after implementation)
  ↓
Phase 4: US2 ← depends on US1 (needs working loop)
  ├─ T020 memory → T021 namespaces → T022 retrieval → T023 decay
  ├─ T024 logic_brain → T025 emotion_brain → T026 action_brain
  ├─ T027 turn_manager → T028 event_bus
  └─ T029-T031 tests
  ↓
Phase 5: US3 ← depends on US2 (needs memory + turn_manager)
  ├─ T032-T034 error detection → T035 weighted decision
  ├─ T036 correction flow → T037 silent archive → T038 twisted state → T039 silence accumulator
  └─ T040-T041 tests
  ↓
Phase 6: US4 ← depends on US2 (needs memory + turn_manager)
  ├─ T042-T044 emotion engine → T045-T046 personality → T047 attention → T048 integration → T049 /mood
  └─ T050-T052 tests
  ↓
Phase 7: US5 ← depends on US4 (needs emotion + personality)
  ├─ T053 boredom → T054 interest → T055 routing → T056 proactive → T057 intent → T058 execution → T059 deferred → T060 compression
  └─ T061-T063 tests
  ↓
Phase 8 (Polish) ← depends on ALL above
  └─ T064-T074 (many parallel)
```

---

## Parallel Execution Examples

### Phase 2 (after T006 config)
```bash
# These can run simultaneously:
Task T007: Implement core/provider.py
Task T008: Implement core/tools.py
Task T009: Implement core/prompt_engine.py
Task T010: Define core/types.py
```

### Phase 3 Tests (after T011-T015)
```bash
Task T016: tests/test_provider.py
Task T017: tests/test_tools.py
Task T018: tests/test_loop.py
Task T019: tests/test_prompt_engine.py
```

### Phase 4 (after T020-T023 memory, independent brain implementations)
```bash
Task T024: LogicBrain    } All three brains
Task T025: EmotionBrain  } can be implemented
Task T026: ActionBrain   } in parallel
```

### Phase 6 + 7 Overlap (after US2 complete)
```bash
# US4 (emotion) and US3 (correction) can proceed in parallel:
US3: T032-T039 (correction system)
US4: T042-T049 (emotion + personality)
# Both depend on US2 turn_manager but not on each other
```

---

## Implementation Strategy

### MVP (Minimal Viable Product): Phase 1-3 only

Complete US1 (Basic Conversation) to have a working chat CLI:
- `T001-T005`: Project setup
- `T006-T010`: Shared infrastructure
- `T011-T019`: ReAct loop + CLI + tests
- **Deliverable**: `chat-core chat` works, AI replies naturally

### Incremental Delivery

| Milestone | Phases | User-Visible Change |
|-----------|--------|---------------------|
| M1: Chat | 1-3 | Terminal chat with personality |
| M2: Memory | +4 | AI remembers facts across sessions |
| M3: Self-Aware | +5 | AI corrects its own mistakes |
| M4: Emotional | +6 | AI has moods and personality-driven behavior |
| M5: Proactive | +7 | AI starts conversations unprompted |
| M6: Ship | +8 | Polished CLI, safety, multi-modal |

---

## Task Summary

| Phase | Tasks | Story |
|-------|-------|-------|
| Phase 1: Setup | T001-T005 (5) | — |
| Phase 2: Foundational | T006-T010 (5) | — |
| Phase 3: US1 Basic Conversation | T011-T019 (9) | P1 🎯 MVP |
| Phase 4: US2 Memory & Recall | T020-T031 (12) | P1 |
| Phase 5: US3 Self-Review & Correction | T032-T041 (10) | P2 |
| Phase 6: US4 Emotional Intelligence | T042-T052 (11) | P2 |
| Phase 7: US5 Proactive & Interest-Driven | T053-T063 (11) | P3 |
| Phase 8: Polish | T064-T074 (11) | P4 |
| **Total** | **74 tasks** | |

---

## Format Validation

✅ All 74 tasks follow checklist format: `- [ ] [TaskID] [P?] [Story?] Description with file path`
✅ Task IDs sequential T001-T074
✅ [P] markers on parallelizable tasks (different files, no dependencies)
✅ [US1]-[US5] story labels on user story phase tasks
✅ File paths included in all implementation task descriptions
✅ Tests organized per phase with [P] markers where parallelizable
