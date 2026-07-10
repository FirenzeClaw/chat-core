# Design: 认知增强 — 直觉 + 创造力 + 幽默 + 道德困境

> **Feature**: cognitive-enhancement (Spec 009)
> **Status**: Design Draft
> **Created**: 2026-07-10
> **Context**: chat-core 当前子Session 推理是固定的 think→act→observe 循环，缺少快速直觉反应、真正的创造性联想、自然的幽默感、以及面对道德两难的深层次决策。本设计补齐四种认知增强能力，全部改造子Session 的推理路径。

---

## 1. 设计目标

- **直觉系统**：三级降级推理（记忆匹配 → Fast Path → 完整 ReAct），受注意力和疲劳状态调制
- **创造力**：双路径概念发散（LLM 概念跳跃 + 记忆联锁放大），合并注入子Session
- **幽默**：轻量规则检测（预期违背 + 双关语）+ 关系安全检测，不强制、只提示
- **道德困境**：MoralConflict 检测 + 双脑 Pro/Con 评估 + 元认知升级兜底
- **与全部已有七系统联动**：Spec 003/005/006/007/008 + 注意力状态机 + 人格系统

---

## 2. 直觉系统 — 三级降级推理

### 2.1 架构

```
用户消息到达:
  │
  ├─ Level 1: 记忆模式匹配 (最快, 零 LLM 成本)
  │     recall = search_chained(query, config) (→ Spec 003)
  │     if 命中 ≥5 条 AND 最高 salience ≥ 7:
  │       → 合成快速回复 (模板拼接高 salience 记忆摘要 + 用户情绪适配)
  │       → 直接 send_reply, 跳过子Session LLM
  │       → inner_thoughts: "[直觉回复] 基于强记忆直接反应"
  │
  ├─ Level 2: Fast Path (不满足 L1, 单次 Flash 调用)
  │     LLM: DeepSeek Flash, no function calling, reasoning_effort=low
  │     prompt: 用户消息 + 记忆回溯 + 当前情绪 + "[直觉模式] 快速给出你的第一反应"
  │     if 回答置信度 > 0.7 (默认长度启发式: 回复≥50字符=高置信度; 可配为自评模式):
  │       → 合成 send_reply
  │       → inner_thoughts: "[快速反应] 第一反应，未经深度思考"
  │
  └─ Level 3: 完整 ReAct (不满足 L1/L2, or L2 置信度 ≤ 0.7)
        现有 ReActLoop 完整推断循环 (不变)
```

### 2.2 状态调制

| 状态 | L1 概率 | L2 概率 | L3 概率 | 说明 |
|------|:---:|:---:|:---:|------|
| FOCUSED (focus≥0.6) | ×1.5 | 正常 | 正常 | 专注时直觉更准 |
| DRIFTING (0.3≤focus<0.6) | 正常 | 正常 | 正常 | 不作调制 |
| DULL (focus<0.3) | ×0.5 | ×0.7 | ×2.0 | 走神时偏向完整推理（强迫自己认真） |
| energy < 0.3 (→ Spec 007) | ×1.3 | ×1.2 | ×0.7 | 累了优先快速反应 |

### 2.3 与注意力的关键差异

注意力状态机改变的是 `_should_continue` 的**回复质量**（段数、详细度），直觉系统改变的是 `_think()` 的**推理深度**（跳过多层推理直接反应）。两者正交可叠加：DULL + L3 = 虽然推理完整但回复极简。

---

## 3. 创造力 — 双路径概念发散

### 3.1 触发条件

`playfulness > 0.5` (→ 人格系统) 或 用户消息含开放性问题（"你觉得为什么..."、"如果...会怎样"）。

### 3.2 Path A: LLM 概念发散

```
Flash 模型单次调用 (no function calling):
  prompt: "对 [{用户消息中的关键词}] 做远距离概念联想,
           输出 3-5 个跨领域映射"
  
  示例: "篮球" →
    - "团队协作 → 蚂蚁社会的分工机制"
    - "弧线美学 → 弹道学和天体轨道"
    - "高度竞争 → 进化论中的自然选择"
    - "仪式感 → 原始部落的战舞"
    - "随机性 → 量子力学的概率波"
```

### 3.3 Path B: 记忆联锁放大 (→ Spec 003)

```
临时扩大 chain_config:
  search_chained(query, RecallChainConfig(
    top_n=5, extensions=[5,5,5,5,5], max_per_level=5
  ))
  → 远距离关联记忆大量涌入
  → 过滤 chain_level ≥ 3 的条目（仅保留意外关联，不保留直接匹配和显式链接）
```

### 3.4 合并注入

```
system prompt 追加:
  "[创造力增强]
   概念发散 (来自远距离联想):
     {Path A 结果}
   意外关联记忆 (你之前没意识到有关联的):
     {Path B 中 chain_level ≥ 3 的条目摘要}"
```

**与人格联动**：高 creativity → Path A 权重更大（更多主动发散），低 creativity → Path B 为主（依赖已有记忆关联）。

---

## 4. 幽默 — 预期违背 + LLM 填充

### 4.1 HumorDetector（纯规则，零 LLM）

```python
class HumorDetector:
    def detect(self, user_message: str, context: list[Message],
               relationship_stage: str) -> list[HumorOpportunity]:
        
        # 关系安全检测 (→ Spec 008)
        if relationship_stage not in ("friend", "close_friend"):
            return []  # 陌生人/熟人不触发幽默
        
        opportunities = []
        
        # 1. 预期违背
        if self._is_question(user_message):
            expected = self._predict_expected_answer(user_message, context)
            if expected:
                opportunities.append(HumorOpportunity(
                    type="expectation_violation",
                    expected=expected,
                    hint=f"用户可能在期待'{expected}'，你可以故意给一个反差回复"
                ))
        
        # 2. 双关语
        ambiguous = self._find_ambiguous_word(user_message)
        if ambiguous:
            opportunities.append(HumorOpportunity(
                type="pun",
                word=ambiguous,
                hint=f"'{ambiguous}'有双重含义，可以巧妙地利用这一点"
            ))
        
        return opportunities
```

### 4.2 消费方式

检测到的幽默机会作为 system prompt **提示**注入，不强制：

```
"[幽默机会] 你可以利用'{word}'的双关含义——但只在觉得合适且自然的时候用。"
```

LLM 自行判断是否采纳。幽默感无法被规则强制。

---

## 5. 道德困境 — MoralConflict + 双脑 Pro/Con

### 5.1 三种冲突类型

| 类型 | 触发条件 | 关联系统 |
|------|---------|---------|
| **诚实 vs 保护** | 用户请 AI 评价某物/某人 + AI 内心判断为负面 + 关系 ≥ 朋友 | Spec 008 closeness |
| **忠诚冲突** | 用户 A 对 AI 说用户 B 的坏话 + A 和 B 都有社交记忆 | Spec 008 跨群 |
| **自我 vs 他人** | energy < 0.2 + 用户想继续聊 + AI 想退出 | Spec 007 EnergyBar |

### 5.2 双脑 Pro/Con 评估

```python
@dataclass
class ProConAssessment:
    """双脑对道德困境的评估结果"""
    logic_score: float      # LogicBrain: 真相/原则的价值
    logic_reasoning: str
    emotion_score: float    # EmotionBrain: 关系/感受的价值
    emotion_reasoning: str
    deadlock: bool          # |logic - emotion| < threshold → 两难
    escalation: bool        # |logic - emotion| > threshold → 升级元认知
```

**决策逻辑**：
```
if |logic - emotion| < 0.2:   → 两难, 写 subconscious/moral_conflict, 子Session 自行决定
elif logic > emotion:          → 真诚路径: 说真话但软化语气
else:                          → 保护路径: 回避直接回答但给建设性反馈

if |logic - emotion| > 0.4:   → 升级到 Spec 006 元认知做深度审视
```

### 5.3 存储

道德困境的决策过程写入 `self/moral/{turn_id}`，供后续元认知分析。"我上次选择了真诚，结果用户三天没跟我说话——下次也许该更小心。"

---

## 6. 数据流集成

```
用户消息:
  │
  ├─ ① 直觉判定 (改造 _think() 之前):
  │     IntuitionEngine.evaluate(memory_hits, attention_state, energy)
  │       ├─ L1 (记忆匹配) → send_reply 直接输出 → 结束
  │       ├─ L2 (Fast Path) → Flash 单次调用 → 可能结束
  │       └─ L3 → 进入正常 ReAct
  │
  ├─ ② 创造力发散 (L3 路径下, _think() 之前):
  │     CreativityEngine.diverge(user_message, personality)
  │       ├─ Path A: Flash 概念发散
  │       ├─ Path B: search_chained(extended_config)
  │       └─ 合并 → 注入 system prompt
  │
  ├─ ③ 幽默检测 (L3 路径下, _think() 之前):
  │     HumorDetector.detect(user_message, context, relationship_stage)
  │       → humor_hints 注入 system prompt (只提示不强制)
  │
  ├─ ④ 子Session ReAct (可能含创造力上下文 + 幽默提示):
  │     think → act → observe → 正常结束
  │
  ├─ ⑤ 道德困境检测 (审查阶段):
  │     MoralConflictDetector.detect(user_message, inner_thoughts, relationship, energy)
  │       ├─ 无冲突 → 正常审查
  │       └─ 有冲突 → ProConAssessor.evaluate() → 双脑评估 → 判定路径
  │              ├─ deadlock → 子Session 自行决定
  │              └─ escalation → 触发 Spec 006 元认知
  │
  └─ ⑥ 归档:
        moral_conflict 决策 → self/moral/{turn_id}
        intuition_level → turn metadata
```

---

## 7. 配置外化

```yaml
systems:
  intuition:
    enabled: true
    level1:
      min_memory_hits: 5
      min_salience: 7
    level2:
      model: deepseek-v4-flash
      confidence_threshold: 0.7
      reasoning_effort: low
    state_modulation:
      focused_l1_boost: 1.5
      dull_l3_boost: 2.0
      low_energy_l1_boost: 1.3
  
  creativity:
    enabled: true
    trigger_playfulness_min: 0.5
    path_a:
      model: deepseek-v4-flash
      num_mappings: 5
    path_b:
      extended_top_n: 5
      extended_extensions: [5, 5, 5, 5, 5]
      extended_max_per_level: 5
      chain_level_filter: 3       # 仅保留 chain_level ≥ 此值的条目
    personality_weight:
      creativity_bias_a: 0.7

  humor:
    enabled: true
    min_relationship_stage: friend
    opportunity_types: [expectation_violation, pun]
    
  moral_conflict:
    enabled: true
    types: [honesty_vs_protection, loyalty_conflict, self_vs_other]
    pro_con:
      logic_model: deepseek-v4-pro
      emotion_model: deepseek-v4-pro
      deadlock_threshold: 0.2
      escalate_to_metacognition: 0.4
```

---

## 8. 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `core/types.py` | + `IntuitionResult`, `CreativityContext`, `HumorOpportunity`, `MoralConflict`, `ProConAssessment` | 数据结构 |
| `systems/intuition.py` | **新建** — `IntuitionEngine`: 三级降级调度 + 状态调制 | 核心 |
| `systems/creativity.py` | **新建** — `CreativityEngine`: Path A LLM 发散 + Path B 联锁放大 + 合并注入 | 核心 |
| `systems/humor.py` | **新建** — `HumorDetector`: 预期违背 + 双关语 + 关系安全 | 核心 |
| `systems/moral.py` | **新建** — `MoralConflictDetector` + `ProConAssessor`: 检测 + 双脑评估 | 核心 |
| `core/loop.py` | `_think()` 前接入 IntuitionEngine (可能跳过完整 ReAct) + 注入 creativity_context + humor_hints | 集成 |
| `core/brain.py` | + `LogicBrain.pro_con()` / `EmotionBrain.pro_con()` → 道德困境 Pro/Con 评估 | 执行 |
| `core/turn_manager.py` | 审查 flow 新增 MoralConflict 分支；归档 moral 决策；moral_conflict 写入 subconscious | 集成 |
| `systems/review.py` | `ReviewSystem` 接入 `MoralConflictDetector` | 集成 |
| `systems/memory.py` | `search_chained()` 新增 `extended_config` 参数 (Path B creativity) | 扩展 |
| `systems/metacognition.py` | 新增 `moral_conflict` 异常触发条件 (Spec 006) | 消费 |
| `config.yaml` | + `systems.intuition` + `systems.creativity` + `systems.humor` + `systems.moral_conflict` | 配置 |
| `tests/test_intuition.py` | **新建** — 三级降级、状态调制、快速回复格式 | 测试 |
| `tests/test_creativity.py` | **新建** — 概念发散、联锁放大、合并注入、人格联动 | 测试 |
| `tests/test_humor.py` | **新建** — 预期违背检测、双关语、关系安全门 | 测试 |
| `tests/test_moral.py` | **新建** — 三种冲突检测、Pro/Con 评估、deadlock、escalation | 测试 |

---

## 9. 全量联动矩阵（Spec 009 + 前 8 个系统）

| 提供方 | → 消费方 | 内容 |
|--------|---------|------|
| Spec 003 search_chained | IntuitionEngine (L1) | 记忆命中数 + salience → 快速回复触发 |
| Spec 003 extended chain | CreativityEngine (Path B) | 远距离联锁记忆 |
| 注意力状态机 (三态) | IntuitionEngine | FOCUSED/DULL → 直觉级别偏好 |
| Spec 007 EnergyBar | IntuitionEngine | 低精力 → 倾向快速反应 |
| Spec 007 EnergyBar | MoralConflictDetector | energy < 0.2 → 自我 vs 他人冲突 |
| PersonalityEngine (playfulness) | CreativityEngine | 触发概率 |
| PersonalityEngine (creativity) | CreativityEngine | Path A/B 权重比 |
| Spec 008 RelationshipEngine | HumorDetector | 关系阶段 → 幽默安全门 |
| Spec 008 RelationshipEngine | MoralConflictDetector | closeness → honesty_vs_protection stakes |
| Spec 005 EmotionEngine | IntuitionEngine (L1) | 情绪适配模板拼接 |
| LogicBrain + EmotionBrain | ProConAssessor | 双脑道德评估 |
| Spec 006 MetacognitionEngine | MoralConflict | deadlock > 0.4 → 升级元认知 |
| MoralConflict 决策 | Spec 003 MemoryStore | 写入 self/moral/{turn_id} |
| Intuition 级别 | turn metadata | 归档以便元认知分析直觉准确率 |

---

## 10. 成功标准

| ID | 标准 | 验证 |
|----|------|------|
| SC-01 | L1 直觉 — 强记忆触发 | ≥5 hit + salience≥7 → 直接 send_reply, 跳过子Session LLM |
| SC-02 | L2 直觉 — 快速反应 | confidence > 0.7 → 单次 Flash 回复, 跳过完整 ReAct |
| SC-03 | L3 降级兜底 | L1/L2 不满足 → 正常 ReActLoop |
| SC-04 | 直觉 — FOCUSED 调制 | 专注态 L1 概率 ×1.5 |
| SC-05 | 直觉 — 低精力调制 | energy < 0.3 → L1/L2 概率提升 |
| SC-06 | 创造力 Path A — 概念发散 | Flash 返回 3-5 个跨领域映射 |
| SC-07 | 创造力 Path B — 联锁放大 | extensions=[5,5,5,5,5], 过滤 chain_level ≥3 |
| SC-08 | 创造力合并注入 | system prompt 含发散映射 + 意外关联记忆 |
| SC-09 | 创造力 — playfulness 触发 | playfulness ≤ 0.5 → 不触发 |
| SC-10 | 幽默 — 预期违背检测 | 反问句被检测 → humor_hint 注入 |
| SC-11 | 幽默 — 关系安全门 | 陌生人/熟人 → 不注入幽默提示 |
| SC-12 | 道德 — 诚实 vs 保护 | 评价请求 + 负面判断 → MoralConflict 触发 |
| SC-13 | 道德 — Pro/Con 双脑评估 | LogicBrain + EmotionBrain 各输出 score + reasoning |
| SC-14 | 道德 — deadlock | |diff| < 0.2 → 两难, 写 subconscious/moral_conflict |
| SC-15 | 道德 — 元认知升级 | |diff| > 0.4 → Spec 006 元认知审查介入 |
| SC-16 | 道德 — 决策归档 | self/moral/{turn_id} 写入完整评估链路 |
| SC-17 | 零回归 | 所有现有 154 tests 通过 |
| SC-18 | 新增测试 ≥ 12 条 | pytest count 验证 |
