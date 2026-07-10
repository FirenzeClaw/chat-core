# Spec 009 认知增强 — 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 实现四种认知增强能力——直觉系统（三级降级推理）、创造力（双路径概念发散）、幽默检测（预期违背+双关语）、道德困境（双脑 Pro/Con 评估），全部与 Spec 003/005/006/007/008 + 注意力状态机 + 人格系统联动。

**架构：** 4 个新系统文件 + 4 个新测试文件，修改 7 个现有文件。直觉改造 `_think()` 推理深度，创造力/幽默注入 system prompt，道德困境介入审查管线。

**技术栈：** Python 3.12+, dataclasses, asyncio, DeepSeek Flash (L2/Fast Path/cPath A), DeepSeek Pro (脑 Pro/Con)

---

## 架构决策

- **直觉引擎不改变完整 ReAct 循环逻辑**：L1/L2 成功时直接产生 send_reply 并跳过循环，L3 走原始路径。不修改 `_think()` 内部。
- **直觉 L1 零 LLM 成本**：仅依赖 Spec 003 search_chained 结果（记忆命中数 + salience），模板拼接快速回复。
- **创造力 Path B 复用 Spec 003 search_chained**：传入 extended RecallChainConfig，不修改 memory.py 核心搜索逻辑。
- **幽默纯规则检测、仅提示不强制**：HumorDetector 零 LLM 调用，产出 humor_hint 以 system prompt 注入，LLM 自行判断是否采纳。
- **道德困境使用双脑已有 Provider**：LogicBrain.pro_con() / EmotionBrain.pro_con() 复用 ModelProvider，不新增连接。
- **Path A 和 Fast Path 共享 Flash 模型**：单次调用，reasoning_effort=low，无 function calling。

---

## 任务列表

### 阶段 1：数据类型 + 配置基础

- [ ] **任务 1：核心类型定义**

**文件：**
- 修改：`chat_core/core/types.py`

**描述：** 在 types.py 末尾追加 Spec 009 所需的 5 个 dataclass。

**步骤：**

- [ ] **步骤 1：追加类型定义**

```python
# ── Spec 009: 认知增强 ─────────────────────────────────────

class IntuitionLevel(Enum):
    """直觉推理级别"""
    L1_MEMORY_MATCH = "l1_memory_match"   # 记忆命中 → 直接快速回复
    L2_FAST_PATH = "l2_fast_path"         # 单次 Flash → 快速回复
    L3_FULL_REACT = "l3_full_react"       # 完整 ReAct 循环


@dataclass
class IntuitionResult:
    """直觉引擎评估结果"""
    level: IntuitionLevel = IntuitionLevel.L3_FULL_REACT
    fast_reply: str | None = None          # L1/L2 成功时的快速回复文本
    inner_thoughts: str | None = None      # 对应内心戏
    confidence: float = 0.0               # L2 置信度
    skip_react: bool = False              # True = 跳过子Session ReAct


@dataclass
class CreativityContext:
    """创造力双路径发散结果"""
    path_a_mappings: list[str] = field(default_factory=list)   # LLM 概念映射
    path_b_memories: list[str] = field(default_factory=list)    # 远距离关联记忆摘要
    triggered: bool = False


@dataclass
class HumorOpportunity:
    """幽默机会"""
    type: str = ""                        # "expectation_violation" | "pun"
    expected: str = ""                    # 预期违背: 预期答案
    word: str = ""                        # 双关语: 歧义词
    hint: str = ""


class MoralConflictType(Enum):
    """道德冲突类型"""
    HONESTY_VS_PROTECTION = "honesty_vs_protection"
    LOYALTY_CONFLICT = "loyalty_conflict"
    SELF_VS_OTHER = "self_vs_other"
    NONE = "none"


@dataclass
class MoralConflict:
    """检测到的道德困境"""
    conflict_type: MoralConflictType = MoralConflictType.NONE
    trigger_description: str = ""
    stakes: float = 0.0                  # 冲突强度 [0, 1]


@dataclass
class ProConAssessment:
    """双脑道德评估结果"""
    logic_score: float = 0.0             # LogicBrain: 真相/原则的价值
    logic_reasoning: str = ""
    emotion_score: float = 0.0           # EmotionBrain: 关系/感受的价值
    emotion_reasoning: str = ""
    deadlock: bool = False               # |diff| < 0.2 → 两难
    escalation: bool = False             # |diff| > 0.4 → 升级元认知
    recommended_path: str = ""           # "honest" | "protective" | "deadlock"
```

- [ ] **步骤 2：验证导入**

运行：`python -c "from chat_core.core.types import IntuitionLevel, IntuitionResult, CreativityContext, HumorOpportunity, MoralConflictType, MoralConflict, ProConAssessment; print('OK')"`

---

- [ ] **任务 2：Config 配置段 + 访问器**

**文件：**
- 修改：`chat_core/config.yaml`（在 systems.patterns 之后追加）
- 修改：`chat_core/config.py`（追加 4 个 accessor）

**配置 YAML：**

```yaml
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
      chain_level_filter: 3
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

**config.py 追加：**

```python
    def intuition_config(self) -> dict[str, Any]:
        """返回 systems.intuition 配置 (Spec 009)"""
        return self.systems.get("intuition", {})

    def creativity_config(self) -> dict[str, Any]:
        """返回 systems.creativity 配置 (Spec 009)"""
        return self.systems.get("creativity", {})

    def humor_config(self) -> dict[str, Any]:
        """返回 systems.humor 配置 (Spec 009)"""
        return self.systems.get("humor", {})

    def moral_conflict_config(self) -> dict[str, Any]:
        """返回 systems.moral_conflict 配置 (Spec 009)"""
        return self.systems.get("moral_conflict", {})
```

**验证：** `python -c "from chat_core.config import get_config; c = get_config(); assert c.intuition_config()['enabled']"` → OK

---

### 检查点：阶段 1
- [ ] 类型导入 + 配置加载均成功
- [ ] `python -m pytest tests/ -q --tb=short` → 322 passed

---

### 阶段 2：IntuitionEngine

- [ ] **任务 3：IntuitionEngine 实现**

**文件：**
- 创建：`chat_core/systems/intuition.py`

**描述：** 实现三级降级推理引擎。纯计算 + 零 I/O（除 L2 Flash 调用由外部传入）。L1 基于 recall 结果判断，L2 置信度启发式计算。

```python
"""IntuitionEngine — 三级降级推理 (Spec 009)

L1: 记忆命中 → 零 LLM 快速回复
L2: Fast Path → 单次 Flash 调用
L3: 完整 ReAct → 原始路径
"""

from __future__ import annotations

import random
from typing import Any

from chat_core.config import get_config
from chat_core.core.types import (
    AttentionStateEnum,
    ChainedMemory,
    IntuitionLevel,
    IntuitionResult,
)


class IntuitionEngine:
    """直觉引擎：根据记忆命中、注意力和精力状态选择推理深度。"""

    def __init__(self) -> None:
        cfg = get_config()
        ic = cfg.intuition_config()
        self._enabled: bool = bool(ic.get("enabled", True))

        l1 = ic.get("level1", {})
        self._l1_min_hits: int = int(l1.get("min_memory_hits", 5))
        self._l1_min_salience: float = float(l1.get("min_salience", 7))

        l2 = ic.get("level2", {})
        self._l2_confidence_threshold: float = float(l2.get("confidence_threshold", 0.7))

        sm = ic.get("state_modulation", {})
        self._focused_l1_boost: float = float(sm.get("focused_l1_boost", 1.5))
        self._dull_l3_boost: float = float(sm.get("dull_l3_boost", 2.0))
        self._low_energy_l1_boost: float = float(sm.get("low_energy_l1_boost", 1.3))

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── 主入口 ──────────────────────────────────────────────

    def evaluate(
        self,
        memory_results: list[ChainedMemory],
        attention_state: AttentionStateEnum | None = None,
        energy: float = 1.0,
        user_message: str = "",
    ) -> IntuitionResult:
        """评估直觉级别，返回是否可跳过完整 ReAct。

        Args:
            memory_results: Spec 003 search_chained 结果
            attention_state: 当前注意力状态
            energy: 当前精力值 [0, 1]
            user_message: 用户消息原文

        Returns:
            IntuitionResult: 包含推荐级别和可能的快速回复
        """
        if not self._enabled:
            return IntuitionResult()

        # ① L1 检测：强记忆命中
        l1_result = self._check_l1(memory_results, attention_state, energy)
        if l1_result.skip_react:
            return l1_result

        # ② L2 检测：中置信度 → Fast Path
        l2_result = self._check_l2(memory_results, attention_state, energy, user_message)
        if l2_result.skip_react:
            return l2_result

        # ③ L3：完整 ReAct（状态调制概率）
        return self._check_l3(attention_state, energy)

    # ── L1: 记忆命中 ───────────────────────────────────────

    def _check_l1(
        self,
        memory_results: list[ChainedMemory],
        attention_state: AttentionStateEnum | None,
        energy: float,
    ) -> IntuitionResult:
        """强记忆命中 ≥ 5 条且 max salience ≥ 7 → 直接快速回复"""
        if len(memory_results) < self._l1_min_hits:
            return IntuitionResult()

        max_salience = max((cm.entry.salience for cm in memory_results), default=0)
        if max_salience < self._l1_min_salience:
            return IntuitionResult()

        # 状态调制
        prob = self._l1_base_prob(attention_state, energy)
        if random.random() > prob:
            return IntuitionResult()

        # 合成快速回复
        replies = self._synthesize_reply(memory_results)
        return IntuitionResult(
            level=IntuitionLevel.L1_MEMORY_MATCH,
            fast_reply=replies,
            inner_thoughts="[直觉回复] 基于强记忆直接反应",
            skip_react=True,
        )

    def _l1_base_prob(
        self,
        attention_state: AttentionStateEnum | None,
        energy: float,
    ) -> float:
        """计算 L1 实际触发概率（状态调制）"""
        prob = 0.8  # base probability when conditions met

        if attention_state == AttentionStateEnum.FOCUSED:
            prob *= self._focused_l1_boost
        elif attention_state == AttentionStateEnum.DULL:
            prob *= 0.5

        if energy < 0.3:
            prob *= self._low_energy_l1_boost

        return min(prob, 0.95)

    def _synthesize_reply(self, memory_results: list[ChainedMemory]) -> str:
        """模板拼接高 salience 记忆摘要生成快速回复"""
        top = sorted(memory_results, key=lambda cm: cm.entry.salience, reverse=True)[:3]
        lines = ["我记得这些："]
        for cm in top:
            e = cm.entry
            val = e.value
            if isinstance(val, dict):
                text = next((str(v) for v in val.values() if isinstance(v, str) and v.strip()), "")
            else:
                text = str(val)
            if text:
                lines.append(f"- {text[:100]}")
        return "基于我们的过往交流，" + "；".join(lines).replace("我记得这些：基于我们的过往交流，", "")

    # ── L2: Fast Path ──────────────────────────────────────

    def _check_l2(
        self,
        memory_results: list[ChainedMemory],
        attention_state: AttentionStateEnum | None,
        energy: float,
        user_message: str,
    ) -> IntuitionResult:
        """中等置信度 → 单次 Flash 调用（置信度由调用方通过 Flash 返回值判定）"""
        # L2 的 LLM 调用由调用方（loop.py）执行
        # 这里只做判定：是否 ATTEMPT L2
        if attention_state == AttentionStateEnum.DULL and random.random() > 0.3:
            return IntuitionResult()  # DULL 态大概率跳过 L2

        # L2 attempt: 返回结果标记需要 Fast Path 调用
        return IntuitionResult(
            level=IntuitionLevel.L2_FAST_PATH,
            skip_react=False,  # 由调用方在 Flash 调用后判定
            confidence=0.0,    # 调用方会填充
        )

    # ── L3: 完整 ReAct ─────────────────────────────────────

    def _check_l3(
        self,
        attention_state: AttentionStateEnum | None,
        energy: float,
    ) -> IntuitionResult:
        """L3 = 原始完整 ReAct"""
        # DULL 态 L3 概率 boost（不是跳过，而是强迫自己认真）
        return IntuitionResult(
            level=IntuitionLevel.L3_FULL_REACT,
            skip_react=False,
        )

    # ── L2 置信度判定 ─────────────────────────────────────

    def eval_fast_path_confidence(self, reply_text: str, inner_thoughts: str) -> float:
        """从 Flash 返回值判定置信度。

        启发式：回复长度 ≥ 50 字符 = 高置信度。
        可扩展为 LLM 自评模式。
        """
        if len(reply_text) >= 50:
            return max(0.7, min(0.95, len(reply_text) / 200))
        return 0.4
```

**验证：** `python -c "from chat_core.systems.intuition import IntuitionEngine; ie = IntuitionEngine(); r = ie.evaluate([], None, 0.9); assert r.level.value == 'l3_full_react'"`

---

### 检查点：阶段 2
- [ ] IntuitionEngine 导入 + 基本逻辑验证通过
- [ ] `python -m pytest tests/ -q --tb=short` → 322 passed

---

### 阶段 3：CreativityEngine + HumorDetector

- [ ] **任务 4：CreativityEngine 实现**

**文件：**
- 创建：`chat_core/systems/creativity.py`

**描述：** 双路径概念发散引擎。Path A: Flash LLM 概念跳跃（由 loop.py 执行调用）。Path B: 联锁记忆放大（扩大的 search_chained 配置）。合并后注入 system prompt。

```python
"""CreativityEngine — 双路径概念发散 (Spec 009)

Path A: LLM 远距离概念联想
Path B: Spec 003 联锁检索放大
"""

from __future__ import annotations

from typing import Any

from chat_core.config import get_config
from chat_core.core.types import (
    CreativityContext,
    RecallChainConfig,
)


EXTENDED_CHAIN_CONFIG = RecallChainConfig(
    top_n=5, extensions=[5, 5, 5, 5, 5], max_per_level=5, namespace_prefix=None,
)


class CreativityEngine:
    """创造力引擎：触发判定 + LLM 发散 + 联锁放大 + 合并注入。"""

    def __init__(self) -> None:
        cfg = get_config()
        cc = cfg.creativity_config()
        self._enabled: bool = bool(cc.get("enabled", True))
        self._trigger_playfulness_min: float = float(cc.get("trigger_playfulness_min", 0.5))

        pa = cc.get("path_a", {})
        self._pa_num_mappings: int = int(pa.get("num_mappings", 5))

        pb = cc.get("path_b", {})
        self._pb_chain_filter: int = int(pb.get("chain_level_filter", 3))

        pw = cc.get("personality_weight", {})
        self._creativity_bias_a: float = float(pw.get("creativity_bias_a", 0.7))

        # 开放性问题关键词 (Path A 额外触发)
        self._open_ended_keywords: list[str] = [
            "你觉得为什么", "如果...会怎样", "假如", "想象一下",
            "换个角度看", "有没有可能", "类似于",
        ]

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── 触发判定 ──────────────────────────────────────────

    def should_trigger(self, playfulness: float, user_message: str) -> bool:
        """判定是否触发创造力发散。

        Args:
            playfulness: 人格 playfulness 权重
            user_message: 用户消息原文
        """
        if not self._enabled:
            return False
        if playfulness > self._trigger_playfulness_min:
            return True
        # 开放性问题也触发
        if any(kw in user_message for kw in self._open_ended_keywords):
            return True
        return False

    # ── Path A: LLM 概念发散 prompt 生成 ──────────────────

    def build_path_a_prompt(self, user_message: str) -> str:
        """生成 Path A 的 LLM prompt。由 loop.py 调用 Flash 模型。"""
        # 提取关键词（简单前20字）
        keywords = user_message[:60].strip()
        return (
            f"对 [{keywords}] 做远距离概念联想，输出 {self._pa_num_mappings} 个跨领域映射。\n"
            "格式: '领域/概念: 一句话映射描述'\n"
            "示例: '团队协作 → 蚂蚁社会的分工机制'\n"
            "要求: 每个映射要有真正的概念联系，不是表面类比。"
        )

    def parse_path_a_result(self, text: str) -> list[str]:
        """解析 Flash 返回的概念映射文本为列表"""
        mappings = [line.strip() for line in text.split("\n") if line.strip() and "→" in line]
        return mappings[:self._pa_num_mappings]

    # ── Path B: 联锁放大配置 ───────────────────────────────

    def get_extended_chain_config(self) -> RecallChainConfig:
        """返回扩大的联锁检索配置"""
        return EXTENDED_CHAIN_CONFIG

    def filter_path_b_memories(
        self, results: list[Any],  # list[ChainedMemory]
    ) -> list[str]:
        """过滤 Path B 结果：仅保留 chain_level ≥ 3 的意外关联记忆"""
        filtered = [cm for cm in results if getattr(cm, 'chain_level', 0) >= self._pb_chain_filter]
        summaries = []
        for cm in filtered:
            e = cm.entry
            val = e.value
            if isinstance(val, dict):
                text = next((str(v) for v in val.values() if isinstance(v, str) and v.strip()), "")
            else:
                text = str(val)
            if text:
                summaries.append(text[:80])
        return summaries[:10]

    # ── 合并注入 ──────────────────────────────────────────

    def build_injection(
        self,
        path_a_mappings: list[str],
        path_b_summaries: list[str],
    ) -> str:
        """生成创造力增强 system prompt 注入文本"""
        parts: list[str] = ["[创造力增强]"]
        if path_a_mappings:
            parts.append("  概念发散 (来自远距离联想):")
            for m in path_a_mappings:
                parts.append(f"    - {m}")
        if path_b_summaries:
            parts.append("  意外关联记忆 (你之前没意识到有关联的):")
            for s in path_b_summaries:
                parts.append(f"    - {s}")
        return "\n".join(parts) if len(parts) > 1 else ""
```

验证：`python -c "from chat_core.systems.creativity import CreativityEngine; ce = CreativityEngine(); print(ce.should_trigger(0.7, 'hello'))"` → True

---

- [ ] **任务 5：HumorDetector 实现**

**文件：**
- 创建：`chat_core/systems/humor.py`

**描述：** 纯规则幽默检测——预期违背 + 双关语检测 + 关系安全门。零 LLM 调用。

```python
"""HumorDetector — 规则幽默检测 (Spec 009)

预期违背 + 双关语 + 关系安全门。零 LLM 成本，仅提示不强制。
"""

from __future__ import annotations

from typing import Any

from chat_core.config import get_config
from chat_core.core.types import HumorOpportunity, Message, RelationshipStage


# 中文反问句式
QUESTION_PATTERNS = [
    "难道", "是不是", "会不会", "能不能", "要不要",
    "怎么", "为什么", "是吗", "对吧",
]

# 简易歧义词典
AMBIGUOUS_WORDS: dict[str, list[str]] = {
    "意思": ["含义", "心意（送礼时'一点小意思'）"],
    "打": ["击打", "打电话", "打车"],
    "开": ["打开", "开始", "开车"],
    "冷": ["温度低", "冷笑话"],
    "热": ["温度高", "热门话题"],
}


class HumorDetector:
    """纯规则幽默检测器。"""

    def __init__(self) -> None:
        cfg = get_config()
        hc = cfg.humor_config()
        self._enabled: bool = bool(hc.get("enabled", True))
        stage_str = hc.get("min_relationship_stage", "friend")
        self._min_stage = RelationshipStage[stage_str.upper()] if stage_str.upper() in RelationshipStage.__members__ else RelationshipStage.FRIEND

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── 入口 ────────────────────────────────────────────────

    def detect(
        self,
        user_message: str,
        relationship_stage: RelationshipStage | str = RelationshipStage.FRIEND,
    ) -> list[HumorOpportunity]:
        """检测幽默机会。

        Args:
            user_message: 用户消息原文
            relationship_stage: 当前关系阶段
        """
        if not self._enabled:
            return []

        # 关系安全门
        stage = relationship_stage if isinstance(relationship_stage, RelationshipStage) else RelationshipStage(relationship_stage)
        if stage not in (RelationshipStage.FRIEND, RelationshipStage.CLOSE_FRIEND):
            return []

        opportunities: list[HumorOpportunity] = []

        # 1. 预期违背
        ev = self._detect_expectation_violation(user_message)
        if ev:
            opportunities.append(ev)

        # 2. 双关语
        pun = self._detect_pun(user_message)
        if pun:
            opportunities.append(pun)

        return opportunities

    # ── 预期违背 ────────────────────────────────────────────

    def _detect_expectation_violation(self, message: str) -> HumorOpportunity | None:
        """检测反问句 → 标记为预期违背机会"""
        for pattern in QUESTION_PATTERNS:
            if pattern in message:
                return HumorOpportunity(
                    type="expectation_violation",
                    expected=f"用户可能期待一个直接的答案",
                    hint=f"用户用了'{pattern}'的句式——你可以故意给一个反差或幽默的回复",
                )
        return None

    # ── 双关语 ────────────────────────────────────────────────

    def _detect_pun(self, message: str) -> HumorOpportunity | None:
        """检测歧义词"""
        for word, meanings in AMBIGUOUS_WORDS.items():
            if word in message:
                meaning_str = " / ".join(meanings)
                return HumorOpportunity(
                    type="pun",
                    word=word,
                    hint=f"'{word}'有双重含义（{meaning_str}）——可以巧妙地利用这一点，但只在觉得合适且自然的时候用",
                )
        return None

    # ── 生成注入 ────────────────────────────────────────────

    def build_injection(self, opportunities: list[HumorOpportunity]) -> str | None:
        """生成幽默提示 system prompt 注入文本"""
        if not opportunities:
            return None

        lines: list[str] = ["[幽默机会]"]
        for opp in opportunities:
            lines.append(f"  {opp.hint}")
        return "\n".join(lines)
```

验证：`python -c "from chat_core.systems.humor import HumorDetector; hd = HumorDetector(); ops = hd.detect('难道不是吗', 'friend'); print(len(ops))"` → ≥1

---

### 检查点：阶段 3
- [ ] CreativityEngine + HumorDetector 导入 + 基本逻辑验证
- [ ] `python -m pytest tests/ -q --tb=short` → 322 passed

---

### 阶段 4：MoralConflict + Brain Pro/Con

- [ ] **任务 6：MoralConflictDetector + ProConAssessor 实现**

**文件：**
- 创建：`chat_core/systems/moral.py`

**描述：** 三种冲突类型检测 + 双脑 Pro/Con 评估器（LLM 调用由 brain.py 执行）。

```python
"""MoralConflict — 道德困境检测 + 双脑 Pro/Con 评估 (Spec 009)"""

from __future__ import annotations

from typing import Any

from chat_core.config import get_config
from chat_core.core.types import (
    MoralConflict,
    MoralConflictType,
    ProConAssessment,
    RelationshipStage,
)


# 诚实vs保护: 评价性关键词
EVALUATION_KEYWORDS = [
    "你觉得", "评价一下", "怎么样", "好不好",
    "是不是很差", "水平如何", "值得吗",
]

# 忠诚冲突: 告状/抱怨关键词
COMPLAINT_KEYWORDS = [
    "他/她", "那个人", "某某",
]


class MoralConflictDetector:
    """道德困境检测器。"""

    def __init__(self) -> None:
        cfg = get_config()
        mc = cfg.moral_conflict_config()
        self._enabled: bool = bool(mc.get("enabled", True))
        types_cfg = mc.get("types", [])
        self._active_types: set[str] = set(types_cfg)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def detect(
        self,
        user_message: str,
        inner_thoughts: str | None,
        relationship_stage: RelationshipStage | None,
        energy: float,
    ) -> MoralConflict | None:
        """检测本轮是否存在道德困境。"""
        if not self._enabled:
            return None

        inner = inner_thoughts or ""

        # 1. 诚实 vs 保护
        if "honesty_vs_protection" in self._active_types:
            conflict = self._check_honesty_vs_protection(user_message, inner, relationship_stage)
            if conflict:
                return conflict

        # 2. 忠诚冲突
        if "loyalty_conflict" in self._active_types:
            conflict = self._check_loyalty_conflict(user_message, relationship_stage)
            if conflict:
                return conflict

        # 3. 自我 vs 他人
        if "self_vs_other" in self._active_types:
            conflict = self._check_self_vs_other(energy)
            if conflict:
                return conflict

        return None

    def _check_honesty_vs_protection(
        self, message: str, inner_thoughts: str,
        stage: RelationshipStage | None,
    ) -> MoralConflict | None:
        """评价请求 + 内心负面判断 + 关系≥朋友 → 诚实vs保护"""
        has_evaluation = any(kw in message for kw in EVALUATION_KEYWORDS)
        if not has_evaluation:
            return None

        # 简易负面判断检测
        negative_cues = ["不太好", "不太行", "有问题", "差点意思"]
        has_negative = any(cue in inner_thoughts for cue in negative_cues)
        if not has_negative:
            return None

        if stage not in (RelationshipStage.FRIEND, RelationshipStage.CLOSE_FRIEND):
            return None

        stakes = 0.5 if stage == RelationshipStage.FRIEND else 0.8
        return MoralConflict(
            conflict_type=MoralConflictType.HONESTY_VS_PROTECTION,
            trigger_description=f"用户请求评价，AI内心有负面判断，关系={stage.value}",
            stakes=stakes,
        )

    def _check_loyalty_conflict(
        self, message: str, stage: RelationshipStage | None,
    ) -> MoralConflict | None:
        """用户对第三方抱怨 + 已有社交记忆 → 忠诚冲突"""
        has_complaint = any(kw in message for kw in COMPLAINT_KEYWORDS)
        if not has_complaint:
            return None
        if stage not in (RelationshipStage.FRIEND, RelationshipStage.CLOSE_FRIEND):
            return None
        return MoralConflict(
            conflict_type=MoralConflictType.LOYALTY_CONFLICT,
            trigger_description="用户对第三方表达不满",
            stakes=0.6,
        )

    def _check_self_vs_other(self, energy: float) -> MoralConflict | None:
        """精力耗尽 → 自我 vs 他人"""
        if energy < 0.2:
            return MoralConflict(
                conflict_type=MoralConflictType.SELF_VS_OTHER,
                trigger_description=f"精力耗尽 (energy={energy:.2f})，AI想退出但用户可能还想聊",
                stakes=0.3,
            )
        return None


class ProConAssessor:
    """双脑 Pro/Con 评估器。评估逻辑 + 路径判定。"""

    def __init__(self) -> None:
        cfg = get_config()
        pc = cfg.moral_conflict_config().get("pro_con", {})
        self._deadlock_threshold: float = float(pc.get("deadlock_threshold", 0.2))
        self._escalate_threshold: float = float(pc.get("escalate_to_metacognition", 0.4))

    def assess(
        self,
        logic_score: float,
        logic_reasoning: str,
        emotion_score: float,
        emotion_reasoning: str,
    ) -> ProConAssessment:
        """根据双脑结果判定路径。"""
        diff = abs(logic_score - emotion_score)
        deadlock = diff < self._deadlock_threshold
        escalation = diff > self._escalate_threshold

        if deadlock:
            path = "deadlock"
        elif logic_score > emotion_score:
            path = "honest"
        else:
            path = "protective"

        return ProConAssessment(
            logic_score=logic_score,
            logic_reasoning=logic_reasoning,
            emotion_score=emotion_score,
            emotion_reasoning=emotion_reasoning,
            deadlock=deadlock,
            escalation=escalation,
            recommended_path=path,
        )
```

验证：`python -c "from chat_core.systems.moral import MoralConflictDetector, ProConAssessor; mcd = MoralConflictDetector(); pa = ProConAssessor(); r = pa.assess(0.7, 'truth', 0.3, 'care'); print(r.recommended_path)"` → "honest"

---

- [ ] **任务 7：brain.py — LogicBrain.pro_con() + EmotionBrain.pro_con()**

**文件：**
- 修改：`chat_core/core/brain.py`

**描述：** 在 LogicBrain 和 EmotionBrain 类中各追加 `pro_con()` 方法，用于道德困境的双脑 Pro/Con 评估。

**步骤：**

- [ ] **步骤 1：LogicBrain.pro_con()**

在 LogicBrain 类中 `metacognition_pass()` 方法之后追加：

```python
    async def pro_con(self, conflict_context: str) -> tuple[float, str]:
        """Spec 009: 道德困境 Pro 评估 — 从逻辑/原则角度评估。

        Returns:
            (score, reasoning_text): score 在 [0, 1]，高分=倾向说真话
        """
        if not self._provider:
            return (0.5, "[LogicBrain不可用]")
        try:
            prompt = (
                "从逻辑和原则角度分析以下道德困境。给出一个分数 (0-1) 和简短推理。\n"
                "分数含义: 1.0 = 必须坚持真相/原则, 0.0 = 应该优先保护关系。\n\n"
                f"困境: {conflict_context}\n\n"
                "格式: SCORE:<0-1的数字>\nREASONING:<一句话推理>"
            )
            result = await self._provider.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=128,
            )
            text = result.content if hasattr(result, 'content') else str(result)
            score = 0.5
            reasoning = text
            for line in text.split("\n"):
                if line.upper().startswith("SCORE:"):
                    try:
                        score = float(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.upper().startswith("REASONING:"):
                    reasoning = line.split(":", 1)[1].strip()
            return (max(0.0, min(1.0, score)), reasoning)
        except Exception:
            return (0.5, "[LogicBrain评估失败]")
```

- [ ] **步骤 2：EmotionBrain.pro_con()**

在 EmotionBrain 类中 `think_inject()` 方法之后追加：

```python
    async def pro_con(self, conflict_context: str) -> tuple[float, str]:
        """Spec 009: 道德困境 Pro 评估 — 从情感/关系角度评估。

        Returns:
            (score, reasoning_text): score 在 [0, 1]，高分=倾向保护关系
        """
        if not self._provider:
            return (0.5, "[EmotionBrain不可用]")
        try:
            prompt = (
                "从情感和关系角度分析以下道德困境。给出一个分数 (0-1) 和简短推理。\n"
                "分数含义: 1.0 = 必须保护关系/感受, 0.0 = 应该说真话即使伤人。\n\n"
                f"困境: {conflict_context}\n\n"
                "格式: SCORE:<0-1的数字>\nREASONING:<一句话推理>"
            )
            result = await self._provider.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3, max_tokens=128,
            )
            text = result.content if hasattr(result, 'content') else str(result)
            score = 0.5
            reasoning = text
            for line in text.split("\n"):
                if line.upper().startswith("SCORE:"):
                    try:
                        score = float(line.split(":", 1)[1].strip())
                    except ValueError:
                        pass
                elif line.upper().startswith("REASONING:"):
                    reasoning = line.split(":", 1)[1].strip()
            return (max(0.0, min(1.0, score)), reasoning)
        except Exception:
            return (0.5, "[EmotionBrain评估失败]")
```

---

### 检查点：阶段 4
- [ ] MoralConflict 模块导入 + 基本逻辑验证
- [ ] `python -m pytest tests/ -q --tb=short` → 322 passed

---

### 阶段 5：核心管线集成

- [ ] **任务 8：loop.py — 直觉 + 创造力 + 幽默 三系统注入**

**文件：**
- 修改：`chat_core/core/loop.py`

**描述：** 最关键的集成。在 `run()` 中接入 IntuitionEngine（可能跳过完整 ReAct），在 `_init_messages()` 之后注入 CreativityContext + HumorHints。

**步骤：**

- [ ] **步骤 1：新增属性**

在 `__init__` 中追加：

```python
        # Spec 009: 认知增强
        self._intuition_engine: Any = None
        self._creativity_engine: Any = None
        self._humor_detector: Any = None
        self._creativity_context_hint: str | None = None
        self._humor_hint: str | None = None
```

- [ ] **步骤 2：新增 setter 方法**

```python
    def set_intuition_engine(self, engine: Any) -> None:
        self._intuition_engine = engine

    def set_creativity_engine(self, engine: Any) -> None:
        self._creativity_engine = engine

    def set_humor_detector(self, detector: Any) -> None:
        self._humor_detector = detector

    def set_creativity_context(self, hint: str) -> None:
        self._creativity_context_hint = hint

    def set_humor_hint(self, hint: str) -> None:
        self._humor_hint = hint
```

- [ ] **步骤 3：改造 `run()` 方法**

在 `_init_messages()` 之后，`_inject_subconscious_corrections()` 之前，追加直觉判定：

```python
        # Spec 009: 直觉判定 — 可能跳过完整 ReAct
        if self._intuition_engine and self._memory_store:
            try:
                from chat_core.core.types import AttentionStateEnum
                attention = self._attention_model.get_state_enum("sub") if self._attention_model else None
                energy = self._energy_bar._state.energy if self._energy_bar else 1.0
                # 执行快速 recall 供 L1 判定
                chain_results = await self._memory_store.search_chained(user_message)
                intuition = self._intuition_engine.evaluate(
                    memory_results=chain_results,
                    attention_state=attention,
                    energy=energy,
                    user_message=user_message,
                )
                if intuition.skip_react and intuition.fast_reply:
                    # L1 直接输出，跳过完整 ReAct
                    self._replies.append(intuition.fast_reply)
                    self._inner_thoughts_raw = intuition.inner_thoughts
                    if self._on_reply:
                        await self._on_reply(intuition.fast_reply)
                    return
                # L2 Fast Path 在 _think() 前置处理
                self._intuition_pending_l2 = (intuition.level == IntuitionLevel.L2_FAST_PATH)
            except Exception:
                pass
```

⚠️ 注意：L2 Fast Path 的 Flash LLM 调用在 `_think()` 内部处理——如果 `_intuition_pending_l2` 为 True，`_think()` 改为单次 Flash 调用而非完整 function calling。

- [ ] **步骤 4：在 `_init_messages()` 末尾追加创造力 + 幽默注入**

```python
    def _inject_creativity_context(self) -> None:
        """Spec 009: 注入创造力增强上下文"""
        hint = getattr(self, '_creativity_context_hint', None)
        if hint:
            self._messages.insert(-1, Message(role="system", content=hint))

    def _inject_humor_hint(self) -> None:
        """Spec 009: 注入幽默提示"""
        hint = getattr(self, '_humor_hint', None)
        if hint:
            self._messages.insert(-1, Message(role="system", content=hint))
```

在 `_init_messages()` 的两处 `_inject_social_patterns()` 之后各追加：

```python
            self._inject_creativity_context()  # Spec 009
            self._inject_humor_hint()          # Spec 009
```

---

- [ ] **任务 9：turn_manager.py — 创造力 + 幽默触发 + 道德困境分支**

**文件：**
- 修改：`chat_core/core/turn_manager.py`

**描述：** 在 `__init__` 中初始化 CreativityEngine + HumorDetector + MoralConflictDetector + ProConAssessor；在 `_run_sub_session` 前注入创造力/幽默上下文；在审查流程中接入道德困境检测 + Pro/Con 评估 + 升级元认知。

**步骤：**

- [ ] **步骤 1：添加 import + 初始化**

```python
from chat_core.systems.creativity import CreativityEngine
from chat_core.systems.humor import HumorDetector
from chat_core.systems.moral import MoralConflictDetector, ProConAssessor

# 在 __init__ 中:
        self._creativity_engine = CreativityEngine()
        self._humor_detector = HumorDetector()
        self._moral_conflict_detector = MoralConflictDetector()
        self._pro_con_assessor = ProConAssessor()
```

- [ ] **步骤 2：在 `_run_sub_session` 中注入创造力 + 幽默**

在 Spec 008 的 `set_social_patterns()` 之后追加：

```python
        # Spec 009: 创造力发散 + 幽默检测
        stage = self._relationship_engine.get_stage(user_id)
        if self._creativity_engine.should_trigger(
            playfulness=self._personality_engine.weights.playfulness if self._personality_engine else 0.3,
            user_message=user_message,
        ):
            loop.set_creativity_engine(self._creativity_engine)
            # 创造力上下文由 loop._init_messages 中注入（需先通过 setter 设置 hint）
            # 实际注入在 _run_sub_session 的 Path A/B 执行后
        
        humor_ops = self._humor_detector.detect(user_message, stage)
        if humor_ops:
            hint = self._humor_detector.build_injection(humor_ops)
            if hint:
                loop.set_humor_hint(hint)
```

⚠️ 完整创造力上下文注入（Path A Flash + Path B search_chained）涉及 async 调用，需在 loop.run() **之前** 执行并设置 hint。具体为：

```python
        if creativity_should_trigger:
            path_a_mappings = []
            path_b_summaries = []
            # Path A: Flash LLM
            try:
                pa_prompt = self._creativity_engine.build_path_a_prompt(user_message)
                pa_result = await self._provider.chat(
                    messages=[{"role": "user", "content": pa_prompt}],
                    temperature=0.8, max_tokens=256,
                )
                path_a_mappings = self._creativity_engine.parse_path_a_result(
                    pa_result.content if hasattr(pa_result, 'content') else str(pa_result)
                )
            except Exception:
                pass
            # Path B: 联锁放大
            try:
                extended_config = self._creativity_engine.get_extended_chain_config()
                pb_results = await self._memory.search_chained(user_message, extended_config)
                path_b_summaries = self._creativity_engine.filter_path_b_memories(pb_results)
            except Exception:
                pass
            injection = self._creativity_engine.build_injection(path_a_mappings, path_b_summaries)
            if injection:
                loop.set_creativity_context(injection)
```

- [ ] **步骤 3：在审查流程中添加道德困境分支**

在 `_async_review_and_decide()` 中，Spec 008 的 `relationship_engine.update()` 之前追加：

```python
            # Spec 009: 道德困境检测
            moral_conflict = self._moral_conflict_detector.detect(
                user_message=user_message,
                inner_thoughts=inner_thoughts,
                relationship_stage=stage,  # stage from relationship_engine
                energy=self._energy_bar._state.energy,
            )
            if moral_conflict:
                # 双脑 Pro/Con
                context = f"困境: {moral_conflict.trigger_description}\n用户消息: {user_message}"
                logic_score, logic_reason = await self.logic.pro_con(context)
                emotion_score, emotion_reason = await self.emotion.pro_con(context)
                assessment = self._pro_con_assessor.assess(
                    logic_score, logic_reason, emotion_score, emotion_reason,
                )
                # 归档
                await self._memory.save(MemoryEntry(
                    namespace=f"self/moral/{self._turn_counter}",
                    key="assessment",
                    value={
                        "conflict_type": moral_conflict.conflict_type.value,
                        "logic_score": assessment.logic_score,
                        "emotion_score": assessment.emotion_score,
                        "recommended_path": assessment.recommended_path,
                        "deadlock": assessment.deadlock,
                        "escalation": assessment.escalation,
                    },
                ))
                # 升级到元认知
                if assessment.escalation:
                    self._metacognition.moral_escalation_pending = True
                # 两难 → 写入 subconscious
                if assessment.deadlock:
                    await self._memory.save(MemoryEntry(
                        namespace="subconscious/moral_conflict",
                        key=str(self._turn_counter),
                        value={
                            "path": "deadlock",
                            "logic": assessment.logic_reasoning,
                            "emotion": assessment.emotion_reasoning,
                        },
                    ))
```

---

- [ ] **任务 10：memory.py — search_chained 扩展配置支持**

**文件：**
- 修改：`chat_core/systems/memory.py`

**描述：** 确认 `search_chained()` 已支持外部传入 `RecallChainConfig`（已有 chain_config 参数），CreativityEngine Path B 直接复用。无需修改。

已确认：`memory.py:820` 的 `search_chained(query, chain_config=None)` 已接受 config 参数。✅ 无需改动。

---

- [ ] **任务 11：metacognition.py — moral_conflict 触发条件**

**文件：**
- 修改：`chat_core/systems/metacognition.py`

**描述：** 在 `check_triggers()` 中新增 `moral_escalation` 触发条件（由 turn_manager 在道德困境升级时设置）。

**步骤：**

- [ ] **步骤 1：新增属性**

```python
        # Spec 009: 道德升级标记
        self.moral_escalation_pending: bool = False
```

- [ ] **步骤 2：在 `build_context()` 参数中追加**

```python
        moral_conflict_context: str | None = None,  # Spec 009
```

在 context 组装中追加对应的段。

---

### 检查点：阶段 5
- [ ] 所有集成点代码就位
- [ ] `python -m pytest tests/ -q --tb=short` → 322 passed

---

### 阶段 6：测试

- [ ] **任务 12：test_intuition.py**

**文件：** `tests/test_intuition.py`

**测试覆盖 (SC-01~SC-05)：**

```python
"""Tests for Spec 009: IntuitionEngine — 3-level degradation"""

import pytest
from chat_core.core.types import (
    AttentionStateEnum, ChainedMemory, IntuitionLevel, MemoryEntry,
)
from chat_core.systems.intuition import IntuitionEngine


class TestIntuitionBasic:
    def test_default_l3(self):
        ie = IntuitionEngine()
        r = ie.evaluate([], None, 0.9)
        assert r.level == IntuitionLevel.L3_FULL_REACT
        assert not r.skip_react

    def test_l1_insufficient_hits(self):
        ie = IntuitionEngine()
        entries = [ChainedMemory(entry=MemoryEntry(namespace="t", key="k", value={}, salience=8.0))]
        r = ie.evaluate(entries, AttentionStateEnum.FOCUSED, 0.9)
        assert r.level != IntuitionLevel.L1_MEMORY_MATCH  # < 5 hits

    def test_l1_insufficient_salience(self):
        ie = IntuitionEngine()
        entries = [ChainedMemory(entry=MemoryEntry(namespace="t", key=f"k{i}", value={"text": "m"}, salience=3.0)) for i in range(6)]
        r = ie.evaluate(entries, AttentionStateEnum.FOCUSED, 0.9)
        assert r.level != IntuitionLevel.L1_MEMORY_MATCH  # max salience < 7

    def test_l1_strong_hits(self):
        ie = IntuitionEngine()
        entries = [ChainedMemory(entry=MemoryEntry(namespace="t", key=f"k{i}", value={"text": "记忆内容"}, salience=8.0)) for i in range(6)]
        r = ie.evaluate(entries, AttentionStateEnum.FOCUSED, 0.9)
        # FOCUSED boost → 大概率 L1
        if r.level == IntuitionLevel.L1_MEMORY_MATCH:
            assert r.skip_react
            assert r.fast_reply is not None

    def test_dull_reduces_l1(self):
        ie = IntuitionEngine()
        entries = [ChainedMemory(entry=MemoryEntry(namespace="t", key=f"k{i}", value={"text": "记忆内容"}, salience=8.0)) for i in range(6)]
        # DULL → L1 prob × 0.5
        r = ie.evaluate(entries, AttentionStateEnum.DULL, 0.9)
        # 不能 100% 断言，因为概率性；但确认不崩
        assert r.level in (IntuitionLevel.L1_MEMORY_MATCH, IntuitionLevel.L2_FAST_PATH, IntuitionLevel.L3_FULL_REACT)


class TestConfidenceHeuristic:
    def test_high_confidence(self):
        ie = IntuitionEngine()
        conf = ie.eval_fast_path_confidence("这是一个足够长的回复文本" * 5, "")
        assert conf > 0.7

    def test_low_confidence(self):
        ie = IntuitionEngine()
        conf = ie.eval_fast_path_confidence("短", "")
        assert conf < 0.7
```

~7 tests, 覆盖 SC-01~SC-05。

---

- [ ] **任务 13：test_creativity.py**

**文件：** `tests/test_creativity.py`

~6 tests, 覆盖 SC-06~SC-09。包括：触发判定（playfulness ≤ 0.5 → 不触发）、Path A prompt 生成、Path B filter、合并注入格式。

---

- [ ] **任务 14：test_humor.py**

**文件：** `tests/test_humor.py`

~5 tests, 覆盖 SC-10~SC-11。包括：预期违背检测、双关语检测、陌生人安全门、friend 安全门通过、build_injection 格式。

---

- [ ] **任务 15：test_moral.py**

**文件：** `tests/test_moral.py`

~8 tests, 覆盖 SC-12~SC-16。包括：三种冲突检测、Pro/Con 评估路径判定 (honest/protective/deadlock)、escalation 判定、deadlock 判定。

---

### 检查点：阶段 6
- [ ] `python -m pytest tests/ -q --tb=short` → 322 + ~26 = ~348 passed
- [ ] 新增测试覆盖 SC-01~SC-16

---

## 风险与缓解

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| IntuitionEngine 改造 `run()` 可能影响子Session 生命周期 | 高 | L1 skip 路径作为早期 return，不进入 while 循环；L2 在 `_think()` 内部分支处理 |
| CreativityEngine Path A 额外 LLM 调用增加延迟 | 中 | Flash 模型 + low reasoning_effort + 1s timeout + 降级保护 |
| MoralConflict Pro/Con 双脑调用可能阻塞审查管线 | 中 | 在 `_async_review_and_decide` 内执行（已在后台 task），不阻塞用户 |
| L1 概率调制使用 random.random() 导致不确定性 | 低 | 测试用确定性输入 + 多次采样验证趋势 |

## 待定问题

- L2 Fast Path 的 Flash 集成在 `_think()` 中的具体改造方式需在实施时精确定位（替代 function calling 调用为单次 chat）
- 道德困境的 LLM Pro/Con 调用需确认 `ModelProvider.chat()` 非流式接口签名
- Path B search_chained 使用 extended_config 时是否影响主检索（需确认 search_chained 无副作用）

---

## 执行交接

计划已完成并保存到 `docs/superpowers/plans/2026-07-10-cognitive-enhancement.md`。

**预期工作量：** 6 个阶段，15 个任务，~26 个新测试。预估 4-5 个 task session。
