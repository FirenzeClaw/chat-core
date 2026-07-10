# Tasks: 设计-实现对齐

**Feature**: `004-design-alignment`
**Source**: [plan.md](./plan.md) | [spec.md](./spec.md)
**Created**: 2026-07-10
**Status**: Ready

---

## Phase 1: 基础改造（共享依赖）

**Goal**: 完成权重、subconscious 读取、递归防护三个底层改动，为 CLI 和 QQ Bot 审查管线提供基础。

**Independent Test**: 权重公式 0.5/0.5 生效、subconscious 注入到 _init_messages、递归深度计数器 ≤ 2。

### Tasks

- [ ] T001 [P] 更新权重公式 0.6/0.4 → 0.5/0.5 in `chat_core/systems/review.py`
- [ ] T002 [P] 子Session `_init_messages()` 新增 subconscious/corrections 读取注入 in `chat_core/core/loop.py`
- [ ] T003 [P] 纠正递归深度 `_in_correction: bool` → `_correction_depth: int` in `chat_core/core/turn_manager.py`

---

## Phase 2: CLI 审查异步化 (US1)

**Goal**: CLI 审查不再阻塞子Session 输出，纠正延迟到下一轮生效，纠正子Session 恢复完整能力。

**User Story**: US1 — CLI 用户收到即时回复，后续发现 AI 自我纠正

**Independent Test**: 子Session send_reply 立即输出到终端，审查在后台异步运行。上一轮纠正写 subconscious，下一轮子Session 自动读取并自然纠正。

### Tasks

- [ ] T004 [US1] `process_turn()` 审查步骤改为 `asyncio.create_task` 异步执行 in `chat_core/core/turn_manager.py`
- [ ] T005 [US1] `_async_review_and_decide()` 新方法：审查 → 权重协商 → 写 subconscious/corrections → 异常静默降级 in `chat_core/core/turn_manager.py`
- [ ] T006 [US1] `_issue_correction()` 删除 `_run_correction_sub_session()` 调用，只保留写 subconscious + 拧巴记录 in `chat_core/core/turn_manager.py`
- [ ] T007 [US1] `_run_correction_sub_session()` 恢复完整能力：max_iter→5, inner_thoughts 开启, wait 开启, 集成 `_correction_depth` 检查（>2 跳过）, 归档纠正内容 in `chat_core/core/turn_manager.py`
- [ ] T008 [US1] 新增 `emotion_alert` 持续通道：情感主脑检测情绪变化 → event_bus.publish → 逻辑主脑调整注入 in `chat_core/core/turn_manager.py`

---

## Phase 3: QQ Bot 审查管线 (US2)

**Goal**: QQ Bot 拥有与 CLI 一致的审查纠正能力，审查异步不阻塞 send_reply。

**User Story**: US2 — QQ Bot 用户经历同样的审查纠正流程

**Independent Test**: QQ Bot 子Session send_reply 立即发送到 QQ，审查异步后台运行，纠正下一轮生效。

### Tasks

- [ ] T009 [US2] `_on_reply` 回调恢复立即调用 `send_fn` 发送到 QQ in `chat_core/qq/adapter.py`
- [ ] T010 [US2] `_inject_async` 返回 `(context, memories)` 供审查使用 in `chat_core/qq/adapter.py`
- [ ] T011 [US2] `_process()` 中 `loop.run()` 后新增 `asyncio.create_task(_async_review())` in `chat_core/qq/adapter.py`
- [ ] T012 [US2] `_async_review()` 新方法：调用 ReviewSystem.review → 写 subconscious/corrections in `chat_core/qq/adapter.py`
- [ ] T013 [US2] 纠正子Session inner_thoughts 和 send_reply 归档到 MemoryStore in `chat_core/qq/adapter.py`

---

## Phase 4: 沉默场景 (US3)

**Goal**: 低权重错误不触发纠正，沉默有累积效应，非永久。

**User Story**: US3 — AI 发现错误但选择不说

**Independent Test**: combined ≤ 0.5 时只归档到 self/noticed，不写 subconscious/corrections。沉默累积器递增。

### Tasks

- [ ] T014 [US3] 审查结果 `combined ≤ 0.5` 时归档到 `self/noticed/`，递增沉默累积器 in `chat_core/core/turn_manager.py`
- [ ] T015 [US3] 沉默累积器阈值触发 FuzzyParam 调整：`_silence_counters[error_type] ≥ 3` 时 `FuzzyParam.base = min(0.3, count × 0.05)` 提升下次纠正概率，确保与异步审查兼容 in `chat_core/core/turn_manager.py`

---

## Phase 5: 测试与验证

**Goal**: 新增 8 条测试覆盖关键改动，全部 129 回归通过。

### Tasks

- [ ] T016 [P] 新建 `tests/test_design_alignment.py`：审查异步 + subconscious 注入 + 递归深度 + 权重 + 拧巴 + 异常降级 + 情绪通道 + 沉默归档
- [ ] T017 回归测试：`pytest tests/ -q` 129 passed，含竞态追踪 (RaceTracker) 冒烟验证 in `tests/`

---

## Dependencies

```
Phase 1 (T001 ∥ T002 ∥ T003)
    ↓
Phase 2 (T004 → T005 → T006 → T007, T008 ∥)
    ↓
Phase 3 (T009 → T010 → T011 → T012 → T013)
    ↓
Phase 4 (T014 → T015)
    ↓
Phase 5 (T016 ∥ T017)
```

---

> **Coordination Note**: T006 和 T014 都修改 `_issue_correction()` 方法。T006 删 immediate correction 调用并保留写入逻辑；T014 加沉默归档路径。实现时需按此顺序在同一方法体中完成，避免冲突。

---

## Parallel Opportunities

| 组 | 任务 | 说明 |
|----|------|------|
| A | T001, T002, T003 | 三文件独立，无依赖 |
| B | T008 ∥ T004–T007 | 情绪通道独立 |
| C | T016, T017 | 新测试和回归可并行 |

---

## MVP Scope

**MVP = Phase 1 + Phase 2（CLI 审查异步化）**

完成后 CLI 即拥有异步审查 + 延迟纠正能力，可独立验证核心设计原则。

---

## File Change Summary

| 文件 | 任务 | 改动量 |
|------|------|--------|
| `systems/review.py` | T001 | ~2 行 |
| `core/loop.py` | T002 | ~20 行 |
| `core/turn_manager.py` | T003–T008, T014–T015 | ~100 行（重构） |
| `qq/adapter.py` | T009–T013 | ~50 行 |
| `tests/test_design_alignment.py` | T016 | ~150 行（新建） |
