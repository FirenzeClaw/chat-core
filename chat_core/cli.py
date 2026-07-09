"""CLI 入口 — prompt_toolkit 交互式聊天 REPL (Phase 8 enhanced)"""

from __future__ import annotations

import asyncio
import signal
import sys
import time
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.status import Status
from rich.text import Text

from chat_core.config import get_config, Config
from chat_core.core.history import HistoryManager
from chat_core.core.loop import ReActLoop, SubSessionConfig, register_sub_session_tools
from chat_core.core.provider import ModelProvider
from chat_core.core.prompt_engine import PromptEngine
from chat_core.core.tools import ToolRegistry
from chat_core.core.types import StreamEventType
from chat_core.systems.emotion import EmotionEngine
from chat_core.systems.personality import PersonalityEngine
from chat_core.systems.attention import AttentionModel
from chat_core.systems.interest import InterestModel
from chat_core.systems.review import extract_intent
from chat_core.systems.memory import MemoryStore


# ── rich console ──────────────────────────────────────────

console = Console()

STYLE = Style.from_dict({
    "prompt": "#00ff87 bold",
    "user": "#5f87ff",
    "ai": "#ff5f87",
    "system": "#888888 italic",
})

# ── 全局状态（用于 Ctrl+C 时保存） ────────────────────────

_emotion_engine: EmotionEngine | None = None
_memory_store: Any = None
_personality_engine: PersonalityEngine | None = None
_history_manager: HistoryManager | None = None


# ── 情绪状态栏 (T064) ──────────────────────────────────────

def _get_dominant_emotion(emotion_engine: EmotionEngine) -> str:
    """获取 sub brain 的主导情绪"""
    try:
        state = emotion_engine.get_state("sub")
        dims = {
            "喜悦": state.joy,
            "悲伤": state.sadness,
            "惊讶": state.surprise,
            "困惑": state.confusion,
            "恐惧": state.fear,
            "愤怒": state.anger,
            "厌恶": state.disgust,
            "兴趣": state.interest,
            "期待": state.anticipation,
            "信任": state.trust,
        }
        dominant = max(dims, key=dims.get)  # type: ignore[arg-type]
        value = dims[dominant]
        if value < 0.1:
            return "平静"
        return f"{dominant}({value:.2f})"
    except Exception:
        return "未知"


def _show_emotion_status(emotion_engine: EmotionEngine) -> None:
    """在底部显示情绪状态栏"""
    dominant = _get_dominant_emotion(emotion_engine)
    status_text = Text()
    status_text.append("┌─ ", style="dim")
    status_text.append("情绪状态", style="bold bright_green")
    status_text.append(" ──────────────────────────────┐", style="dim")
    status_text.append(f"\n│ 主导情绪: {dominant:<30} │", style="")
    status_text.append("\n└──────────────────────────────────────────┘", style="dim")
    console.print(status_text)


# ── Ctrl+C 处理器 (T067 enhanced) ──────────────────────────

class InterruptHandler:
    """两级中断：第一次请求停止，第二次保存状态后强制退出"""

    def __init__(self):
        self._last_sigint = 0.0
        self._loop: ReActLoop | None = None

    def set_loop(self, loop: ReActLoop | None) -> None:
        self._loop = loop

    def handle(self, signum: int, frame: object) -> None:
        now = time.time()
        if now - self._last_sigint < 2.0:
            # 第二次 Ctrl+C：保存状态后强制退出 (T067)
            console.print("\n[bold red]强制退出 — 正在保存状态...[/]")
            _save_state_on_exit()
            sys.exit(0)
        self._last_sigint = now
        if self._loop:
            self._loop.cancel()
            console.print("\n[yellow]⏸ 已请求停止... (再按一次 Ctrl+C 强制退出并保存状态)[/]")
        else:
            console.print("\n[yellow]⏸ 正在处理...[/]")


_interrupt = InterruptHandler()


def _save_state_on_exit() -> None:
    """退出时保存情绪和记忆状态 (T067)"""
    global _emotion_engine, _history_manager
    try:
        if _emotion_engine:
            # 通过 event loop 调度异步 stop（signal handler 中不能 await）
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.call_soon_threadsafe(
                        lambda: asyncio.ensure_future(_emotion_engine.stop())
                    )
            except RuntimeError:
                pass
            state = _emotion_engine.get_all_states()
            console.print("[dim]情绪引擎已停止，状态已保存[/]")
    except Exception:
        console.print("[dim]情绪状态保存失败[/]")


async def _graceful_exit(
    emotion_engine: EmotionEngine | None,
    memory_store: Any = None,
) -> None:
    """优雅退出：等待当前 turn 完成 → 保存状态 → 退出 (T067)"""
    if emotion_engine:
        try:
            await emotion_engine.stop()
        except Exception:
            pass
    if memory_store:
        try:
            await memory_store.close()
        except Exception:
            pass
    console.print("[yellow]再见！[/]")


# ── 打字动画 (T064) ────────────────────────────────────────

async def _on_wait_start(seconds: float) -> None:
    """打字动画开始 — 显示 '...' 动画"""
    global _typing_status
    _typing_status = Status(
        f"[dim bright_magenta]小深正在输入{'...' * min(3, max(1, int(seconds * 2)))}[/]",
        spinner="dots",
    )
    _typing_status.start()


async def _on_wait_end() -> None:
    """打字动画结束"""
    global _typing_status
    if _typing_status:
        _typing_status.stop()
        _typing_status = None


_typing_status: Status | None = None


# ── 回复回调 ──────────────────────────────────────────────

async def _on_reply(text: str) -> None:
    """send_reply 回调 — 用 rich 渲染 AI 回复"""
    panel = Panel(
        Markdown(text),
        border_style="bright_magenta",
        title="小深",
        title_align="left",
    )
    console.print(panel)


# ── 消息处理 ──────────────────────────────────────────────

def _record_topics(text: str, interest_model: InterestModel) -> None:
    """从文本中提取关键词话题并记录到 InterestModel。"""
    import re
    cleaned = re.sub(r'[^\u4e00-\u9fff]', ' ', text)
    tokens = cleaned.split()
    seen: set[str] = set()
    for token in tokens:
        if 2 <= len(token) <= 4 and token not in seen:
            seen.add(token)
            interest_model.record_topic(token)


async def _process_message(
    message: str,
    loop: ReActLoop,
    config: Config,
    interest_model: InterestModel | None = None,
) -> None:
    """处理单条用户消息"""
    _interrupt.set_loop(loop)

    try:
        await loop.run(message)
    except Exception as e:
        console.print(f"[red]错误: {e}[/]")
    finally:
        _interrupt.set_loop(None)

    # 兜底：显示未被 callback 覆盖的回复
    if loop.replies:
        for r in loop.replies:
            pass  # 回复已通过 _on_reply callback 显示，无需重复
    if not loop.replies and not loop.inner_thoughts:
        console.print("[dim](无回复)[/]")

    # 显示内心戏 + Phase 7 意图提取（debug 模式）
    if loop.inner_thoughts:
        inner_text = loop.inner_thoughts
        panel_content = Text(inner_text, style="italic")

        # Phase 7: 提取话题 → InterestModel，提取意图
        if interest_model and inner_text:
            _record_topics(inner_text, interest_model)
            intent = extract_intent(inner_text)
            if intent.action.value != "none":
                panel_content.append("\n\n")
                panel_content.append(
                    f"[意图] {intent.action.value}: {intent.detail[:100]}",
                    style="bold yellow",
                )

        console.print(
            Panel(
                panel_content,
                border_style="dim",
                title="[dim]内心戏 (仅 debug 可见)[/]",
                title_align="left",
            )
        )


# ── 主聊天循环 ────────────────────────────────────────────

async def _chat_loop(config_path: Path | None = None) -> None:
    """主聊天循环"""
    global _emotion_engine, _personality_engine, _history_manager

    config = get_config(config_path)

    # 检查 API key
    api_cfg = config.brain_api_config("sub_session")
    if not api_cfg.get("api_key", ""):
        console.print(
            "[bold red]❌ 未设置 API Key[/]\n"
            "[dim]请设置环境变量后重试:[/]\n"
            "  CMD:   set DEEPSEEK_API_KEY=sk-xxx && chat-core\n"
            "  PS:    $env:DEEPSEEK_API_KEY='sk-xxx'; chat-core\n"
            "[dim]或将 key 写入 chat_core/.env 文件: DEEPSEEK_API_KEY=sk-xxx[/]"
        )
        return

    # 初始化组件
    provider = ModelProvider(api_cfg)
    prompt_engine = PromptEngine(config.prompts)

    # Phase 6: 初始化情绪/人格/注意力引擎
    emotion_engine = EmotionEngine()
    personality_engine = PersonalityEngine()
    attention_model = AttentionModel()
    _emotion_engine = emotion_engine
    _personality_engine = personality_engine

    # 初始化记忆系统
    memory_config = config.memory_config()
    memory_store = MemoryStore(memory_config.get("db_path", "./data/memory.db"))
    await memory_store.open()
    _memory_store = memory_store

    # Phase 7: 初始化兴趣模型
    interest_model = InterestModel()

    # T065: 历史管理器
    history_manager = HistoryManager()
    _history_manager = history_manager

    # 启动情绪引擎后台 tick
    await emotion_engine.start()

    system_prompt = prompt_engine.build_sub_session_prompt()

    # 历史记录目录（用于 prompt_toolkit FileHistory）
    hist_dir = Path(config.history.get("path", "./data/history/"))
    hist_dir.mkdir(parents=True, exist_ok=True)
    pt_history_file = hist_dir / ".prompt_toolkit_history"

    # prompt_toolkit session
    session = PromptSession(
        history=FileHistory(str(pt_history_file)),
        style=STYLE,
    )

    # 快捷键
    kb = KeyBindings()

    @kb.add("c-c")
    def _(event: object) -> None:
        _interrupt.handle(signal.SIGINT, None)

    console.print(
        Panel(
            "[bold bright_green]chat-core[/] — 四脑模型 AI 伙伴\n"
            "输入消息开始对话 | [bold]/quit[/] 退出 | [bold]/help[/] 查看帮助",
            border_style="green",
        )
    )

    # 事件循环
    while True:
        try:
            user_input = await session.prompt_async(
                [("class:prompt", "你: ")],
                key_bindings=kb,
            )
        except (EOFError, KeyboardInterrupt):
            await _graceful_exit(emotion_engine, _memory_store)
            break

        user_input = user_input.strip()
        if not user_input:
            continue

        # 斜杠命令
        if user_input.startswith("/"):
            cmd = user_input.lower()
            if cmd in ("/quit", "/exit"):
                # T067: 等待当前 turn → 保存状态 → 退出
                await _graceful_exit(emotion_engine, _memory_store)
                break
            elif cmd == "/help":
                console.print(
                    "[bold]可用命令:[/]\n"
                    "  /quit, /exit  — 退出\n"
                    "  /help          — 帮助\n"
                    "  /mood          — 查看 AI 内部状态 (情绪·人格·注意力)\n"
                    "  /interests     — 查看 AI 当前关注的话题\n"
                    "  /memories [q]  — 搜索记忆 (Phase 4)\n"
                )
            elif cmd.startswith("/mood"):
                _show_mood(emotion_engine, personality_engine, attention_model)
            elif cmd.startswith("/interests"):
                _show_interests(interest_model)
            elif cmd.startswith("/memories"):
                query = user_input[len("/memories"):].strip()
                if memory_store:
                    entries = await memory_store.search(query, top_n=10) if query else await memory_store.query("user/default", limit=10)
                    if entries:
                        mem_table = Table(title=f"记忆检索: {query or '最近'}", show_header=True, header_style="bold")
                        mem_table.add_column("命名空间", style="dim")
                        mem_table.add_column("内容", style="")
                        for e in entries:
                            mem_table.add_row(
                                f"{e.namespace}/{e.key}",
                                str(e.value)[:200],
                            )
                        console.print(mem_table)
                    else:
                        console.print(f"[dim]未找到 '{query}' 的相关记忆[/]")
                else:
                    console.print("[dim]记忆系统未初始化[/]")
            else:
                console.print(f"[dim]未知命令: {user_input}[/]")
            continue

        # 创建新的 ReActLoop
        tools = ToolRegistry()
        personality_temp = personality_engine.get_llm_temperature("sub_session")
        sub_config = SubSessionConfig(
            max_iter=config.brain_max_iter("sub_session"),
            temperature=personality_temp,
        )

        loop = ReActLoop(
            provider=provider,
            tool_registry=tools,
            system_prompt=system_prompt,
            config=sub_config,
            attention_model=attention_model,
        )
        register_sub_session_tools(tools, loop, memory_store)
        loop.set_reply_callback(_on_reply)
        # T064: 设置打字动画回调
        loop.set_wait_callbacks(_on_wait_start, _on_wait_end)

        # 显示用户消息
        console.print(Panel(
            user_input,
            border_style="bright_blue",
            title="你",
            title_align="left",
        ))

        # T065: 记录用户消息
        history_manager.append(role="user", content=user_input)

        await _process_message(user_input, loop, config, interest_model)

        # T065: 记录 AI 回复
        if loop.replies:
            full_reply = "\n".join(loop.replies)
            brain_metadata = {
                "speaker": "sub_session",
                "inner_thoughts": loop.inner_thoughts,
            }
            history_manager.append(
                role="assistant",
                content=full_reply,
                brain_metadata=brain_metadata,
            )

        # T064: 显示情绪状态栏
        _show_emotion_status(emotion_engine)

    # 清理：停止情绪引擎后台 tick
    await emotion_engine.stop()


# ── /mood 命令显示 ────────────────────────────────────────

def _show_mood(
    emotion_engine: EmotionEngine,
    personality_engine: PersonalityEngine,
    attention_model: AttentionModel,
) -> None:
    """显示 /mood 命令输出：情绪向量、人格权重、注意力水平"""
    from rich.table import Table

    console.print(Panel("[bold]AI 内部状态[/]", border_style="bright_green"))

    # 1. 情绪向量
    mood_table = Table(title="情绪向量 (per brain)", show_header=True, header_style="bold")
    mood_table.add_column("维度", style="dim")
    for brain_name in ["logic", "emotion", "sub"]:
        mood_table.add_column(brain_name, justify="right")

    all_states = emotion_engine.get_all_states()
    emotion_dims = [
        "surprise", "confusion", "fear", "anger", "disgust",
        "joy", "sadness", "interest", "anticipation", "trust",
    ]
    for dim in emotion_dims:
        row = [dim]
        for brain_name in ["logic", "emotion", "sub"]:
            val = getattr(all_states[brain_name], dim, 0.0)
            # 颜色编码
            color = "green" if val > 0.7 else "yellow" if val > 0.3 else "dim"
            row.append(f"[{color}]{val:.3f}[/]")
        mood_table.add_row(*row)

    console.print(mood_table)
    console.print()

    # 2. 人格权重
    pw_table = Table(title="人格权重", show_header=True, header_style="bold")
    pw_table.add_column("权重", style="dim")
    pw_table.add_column("值", justify="right")
    pw_table.add_column("条形", justify="left")

    pw = personality_engine.summary()
    for name, value in pw.items():
        bar_len = int(value * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        color = "green" if value > 0.7 else "yellow" if value > 0.3 else "red"
        pw_table.add_row(name, f"[{color}]{value:.2f}[/]", f"[{color}]{bar}[/]")

    console.print(pw_table)
    console.print()

    # 3. 注意力水平
    attn_table = Table(title="注意力水平", show_header=True, header_style="bold")
    attn_table.add_column("脑", style="dim")
    attn_table.add_column("Focus", justify="right")
    attn_table.add_column("Dominance", justify="right")

    attn_states = attention_model.get_all_states()
    for brain_name in ["logic", "emotion", "sub"]:
        state = attn_states[brain_name]
        f_color = "green" if state.focus > 0.7 else "yellow" if state.focus > 0.3 else "red"
        d_color = "green" if state.dominance > 0.7 else "yellow" if state.dominance > 0.3 else "red"
        attn_table.add_row(
            brain_name,
            f"[{f_color}]{state.focus:.3f}[/]",
            f"[{d_color}]{state.dominance:.3f}[/]",
        )

    console.print(attn_table)


def _show_interests(interest_model: InterestModel) -> None:
    """显示 /interests 命令输出：当前关注的话题"""
    from rich.table import Table

    console.print(Panel("[bold]兴趣话题追踪[/]", border_style="bright_green"))

    top = interest_model.get_top_interests(10)
    if not top:
        console.print("[dim]暂无追踪的话题[/]")
        return

    table = Table(title="话题权重", show_header=True, header_style="bold")
    table.add_column("话题", style="dim")
    table.add_column("提及次数", justify="right")
    table.add_column("权重", justify="right")
    table.add_column("条形", justify="left")

    for topic, weight in top:
        count = interest_model.get_mention_count(topic)
        bar_len = int(weight * 20)
        bar = "█" * bar_len + "░" * (20 - bar_len)
        color = "green" if weight > 0.5 else "yellow" if weight > 0.2 else "dim"
        table.add_row(
            topic,
            str(count),
            f"[{color}]{weight:.3f}[/]",
            f"[{color}]{bar}[/]",
        )

    console.print(table)
    console.print(f"[dim]共追踪 {interest_model.topic_count} 个话题[/]")


# ── 入口 ────────────────────────────────────────────────────

def main() -> None:
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="chat-core — 四脑模型 AI 聊天 CLI")
    parser.add_argument("-c", "--config", type=Path, help="配置文件路径")
    parser.add_argument("--no-color", action="store_true", help="禁用彩色输出")
    args = parser.parse_args()

    if args.no_color:
        console.no_color = True

    signal.signal(signal.SIGINT, _interrupt.handle)

    try:
        asyncio.run(_chat_loop(args.config))
    except KeyboardInterrupt:
        console.print("\n[yellow]再见！[/]")


if __name__ == "__main__":
    main()
