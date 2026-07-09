"""Interest system — FuzzyParam, silence accumulator, and InterestModel (Phase 5, T039 + Phase 7, T054)"""

from __future__ import annotations

import random
import time
from datetime import datetime


class FuzzyParam:
    """A fuzzy parameter that adds controlled noise around a base value.

    Uses a simple amplitude-driven random offset plus tiny gaussian-like
    jitter for realistic variation.

    Attributes:
        base: The central value of the parameter.
        amplitude: Half-width of the uniform noise band.

    Example:
        >>> fp = FuzzyParam(0.2, amplitude=0.1)
        >>> v = fp.sample()  # roughly in [0.15, 0.25]
    """

    def __init__(self, base: float, amplitude: float = 0.1):
        if amplitude < 0:
            raise ValueError("amplitude must be non-negative")
        self.base = float(base)
        self.amplitude = float(amplitude)

    def sample(self) -> float:
        """Sample a value with noise.

        Formula: base + amplitude * (random() - 0.5) + noise

        The uniform term (random() - 0.5) * amplitude gives ±amplitude/2
        range. The noise term adds a tiny extra jitter using triangular
        distribution (sum of two uniforms minus one).
        """
        uniform_offset = self.amplitude * (random.random() - 0.5)
        # Triangular noise: sum of two uniforms gives a triangular
        # distribution with lower tails than pure uniform
        noise = (random.random() + random.random() - 1.0) * 0.02
        return self.base + uniform_offset + noise

    def __repr__(self) -> str:
        return f"FuzzyParam(base={self.base:.4f}, amplitude={self.amplitude:.4f})"


class SilenceAccumulator:
    """Tracks silence counts per error type and computes accumulated weight.

    Formula: base = min(0.3, silence_count × 0.05)

    Each time a non-critical error is silently observed (combined ≤ 0.5),
    the accumulator for that error type increments and the effective
    weight grows. This models growing concern over repeated minor issues.
    """

    MAX_BASE = 0.3
    INCREMENT = 0.05

    def __init__(self):
        self._counters: dict[str, int] = {}
        self._fuzzy_params: dict[str, FuzzyParam] = {}

    def increment(self, error_type: str) -> float:
        """Increment the silence counter for an error type.

        Returns the new fuzzy base value.
        """
        count = self._counters.get(error_type, 0) + 1
        self._counters[error_type] = count

        base = min(self.MAX_BASE, count * self.INCREMENT)
        fp = FuzzyParam(base, amplitude=0.05)
        self._fuzzy_params[error_type] = fp
        return base

    def get_base(self, error_type: str) -> float:
        """Get the current base value for an error type."""
        count = self._counters.get(error_type, 0)
        return min(self.MAX_BASE, count * self.INCREMENT)

    def get_fuzzy(self, error_type: str) -> FuzzyParam:
        """Get or create a FuzzyParam for an error type."""
        if error_type not in self._fuzzy_params:
            base = self.get_base(error_type)
            self._fuzzy_params[error_type] = FuzzyParam(base, amplitude=0.05)
        return self._fuzzy_params[error_type]

    def reset(self, error_type: str) -> None:
        """Reset the accumulator for an error type."""
        self._counters.pop(error_type, None)
        self._fuzzy_params.pop(error_type, None)

    @property
    def all_bases(self) -> dict[str, float]:
        """Get all current base values."""
        return {k: self.get_base(k) for k in self._counters}

    def __repr__(self) -> str:
        return f"SilenceAccumulator({self.all_bases})"


class InterestModel:
    """话题兴趣追踪模型 (Phase 7, T054).

    追踪每次对话中话题出现的次数，累积权重，
    支持按小时衰减。用于驱动主动行为中的兴趣话题选择。

    Attributes:
        topic_trigger_threshold: 同一话题触发阈值（默认 3 次提及）
        topic_weight_increment: 每次提及的权重增量（默认 0.1）
        decay_per_hour: 每小时衰减量（默认 0.05）
    """

    def __init__(
        self,
        topic_trigger_threshold: int = 3,
        topic_weight_increment: float = 0.1,
        decay_per_hour: float = 0.05,
    ) -> None:
        self.topic_trigger_threshold = topic_trigger_threshold
        self.topic_weight_increment = topic_weight_increment
        self.decay_per_hour = decay_per_hour

        # 内部状态: {topic: {"count": int, "weight": float}}
        self._topics: dict[str, dict[str, float]] = {}
        self._last_decay: float = time.time()

    # ── 记录 ──────────────────────────────────────────────────

    def record_topic(self, topic: str) -> None:
        """记录一次话题出现，递增计数和权重。

        Args:
            topic: 话题名称或关键词
        """
        topic = topic.strip().lower()
        if not topic:
            return

        if topic not in self._topics:
            self._topics[topic] = {"count": 0.0, "weight": 0.0}

        entry = self._topics[topic]
        entry["count"] += 1.0
        entry["weight"] += self.topic_weight_increment
        # 权重上限 1.0
        entry["weight"] = min(1.0, entry["weight"])

    # ── 查询 ──────────────────────────────────────────────────

    def get_interest_weight(self, topic: str) -> float:
        """获取指定话题的当前权重。

        Args:
            topic: 话题名称

        Returns:
            当前权重（0.0 ~ 1.0），未记录过返回 0.0
        """
        topic = topic.strip().lower()
        entry = self._topics.get(topic)
        return float(entry["weight"]) if entry else 0.0

    def get_mention_count(self, topic: str) -> int:
        """获取指定话题的提及次数。

        Args:
            topic: 话题名称

        Returns:
            提及次数
        """
        topic = topic.strip().lower()
        entry = self._topics.get(topic)
        return int(entry["count"]) if entry else 0

    def is_triggered(self, topic: str) -> bool:
        """检查话题是否达到触发阈值。

        Args:
            topic: 话题名称

        Returns:
            提及次数 >= threshold
        """
        return self.get_mention_count(topic) >= self.topic_trigger_threshold

    def get_top_interests(self, n: int = 5) -> list[tuple[str, float]]:
        """获取权重最高的 N 个话题。

        Args:
            n: 返回数量

        Returns:
            [(topic, weight), ...] 按权重降序排列
        """
        sorted_topics = sorted(
            self._topics.items(),
            key=lambda item: item[1]["weight"],
            reverse=True,
        )
        return [(topic, float(entry["weight"])) for topic, entry in sorted_topics[:n]]

    # ── 衰减 ──────────────────────────────────────────────────

    def decay_all(self) -> None:
        """对所有话题施加按小时衰减。

        每经过一小时（从上次衰减算起），每个话题的权重减去 decay_per_hour。
        count 不受衰减影响（保留历史计数）。
        """
        now = time.time()
        elapsed_hours = (now - self._last_decay) / 3600.0
        if elapsed_hours <= 0:
            return

        self._last_decay = now
        decay_amount = self.decay_per_hour * elapsed_hours

        # 移除权重降至 0 以下的话题
        expired: list[str] = []
        for topic, entry in self._topics.items():
            entry["weight"] = max(0.0, entry["weight"] - decay_amount)
            if entry["weight"] <= 0.0:
                expired.append(topic)

        for topic in expired:
            del self._topics[topic]

    # ── 辅助 ──────────────────────────────────────────────────

    @property
    def topic_count(self) -> int:
        """当前追踪的话题总数"""
        return len(self._topics)

    @property
    def all_topics(self) -> dict[str, dict[str, float]]:
        """返回所有话题及其计数和权重（副本）"""
        return {
            topic: dict(entry)
            for topic, entry in self._topics.items()
        }

    def __repr__(self) -> str:
        topics = self.get_top_interests(5)
        parts = [f"{t}={w:.3f}" for t, w in topics]
        return f"InterestModel(topics=[{', '.join(parts)}])" if parts else "InterestModel(empty)"
