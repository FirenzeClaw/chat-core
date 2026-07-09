# Tasks: QQ Bot 集成

**Feature**: `002-qq-bot-integration`
**Source Plan**: [plan.md](./plan.md)
**Source Spec**: [spec.md](./spec.md)
**Created**: 2026-07-09

---

## Phase Mapping (plan.md → tasks.md)

| Plan Phase | Tasks Phase | Description |
|------------|-------------|-------------|
| Phase 1 (Protocol Polish) | Phase 2 | Foundational: protocol + sessions |
| Phase 2 (Core Architecture) | Phase 3-5 | US1 + US2 + US3 |
| Phase 3 (Entry + Emotion) | Phase 3 | Integrated into US3 |
| Phase 4 (Namespace) | Phase 6 | US4 |
| Phase 5 (Proactive + Profile) | Phase 7-8, 10 | US5 + US6 + US7 |
| Phase 6 (Integration + Docs) | Phase 9 | Polish & Integration |

---

## User Stories

| ID | Priority | Story | Core FRs |
|----|----------|-------|----------|
| US1 | P1 | QQ 私聊对话 — 用户在 QQ 私聊中与 AI 对话，2-5 秒内收到人格化回复 | FR-1~5, FR-14 |
| US2 | P1 | 群聊 @ + 旁听 — @机器人 触发回复，非@消息旁听记忆 | FR-18 |
| US3 | P1 | 多用户竞态 — 全局双主脑 + 多子 Session + 竞态驱动烦躁 + 潜意识调节 | FR-6~13 |
| US4 | P2 | 记忆隔离 — 私聊/群聊命名空间隔离 + 群聊联动检索 | FR-16, FR-17 |
| US5 | P2 | 用户画像 — 昵称获取 + 事实提取 + 群聊画像 | FR-23~25 |
| US6 | P2 | 主动发言 — ProactiveSystem 在 QQ 模式工作 | FR-19, FR-20 |
| US7 | P3 | 会话管理 — TTL 过期 + API 错误降级 + 配置 | FR-21, FR-22, FR-12 |

---

## Phase 1: Setup (Existing Code Audit)

**Goal**: Confirm existing qq/ module baseline and identify precise diff.

- [ ] T001 Audit existing `chat_core/qq/protocol.py` — list all functions, confirm which are correct vs need modification per spec FR-1~5, FR-23
- [ ] T002 [P] Audit existing `chat_core/qq/sessions.py` — confirm `conversation_history` removal scope, check all callers in adapter.py and qq_bot.py
- [ ] T003 [P] Audit existing `chat_core/qq/adapter.py` — document all dependencies on TurnManager, confirm rewrite boundary
- [ ] T004 [P] Verify `aiohttp` installed and importable — `python -c "import aiohttp; print(aiohttp.__version__)"`

---

## Phase 2: Foundational — Protocol Polish & Sessions Rewrite

**Goal**: QQ protocol layer production-ready. UserSession stripped to metadata only.

**⚠️ CRITICAL**: No user story implementation can begin until this phase is complete.

- [ ] T005 [P] Rewrite `chat_core/qq/sessions.py` — remove `conversation_history`, `add_turn()`, `total_turns`. Keep `user_id`, `session_key`, `turn_counter`, `last_active`. Add `sub_session` reference field (type Any, lazy binding). (FR-7, FR-12)
- [ ] T006 [P] Add `fetch_user_nickname(openid: str) -> str` to `chat_core/qq/protocol.py` — call `GET /v2/users/{openid}`, return nickname string. On failure return empty string. (FR-23)
- [ ] T007 [P] Add error classification to `send_message()` in `chat_core/qq/protocol.py` — 22009 retry with backoff (1s/3s/9s), 304082/304083 retry once, others log+drop. (FR-3)
- [ ] T008 Update `tests/test_qq_sessions.py` — remove `test_add_turn`, `test_history_limit`, `test_total_turns`. Add `test_sub_session_binding`.
- [ ] T009 [P] Update `tests/test_qq_protocol.py` — add `test_fetch_nickname_success`, `test_fetch_nickname_failure`, `test_send_error_retry_22009`, `test_send_error_noretry`.

**Checkpoint**: `python -m pytest tests/test_qq_protocol.py tests/test_qq_sessions.py -v` — all pass.

---

## Phase 3: US1 — QQ Private Chat (P1)

**Goal**: User sends message in QQ private chat → AI replies with full personality pipeline.

**Independent Test**: Mock QQ WebSocket event → BotAdapter.process_message() → assert reply text matches persona. No real QQ connection needed.

### Core Implementation

- [ ] T010 [US1] Rewrite `chat_core/qq/adapter.py` — `BotAdapter` class with constructor accepting all dependencies: `provider`, `memory_store`, `prompt_engine`, `personality_engine`, `emotion_engine`, `logic_brain`, `emotion_brain`, `race_tracker`, `subconscious_injector`. Store as instance fields. Separate optional from required via keyword defaults. (FR-6, FR-14)
- [ ] T011 [US1] Implement `BotAdapter._sub_sessions: dict[str, ReActLoop]` in `chat_core/qq/adapter.py` — keyed by session_key, lazy creation on first message per user. (FR-7, FR-12)
- [ ] T012 [US1] Implement `BotAdapter._get_or_create_sub_session(session_key, system_prompt, config) -> ReActLoop` in `chat_core/qq/adapter.py` — create ReActLoop with tools, set reply/stream callbacks (stream disabled for QQ), return instance. Reuse existing if not TTL-expired. (FR-11, FR-12)
- [ ] T013 [US1] Implement `BotAdapter.process_message(ctx: MessageContext) -> str | None` in `chat_core/qq/adapter.py` — for C2C messages: create/retrieve sub-session → build system prompt via prompt_engine → `await sub_session.run(ctx.content)` → collect replies → return reply text. (FR-3)
- [ ] T013a [US1] Implement archive after sub-session in `BotAdapter.process_message()` in `chat_core/qq/adapter.py` — after sub-session completes, write turn summary to MemoryStore via `MemoryEntry` at `user/{uid}/c2c/conversations`. Include user_message, reply, turn_id, timestamp. (FR-13)
- [ ] T014 [US1] Wire reply callback in `BotAdapter` — sub-session's `_on_reply` callback collects reply segments into list, process_message returns joined text. No rich.Live rendering. (FR-14)

### Wiring

- [ ] T015 [US1] Update `chat_core/qq_bot.py` — create `BotAdapter` with shared deps, wire `on_qq_message` → `adapter.process_message(ctx)` → `send_message(ctx, reply)`. (FR-1, FR-3)

### Tests

- [ ] T016 [US1] Create `tests/test_qq_adapter.py` — `TestC2CConversation`: mock ReActLoop.run() returns "你好呀！", verify adapter returns same text. Mock sub-session reuse across 2 turns.

**Checkpoint**: `python -m pytest tests/test_qq_adapter.py -v` — C2C conversation test passes.

---

## Phase 4: US2 — Group Chat @ Reply + Passive Observe (P1)

**Goal**: @bot in group → reply. Non-@ messages → silent observe to memory. No reply.

**Independent Test**: Mock GROUP_AT_MESSAGE_CREATE event → assert reply. Mock GROUP_MESSAGE_CREATE (no @) → assert no reply, assert memory write.

- [ ] T017 [US2] Add group message routing to `BotAdapter.process_message()` in `chat_core/qq/adapter.py` — if `ctx.is_group and not ctx.is_at`: call `_passive_observe(ctx)`, return None. (FR-18)
- [ ] T018 [US2] Implement `BotAdapter._passive_observe(ctx)` in `chat_core/qq/adapter.py` — write observation to `user/{uid}/group/{gid}/observations` namespace in MemoryStore. Fire-and-forget (no await of memory write should block reply). (FR-18)
- [ ] T019 [US2] Add `tests/test_qq_adapter.py::TestGroupObserve` — mock GROUP_MESSAGE_CREATE → assert process_message returns None → assert memory_store.save was called with correct namespace.

**Checkpoint**: Group @ gets reply, non-@ gets silent observe, memory namespace correct.

---

## Phase 5: US3 — Multi-User Concurrency with Race Tracking (P1)

**Goal**: Global dual-brain + multiple sub-sessions + race-driven emotion + subconscious modulation.

**Independent Test**: Create 3 concurrent turn simulations → assert race_tracker.active_count = 3 → assert EmotionEngine.anger accelerated → assert SubconsciousInjector downgrades context in high-race condition.

### Race Tracker

- [ ] T020 [US3] Create `chat_core/qq/race_tracker.py` — `RaceTracker` class with `active_count: int`, `enter()` / `exit()` methods, `severity` property returning `"low"` (1-2), `"medium"` (3-4), `"high"` (5+). (FR-8)
- [ ] T021 [US3] Add `RaceTracker.attach_emotion(emotion_engine)` in `chat_core/qq/race_tracker.py` — on each `enter()`, call `emotion_engine.accelerate("anger", factor=0.1 * active_count)`. On `exit()`, factor decreases as active_count drops. (FR-9)

### Subconscious Injector

- [ ] T022 [US3] Create `chat_core/qq/subconscious.py` — `SubconsciousInjector` class with `inject(context: str, severity: str) -> str`:
  - `"low"`: return context unchanged
  - `"medium"`: truncate to first 50% (by character count)
  - `"high"`: extract first 2 sentences as direction summary. (FR-10)
- [ ] T023 [US3] Add `SubconsciousInjector.priority_sort(contexts: dict[str, str]) -> list[str]` in `chat_core/qq/subconscious.py` — sort sub-sessions by recency (most recently active gets highest priority context). (FR-10 — 顾此薄彼)

### Adapter Integration

- [ ] T024 [US3] Modify `BotAdapter.process_message()` in `chat_core/qq/adapter.py` — wrap sub-session execution with `race_tracker.enter()` / `race_tracker.exit()`. Pass dual-brain recall results through `subconscious_injector.inject()` before injecting into sub-session. Do NOT modify constructor (already defined in T010). (FR-8, FR-9, FR-10)
- [ ] T025 [US3] Modify `BotAdapter.process_message()` in `chat_core/qq/adapter.py` — wrap sub-session execution with `race_tracker.enter()` / `race_tracker.exit()`. Pass dual-brain recall results through `subconscious_injector.inject()` before injecting into sub-session. (FR-8, FR-9, FR-10)
- [ ] T026 [US3] Implement global dual-brain recall in `BotAdapter` — before each sub-session run, call `logic_brain.think_pre(user_message)` and `emotion_brain.think_pre(user_message)` in parallel (asyncio.gather). Merge results as injection context. (FR-6)

### Entry Wiring

- [ ] T027 [US3] Update `chat_core/qq_bot.py` — create global `LogicBrain`, `EmotionBrain`, `RaceTracker`, `SubconsciousInjector`, pass to `BotAdapter`. Create `EmotionEngine`, start tick, pass to `RaceTracker.attach_emotion()`. (FR-6, FR-8, FR-9)
- [ ] T028 [US3] Update `config.yaml` — remove `max_concurrent_turns` from `qq_bot` section.

### Tests

- [ ] T029 [US3] Create `tests/test_race_tracker.py` — `test_enter_exit_count`, `test_severity_low`, `test_severity_medium`, `test_severity_high`, `test_emotion_acceleration`.
- [ ] T030 [US3] Create `tests/test_subconscious.py` — `test_inject_low_unchanged`, `test_inject_medium_truncated`, `test_inject_high_summary_only`, `test_priority_sort_by_recency`.

**Checkpoint**: 3 concurrent turns → race_tracker=3 → anger accelerated → medium severity → context 50% truncated.

---

## Phase 6: US4 — Memory Namespace Isolation & Cross-Scene Recall (P2)

**Goal**: Private chat memories in `c2c/`, group in `group/{gid}/`. Group recall also searches `c2c/` of known users for cross-scene linkage.

**Independent Test**: Write C2C memory → write group memory → recall in group → assert both namespaces searched.

- [ ] T031 [US4] Add archive logic to `BotAdapter` in `chat_core/qq/adapter.py` — after sub-session completes, write turn summary to `user/{uid}/c2c/conversations` (private) or `user/{uid}/group/{gid}/conversations` (group). Detect scene from `MessageContext.scene`. (FR-16)
- [ ] T032 [US4] Modify dual-brain recall in `BotAdapter` — in group scene, extend recall namespace_prefix to also include `user/{uid}/c2c/*` for each user who has C2C history and is present in the current group. (FR-17)
- [ ] T033 [US4] Add `MessageContext.scene` property to `chat_core/qq/protocol.py` — returns `"c2c"` if `is_direct`, `"group"` if `is_group`. (FR-16)
- [ ] T034 [US4] Add `tests/test_qq_adapter.py::TestNamespaceIsolation` — write C2C entry, write group entry, verify namespace paths. Mock recall to verify cross-scene prefix expansion.

**Checkpoint**: Group recall searches both `group/{gid}/*` and `c2c/*` for known users.

---

## Phase 7: US5 — User Profile Building (P2)

**Goal**: First message fetches QQ nickname → stores in memory. LogicBrain extracts facts over time.

**Independent Test**: Mock first message → assert `fetch_user_nickname` called → assert `user/{uid}/profile` created with nickname.

- [ ] T035 [US5] Add first-message detection to `BotAdapter.process_message()` in `chat_core/qq/adapter.py` — if `UserSession.turn_counter == 0`, call `fetch_user_nickname(ctx.user_id)`, write result to `MemoryStore` at `user/{uid}/profile`. (FR-23)
- [ ] T036 [US5] Implement fact extraction hook in `BotAdapter` in `chat_core/qq/adapter.py` — after each turn, parse reply for factual statements (reuse LogicBrain's existing fact extraction from `_archive_turn` if available), write to `user/{uid}/facts`. (FR-24)
- [ ] T037 [US5] Ensure group speakers get independent profiles in `BotAdapter` — each `user_id` in group messages gets its own `user/{uid}/profile` entry. (FR-25)
- [ ] T038 [US5] Add `tests/test_qq_adapter.py::TestProfileBuilding` — mock first message → verify nickname saved to memory_store. Mock multi-turn → verify fact accumulation.

**Checkpoint**: New user → nickname stored in profile → facts accumulate across turns.

---

## Phase 8: US6 — Proactive Speech (P2)

**Goal**: ProactiveSystem triggers via global dual-brain, sends through QQ REST API.

**Independent Test**: Manually invoke proactive callback → assert `send_message()` called with correct content.

- [ ] T039 [US6] Initialize ProactiveSystem in `BotAdapter.__init__()` in `chat_core/qq/adapter.py` — using global `logic_brain`, `emotion_brain`, `emotion_engine`, `personality_engine`, `memory_store`. Wire `reply_callback` to `send_message()`. (FR-19)
- [ ] T040 [US6] Add rate limit compliance wrapper in `chat_core/qq/adapter.py` — before proactive send, check QQ rate limits. If would exceed, defer or skip. (FR-20)
- [ ] T041 [US6] Handle proactive speech in race-affected mode — when `race_severity >= "medium"`, proactive speech priority lowered, may be deferred until race eases. (FR-10 — consistent with subconscious modulation)

**Checkpoint**: Proactive trigger → send_message → QQ user receives unsolicited message.

---

## Phase 10: US7 — Session Management & Config (P3)

**Goal**: TTL cleanup, config schema, health endpoint, error recovery.

**Independent Test**: Set TTL=1s → wait 2s → verify sub-session cleaned up. Mock QQ 22009 error → verify retry behavior.

- [ ] T047 [US7] Implement sub-session TTL cleanup in `BotAdapter` in `chat_core/qq/adapter.py` — add periodic `_cleanup_expired()` async task (runs every 300s), remove sub-sessions with `last_active > session_ttl`. Log cleaned count. (FR-12, SC-6)
- [ ] T048 [US7] Wire QQ config schema in `chat_core/config.yaml` — validate `qq_bot` section has required fields (appid, secret), optional fields (ws_url, group_reply_enabled, c2c_reply_enabled, passive_observe, session_ttl, health_port) with defaults. (FR-21, FR-22)
- [ ] T049 [US7] Ensure health endpoint survives QQ disconnect — `_run_health_server()` runs independently of WebSocket state. Returns `{"status": "ok", "qq_connected": bool}`. (FR-22)

**Checkpoint**: Sub-sessions auto-expire. Config validated on startup. Health endpoint works when QQ WS is down.

---

## Phase 9: Polish & Integration

**Goal**: Full test suite passes, docs updated.

- [ ] T042 Run full regression: `python -m pytest tests/ -v --ignore=tests/spec_e2e_test.py --ignore=tests/test_brain.py` — all existing tests pass. (SC-7)
- [ ] T043 [P] Update `AGENTS.md` — add `chat_core/qq/` to file index, add QQ Bot architecture section (dual-brain + multi sub-session + race tracker), update callback chain diagram.
- [ ] T044 [P] Update `CHANGELOG.md` — add `## [Unreleased] — QQ Bot 集成` entry.
- [ ] T045 [P] Clean up `chat_core/qq/__init__.py` — ensure lazy imports still resolve all exported symbols.
- [ ] T046 Verify startup: `python -c "from chat_core.qq import BotAdapter, MessageContext, RaceTracker"` imports cleanly without QQ credentials.

---

## Dependency Graph

```
Phase 1 (Audit)
  └─► Phase 2 (Foundational) ── Must complete ──►
       ├─► Phase 3 (US1: C2C) ────►
       │     ├─► Phase 4 (US2: Group)
       │     └─► Phase 6 (US4: Namespace)
       ├─► Phase 5 (US3: Race) ──►
       │     ├─► Phase 7 (US5: Profile)
       │     ├─► Phase 8 (US6: Proactive)
       │     └─► Phase 10 (US7: Session Mgmt)
       └─► Phase 9 (Polish)
```

**US1 and US3 can start in parallel** after Phase 2. US2 depends on US1 (needs process_message). US4 depends on US1 (needs archive). US5 and US6 depend on US3 (needs global dual-brain).

---

## Parallel Execution Examples

### Batch 1 (Phase 2 complete)
```
Agent A: US1 (T010-T016) — C2C pipeline
Agent B: US3 (T020-T030) — RaceTracker + SubconsciousInjector
```
Both work on different files: adapter.py sections are separable (C2C flow vs race tracking).

### Batch 2 (US1 + US3 complete)
```
Agent A: US2 (T017-T019) — Group routing
Agent B: US4 (T031-T034) — Namespace isolation
Agent C: US5 (T035-T038) — Profile building
```
All extend adapter.py but in different methods/functions.

---

## Suggested MVP Scope

**MVP = Phase 1 + Phase 2 + Phase 3 (US1)**

After MVP: single user can chat with AI via QQ private chat. The AI has personality and memory. No group support, no race tracking, no profile building. Core value proposition delivered.

---

## Format Validation

All tasks follow `- [ ] [TaskID] [P?] [Story?] Description with file path` format.
- 49 tasks total
- US1: 8 tasks (T010-T016, T013a)
- US2: 3 tasks (T017-T019)
- US3: 11 tasks (T020-T030)
- US4: 4 tasks (T031-T034)
- US5: 4 tasks (T035-T038)
- US6: 3 tasks (T039-T041)
- US7: 3 tasks (T047-T049)
- Setup + Foundational: 9 tasks (T001-T009)
- Polish: 5 tasks (T042-T046)
