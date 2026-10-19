"""Everything the bot owner does in their private chat with the bot."""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from . import broadcast, browse, cron, db, status
from .keyboards import (admin_keyboard, browse_source_keyboard, cancel_keyboard, hidden_keyboard)

log = logging.getLogger("admin")
_tasks: set = set()


def spawn(coro) -> None:
    """Run long jobs (cron, downloads, broadcast) in the background so polling is never blocked."""
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(lambda x: (_tasks.discard(x), x.exception() and log.error("task failed: %r", x.exception())))


async def handle_message(msg: Message, bot: Bot, cfg: dict, bot_id: str) -> None:
    text = msg.text or ""
    chat_id = msg.chat.id
    state = await db.get_admin_state(bot_id)
    tracking = db.get_tracking(cfg)
    mode = tracking["mode"]

    async def say(t: str, kb=None):
        await bot.send_message(chat_id, t, reply_markup=kb)

    if text == "❌ Cancel":
        await db.set_admin_state(bot_id, "")
        return await say("❌ Cancelled.", admin_keyboard(mode))
    if text == "⌨️ Hide Keyboard":
        return await say("⌨️ Hidden. Send /start to bring it back.", hidden_keyboard())
    if text == "/start" or text.startswith("/start "):
        await db.set_admin_state(bot_id, "")
        return await say("👋 <b>Welcome Admin.</b>", admin_keyboard(mode))
    if "Mode: " in text:
        new = "OFF" if mode == "ON" else "ON"
        await db.set_mode(bot_id, new)
        return await say(f"✅ Mode → <b>{new}</b>", admin_keyboard(new))
    if text == "📥 Browse Videos":
        await db.set_admin_state(bot_id, "")
        return await say("📡 <b>Select API to browse</b>", browse_source_keyboard())
    if state.startswith("waiting_page_") and text.strip().isdigit():
        src = state[len("waiting_page_"):]
        await db.set_admin_state(bot_id, "")
        return await browse.page(bot, bot_id, chat_id, int(text), src, cfg)
    if text == "📢 Broadcast":
        await db.set_admin_state(bot_id, "waiting_broadcast")
        return await say("📝 Send the message to broadcast.", cancel_keyboard())
    # Menu buttons win over a pending broadcast (otherwise tapping "📊 Status" would broadcast it).
    if text == "📊 Status":
        if state == "waiting_broadcast":
            await db.set_admin_state(bot_id, "")
        return await status.send(bot, bot_id, chat_id, cfg)
    if text == "▶️ Run Cron":
        if state == "waiting_broadcast":
            await db.set_admin_state(bot_id, "")
        return await cron.run(bot, bot_id, chat_id, None)
    if state == "waiting_broadcast":
        await db.set_admin_state(bot_id, "")
        spawn(broadcast.run(bot, bot_id, chat_id, msg.message_id, cfg.get("admin_id"), mode))
        return
    await say("👋 <b>Welcome Admin.</b>", admin_keyboard(mode))


async def handle_callback(cq: CallbackQuery, bot: Bot, cfg: dict, bot_id: str) -> None:
    data = cq.data or ""
    await cq.answer()
    if not cq.message:
        return
    chat_id = cq.message.chat.id
    mid = cq.message.message_id

    if data in ("cron_source_old", "cron_source_vid65", "cron_source_both"):
        source = data.replace("cron_source_", "")
        try:
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=mid,
                                                reply_markup=InlineKeyboardMarkup(inline_keyboard=[]))
        except Exception:
            pass
        spawn(cron.run(bot, bot_id, chat_id, source))
    elif data.startswith("browse_src_"):
        src = data.replace("browse_src_", "")
        await db.set_admin_state(bot_id, "waiting_page_" + src)
        try:
            await bot.delete_message(chat_id, mid)
        except Exception:
            pass
        await bot.send_message(chat_id, f"🔢 Send a <b>page number</b> for {src.upper()}.", reply_markup=cancel_keyboard())
    elif data.startswith("abrowse_"):
        await browse.render(bot, chat_id, data, mid, cfg)
    elif data.startswith("aget_"):
        parts = data.split("_")
        if len(parts) >= 4:
            spawn(browse.download_by_index(bot, bot_id, chat_id, parts[1], parts[2], parts[3], cfg))
