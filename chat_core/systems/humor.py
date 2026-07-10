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
