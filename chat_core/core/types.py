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


@dataclass
class AttentionState:
    focus: float = 0.8
    dominance: float = 0.7


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


# ── 工具上下文 ──────────────────────────────────────────────

@dataclass
class ToolContext:
    root_dir: str = ""
    signal: Any = None  # asyncio cancellation signal
    session_id: str = ""
