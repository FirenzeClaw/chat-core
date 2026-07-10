# chat-core 子系统实施路线图

> **生成时间**: 2026-07-10
> **状态**: Phase 1 ✅ + Phase 2 推进中 (Spec 005 ✅, Spec 007 ✅)
> **背景**: 基于 19 项"与人类的差异"分析，产出 8 个 Spec（含 1 个基础系统），覆盖全部可实现差距。

---

## 一、全量 Spec 总览

| # | Spec | 设计文档 | 新 FR | 新 tests | 新文件 | 状态 |
|:---:|------|---------|:---:|:---:|:---:|:---:|
| — | **注意力状态机** (基础) | `2026-07-10-attention-state-machine-design.md` | — | +14 | 1 | ✅ 已完成 |
| 003 | 记忆联锁 + 遗忘曲线 | `2026-07-10-memory-chain-recall-design.md` | 26 | +7 | 0 | ✅ §12 完成 (核心已落地) |
| 005 | 复合情绪 + 防御机制 | `2026-07-10-compound-emotion-defense-design.md` | 26 | +42 | 2 | ✅ 已完成 |
| 006 | 元认知深度 | `2026-07-10-metacognition-depth-design.md` | 14 | +29 | 2 | ✅ 已完成 |
| 007 | 具身感知 (疲劳+主观时间) | `2026-07-10-embodied-perception-design.md` | 16 | +15 | 2 | ✅ 已完成 |
| 008 | 社交与关系 | `2026-07-10-social-relationship-design.md` | 16 | +43 | 3 | ✅ 已完成 |
| 009 | 认知增强 | `2026-07-10-cognitive-enhancement-design.md` | 18 | +52 | 4 | ✅ 已完成 |
| 010 | 价值体系 + 自我叙事 | `2026-07-10-values-narrative-design.md` | 14 | +18 | 2 | ✅ 已完成 |
| 011 | 沉默语义 + 动机系统 | `2026-07-10-silence-motivation-design.md` | 18 | +92 | 3 | ✅ 已完成 |

> **总计**: ~148 新 FR，~281 新 tests，18 个新系统文件。**全部完成。**

---

## 二、依赖拓扑（实施顺序）

```
注意力状态机 (WIP)
    │
    ├─→ Spec 003 (记忆联锁+遗忘)
    │       │
    │       └─→ Spec 009 (认知增强 — Path B 创造力)
    │
    ├─→ Spec 005 (复合情绪+防御)
    │       │
    │       ├─→ Spec 006 (元认知) ──→ Spec 010 (价值观+叙事)
    │       │       │                    │
    │       │       └─→ Spec 008 (关系) ─┘
    │       │       │
    │       │       └─→ Spec 009 (道德困境升级)
    │       │
    │       ├─→ Spec 007 (具身感知)
    │       │       │
    │       │       └─→ Spec 008 (低精力降主动)
    │       │       └─→ Spec 009 (直觉状态调制)
    │       │
    │       └─→ Spec 009 (情绪调制创造力+幽默)
    │
    └─→ Spec 011 (沉默语义+动机)
            │
            └─→ Spec 006 (沉默模式进入元认知)
            └─→ Spec 008 (沉默驱动关系调整)
            └─→ Spec 010 (价值观沉默后悔)
```

**关键路径**: 注意力状态机 → 005 → 006 → 010

---

## 三、分阶段实施计划

### Phase 1: 底层基础（可并行）

| 优先级 | Spec | 理由 | 预估工作量 |
|:---:|------|------|:---:|
| P0 | 注意力状态机 | 阻断子Session（focus<0.15），已设计，已完成 | ✅ |
| P0 | Spec 003 §12 | 幂律遗忘 + 双向迁移；核心联锁检索已落地 | ✅ |

**Phase 1 验收**: 注意力三态 + 幂律遗忘 + 175 tests 零回归 ✅

---

### Phase 2: 情绪与自我（串行依赖）

| 优先级 | Spec | 依赖 | 预估工作量 |
|:---:|------|------|:---:|
| P1 | Spec 005 | 注意力状态机（compound_alert） | 5-7 天 |
| P1 | Spec 007 | Spec 005（情绪调制主观时间 + 防御联动能量） | 3-4 天 |
| P1 | Spec 006 | Spec 005 + 003 + 007（复合情绪+记忆+能量数据） | 4-5 天 |
| P1 | Spec 010 | Spec 006（元认知发现防御→调权） | 3-4 天 |

**Phase 2 验收**: AI 有复合情绪 + 会疲劳 + 会自我反思 + 有价值观

---

### Phase 3: 社交与认知（可部分并行）

| 优先级 | Spec | 依赖 | 预估工作量 |
|:---:|------|------|:---:|
| P2 | Spec 008 | Spec 003 + 005 + 006 + 007（全底层） | ✅ 已完成 |
| P2 | Spec 011 | Spec 007 + 008（沉默需能量+关系数据） | ✅ 已完成 |
| P2 | Spec 009 | Spec 003 + 005 + 007 + 008（全底层） | ✅ 已完成 |

**Phase 3 验收**: AI 有社交梯度 + 群感知 + 直觉 + 创造力 + 幽默 + 道德判断 + 沉默语义 + 动机系统 ✅

---

## 四、改动文件热力图

按被修改频次排序（越改越多的文件应优先稳定接口）：

| 文件 | Phase 1 | Phase 2 | Phase 3 | 总改动 |
|------|:---:|:---:|:---:|:---:|
| `core/types.py` | +2 | +16 | +12 | **30** |
| `core/turn_manager.py` | 1 | 5 | 4 | **10** |
| `config.yaml` | 2 | 4 | 4 | **10** |
| `core/loop.py` | 1 | 4 | 2 | **7** |
| `systems/memory.py` | 1 | 2 | 3 | **6** |
| `systems/emotion.py` | 1 | 3 | 2 | **6** |
| `core/brain.py` | 1 | 3 | 2 | **6** |
| `systems/metacognition.py` | — | 2 | 2 | **4** |
| `systems/defense.py` | — | 3 | 1 | **4** |
| `systems/attention.py` | 1 | 1 | 1 | **3** |

**建议**: `core/types.py` 先定义全部新增 dataclass（一次性），各 Spec 逐步消费。

---

## 五、测试策略

| 阶段 | 新增 tests | 回归基线 | 策略 |
|------|:---:|:---:|------|
| Phase 1 | +14 | 168 | 注意力状态机 14 测试，零回归 ✅ |
| Phase 2 | ~104 | 290 | 情绪+能量+元认知+价值观集成测试 ✅ |
| Phase 3 | ~163 | 466 | 关系+认知+沉默+动机全量集成测试 ✅ |
| **最终** | **~281** | **466** | 全量 `pytest tests/ -q` 通过 (1 预存 flaky) |

---

## 六、配置新增总览

```yaml
# config.yaml 新增段（全部 Phase 完成后）
systems:
  attention:        # Phase 1 — 状态机参数
    state_machine: {...}
    drift: {...}
    fatigue: {...}
  memory:           # Phase 1 — 原有 systems.memory.decay 段追加
    decay: {...}
  emotion:          # Phase 2 — 原有 systems.emotion 下追加
    compound: {...}
    defense: {...}
    vulnerability: {...}
  energy:           # Phase 2
    {...}
  subjective_time:  # Phase 2
    {...}
  metacognition:    # Phase 2
    {...}
  values:           # Phase 2
    {...}
  narrative:        # Phase 2
    {...}
  relationship:     # Phase 3
    {...}
  group_dynamics:   # Phase 3
    {...}
  patterns:         # Phase 3
    {...}
  silence_semantics:# Phase 3
    {...}
  motivations:      # Phase 3
    {...}
  loneliness:       # Phase 3
    {...}
  intuition:        # Phase 3
    {...}
  creativity:       # Phase 3
    {...}
  humor:            # Phase 3
    {...}
  moral_conflict:   # Phase 3
    {...}
```

---

## 七、风险与注意事项

| 风险 | 影响 | 缓解 |
|------|------|------|
| `core/types.py` 30 次改动 → 合并冲突 | 🔴 | Phase 1 即定义全部新增 dataclass 骨架，后续只填字段 |
| Spec 006 依赖 10 个数据源 → 集成复杂 | 🟡 | `build_context()` 参数逐步追加，每个 Phase 增量测试 |
| Spec 003 幂律衰减 `created_at_epoch` 列未落地 | ✅ | 已完成 schema 迁移 + 存量回填 |
| 注意力状态机 + Spec 005 同时改 `systems/emotion.py` | 🟡 | 注意力先落地，Spec 005 在其稳定后叠加 |
| QQ Bot 多用户场景部分 Spec 未显式测试 | 🟢 | Spec 008/011 的 per-user 行为在 Phase 3 专项测试 |

---

## 八、下一步行动

1. ✅ 注意力状态机实施（已完成: 3 Sessions, 10 Tasks, 9 Files, 168 tests）
2. ✅ Spec 003 §12 幂律遗忘 + 双向迁移 (已完成: 1 Session, 7 Tasks, +7 tests)
3. ✅ Phase 2 全部完成 (Spec 005 ✅ → 007 ✅ → 006 ✅ → 010 ✅)
4. ✅ Phase 3 全部完成 (Spec 008 ✅ → 009 ✅ → 011 ✅)
5. 🎉 **全部 8 个 Spec 实施完毕。最终: 466 tests, 18 个新系统文件。**
6. 📋 **实施完整度审计完成 (2026-07-10)**：见 `docs/superpowers/specs/2026-07-10-spec-completeness-audit.md`
   - 核心引擎: 完整 (93% FR 覆盖)
   - 发现 6 个 P0 管线断裂 + 1 个 P1 参数桥接缺失 + 8 个 P2 局部遗漏
   - QQ Bot (adapter.py) 缺失 Spec 005/008/009/011 集成
   - 预估修复总改动量 ~150 行
7. ✅ **CLI 管线修复完成 (2026-07-10)**：见 `docs/superpowers/plans/2026-07-10-spec-completeness-fix.md`
   - P0 修复 6 项: SILENCE 路径、关系/防御调制、insight 注入、道德集成、build_context 桥接
   - P1 修复 1 项: 情感共鸣 valence
   - P2 修复 8 项: stub 方法、SubjectiveClock、脆弱安全门、moral escalation、compound_alert、ANGRY delta、编码约定
   - 附带修复: InterestModel 初始化排序 bug、SQLite migration DEFAULT (unixepoch()) bug
   - 最终: 467 tests, 0 failures
8. ✅ **QQ Bot Spec 集成完成 (2026-07-10)**：见 `docs/superpowers/plans/2026-07-10-qq-bot-spec-integration.md`
   - adapter.py 集成 14 子系统 (Spec 005/008/009/010/011)
   - Stage 1: DefenseEngine + ValueEngine + 脆弱感检测
   - Stage 2: RelationshipEngine + PatternDetector + GroupDynamics 补充 + 关系调制
   - Stage 3a: SilenceClassifier + MotivationEngine + LonelinessDetector
   - Stage 3b: IntuitionEngine + CreativityEngine + HumorDetector + MoralConflictDetector
   - adapter.py 719→1010 行 (+291), qq_bot.py 零变更
   - QQ Bot 已启动运行，子系统验证 rel/pat/sil/mot/lon/cre/hum/mor=on
9. ⚠️ **已知问题**: DeepSeek API 400 偶发于子Session 第 3+ 段 (错误信息 "An assistant...")，根因待确认
   - QQ Bot (adapter.py) 缺失 Spec 005/008/009/011 集成
   - 预估修复总改动量 ~150 行
