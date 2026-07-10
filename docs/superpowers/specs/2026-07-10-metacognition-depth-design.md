# Design: 元认知深度

> **Feature**: metacognition-depth (Spec 006)
> **Status**: Implemented ✅ (2026-07-10, 14 commits, 29 tests, 零回归)
> **Created**: 2026-07-10
> **Context**: chat-core 当前 inner_thoughts 只有单层反思（"我刚才说得好不好"），缺少跨多轮的自我觉察、审查标准的自我质疑、行为模式的发现。元认知系统补齐"反思的反思"能力，产出文本洞察和结构化参数调节。

---

## 1. 设计目标

- **定期 + 异常双触发**：每 N 轮自动审视 + 异常事件即时审视
- **双输出**：自然语言洞察（注入 system prompt）+ 结构化参数调节（直接修改运行时行为）
- **与已有系统联动**：消费复合情绪趋势、防御意识历史、记忆回溯质量——全部来自 Spec 003/005
- **无新增 LLM 依赖**：复用 LogicBrain（DeepSeek Pro），单次 function calling

---

## 2. 触发机制

```
每次 _async_review_and_decide() 完成后:
  │
  ├─ 定期触发: turn_counter % N == 0  (N 默认 5, 可配置)
  │
  └─ 异常触发 (任一满足):
       ├─ 审查连续 ≥3 轮同结论 (连 CORRECT 或 连 SILENCE)
       ├─ 防御连续 ≥2 轮被激活
       ├─ |Δcompound| > 0.4  (情绪冲击——复用 Spec 005 compound_alert)
       └─ inner_thoughts 连续 ≥3 轮含同类自我批评关键词
          ("不该这么说"/"又说错了"/"太机械了"/"没意思"/"不想聊了")
```

触发后重置所有异常计数器，同 turn 不重复触发。

---

## 3. 输入数据（联动全部归档系统）

LogicBrain 的元认知 pass 接收结构化上下文，包含 Spec 003/005/006 的关键信号：

```
[元认知审查] 请审视你最近的行为模式:

最近 N 轮:
  - turn_042: 话题="游戏", 基本情绪=joy 0.6 trust 0.7,
    复合情绪=gratification 0.3, 审查=SILENCE, 防御=无, 记忆回溯=5条(2级链)
  - turn_043: 话题="游戏", 基本情绪=joy 0.5 trust 0.5,
    复合情绪=gratification 0.1 resentment 0.2, 审查=CORRECT, 防御=DENIAL,
    防御意识="拒绝了关于身份信息的纠正", 记忆回溯=3条(1级链)
  ...

复合情绪趋势 (← Spec 005):
  gratification: 0.2→0.1→0.05 ↓ (满足感消退)
  resentment:    0.0→0.2→0.4  ↑ (积累不满)
  guilt:         0.1→0.3→0.2  → (先升后降, 可能被防御机制压制)

防御模式总结 (← Spec 005 defense_awareness):
  近N轮防御激活率: 40% (2/5)
  主要防御类型: DENIAL (50%), PROJECT (50%)
  关联话题: "游戏"(2次)
  防御意识记录:
    - "[自我感知] 你之前有防御反应，拒绝了关于X的纠正"
    - "[自我感知] 你把部分错误归因于外部因素"

记忆系统状态 (← Spec 003):
  平均回溯条目: 3.8 (偏低, 正常 6~13)
  空回溯次数: 1 ("脑子里暂时一片空白")
  衰减预警: 3条记忆滑向短期 (salience 4~5区间)
  深刻记忆稳固: 12条 (decay_curve='deep')

当前系统状态:
  审查阈值: 0.5, 防御基线概率: 0.7 (impulsiveness=0.3)
  兴趣权重top3: 游戏(0.8), AI(0.6), 篮球(0.4)
  注意力状态: DRIFTING (focus=0.45) (← 注意力状态机)
	精力与主观时间 (← Spec 007):
	  当前精力: 0.41, 趋势: 0.85→0.41 ↓ (持续消耗)
	  主观时间感知: 平均 speed_factor 0.65 (偏向沉浸)

	价值观状态 (← Spec 010):
	  Honesty: 0.68 (↓), Care: 0.63 (↑), Growth: 0.80
	  当前自我叙述: "我是一个倾向于诚实的人，但最近..."

	沉默模式 (← Spec 011):
	  近 N 轮: TACIT 2次, STRATEGIC 1次, ANGRY 1次
	  趋势: ANGRY 近期出现 → 可能在积累不满

	活跃动机 (← Spec 011):
	  当前: [socialize (boredom=0.6), check_on_user_X (care=0.63)]
	  冲突: 无

	关系分布 (← Spec 008):
	  密友 1人 (小刚), 朋友 3人, 熟人 12人
	  最近关系变化: 小刚 → 密友 (升级)

	直觉使用率 (← Spec 009):
	  L1: 15% (命中率 80%), L2: 30% (置信度均值 0.72), L3: 55%

	脆弱历史 (← Spec 005 §9):
	  最近 1 次: turn_048, 对 小刚, 触发=guilt(0.72)
	  对方回应: supportive, closeness +0.05
```

---

## 4. 双输出：metacognition_report 工具

LogicBrain 使用单次 function calling，工具 `metacognition_report`：

```python
{
    "type": "function",
    "function": {
        "name": "metacognition_report",
        "description": "提交元认知审查结论：文本洞察 + 可选参数调节",
        "parameters": {
            "type": "object",
            "properties": {
                "insight_text": {
                    "type": "string",
                    "description": "自然语言自我洞察，例如'我发现在游戏话题上连续使用了防御机制'"
                },
                "param_overrides": {
                    "type": "object",
                    "properties": {
                        "review_threshold_offset": {
                            "type": "number",
                            "description": "审查阈值偏移，范围 ±0.15。正数=更严格"
                        },
                        "defense_prob_multiplier": {
                            "type": "number",
                            "description": "防御概率乘数，范围 0.5~2.0。小于1=减少防御"
                        },
                        "interest_modulations": {
                            "type": "object",
                            "description": "话题兴趣调制，{topic_name: ±0.3}。如 {'游戏': -0.2}"
                        },
                        "emotion_threshold_offset": {
                            "type": "number",
                            "description": "情绪交互阈值偏移，范围 ±0.1"
                        },
                        "inner_thoughts_mode": {
                            "type": "string",
                            "enum": ["full", "brief", "minimal"],
                            "description": "内心戏详细度：完整/简略/极简"
                        }
                    }
                },
                "confidence": {
                    "type": "number",
                    "description": "此次洞察的确定度 0~1",
                    "minimum": 0, "maximum": 1
                }
            },
            "required": ["insight_text", "confidence"]
        }
    }
}
```

**confidence < 0.6** → 只应用 insight_text（注入 system prompt），不应用 param_overrides。

---

## 5. 参数调节生命周期

```python
@dataclass
class MetaParamOverrides:
    """临时参数覆盖容器。由 TurnManager 维护，注入各子系统。"""
    
    review_threshold_offset: float = 0.0
    defense_prob_multiplier: float = 1.0
    interest_modulations: dict[str, float] = field(default_factory=dict)
    emotion_threshold_offset: float = 0.0
    inner_thoughts_mode: str = "full"
    
    _applied_at_turn: int = 0
    _expiry_turns: int = 5
    
    def apply(self, report: MetacognitionReport, turn_counter: int) -> None:
        """应用元认知报告。confidence < 0.6 时只写文本不调参。"""
        if report.confidence < 0.6:
            return
        overrides = report.param_overrides
        if overrides.review_threshold_offset is not None:
            self.review_threshold_offset = overrides.review_threshold_offset
        if overrides.defense_prob_multiplier is not None:
            self.defense_prob_multiplier = overrides.defense_prob_multiplier
        if overrides.interest_modulations:
            self.interest_modulations.update(overrides.interest_modulations)
        if overrides.emotion_threshold_offset is not None:
            self.emotion_threshold_offset = overrides.emotion_threshold_offset
        if overrides.inner_thoughts_mode is not None:
            self.inner_thoughts_mode = overrides.inner_thoughts_mode
        self._applied_at_turn = turn_counter
    
    def is_expired(self, turn_counter: int) -> bool:
        return turn_counter - self._applied_at_turn >= self._expiry_turns
    
    def get_review_threshold(self, base: float = 0.5) -> float:
        if self.is_expired(turn_counter):
            return base
        return max(0.35, min(0.65, base + self.review_threshold_offset))
```

覆盖过期后（默认 N 轮），参数自动恢复默认。下一轮元认知可重新覆盖或调整。

---

## 6. 消费点分布

| 参数 | 消费方 | 读取时机 |
|------|--------|---------|
| `review_threshold_offset` | `ReviewSystem` | 每次 `review()` 计算 `combined` 前 |
| `defense_prob_multiplier` | `DefenseEngine` | 每次 `evaluate()` 的 `final_prob` 计算 |
| `interest_modulations` | `InterestModel` | 每次 `match(topic)` 后应用偏移 |
| `emotion_threshold_offset` | `EmotionEngine` | 每次 `tick()` 步骤①前读取 |
| `inner_thoughts_mode` | `ReActLoop` | `_think()` 注入 system prompt 提示 |

---

## 7. 数据流集成

```
_async_review_and_decide() 结束后:
  │
  ├─ MetacognitionEngine.check_triggers(turn_counter, review_history, defense_history, compound_delta, inner_thoughts)
  │     ├─ 定期 → True if turn_counter % N == 0
  │     ├─ 审查连判 → True if last 3 reviews same decision
  │     ├─ 防御连发 → True if last 2 turns had defense
  │     └─ 自我批评连发 → True if last 3 inner_thoughts match keywords
  │
  ├─ 不触发 → 结束
  │
  └─ 触发:
       │
       ├─ 组装元认知上下文:
       │     - 最近 N 轮摘要 (话题、基本情绪、复合情绪、审查结果、防御、防御意识、记忆回溯)
       │     - 复合情绪趋势 (← Spec 005 EmotionEngine 历史)
       │     - 防御模式总结 (← Spec 005 defense_awareness 条目)
       │     - 记忆系统状态 (← Spec 003 回溯统计 + 衰减预警)
       │     - 注意力状态 (← 注意力状态机)
       │
       ├─ LogicBrain.metacognition_pass(context) → LLM (DeepSeek Pro)
       │     工具: metacognition_report (单次调用)
       │
       ├─ 解析 report:
       │     ├─ insight_text → MemoryStore.save("self/metacognition/{timestamp}")
       │     ├─ insight_text → 注入下一轮 _init_messages 的 system prompt:
       │     │     "[自我洞察] {insight_text}"
       │     └─ param_overrides (if confidence ≥ 0.6) → MetaParamOverrides.apply()
       │
       └─ 重置异常计数器
```

---

## 8. 配置外化

```yaml
systems:
  metacognition:
    enabled: true
    periodic_interval: 5          # 每 N 轮定期触发
    anomaly_detection:
      review_streak: 3            # 审查连续 ≥3 同结论
      defense_streak: 2           # 防御连续 ≥2 轮
      self_criticism_streak: 3    # inner_thoughts 连 ≥3 含自我批评
      self_criticism_keywords:    # 触发关键词
        - "不该这么说"
        - "又说错了"
        - "太机械了"
        - "没意思"
        - "不想聊了"
    param_limits:
      review_threshold_range: [0.35, 0.65]
      defense_prob_range: [0.1, 0.95]
      interest_mod_range: [-0.3, 0.3]
      emotion_threshold_range: [0.2, 0.4]
    confidence_threshold: 0.6     # 低于此只应用文本，不调参数
    override_expiry_turns: 5      # 参数覆盖 N 轮后自动过期
```

**禁用降级路径**：当 `enabled: false` 时，`MetacognitionEngine` 不初始化，`MetaParamOverrides` 容器仍然存在但所有字段保持默认（offset=0.0, multiplier=1.0, modulations={}, mode="full"）。各消费子系统读取时不产生调制效应。

**LLM 调用失败处理**：`metacognition_pass()` 失败时（遵循 AGENTS.md：LLM 失败不抛异常），本次元认知静默中止。insight_text 不写入，param_overrides 不应用。异常计数器仍重置（避免反复重试）。失败写日志但不阻塞 turn。

---

## 9. 与已有系统的联动矩阵

| 数据源 | 提供方 | 消费方式 |
|--------|--------|---------|
| 复合情绪趋势 (Δ per dimension) | Spec 005 EmotionEngine | 元认知上下文 §3 + 异常触发 §2 |
| 防御意识历史 (defense_awareness 条目) | Spec 005 DefenseEngine → subconscious | 元认知上下文 §3 → 发现防御模式 |
| 记忆回溯统计 (命中数、链长、空回溯) | Spec 003 search_chained | 元认知上下文 §3 → 感知记忆衰退 |
| 记忆衰减预警 (salience 4~5 区间) | Spec 003 effective_salience | 元认知上下文 §3 → 建议主动回忆加固 |
| 注意力状态 (FOCUSED/DRIFTING/DULL) | 注意力状态机 | 元认知上下文 §3 → 解释行为模式 |
| 审查结果历史 | TurnManager | 异常触发 §2 + 上下文 §3 |
| inner_thoughts 文本 | ReActLoop | 异常触发 §2 (关键词检测) |

---

## 10. 改动文件清单

| 文件 | 改动 | 说明 |
|------|------|------|
| `core/types.py` | + `MetacognitionReport` dataclass；+ `MetaParamOverrides` 容器类 | 数据结构 |
| `systems/metacognition.py` | **新建** — `MetacognitionEngine`: 触发判定 `check_triggers()`、上下文组装 `build_context()`、report 解析 | 核心 |
| `core/brain.py` | + `LogicBrain.metacognition_pass()` — 单次 LLM 调用，注册工具 `metacognition_report` | 执行 |
| `core/turn_manager.py` | `_async_review_and_decide()` 后接 `MetacognitionEngine`；`_init_messages` 注入 insight_text；初始化 `MetaParamOverrides` 并注入各子系统；维护审查/防御/self_criticism 计数器 | 集成 |
| `systems/review.py` | `ReviewSystem` 读取 `MetaParamOverrides.get_review_threshold()` | 消费 |
| `systems/defense.py` | `DefenseEngine` 读取 `MetaParamOverrides.defense_prob_multiplier` | 消费 |
| `systems/interest.py` | `InterestModel` 读取 `MetaParamOverrides.interest_modulations` | 消费 |
| `systems/emotion.py` | `EmotionEngine` 读取 `MetaParamOverrides.emotion_threshold_offset`；新增 `get_compound_trend()` 方法 | 消费 + 提供 |
| `core/loop.py` | `ReActLoop` 读取 `MetaParamOverrides.inner_thoughts_mode` 调制详细度 | 消费 |
| `config.yaml` | + `systems.metacognition` 段 | 配置 |
| `tests/test_metacognition.py` | **新建** — 触发条件、输出解析、参数调节、过期机制、confidence 阈值、联动数据验证 | 测试 |

---

## 11. 成功标准

| ID | 标准 | 验证 |
|----|------|------|
| SC-01 | 定期触发 | turn_5 → 元认知执行，insight_text 写入 self/metacognition |
| SC-02 | 异常触发 — 审查连判 | 连续 3 轮 CORRECT → 触发 |
| SC-03 | 异常触发 — 防御连发 | 连续 2 轮防御激活 → 触发 |
| SC-04 | 文本洞察注入 system prompt | insight_text 出现在下一轮 _init_messages 中 |
| SC-05 | 参数调节 — 审查阈值 | confidence≥0.6, offset=+0.1 → ReviewSystem 用 0.6 判定 |
| SC-06 | confidence 阈值保护 | confidence=0.5 → 只写文本，不调参数 |
| SC-07 | 参数覆盖过期 | N 轮后 param_overrides 自动恢复默认 |
| SC-08 | inner_thoughts 模式切换 | mode='brief' → ReActLoop 缩短内心戏要求 |
| SC-09 | 自我批评关键词触发 | 连续 3 轮 "不该这么说" → 异常触发 |
| SC-10 | 复合情绪趋势进入上下文 | gratification 0.2→0.1→0.05 出现在元认知输入中 |
| SC-11 | 防御意识历史进入上下文 | defense_awareness 条目出现在元认知输入中 |
| SC-12 | 记忆衰减预警进入上下文 | salience 4~5 区间记忆数出现在元认知输入中 |
| SC-13 | 零回归 | 所有现有 154 tests 通过 |
| SC-14 | 新增测试 ≥ 10 条 | pytest count 验证 |
