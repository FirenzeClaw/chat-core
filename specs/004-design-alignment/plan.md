# Implementation Plan: 设计-实现对齐

**Feature**: `004-design-alignment`
**Source Spec**: [spec.md](./spec.md)
**Created**: 2026-07-10
**Status**: Draft

---

## Technical Context

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Language | Python 3.12+ | 与 chat-core 统一 |
| Async Pattern | `asyncio.create_task()` for review, `await` for sub-session | 审查不阻塞子Session 输出 |
| Review System | 复用 `ReviewSystem` | 审查逻辑不变，仅时序改为异步 |
| Correction Injection | `subconscious/corrections` namespace via MemoryStore | 跨 turn 持久化纠正，子Session 下一轮读取 |
| Subconscious Read | `_init_messages()` 中查询 `subconscious/corrections` | 在首次 `_think()` 前注入，LLM 自然感知 |
| Recursion Guard | `int` counter (`_correction_depth ≤ 2`) | 替换布尔 `_in_correction`，支持 2 层纠正 |
| Weights | `combined = logic × 0.5 + emotion × 0.5` | 设计 Ch6.1 要求双脑平等 |

### Performance Target

| Metric | Target | Source |
|--------|--------|--------|
| CLI first reply | ≤ 3 seconds | SC-1 |
| QQ Bot first reply | ≤ 5 seconds | SC-3 |
| Review overhead on first reply | ≤ 0.3 seconds | SC-2 |
| All existing tests | 129 passed | SC-5 |
| New tests | ≥ 8 | SC-8 |

### Modules to Modify

| Module | Changes | Risk |
|--------|---------|------|
| `core/turn_manager.py` | 审查异步化、纠正延迟、纠正恢复完整能力 | 🔴 核心时序重排 |
| `core/loop.py` | `_init_messages()` 新增 subconscious 读取 | 🟡 注入时机需精确 |
| `systems/review.py` | 权重公式 `0.6/0.4` → `0.5/0.5` | 🟢 一行改动 |
| `qq/adapter.py` | 新增异步审查 + 纠正管线、还原 send_reply 立即发送 | 🔴 QQ Bot 新增管线 |

---

## Constitution Check

N/A — 项目无 constitution.md。本计划遵循 `chat-core-design.md` 作为设计基线，`AGENTS.md` 作为编码约定基线。

> **Note**: `tasks.md` is the authoritative task list with final T001–T017 numbering. This plan's earlier T001–T012 numbering is superseded.

---

## Phase 1: 基础改造（ReviewSystem + ReActLoop）

### T001: ReviewSystem 权重公式更新
- **文件**: `systems/review.py`
- **改动**: `combined = logic_weight * 0.6 + emotion_weight * 0.4` → `0.5 + 0.5`
- **位置**: `review.py:353` 和 `review.py:463`
- **验证**: 运行 `test_review` 相关测试

### T002: ReActLoop 新增 subconscious 读取
- **文件**: `core/loop.py`
- **改动**: `_init_messages()` 中查询 `subconscious/corrections` namespace，以 `Message(role="system", content=f"[注意] {correction}")` 注入
- **逻辑**: 查询匹配当前 user_id 的 corrections，过滤已处理的（按 `last_access`），注入后更新 `last_access`
- **验证**: 新测试 — 注入后 `_messages` 含 system message

### T003: 纠正递归深度计数器
- **文件**: `core/turn_manager.py`
- **改动**: `_in_correction: bool` → `_correction_depth: int`，纠正前 `depth += 1`，纠正后 `depth -= 1`，`depth > 2` 时跳过
- **验证**: 新测试 — 连续 3 次纠正触发后第 3 次被跳过

---

## Phase 2: CLI 审查异步化

### T004: TurnManager 审查改异步
- **文件**: `core/turn_manager.py`
- **改动**: `process_turn()` 中第 4-5 步（审查+决策）从 `await` 改为 `asyncio.create_task(_async_review_and_decide(...))`
- **关键**: `_async_review_and_decide` 是新的异步方法，包含审查、权重协商、写 subconscious。异常静默降级（logger.exception）
- **时序**: `process_turn()` 在第 3 步子Session 完成后立即返回，审查在后台运行
- **验证**: 子Session 输出后 `process_turn()` 立即返回（不等待审查）

### T005: _issue_correction 改为只写不跑
- **文件**: `core/turn_manager.py`
- **改动**: `_issue_correction()` 删除 `_run_correction_sub_session()` 调用，只保留写入 `subconscious/corrections`
- **保留**: 拧巴记录写入 `self/feelings/twisted`
- **验证**: 审查后 subconscious 有 corrections 条目，但无纠正子Session 运行

### T006: 纠正子Session 恢复完整能力
- **文件**: `core/turn_manager.py`
- **改动**: `_run_correction_sub_session()` 恢复完整 ReActLoop：
  - `max_iter`: 2 → 5
  - 重新开启 `inner_thoughts` 工具
  - 纠正的 inner_thoughts 归档到 `self/inner_thoughts/`
  - 纠正的 send_reply 归档到 `user/{uid}/conversations/`
  - 恢复 `wait` 工具（自然停顿）
- **验证**: 纠正子Session 可多段发言 + 有内心戏

### T007: 双脑持续情绪通知通道
- **文件**: `core/turn_manager.py`
- **改动**: 在 `_dual_recall` 完成后，情感主脑检测显著情绪变化 → `event_bus.publish("emotion_alert", ...)` → 逻辑主脑 Phase 2 注入时读取调整方向
- **验证**: 情绪变化时 alert 事件被发布

---

## Phase 3: QQ Bot 审查管线

### T008: BotAdapter 还原 send_reply 立即发送
- **文件**: `qq/adapter.py`
- **改动**: `_on_reply` 回调恢复 `send_fn(text)` 调用
- **验证**: 子Session 发言直接发送到 QQ

### T009: BotAdapter 新增异步审查
- **文件**: `qq/adapter.py`
- **改动**: `_process()` 中 `loop.run()` 完成后，`asyncio.create_task(self._async_review(replies, inner, memories, ctx))`
- **新方法**: `_async_review()` — 调用 `self._review_system.review()` → 写 `subconscious/corrections`
- **注意**: 需要从 `_inject_async` 获取 memories。方案：修改 `_inject_async` 返回 `(context, memories)`
- **验证**: QQ Bot 有审查纠正能力

### T010: 纠正内容归档
- **文件**: `qq/adapter.py`
- **改动**: 纠正子Session 的 inner_thoughts 和 send_reply 写入 MemoryStore
- **验证**: 纠正产生的内心戏和发言可查询

---

## Phase 4: 测试与验证

### T011: 新增测试
- **文件**: `tests/test_design_alignment.py`（新建）
- **覆盖**:
  1. 审查异步执行（不阻塞子Session）
  2. subconscious/corrections 注入到 `_init_messages`
  3. 纠正递归深度 ≤ 2
  4. 权重公式 `0.5/0.5`
  5. 纠正子Session 有 inner_thoughts
  6. 拧巴记录写入 `self/feelings/twisted`
  7. 审查异常静默降级
  8. 情感通知通道发布事件
- **验证**: `pytest tests/test_design_alignment.py -v` 全部通过

### T012: 回归测试
- **验证**: `pytest tests/ -q` 129 tests 全部通过

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| 审查异步化导致 turn_manager 状态竞争 | 🔴 高 | 审查任务操作独立的 subconscious namespace，不修改 turn_manager 共享状态 |
| 子Session 读 subconscious 时机错误 | 🟡 中 | 在 `_init_messages` 中、首次 `_think()` 前注入，不影响 ReAct 循环逻辑 |
| QQ Bot 异步审查增加延迟 | 🟢 低 | 审查是 `create_task`，不影响 send_reply 发送 |
| 纠正能力恢复后 recursive 爆炸 | 🟡 中 | `_correction_depth ≤ 2` 硬限制 |

---

## Dependency Order

```
Phase 1 (T001→T002→T003)  ← 无依赖，可并行
    ↓
Phase 2 (T004→T005→T006, T007 可并行)
    ↓
Phase 3 (T008→T009→T010)  ← 依赖 Phase 1
    ↓
Phase 4 (T011→T012)
```
