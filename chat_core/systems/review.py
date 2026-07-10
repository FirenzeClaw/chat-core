"""ReviewSystem — 3-layer error detection, weighted decision, and intent extraction (Phase 5, T032-T035 + Phase 7, T057)"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from chat_core.core.provider import ModelProvider
from chat_core.core.types import (
    DecisionType,
    ErrorType,
    FactError,
    Intent,
    IntentStatus,
    IntentType,
    MemoryEntry,
    Message,
    ReviewResult,
    ToneErrorType,
    ToneIssue,
)
from chat_core.systems.memory import MemoryStore


class ReviewSystem:
    """3-layer error detection and weighted decision engine.

    Layer 1: Keyword entity extraction from reply + comparison with memory recall.
    Layer 2: Check candidate errors against subconscious/corrections cache.
    Layer 3: LLM-based review (1s timeout → skip).
    """

    # ── Error / Tone weight maps (T035) ──────────────────────

    ERROR_TYPE_WEIGHTS: dict[ErrorType, float] = {
        ErrorType.IDENTITY_ERROR: 0.9,
        ErrorType.CONTRADICTION: 0.8,
        ErrorType.FACT_ERROR: 0.7,
        ErrorType.MINOR_DETAIL: 0.3,
        ErrorType.NO_ERROR: 0.0,
    }

    TONE_ERROR_WEIGHTS: dict[ToneErrorType, float] = {
        ToneErrorType.HURTFUL: 0.95,
        ToneErrorType.INSENSITIVE: 0.7,
        ToneErrorType.TONE_HARSH: 0.6,
        ToneErrorType.TONE_COLD: 0.5,
        ToneErrorType.MINOR_TONE: 0.3,
    }

    # ── Entity extraction patterns ───────────────────────────

    # Common Chinese surnames (百家姓 top 50)
    _SURNAMES = (
        "李王张刘陈杨赵黄周吴徐孙马胡朱郭何罗高林郑梁谢宋唐许韩冯邓曹彭曾肖田董潘袁蔡蒋余于杜叶程魏苏"
        "吕丁任卢姚沈钟姜崔谭陆范汪廖石金贾夏韦付方白邹孟"
    )

    # Chinese character range (used inline in patterns below)
    _CJK_RANGE = r"\u4e00-\u9fff"

    # Pattern: surname + 1-2 given name characters (non-greedy)
    _RE_CHINESE_NAME = re.compile(
        rf"[{_SURNAMES}][{_CJK_RANGE}]{{1,2}}?"
    )

    # Place patterns: two strategies combined, plus stop-word cleanup
    # Strategy 1: preposition + short place name + optional suffix
    _RE_PLACE_PREFIX = re.compile(
        r"(?:在|去|到|从|来自|住在|位于)"
        rf"([{_CJK_RANGE}]{{2,4}}?)"
        r"(?:省|市|县|区|镇|村|路|街|广场|公园|大厦|中心|学校|医院|公司|酒店)?"
        r"(?=[。，！？；、\s]|$)"
    )

    # Strategy 2: Place names ending with known place suffix (non-greedy)
    _RE_PLACE_SUFFIX = re.compile(
        rf"([{_CJK_RANGE}]{{2,5}}?(?:省|市|县|区|镇|村|路|街|广场|公园|大厦|中心|学校|医院|公司|酒店))"
    )

    # Common CJK prepositions / stop words to strip from entity edges
    _STOP_LEADING = set(
        "在去到来从往至向朝对跟和与及的为被把让给叫请使用由"
        "说都就也已还又再便才刚正只仅光单当每各某此其之而"
        "你我他她它这那"
    )

    def _clean_place(self, text: str) -> str:
        """Strip leading stop characters from an extracted place entity."""
        while text and text[0] in self._STOP_LEADING:
            text = text[1:]
        return text

    # Time patterns
    _RE_TIME = re.compile(
        r"("
        r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}年\d{1,2}月|\d{1,2}月\d{1,2}日|"
        r"\d{4}年|"
        r"星期[一二三四五六日天]|"
        r"周[一二三四五六日]|"
        r"今天|昨天|明天|前天|后天|"
        r"上午|下午|中午|晚上|早上|傍晚|凌晨|"
        r"\d{1,2}[点时]半?|"
        r"去年|今年|明年|上个月|下个月"
        r")"
    )

    # Number/quantity patterns
    _RE_NUMBER = re.compile(
        r"("
        r"\d+(?:\.\d+)?(?:万|亿|千|百)?"
        r"(?:元|块|个|岁|年|天|小时|分钟|秒|公里|米|厘米|斤|公斤|克|吨|度|次|遍|本|张|条|位|名)?"
        r"|"
        r"[一二三四五六七八九十百千万亿]+"
        r"(?:元|块|个|岁|年|天|小时|分钟|秒|公里|米|斤|公斤|克|吨)?"
        r")"
    )

    def __init__(self, provider: ModelProvider, memory: MemoryStore):
        self._provider = provider
        self._memory = memory

    # ── Layer 1: Entity extraction ──────────────────────────

    def _extract_entities(self, text: str) -> dict[str, set[str]]:
        """Extract named entities from reply text using regex patterns.

        Returns dict with keys: names, places, times, numbers.
        Values are deduplicated sets of matched strings.
        """
        entities: dict[str, set[str]] = {
            "names": set(),
            "places": set(),
            "times": set(),
            "numbers": set(),
        }

        for m in self._RE_CHINESE_NAME.finditer(text):
            name = m.group().strip()
            if len(name) >= 2:
                entities["names"].add(name)

        for m in self._RE_PLACE_PREFIX.finditer(text):
            place = self._clean_place(m.group(1).strip())
            if len(place) >= 2:
                entities["places"].add(place)

        for m in self._RE_PLACE_SUFFIX.finditer(text):
            place = self._clean_place(m.group(1).strip())
            if len(place) >= 2:
                entities["places"].add(place)

        for m in self._RE_TIME.finditer(text):
            entities["times"].add(m.group().strip())

        for m in self._RE_NUMBER.finditer(text):
            entities["numbers"].add(m.group().strip())

        return entities

    # ── Layer 1: Compare with memory ────────────────────────

    def _find_conflicts(
        self,
        reply_text: str,
        entities: dict[str, set[str]],
        memories: list[MemoryEntry],
    ) -> list[tuple[str, str, str]]:
        """Compare extracted entities against memory recall.

        Returns list of (category, reply_entity, memory_entity) for conflicts.
        """
        candidates: list[tuple[str, str, str]] = []
        memory_text = " ".join(
            json.dumps(m.value, ensure_ascii=False) for m in memories
        )

        # For each entity category, check whether reply entities are supported by memory
        for category, ents in entities.items():
            for ent in ents:
                if ent in memory_text:
                    # Entity appears verbatim in memory → OK
                    continue

                # Entity not in memory → check if memory has a different value
                # for the same category
                mem_entities = self._extract_entities(memory_text)
                if mem_entities.get(category):
                    # There are same-category entities in memory that don't
                    # match the reply → potential conflict
                    for mem_ent in list(mem_entities[category])[:3]:
                        if mem_ent != ent:
                            candidates.append((category, ent, mem_ent))

        return candidates

    # ── Layer 2: Correction cache check ─────────────────────

    async def _has_cached_correction(self, entity: str) -> bool:
        """Check if a correction for this entity already exists in
        subconscious/corrections."""
        corrections = await self._memory.query("subconscious/corrections", limit=20)
        for c in corrections:
            search_text = c.key + " " + json.dumps(c.value, ensure_ascii=False)
            if entity in search_text:
                return True
        return False

    # ── Layer 3: LLM-based review ───────────────────────────

    async def _llm_review(
        self,
        reply_excerpt: str,
        memory_value: str,
    ) -> dict[str, Any]:
        """LLM-based fact-check with 1s timeout.

        Prompt format: "子Session说'{reply_excerpt}'。记忆显示'{memory_value}'。这是错误吗？"
        Returns {"is_error": bool, "severity": float}.
        On timeout or error: returns {"is_error": False, "severity": 0.0, "skipped": True}.
        """
        reply_excerpt = reply_excerpt[:200]
        memory_value = memory_value[:200]
        prompt = (
            f"子Session说'{reply_excerpt}'。记忆显示'{memory_value}'。这是错误吗？"
        )
        messages = [Message(role="user", content=prompt)]

        try:
            result = await asyncio.wait_for(
                self._provider.chat(
                    messages=messages,
                    model=self._provider._default_model,
                    max_tokens=128,
                    temperature=0.1,
                    reasoning_effort="medium",
                ),
                timeout=1.0,
            )
            content = result.content.strip().lower()

            is_error = any(
                w in content
                for w in ["错误", "是", "yes", "true", "对", "有问题", "不一致", "冲突"]
            )

            # Extract severity from response
            severity = 0.5  # default
            if any(w in content for w in ["严重", "非常重要", "重大", "关键"]):
                severity = 0.9
            elif any(w in content for w in ["中等", "一般"]):
                severity = 0.5
            elif any(w in content for w in ["轻微", "小", "不重要", "细节"]):
                severity = 0.3

            return {"is_error": is_error, "severity": severity}
        except asyncio.TimeoutError:
            return {"is_error": False, "severity": 0.0, "skipped": True}
        except Exception:
            return {"is_error": False, "severity": 0.0, "skipped": True}

    # ── Tone review (heuristic) ─────────────────────────────

    def _review_tone(self, reply_text: str) -> tuple[list[ToneIssue], float]:
        """Heuristic tone review: keyword-based detection of
        harsh, cold, insensitive, or hurtful language.

        Returns (list of ToneIssue, max_weight).
        """
        tone_issues: list[ToneIssue] = []
        emotion_weight = 0.0

        # Harsh language patterns
        harsh_keywords = ["傻", "蠢", "白痴", "滚", "闭嘴", "弱智", "有病", "神经病"]
        for kw in harsh_keywords:
            if kw in reply_text:
                ti = ToneIssue(
                    issue_type=ToneErrorType.TONE_HARSH,
                    description=f"Detected harsh language: '{kw}'",
                    weight=self.TONE_ERROR_WEIGHTS[ToneErrorType.TONE_HARSH],
                )
                tone_issues.append(ti)
                emotion_weight = max(emotion_weight, 0.6)
                break  # one match is enough for this category

        # Cold/disengaged tone
        cold_keywords = ["哦", "嗯", "知道了", "随便", "无所谓", "关我什么事", "不关我事"]
        cold_exact = ["哦", "嗯"]  # Only match standalone
        for kw in cold_keywords:
            if kw in cold_exact:
                # Check for standalone use (surrounded by punctuation or line boundaries)
                if re.search(rf"(?:^|[。！？\n\r\t ]){re.escape(kw)}(?:$|[。！？\n\r\t ])", reply_text):
                    ti = ToneIssue(
                        issue_type=ToneErrorType.TONE_COLD,
                        description=f"Detected cold/disengaged tone: '{kw}'",
                        weight=self.TONE_ERROR_WEIGHTS[ToneErrorType.TONE_COLD],
                    )
                    tone_issues.append(ti)
                    emotion_weight = max(emotion_weight, 0.5)
                    break
            elif kw in reply_text:
                ti = ToneIssue(
                    issue_type=ToneErrorType.TONE_COLD,
                    description=f"Detected cold/disengaged tone: '{kw}'",
                    weight=self.TONE_ERROR_WEIGHTS[ToneErrorType.TONE_COLD],
                )
                tone_issues.append(ti)
                emotion_weight = max(emotion_weight, 0.5)
                break

        # Insensitive language
        insensitive_keywords = ["活该", "自找的", "哭有什么用", "至于吗", "你活该"]
        for kw in insensitive_keywords:
            if kw in reply_text:
                ti = ToneIssue(
                    issue_type=ToneErrorType.INSENSITIVE,
                    description=f"Detected insensitive language: '{kw}'",
                    weight=self.TONE_ERROR_WEIGHTS[ToneErrorType.INSENSITIVE],
                )
                tone_issues.append(ti)
                emotion_weight = max(emotion_weight, 0.7)
                break

        # Hurtful language (combos: requires both words)
        hurtful_combos = [("讨厌", "你"), ("恨", "你"), ("恶心", "你"), ("失望", "对你")]
        for w1, w2 in hurtful_combos:
            if w1 in reply_text and (not w2 or w2 in reply_text):
                ti = ToneIssue(
                    issue_type=ToneErrorType.HURTFUL,
                    description=f"Detected potentially hurtful language: '{w1}'",
                    weight=self.TONE_ERROR_WEIGHTS[ToneErrorType.HURTFUL],
                )
                tone_issues.append(ti)
                emotion_weight = max(emotion_weight, 0.95)
                break

        return tone_issues, emotion_weight

    # ── Weighted decision (T035) ────────────────────────────

    def _compute_decision(
        self,
        logic_weight: float,
        emotion_weight: float,
        meta_overrides: "MetaParamOverrides | None" = None,
        turn_counter: int = 0,
        value_engine: Any = None,  # Spec 010
    ) -> DecisionType:
        """Weighted decision: combined = logic * 0.5 + emotion * 0.5.

        > threshold → CORRECT (or TWISTED if logic > 0.8 and emotion < 0.3)
        ≤ threshold → SILENCE

        Spec 010: threshold = base_honesty × value_engine.get_modulation("review_threshold")
        Spec 006: + meta_overrides offset (applied after baseline modulation).
        """
        combined = logic_weight * 0.5 + emotion_weight * 0.5

        base_threshold = 0.5
        # Spec 010: 价值观基线调制 (honesty factor)
        if value_engine is not None:
            base_threshold *= value_engine.get_modulation("review_threshold")

        # Spec 006: 元认知偏移 (applied after baseline)
        if meta_overrides is not None:
            threshold = meta_overrides.get_review_threshold(base=base_threshold, turn_counter=turn_counter)
        else:
            threshold = base_threshold

        if combined > threshold:
            # T038: Twisted state check
            if logic_weight > 0.8 and emotion_weight < 0.3:
                return DecisionType.TWISTED
            return DecisionType.CORRECT
        else:
            return DecisionType.SILENCE

    # ── Main review pipeline ────────────────────────────────

    async def review(
        self,
        replies: list[str],
        inner_thoughts: str | None,
        memories: list[MemoryEntry],
        user_message: str,
        **kwargs: Any,
    ) -> ReviewResult:
        """Execute the 3-layer review pipeline.

        1. Layer 1: Extract entities from reply, compare with memory.
        2. Layer 2: For each candidate, check subconscious/corrections cache.
        3. Layer 3: For remaining candidates, LLM review (1s timeout).
        4. Tone review: heuristic keyword-based tone check.
        5. Weighted decision: combine logic + emotion weights.

        Spec 006: 接受 meta_overrides 和 turn_counter 关键字参数传递到 _compute_decision。
        """
        review = ReviewResult()
        reply_text = " ".join(replies)

        # ── Logic review ─────────────────────────────────────
        entities = self._extract_entities(reply_text)
        candidates = self._find_conflicts(reply_text, entities, memories)

        logic_errors: list[FactError] = []
        logic_weight = 0.0

        for category, entity, mem_entity in candidates:
            # Layer 2: check correction cache
            cached = await self._has_cached_correction(entity)

            if cached:
                # Correction already exists → confirm error without LLM
                fe = FactError(
                    error_type=ErrorType.CONTRADICTION,
                    description=(
                        f"Entity '{entity}' in category '{category}' "
                        f"conflicts with memory '{mem_entity}' (cached correction)"
                    ),
                    conflicting_memory_key=f"subconscious/corrections/{entity}",
                    weight=self.ERROR_TYPE_WEIGHTS[ErrorType.CONTRADICTION],
                )
                logic_errors.append(fe)
                logic_weight = max(logic_weight, 0.8)
                continue

            # Layer 3: LLM review
            # Build excerpt around the entity
            idx = reply_text.find(entity)
            start = max(0, idx - 20)
            end = min(len(reply_text), idx + len(entity) + 30)
            excerpt = reply_text[start:end]

            llm_result = await self._llm_review(excerpt, mem_entity)

            if llm_result.get("is_error", False):
                error_type = ErrorType.FACT_ERROR
                if category == "names":
                    error_type = ErrorType.IDENTITY_ERROR

                severity = llm_result.get("severity", 0.5)
                weight = self.ERROR_TYPE_WEIGHTS[error_type] * severity

                fe = FactError(
                    error_type=error_type,
                    description=(
                        f"Entity '{entity}' in category '{category}' "
                        f"conflicts with memory '{mem_entity}'"
                    ),
                    conflicting_memory_key=f"memory/{category}/{mem_entity}",
                    weight=weight,
                )
                logic_errors.append(fe)
                logic_weight = max(logic_weight, weight)

        review.logic_errors = logic_errors
        review.logic_weight = logic_weight

        # Set logic verdict
        if logic_weight >= 0.6:
            review.logic_verdict = "error_found"
        elif logic_weight >= 0.3:
            review.logic_verdict = "minor_issue"
        else:
            review.logic_verdict = "ok"

        # ── Emotion / tone review ────────────────────────────
        tone_issues, emotion_weight = self._review_tone(reply_text)
        review.emotion_issues = tone_issues
        review.emotion_weight = emotion_weight

        # Set emotion verdict
        if emotion_weight >= 0.6:
            review.emotion_verdict = "tone_issue"
        elif emotion_weight >= 0.3:
            review.emotion_verdict = "minor_tone"
        else:
            review.emotion_verdict = "ok"

        # ── Weighted decision ────────────────────────────────
        review.combined_weight = logic_weight * 0.5 + emotion_weight * 0.5
        review.decision = self._compute_decision(
            logic_weight, emotion_weight,
            meta_overrides=kwargs.get("meta_overrides"),
            turn_counter=kwargs.get("turn_counter", 0),
            value_engine=kwargs.get("value_engine"),  # Spec 010
        )

        return review


# ── Intent Extraction (Phase 7, T057) ────────────────────────────

# Regex: 我是否想要做什么[:：]\s*(.+?)(?:\n\n|$)
_RE_INTENT = re.compile(
    r"我是否想要做什么[：:]\s*(.+?)(?:\n\n|$)",
    re.DOTALL,
)

# Classification keyword mappings
_INTENT_KEYWORDS: dict[IntentType, list[str]] = {
    IntentType.SEARCH: ["搜索", "查", "找", "检索", "查询", "了解"],
    IntentType.SPEAK: ["告诉", "说", "提醒", "表达", "分享", "讲", "回复"],
    IntentType.REMEMBER: ["记住", "记录", "记忆", "保存", "备忘"],
}

# Fallback trigger keywords
_FALLBACK_KEYWORDS: list[str] = ["想", "要", "该"]


def extract_intent(
    inner_thoughts_text: str,
    provider: ModelProvider | None = None,
) -> Intent:
    """从内心戏文本中提取意图 (Phase 7, T057).

    1. 正则匹配 `我是否想要做什么[:：] ...`
    2. 关键词分类 → IntentType
    3. 无匹配 + 有想/要/该关键词 → LLM 轻量提取（fallback）
    4. 无匹配 → IntentType.NONE

    Args:
        inner_thoughts_text: 内心戏原始文本
        provider: 可选的 ModelProvider，用于 LLM fallback

    Returns:
        Intent 对象
    """
    if not inner_thoughts_text:
        return Intent()

    # Step 1: Regex match
    m = _RE_INTENT.search(inner_thoughts_text)
    if m:
        detail = m.group(1).strip()
        if detail:
            intent_type = _classify_intent(detail)
            return Intent(
                action=intent_type,
                detail=detail,
                confidence=0.8,
            )

    # Step 2: Fallback — check for 想/要/该 keywords
    if any(kw in inner_thoughts_text for kw in _FALLBACK_KEYWORDS):
        # Try LLM extraction if provider available
        if provider is not None:
            intent = _llm_extract_intent(inner_thoughts_text, provider)
            if intent is not None:
                return intent

        # No LLM or LLM failed: try keyword match on entire text
        overall_type = _classify_intent(inner_thoughts_text)
        if overall_type != IntentType.NONE:
            return Intent(
                action=overall_type,
                detail=inner_thoughts_text[:200],
                confidence=0.4,
            )

    return Intent()


def _classify_intent(text: str) -> IntentType:
    """根据文本中的关键词分类意图类型。

    Args:
        text: 意图描述文本

    Returns:
        匹配的 IntentType，无匹配返回 NONE
    """
    text_lower = text.lower()
    for intent_type, keywords in _INTENT_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return intent_type
    return IntentType.NONE


def _llm_extract_intent(
    text: str,
    provider: ModelProvider,
) -> Intent | None:
    """使用轻量 LLM 提取意图 (max_tokens=64, timeout=1s).

    Args:
        text: 内心戏文本（截取前 300 字符）
        provider: ModelProvider 实例

    Returns:
        Intent 对象，失败返回 None
    """
    text_snippet = text[:300]
    prompt = (
        f"从以下内心戏中提取意图。返回JSON格式: {{\"action\": \"search|speak|remember|none\", \"detail\": \"具体内容\"}}\n\n"
        f"内心戏: {text_snippet}"
    )
    messages = [Message(role="user", content=prompt)]

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在已有 event loop 中，不能使用 wait_for 阻塞
            # 使用 create_task + 超时回退
            return _sync_fallback_classify(text)
        else:
            result = asyncio.run(
                asyncio.wait_for(
                    provider.chat(
                        messages=messages,
                        max_tokens=64,
                        temperature=0.1,
                        reasoning_effort="low",
                    ),
                    timeout=1.0,
                )
            )
    except (asyncio.TimeoutError, RuntimeError, Exception):
        return _sync_fallback_classify(text)

    content = result.content.strip()
    # Try to parse JSON from response
    try:
        # Find JSON object in response
        json_match = re.search(r'\{[^}]+\}', content)
        if json_match:
            data = json.loads(json_match.group())
            action_str = data.get("action", "none").lower()
            action_map: dict[str, IntentType] = {
                "search": IntentType.SEARCH,
                "speak": IntentType.SPEAK,
                "remember": IntentType.REMEMBER,
            }
            action = action_map.get(action_str, IntentType.NONE)
            detail = data.get("detail", "")
            if action != IntentType.NONE and detail:
                return Intent(
                    action=action,
                    detail=str(detail),
                    confidence=0.6,
                )
    except (json.JSONDecodeError, AttributeError):
        pass

    return _sync_fallback_classify(text)


def _sync_fallback_classify(text: str) -> Intent | None:
    """同步降级分类（无需 LLM）。"""
    action = _classify_intent(text)
    if action != IntentType.NONE:
        return Intent(
            action=action,
            detail=text[:200],
            confidence=0.3,
        )
    return None
