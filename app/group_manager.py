"""Bot added to / removed from chats, and private-chat block/unblock tracking."""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import ChatMemberUpdated, ReplyKeyboardRemove

from . import db
from .keyboards import group_reply_markup, group_start_enabled
from .utils import chat_ref, esc

log = logging.getLogger("group_manager")
IN = {"member", "administrator", "creator", "restricted"}


async def send_group_start_button(bot: Bot, cfg: dict, chat_id) -> None:
    if not group_start_enabled(cfg):
        return
    await bot.send_message(
        chat_id, "👋 <b>Welcome!</b>\n\nPress <b>🚀 Start</b> below to register yourself with the bot.",
        reply_markup=group_reply_markup(cfg))


async def handle_my_chat_member(upd: ChatMemberUpdated, bot: Bot, cfg: dict, bot_id: str) -> None:
    chat = upd.chat
    chat_id = str(chat.id)
    title = chat.title or chat.username or chat_id
    ctype = chat.type
    admin_id = cfg.get("admin_id")

    if ctype == "private":
        st = upd.new_chat_member.status
        if st == "kicked":
            await db.mark_blocked(bot_id, chat.id)
        elif st == "member":
            await db.mark_active(bot_id, chat.id)
        return

    if ctype in ("group", "supergroup", "channel"):
        await db.upsert_chat(bot_id, chat_id, "channel" if ctype == "channel" else "group", str(title))

    if ctype not in ("group", "supergroup"):
        return

    was_in = upd.old_chat_member.status in IN
    is_in = upd.new_chat_member.status in IN
    actor = upd.from_user
    actor_name = f"{actor.first_name or ''} {actor.last_name or ''}".strip()
    actor_un = actor.username or ""

    if not was_in and is_in:
        await db.add_group(bot_id, chat_id)
        log.info("[%s] Auto-registered group %s (%s), added by %s", bot_id, chat_id, title, actor_name)
        await db.log_event(bot_id, f"Bot added to group '{title}' ({chat_id}) by {actor_name}")
        note = ("the <b>🚀 Start</b> registration button was posted." if group_start_enabled(cfg)
                else "the <b>🚀 Start</b> button is switched OFF in the admin panel, so it was not posted.")
        if admin_id:
            try:
                await bot.send_message(
                    admin_id,
                    f"✅ <b>Bot added to a new group!</b>\n\n👥 <b>{esc(title)}</b>\n🆔 <code>{chat_id}</code>\n"
                    f"👤 Added by: {esc(actor_name)}" + (f" (@{esc(actor_un)})" if actor_un else "") +
                    f"\n\nThe group was added to both API target lists and {note}")
            except Exception as e:
                log.warning("admin notify failed: %s", e)
        try:
            await send_group_start_button(bot, cfg, chat.id)
        except Exception as e:
            log.warning("start button post failed in %s: %s", chat_id, e)
    elif was_in and not is_in:
        await db.remove_group(bot_id, chat_id)
        log.info("[%s] Auto-removed group %s (%s)", bot_id, chat_id, title)
        await db.log_event(bot_id, f"Bot removed from group '{title}' ({chat_id})", "warn")
        if admin_id:
            try:
                await bot.send_message(
                    admin_id,
                    f"⚠️ <b>Bot removed from a group</b>\n\n👥 {esc(title)}\n🆔 <code>{chat_id}</code>\n\n"
                    "It has been automatically removed from both target lists.")
            except Exception as e:
                log.warning("admin notify failed: %s", e)


def groups_of(cfg: dict) -> list:
    seen: list = []
    for src in ("old", "vid65"):
        for g in (cfg.get("apis", {}).get(src, {}) or {}).get("groups", []) or []:
            if str(g) not in seen:
                seen.append(str(g))
    return seen


async def clear_group_keyboards(bot: Bot, groups: list) -> tuple:
    """Hide the 🚀 Start keyboard in every group (post a tiny message with remove_keyboard, then delete it)."""
    ok = fail = 0
    for g in groups:
        try:
            r = await bot.send_message(chat_ref(g), ".", reply_markup=ReplyKeyboardRemove())
            await bot.delete_message(chat_ref(g), r.message_id)
            ok += 1
        except Exception as e:
            log.warning("clear keyboard in %s failed: %s", g, e)
            fail += 1
        await asyncio.sleep(0.3)
    return ok, fail


async def post_group_start_buttons(bot: Bot, cfg: dict, groups: list) -> tuple:
    ok = fail = 0
    for g in groups:
        try:
            await send_group_start_button(bot, cfg, chat_ref(g))
            ok += 1
        except Exception as e:
            log.warning("post start button in %s failed: %s", g, e)
            fail += 1
        await asyncio.sleep(0.3)
    return ok, fail
