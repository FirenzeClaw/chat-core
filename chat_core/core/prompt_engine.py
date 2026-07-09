"""Prompt 编译引擎 — 三层编译：persona + rules + tools → system prompt"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class PromptEngine:
    """编译 persona/rules/tools 三份 YAML 为每脑独立的 system prompt"""

    def __init__(self, prompts_config: dict[str, str] | None = None):
        """
        Args:
            prompts_config: {"persona": "path", "rules": "path", "tools": "path"}
        """
        self._cfg = prompts_config or {}
        self._persona: dict[str, Any] = {}
        self._rules: dict[str, Any] = {}
        self._tools: dict[str, Any] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        base = Path(__file__).parent.parent  # chat_core/
        self._persona = self._load_yaml(base / self._cfg.get("persona", "prompts/persona.yaml"))
        self._rules = self._load_yaml(base / self._cfg.get("rules", "prompts/rules.yaml"))
        self._tools = self._load_yaml(base / self._cfg.get("tools", "prompts/tools.yaml"))
        self._loaded = True

    @staticmethod
    def _load_yaml(path: Path | str) -> dict[str, Any]:
        p = Path(path)
        if not p.exists():
            return {}
        with open(p, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # ── 子Session system prompt ─────────────────────────────

    def build_sub_session_prompt(self, runtime_state: dict[str, Any] | None = None) -> str:
        """构建子Session 的完整 system prompt，注入运行时状态"""
        self._ensure_loaded()
        parts: list[str] = []

        # ⛔ 强制工具使用声明 — 必须放在最前面
        parts.append(
            "⛔ 【强制规则】你只能通过调用工具来回复用户。send_reply 是你唯一的嘴。"
            "绝不要直接输出文本——如果要说任何话，必须调用 send_reply(text=你的话)。"
        )

        # 工作流引导
        parts.append(
            "【工作流】\n"
            "1. 先 recall 查记忆（如果有需要确认的事实）\n"
            "2. send_reply 说一段话（2-4句）→ 可选 wait → send_reply 接着说 → 直到说完\n"
            "3. 全部说完后：调用 inner_thoughts 写内心戏 → 自动结束（调用 inner_thoughts 就是结束信号，无需再调 done）"
        )

        # 身份层
        identity = self._persona.get("identity", {})
        parts.append(
            f"你是{identity.get('name', '小深')}。{identity.get('archetype', '一个有趣的聊天伙伴')}。"
            f"说话风格：{identity.get('tone', '真诚自然')}。"
        )

        # 风格指南
        style = self._persona.get("style_guide", {})
        if style.get("do"):
            parts.append(f"你应该：{'；'.join(style['do'])}")
        if style.get("avoid"):
            parts.append(f"避免：{'；'.join(style['avoid'])}")

        # 行为规范
        speech = self._rules.get("speech_protocol", {})
        parts.append(
            f"【发言协议】{speech.get('rule', '')}"
            f"步骤1: {speech.get('step1', '')}"
            f"步骤2: {speech.get('step2', '')}"
        )
        for c in speech.get("constraints", []):
            parts.append(f"- {c}")

        reply = self._rules.get("reply_rules", [])
        if reply:
            parts.append("【回复规范】" + "；".join(reply))

        inner = self._rules.get("inner_thoughts_rules", [])
        if inner:
            parts.append("【内心戏规范】" + "；".join(inner))

        tool_rules = self._rules.get("tool_rules", [])
        if tool_rules:
            parts.append("【工具规范】" + "；".join(tool_rules))

        safety = self._rules.get("safety", [])
        if safety:
            parts.append("【安全】" + "；".join(safety))

        # 工具说明
        tg = self._tools.get("tool_usage_guidelines", {})
        for tool_name, info in tg.items():
            if isinstance(info, dict) and tool_name != "general":
                tips = info.get("tips", [])
                parts.append(f"[{tool_name}] {info.get('description', '')} — {'；'.join(tips)}")

        # 运行时状态注入
        if runtime_state:
            if runtime_state.get("emotion"):
                parts.append(f"【当前情绪】{runtime_state['emotion']}")
            if runtime_state.get("attention"):
                parts.append(f"【注意力】focus={runtime_state['attention'].get('focus', 0.9):.2f}")
            if runtime_state.get("short_term"):
                parts.append(f"【近期动态】{runtime_state['short_term']}")
            if runtime_state.get("corrections"):
                parts.append(f"【注意】{runtime_state['corrections']}")

        return "\n\n".join(parts)

    # ── 主脑 system prompt ─────────────────────────────────

    def build_logic_brain_prompt(self) -> str:
        """逻辑主脑的 system prompt — 无发言能力，只有审查和记忆"""
        self._ensure_loaded()
        identity = self._persona.get("identity", {})
        return (
            f"你是{identity.get('name', '小深')}的逻辑主脑。你的职责是：\n"
            f"1. 搜索记忆(recall)获取相关事实\n"
            f"2. 保存重要事实到记忆(memory_save)\n"
            f"3. 建立记忆之间的关联(memory_link)\n"
            f"4. 向发言脑注入上下文和方向指导(inject_to_sub)\n"
            f"5. 审查发言脑的输出是否正确\n\n"
            f"你不直接对用户说话。记住：子Session是唯一的嘴。"
        )

    def build_emotion_brain_prompt(self) -> str:
        """情感主脑的 system prompt — 情感标签 + 语气审查"""
        self._ensure_loaded()
        identity = self._persona.get("identity", {})
        return (
            f"你是{identity.get('name', '小深')}的情感主脑。你的职责是：\n"
            f"1. 搜索记忆(recall)获取情感关联\n"
            f"2. 给已有记忆追加情感标签(memory_tag)\n"
            f"3. 向发言脑注入情感方向指导(inject_to_sub)\n"
            f"4. 审查发言脑的语气是否恰当\n\n"
            f"你不直接对用户说话。记住：子Session是唯一的嘴。"
        )

    def build_action_brain_prompt(self, task: str) -> str:
        """行为脑的 system prompt — 执行单一搜索/抓取任务"""
        return (
            f"你是一个信息检索助手。任务: {task}\n"
            f"可用工具: search(搜索互联网), recall(搜索本地记忆), web_fetch(抓取网页)\n"
            f"完成后直接输出结果，不需要 done 或 inner_thoughts。"
        )
