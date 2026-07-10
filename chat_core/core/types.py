"""共享数据类型 — 所有模块的基础类型定义"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


# ── LLM 消息类型 ────────────────────────────────────────────

@dataclass
class ToolCall:
    id: str
    type: str = "function"
    function_name: str = ""
    function_args: str = ""


@dataclass
class ToolSpec:
    type: str = "function"
    function: dict[str, Any] = field(default_factory=dict)


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None
    name: str | None = None
    reasoning_content: str | None = None  # DeepSeek 推理链（必须在多轮对话中回传）


class StreamEventType(Enum):
    CONTENT_DELTA = "content_delta"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_END = "tool_call_end"
    DONE = "done"
    ERROR = "error"


@dataclass
class StreamEvent:
    type: StreamEventType
    content: str | None = None
    tool_call_id: str | None = None
    tool_call_name: str | None = None
    tool_call_args: str | None = None
    error: str | None = None
    usage: Usage | None = None
    reasoning_content: str | None = None  # DeepSeek 推理链内容


@dataclass
class NonStreamResult:
    content: str
    tool_calls: list[ToolCall]
    usage: Usage
    reasoning_content: str | None = None  # DeepSeek 推理链（非流式调用也需回传）


# ── Token 用量 ──────────────────────────────────────────────

@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0

    def add(self, other: Usage) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.prompt_cache_hit_tokens += other.prompt_cache_hit_tokens
        self.prompt_cache_miss_tokens += other.prompt_cache_miss_tokens

    def clone(self) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            prompt_cache_hit_tokens=self.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=self.prompt_cache_miss_tokens,
        )

    @staticmethod
    def zero() -> Usage:
        return Usage()


# ── Turn 状态 ────────────────────────────────────────────────

class TurnStatus(Enum):
    IDLE = "idle"
    DUAL_RECALL = "dual_recall"
    INJECTING = "injecting"
    SUB_SESSION = "sub_session"
    REVIEWING = "reviewing"
    DECIDING = "deciding"
    CORRECTING = "correcting"
    ARCHIVING = "archiving"
    DONE = "done"


class DecisionType(Enum):
    CORRECT = "correct"
    SILENCE = "silence"
    TWISTED = "twisted"


class ErrorType(Enum):
    IDENTITY_ERROR = "identity_error"
    CONTRADICTION = "contradiction"
    FACT_ERROR = "fact_error"
    MINOR_DETAIL = "minor_detail"
    NO_ERROR = "no_error"


class ToneErrorType(Enum):
    HURTFUL = "hurtful"
    INSENSITIVE = "insensitive"
    TONE_HARSH = "tone_harsh"
    TONE_COLD = "tone_cold"
    MINOR_TONE = "minor_tone"


class IntentType(Enum):
    SEARCH = "search"
    SPEAK = "speak"
    REMEMBER = "remember"
    NONE = "none"


class IntentStatus(Enum):
    PENDING = "pending"
    EXECUTING = "executing"
    DEFERRED = "deferred"
    RESOLVED = "resolved"
    EXPIRED = "expired"


class RelationType(Enum):
    EXTENDS = "extends"
    CONTRADICTS = "contradicts"
    RELATED_TO = "related_to"


# ── 回复相关 ──────────────────────────────────────────────────

@dataclass
class ReplySegment:
    text: str
    wait_before: float | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class FeelingLabel:
    primary: str = ""
    valence: float = 0.0  # -1 to 1


@dataclass
class UserMoodRead:
    mood: str = ""
    need: str = ""


@dataclass
class Intent:
    action: IntentType = IntentType.NONE
    detail: str = ""
    confidence: float = 0.0
    assessed_weight: float | None = None
    status: IntentStatus = IntentStatus.PENDING
    revisit_condition: str | None = None


@dataclass
class InnerThought:
    raw: str = ""
    feeling: FeelingLabel = field(default_factory=FeelingLabel)
    reflection: str = ""
    summary: str = ""
    topics: list[str] = field(default_factory=list)
    user_read: UserMoodRead = field(default_factory=UserMoodRead)
    self_assessment: str = ""
    intent: Intent | None = None


@dataclass
class FactError:
    error_type: ErrorType = ErrorType.NO_ERROR
    description: str = ""
    conflicting_memory_key: str = ""
    weight: float = 0.0


@dataclass
class ToneIssue:
    issue_type: ToneErrorType = ToneErrorType.MINOR_TONE
    description: str = ""
    weight: float = 0.0


@dataclass
class ReviewResult:
    logic_verdict: str = "ok"  # "ok" | "error_found" | "minor_issue"
    logic_weight: float = 0.0
    logic_errors: list[FactError] = field(default_factory=list)
    emotion_verdict: str = "ok"  # "ok" | "tone_issue" | "minor_tone"
    emotion_weight: float = 0.0
    emotion_issues: list[ToneIssue] = field(default_factory=list)
    combined_weight: float = 0.0
    decision: DecisionType = DecisionType.SILENCE


@dataclass
class CorrectionCmd:
    source: str = ""  # "logic" | "emotion" | "combined"
    message: str = ""
    written_to: str = ""
    is_twisted: bool = False


@dataclass
class ConversationTurn:
    turn_id: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    user_message: str = ""
    logic_injection: dict[str, Any] = field(default_factory=dict)
    emotion_injection: dict[str, Any] = field(default_factory=dict)
    sub_session_id: str = ""
    reply_segments: list[ReplySegment] = field(default_factory=list)
    inner_thoughts_raw: str | None = None
    inner_thoughts_parsed: InnerThought | None = None
    review: ReviewResult | None = None
    correction: CorrectionCmd | None = None
    status: TurnStatus = TurnStatus.IDLE


# ── 记忆相关 ──────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    namespace: str = ""
    key: str = ""
    value: dict[str, Any] = field(default_factory=dict)
    layer: str = "gist"  # "gist" | "detail"
    salience: float = 5.0
    entity_type: str = ""
    topic_tags: list[str] = field(default_factory=list)
    emotional_tags: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None
    ttl: int | None = None
    # Spec 003: recall 深刻化字段
    access_count: int = 0
    last_access: str | None = None
    decay_curve: str = "standard"
    # 衰减系统字段
    auto_migrate: int = 0
    decay_start: str | None = None
    # Spec 003 §12: 幂律衰减时间基准 (unix timestamp)
    created_at_epoch: float | None = None


@dataclass
class MemoryLink:
    from_key: str = ""
    to_key: str = ""
    relation: RelationType = RelationType.RELATED_TO


# ── Spec 003: 记忆联锁 ──────────────────────────────────────

@dataclass
class ChainedMemory:
    """联锁记忆条目：MemoryEntry + 联锁元数据"""
    entry: MemoryEntry
    chain_level: int = 0          # 0=direct, 1=links, 2=topic_tags, 3=entity, 4=namespace
    chain_parent_key: str | None = None  # direct 时为 None
    relevance_score: float = 0.0


@dataclass
class RecallChainConfig:
    """recall 联锁配置"""
    top_n: int                    # FTS5 主检索返回条数
    extensions: list[int]         # 每个 rank 的延伸数量 [N₀, N₁, ...]
    max_per_level: int            # 每级 fallback 单次取出的上限
    namespace_prefix: str | None = None  # 命名空间限制


# 主脑配置: top 5 + 延伸 3/2/2/1/0 = 最多 13 条
LOGIC_BRAIN_CHAIN_CONFIG = RecallChainConfig(
    top_n=5,
    extensions=[3, 2, 2, 1, 0],
    max_per_level=3,
    namespace_prefix=None,
)

# 子Session 配置: top 3 + 延伸 2/1/0 = 最多 6 条
SUB_SESSION_CHAIN_CONFIG = RecallChainConfig(
    top_n=3,
    extensions=[2, 1, 0],
    max_per_level=2,
    namespace_prefix=None,
)


# ── 情绪与人格 ──────────────────────────────────────────────

@dataclass
class EmotionState:
    brain: str = ""  # "logic" | "emotion" | "sub"
    surprise: float = 0.0
    confusion: float = 0.0
    fear: float = 0.0
    anger: float = 0.0
    disgust: float = 0.0
    joy: float = 0.5
    sadness: float = 0.0
    interest: float = 0.5
    anticipation: float = 0.0
    trust: float = 0.5
    last_tick: datetime = field(default_factory=datetime.now)

    # Spec 005: 12 维复合情绪
    bittersweet: float = 0.0      # 怀念 (joy × sadness)
    guilt: float = 0.0            # 愧疚 (sadness × fear)
    anxiety: float = 0.0          # 焦虑 (fear × anticipation)
    contempt: float = 0.0         # 轻蔑 (anger × disgust)
    gratification: float = 0.0    # 欣慰 (joy × trust)
    disappointment: float = 0.0   # 失望 (sadness × surprise)
    envy: float = 0.0             # 嫉妒 (sadness × anger)
    pride: float = 0.0            # 骄傲 (joy × anticipation)
    resentment: float = 0.0       # 怨恨 (anger × sadness)
    awe: float = 0.0              # 敬畏 (fear × surprise × trust)
    nostalgia: float = 0.0        # 怀旧 (joy × sadness × interest)
    bewilderment: float = 0.0     # 困惑加深 (confusion × fear)


# ── Spec 005: 防御机制 ──────────────────────────────────────────

class DefenseType(Enum):
    DIRECT = "direct"           # 无防御，直接纠正
    DENIAL = "denial"           # 否认：不写 correction
    RATIONALIZE = "rationalize" # 合理化：写 correction + 辩护
    PROJECT = "project"         # 投射：归因转向用户


@dataclass
class DefenseResult:
    defense_type: DefenseType
    correction_text: str | None = None       # 写入 corrections 的文本 (DENIAL 为 None)
    inner_reflection: str = ""               # self/defenses 归档
    defense_awareness: str = ""              # subconscious/defense_awareness
    emotion_delta: dict[str, float] = field(default_factory=dict)
    silence_increment: int = 0               # DENIAL → 1, 其余 → 0


@dataclass
class PersonalityWeights:
    curiosity: float = 0.7
    sociability: float = 0.8
    playfulness: float = 0.6
    empathy: float = 0.5
    assertiveness: float = 0.3
    creativity: float = 0.6
    impulsiveness: float = 0.2
    loyalty: float = 0.75


# ── 注意力状态机 (注意力状态机 Phase 1) ────────────────────────────


class AttentionStateEnum(Enum):
    """三态注意力状态"""
    FOCUSED = "focused"      # focus ≥ 0.6
    DRIFTING = "drifting"    # 0.3 ≤ focus < 0.6
    DULL = "dull"            # focus < 0.3


class AttentionEvent(Enum):
    """注意力状态转移事件"""
    USER_MESSAGE = "user_message"
    EMOTION_POSITIVE = "emotion_positive"
    EMOTION_NEGATIVE = "emotion_negative"
    EMOTION_SHOCK = "emotion_shock"
    MEMORY_STRONG_HIT = "memory_strong_hit"
    MEMORY_MISS = "memory_miss"
    TOPIC_MATCH = "topic_match"
    RACE_MILD = "race_mild"
    RACE_SEVERE = "race_severe"
    SHORT_REPLY_STREAK = "short_reply_streak"
    SILENCE_TICK = "silence_tick"
    INTENT_DETECTED = "intent_detected"
    CORRECTION_TRIGGERED = "correction_triggered"


@dataclass
class AttentionState:
    focus: float = 0.8
    dominance: float = 0.7
    fatigue: float = 0.0  # 新增：疲劳累积因子 [0.0, 1.0]


# ── 行为脑 ────────────────────────────────────────────────────

@dataclass
class ActionResult:
    task: str = ""
    task_type: str = ""  # "search" | "recall" | "web_fetch" | "describe_image"
    output: str = ""
    raw: dict[str, Any] | None = None
    sources: list[str] = field(default_factory=list)
    session_id: str = ""
    elapsed_ms: int = 0
    success: bool = True
    error: str | None = None


# ── Spec 007: 具身感知 ─────────────────────────────────────

@dataclass
class EnergyState:
    """精力状态 (Spec 007)"""
    energy: float = 0.9          # [0.0, 1.0]
    last_update: float = 0.0     # unix timestamp
    total_turns_today: int = 0


@dataclass
class SubjectiveTimePerception:
    """主观时间感知 (Spec 007) — 写入 turn memory 供 Spec 003 回溯"""
    speed_factor: float = 1.0
    perception: str = "normal"   # "immersed" | "normal" | "dragging"
    description: str = ""
    fatigue_at_end: float = 0.9


# ── 工具上下文 ──────────────────────────────────────────────

@dataclass
class ToolContext:
    root_dir: str = ""
    signal: Any = None  # asyncio cancellation signal
    session_id: str = ""


# ── Spec 006: 元认知深度 ─────────────────────────────────────

@dataclass
class MetacognitionReport:
    """元认知审查结论 — 对应 metacognition_report 工具返回值"""
    insight_text: str = ""
    confidence: float = 0.0
    param_overrides: "MetaParamOverrides | None" = None


@dataclass
class MetaParamOverrides:
    """临时参数覆盖容器。由 TurnManager 维护，注入各子系统。

    覆盖过期后（默认 N 轮），参数自动恢复默认。

    ⚠️ Sentinel 字段 (_xxx_set) 用于区分"LLM 未返回该字段"与"LLM 返回了默认值"。
    例如 LLM 合法返回 review_threshold_offset=0.0 也应被 apply。
    """

    review_threshold_offset: float = 0.0      # ±0.15
    defense_prob_multiplier: float = 1.0      # 0.5~2.0
    interest_modulations: dict[str, float] = field(default_factory=dict)  # {topic: ±0.3}
    emotion_threshold_offset: float = 0.0     # ±0.1
    inner_thoughts_mode: str = "full"         # "full" | "brief" | "minimal"

    # Sentinel: True 表示 LLM 显式设置了对应字段
    _review_threshold_set: bool = False
    _defense_prob_set: bool = False
    _emotion_threshold_set: bool = False
    _inner_thoughts_set: bool = False

    _applied_at_turn: int = 0
    _expiry_turns: int = 5

    def apply(self, report: MetacognitionReport, turn_counter: int) -> None:
        """应用元认知报告。confidence < 0.6 时只写文本不调参。

        ⚠️ 使用 is not None 判断字段是否被 LLM 显式设置（而非默认值比较）。
        例如 LLM 合法返回 review_threshold_offset=0.0 也需要被应用。
        """
        if report.confidence < 0.6:
            return
        overrides = report.param_overrides
        if overrides is None:
            return
        # 使用 sentinel 标记：LLM 不返回的字段在解析时保持 None
        if overrides._review_threshold_set:
            self.review_threshold_offset = overrides.review_threshold_offset
        if overrides._defense_prob_set:
            self.defense_prob_multiplier = overrides.defense_prob_multiplier
        if overrides.interest_modulations:
            self.interest_modulations.update(overrides.interest_modulations)
        if overrides._emotion_threshold_set:
            self.emotion_threshold_offset = overrides.emotion_threshold_offset
        if overrides._inner_thoughts_set:
            self.inner_thoughts_mode = overrides.inner_thoughts_mode
        self._applied_at_turn = turn_counter

    def is_expired(self, turn_counter: int) -> bool:
        return turn_counter - self._applied_at_turn >= self._expiry_turns

    def get_review_threshold(self, base: float = 0.5, turn_counter: int = 0) -> float:
        if self.is_expired(turn_counter):
            return base
        return max(0.35, min(0.65, base + self.review_threshold_offset))


# 自我批评触发关键词
SELF_CRITICISM_KEYWORDS: list[str] = [
    "不该这么说", "又说错了", "太机械了", "没意思", "不想聊了",
]
