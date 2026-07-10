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
