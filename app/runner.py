"""Runs one long-polling aiogram bot per enabled row of the `bots` table and keeps them in sync."""
import asyncio
import logging
import time
from typing import Awaitable, Callable, Dict, Optional

import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode

from . import config, db
from .handlers import build_router

log = logging.getLogger("runner")
ALLOWED = ["message", "callback_query", "chat_member", "my_chat_member"]


class CfgMiddleware(BaseMiddleware):
    """Loads the bot's CURRENT config from MySQL for every update, so panel edits apply instantly."""

    def __init__(self, bot_id: str):
        self.bot_id = bot_id

    async def __call__(self, handler, event, data):
        cfg = await db.find_bot(self.bot_id)
        if not cfg:
            return None
        data["cfg"], data["bot_id"] = cfg, self.bot_id
        return await handler(event, data)


async def pick_server(token: str, primary: str, fallback: str) -> str:
    """Use the primary Telegram API server if it answers, otherwise the fallback."""
    candidates = [s.strip().rstrip("/") for s in (primary, fallback, "https://api.telegram.org") if s and s.strip()]
    seen = []
    for c in candidates:
        if c in seen:
            continue
        seen.append(c)
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=12)) as s:
                async with s.get(f"{c}/bot{token}/getMe") as r:
                    data = await r.json(content_type=None)
                    # Strictly ensure it's a valid Telegram response before returning
                    if isinstance(data, dict) and data.get("ok") is True:
                        return c
        except Exception as e:
            log.warning("API server %s unreachable: %s", c, e)
            
    # CRITICAL FIX: Always fallback to official Telegram if custom servers fail, 
    # instead of returning seen[0] (which is the broken custom URL).
    log.warning("All custom API servers failed. Falling back to official Telegram API.")
    return "https://api.telegram.org"


class Runner:
    def __init__(self, cfg: dict):
        self.bot_id = cfg["id"]
        self.sig = self.signature(cfg)
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.task: Optional[asyncio.Task] = None
        self.username: Optional[str] = None
        self.started_at: Optional[float] = None
        self.last_error: str = ""

    @staticmethod
    def signature(cfg: dict) -> tuple:
        tg = cfg.get("telegram", {}) or {}
        return (cfg.get("token"), tg.get("api_server"), tg.get("api_server_fallback"))

    def start(self, cfg: dict) -> None:
        self.task = asyncio.create_task(self._run(cfg), name=f"bot-{self.bot_id}")

    async def _run(self, cfg: dict) -> None:
        tg = cfg.get("telegram", {}) or {}
        delay = 5
        while True:
            try:
                server = await pick_server(cfg["token"], tg.get("api_server", ""), tg.get("api_server_fallback", ""))
                session = AiohttpSession(api=TelegramAPIServer.from_base(server), timeout=1800)
                self.bot = Bot(cfg["token"], session=session, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                self.dp = Dispatcher()
                self.dp.update.outer_middleware(CfgMiddleware(self.bot_id))
                self.dp.include_router(build_router())
                me = await self.bot.get_me()
                log.info("[%s] started as @%s via %s", self.bot_id, me.username, server)
                self.username, self.started_at, self.last_error = me.username, time.time(), ""
                await self.bot.delete_webhook(drop_pending_updates=False)  # polling and webhooks are exclusive
                await self.dp.start_polling(self.bot, allowed_updates=ALLOWED, handle_signals=False,
                                            polling_timeout=30, close_bot_session=False)
                return  # stopped on purpose
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error, self.username = str(e)[:200], None
                log.exception("[%s] crashed - restarting in %ss", self.bot_id, delay)
                await self._close()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)

    async def _close(self) -> None:
        try:
            if self.bot:
                await self.bot.session.close()
        except Exception:
            pass
        self.bot = self.dp = None

    async def stop(self) -> None:
        try:
            if self.dp:
                await self.dp.stop_polling()
        except Exception:
            pass
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except BaseException:
                pass
        await self._close()
        log.info("[%s] stopped", self.bot_id)


class Supervisor:
    def __init__(self):
        self.runners: Dict[str, Runner] = {}
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()

    async def sync(self) -> None:
        bots = await db.list_bots()
        wanted = {b["id"]: b for b in bots if b.get("enabled") and b.get("token")}
        for bid in list(self.runners):
            r = self.runners[bid]
            cfg = wanted.get(bid)
            if cfg is None or r.sig != Runner.signature(cfg) or (r.task and r.task.done()):
                await r.stop()
                del self.runners[bid]
        for bid, cfg in wanted.items():
            if bid not in self.runners:
                r = Runner(cfg)
                self.runners[bid] = r
                r.start(cfg)

    async def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                await self.sync()
            except Exception:
                log.exception("supervisor sync failed")
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=config.SUPERVISOR_INTERVAL)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self._stop.is_set():
                break

    def poke(self) -> None:
        """Re-read the bots right now (called after the web panel saved something)."""
        self._wake.set()

    def get_bot(self, bot_id: str):
        r = self.runners.get(bot_id)
        return r.bot if r and r.bot and r.task and not r.task.done() else None

    def info(self, bot_id: str) -> dict:
        r = self.runners.get(bot_id)
        running = bool(r and r.bot and r.username)
        return {"running": running, "username": r.username if r else None,
                "started_at": r.started_at if r else None, "error": r.last_error if r else ""}

    async def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        for r in list(self.runners.values()):
            await r.stop()
        self.runners.clear()