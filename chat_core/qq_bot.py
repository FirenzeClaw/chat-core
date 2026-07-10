"""QQ Bot 入口 — chat-core 的 QQ 机器人运行模式

启动方式:
    python -m chat_core.qq_bot

全局架构:
    - 全局双主脑 (LogicBrain + EmotionBrain) — AI 的核心自我
    - 全局 EmotionEngine — 竞态驱动烦躁
    - 每对话者子 Session (ReActLoop) — AI 的"注意力线程"
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

from chat_core.config import get_config
from chat_core.core.brain import ActionBrainPool, EmotionBrain, LogicBrain
from chat_core.core.provider import ModelProvider
from chat_core.core.prompt_engine import PromptEngine
from chat_core.systems.memory import MemoryStore
from chat_core.systems.emotion import EmotionEngine
from chat_core.systems.personality import PersonalityEngine

logger = logging.getLogger("chat_core.qq_bot")


def _setup_logging(level: str = "INFO") -> None:
    from pathlib import Path

    log_dir = Path("data")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "qq_bot.log"

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(str(log_file), encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


async def _run_health_server(port: int) -> None:
    from aiohttp import web

    async def health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "service": "chat-core-qq-bot"})

    app = web.Application()
    app.router.add_get("/v1/health", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("健康检查: http://0.0.0.0:%d/health", port)


async def run_bot(config_path: Path | None = None) -> None:
    config = get_config(config_path)
    qq_cfg = config.qq_config()

    appid = qq_cfg.get("appid", "")
    secret = qq_cfg.get("secret", "")
    if not appid or not secret:
        logger.error("QQ Bot AppID/Secret 未配置")
        return

    api_cfg = config.brain_api_config("sub_session")
    if not api_cfg.get("api_key", ""):
        logger.error("LLM API Key 未配置")
        return

    logger.info("=" * 50)
    logger.info("chat-core QQ Bot 启动中...")
    logger.info("  AppID: %s...", appid[:8])
    logger.info("  LLM:   %s", api_cfg.get("model", "unknown"))
    logger.info("=" * 50)

    # ── 共享组件 ──────────────────────────────────────────
    provider = ModelProvider(api_cfg)

    memory_store = MemoryStore(config.memory_config().get("db_path", "./data/memory.db"))
    await memory_store.open()
    logger.info("记忆系统就绪")

    prompt_engine = PromptEngine(config.prompts)

    personality_engine = PersonalityEngine()
    logger.info("人格引擎: curiosity=%.1f sociability=%.1f",
                 personality_engine.weights.curiosity, personality_engine.weights.sociability)

    # ── 全局双主脑 ────────────────────────────────────────
    logic_brain = LogicBrain(provider, memory_store, prompt_engine)
    emotion_brain = EmotionBrain(provider, memory_store, prompt_engine)
    logger.info("全局双主脑就绪")

    # ── 全局情绪引擎 ──────────────────────────────────────
    emotion_engine = EmotionEngine()
    await emotion_engine.start()
    logger.info("EmotionEngine 已启动")

    # ── 竞态追踪 ──────────────────────────────────────────
    from chat_core.qq.race_tracker import RaceTracker
    from chat_core.qq.subconscious import SubconsciousInjector

    race_tracker = RaceTracker()
    race_tracker.attach_emotion(emotion_engine)
    subconscious = SubconsciousInjector()
    logger.info("RaceTracker + SubconsciousInjector 已初始化")

    # ── jieba ─────────────────────────────────────────────
    try:
        import jieba
        jieba.cut("预加载")
        logger.info("jieba 已预加载")
    except ImportError:
        pass

    # ── BotAdapter ────────────────────────────────────────
    from chat_core.qq.adapter import BotAdapter
    from chat_core.qq.protocol import run_qq_loop, send_message

    adapter = BotAdapter(
        provider=provider,
        memory_store=memory_store,
        prompt_engine=prompt_engine,
        personality_engine=personality_engine,
        emotion_engine=emotion_engine,
        logic_brain=logic_brain,
        emotion_brain=emotion_brain,
        race_tracker=race_tracker,
        subconscious_injector=subconscious,
        qq_appid=appid,
        qq_secret=secret,
    )
    logger.info("BotAdapter 就绪 (全局双主脑 + 多子 Session + ProactiveSystem)")

    # ── QQ 消息回调 ───────────────────────────────────────
    async def on_qq_message(ctx):
        try:
            async def _send(text: str):
                await send_message(ctx, text, appid=appid, secret=secret)

            await adapter.process_message(ctx, send_fn=_send)
        except Exception:
            logger.exception("消息处理异常: user=%s", ctx.user_id[:12])

    # ── 健康检查 ──────────────────────────────────────────
    health_port = int(qq_cfg.get("health_port", 18090))
    health_task = asyncio.create_task(_run_health_server(health_port))

    # ── 定期清理过期会话 ──────────────────────────────────
    async def _periodic_cleanup():
        while True:
            await asyncio.sleep(300)
            cleaned = adapter.cleanup_expired()
            if cleaned:
                logger.info("清理过期会话: %d", cleaned)

    cleanup_task = asyncio.create_task(_periodic_cleanup())

    # ── 信号处理 ──────────────────────────────────────────
    shutdown_event = asyncio.Event()

    def _signal_handler(signum: int, frame: object) -> None:
        logger.info("收到信号 %d，关闭...", signum)
        shutdown_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # ── QQ WebSocket ──────────────────────────────────────
    ws_url = qq_cfg.get("ws_url", "wss://api.sgroup.qq.com/websocket")
    ws_task = asyncio.create_task(_run_qq_with_shutdown(
        on_qq_message, appid, secret, ws_url, shutdown_event,
    ))

    await shutdown_event.wait()

    # ── 关闭 ──────────────────────────────────────────────
    logger.info("正在关闭...")
    ws_task.cancel()
    cleanup_task.cancel()
    health_task.cancel()

    try:
        await emotion_engine.stop()
    except Exception:
        pass
    try:
        await memory_store.close()
    except Exception:
        pass

    status = adapter.status()
    logger.info("已关闭 — sessions=%d subs=%d race=%s",
                 status["active_sessions"], status["active_sub_sessions"], status["race_severity"])


async def _run_qq_with_shutdown(on_message, appid, secret, ws_url, shutdown_event):
    from chat_core.qq.protocol import run_qq_loop as _run_loop

    loop_task = asyncio.create_task(_run_loop(on_message, appid=appid, secret=secret, ws_url=ws_url))
    done, _ = await asyncio.wait(
        [loop_task, asyncio.create_task(shutdown_event.wait())],
        return_when=asyncio.FIRST_COMPLETED,
    )
    if not loop_task.done():
        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="chat-core QQ Bot")
    parser.add_argument("-c", "--config", type=Path, help="配置文件路径")
    parser.add_argument("--log-level", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    _setup_logging(args.log_level)

    try:
        asyncio.run(run_bot(args.config))
    except KeyboardInterrupt:
        logger.info("已退出")


if __name__ == "__main__":
    main()
