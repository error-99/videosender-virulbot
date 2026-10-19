"""Regular-user side: private DM and group chat logic."""
import logging
import re

from aiogram import Bot
from aiogram.types import ChatMemberUpdated, Message, ReplyKeyboardRemove

from . import db
from .keyboards import START_TEXT, dm_start_keyboard, group_start_enabled
from .utils import esc, source_for_chat

log = logging.getLogger("user")
START_RE = re.compile(r"^/start(@\w+)?(\s.*)?$")


async def notify_admin_new_user(bot: Bot, cfg: dict, via: str, first_name, username, user_id, source=None) -> None:
    admin_id = cfg.get("admin_id")
    if not admin_id:
        return
    u = f"@{esc(username)}" if username and username != "None" else "—"
    t = f"🆕 <b>New User ({via})</b>\n\n👤 {esc(first_name)}\n🔗 {u}\n🆔 <code>{user_id}</code>"
    if source:
        t += f"\n📍 Group: <code>{esc(source)}</code>"
    try:
        await bot.send_message(admin_id, t)
    except Exception as e:
        log.warning("notify_admin_new_user failed: %s", e)


async def handle_private_message(msg: Message, bot: Bot, cfg: dict, bot_id: str) -> None:
    user = msg.from_user
    if not user:
        return
    text = (msg.text or "").strip()
    is_start = text == START_TEXT or bool(START_RE.match(text))

    is_new = await db.touch_user(bot_id, user.id, user.username, user.first_name, None)
    if is_new:
        await notify_admin_new_user(bot, cfg, "Private DM", user.first_name, user.username, user.id)

    if is_start:
        tpl = str((cfg.get("messages", {}) or {}).get("start") or "👋 Hello {first_name}!")
        await bot.send_message(msg.chat.id, tpl.replace("{first_name}", esc(user.first_name or "User")),
                               reply_markup=dm_start_keyboard())


async def _welcome_dm(bot: Bot, cfg: dict, user) -> None:
    tpl = str((cfg.get("messages", {}) or {}).get("welcome_dm") or "👋 Welcome {first_name}!")
    try:  # Telegram refuses DMs to users who never opened the bot - that's normal
        await bot.send_message(user.id, tpl.replace("{first_name}", esc(user.first_name or "User")),
                               reply_markup=dm_start_keyboard())
    except Exception as e:
        log.debug("welcome DM to %s not delivered: %s", user.id, e)


async def group_join_via_chat_member(upd: ChatMemberUpdated, bot: Bot, cfg: dict) -> None:
    new_s, old_s = upd.new_chat_member.status, upd.old_chat_member.status
    user = upd.new_chat_member.user
    if (user and not user.is_bot and new_s in ("member", "restricted")
            and old_s not in ("member", "restricted", "creator", "administrator")):
        await _welcome_dm(bot, cfg, user)


async def group_new_chat_members(msg: Message, bot: Bot, cfg: dict) -> None:
    for user in msg.new_chat_members or []:
        if not user.is_bot:
            await _welcome_dm(bot, cfg, user)


async def handle_group_message(msg: Message, bot: Bot, cfg: dict, bot_id: str) -> None:
    """Only the group 🚀 Start button is handled. Every other message is left untouched."""
    user = msg.from_user
    if not user or (msg.text or "").strip() != START_TEXT:
        return
    chat_id = msg.chat.id

    if not group_start_enabled(cfg):
        # Button switched OFF in the admin panel: register nobody, clear any stale keyboard.
        try:
            r = await bot.send_message(chat_id, ".", reply_markup=ReplyKeyboardRemove())
            await bot.delete_message(chat_id, r.message_id)
        except Exception as e:
            log.debug("cleanup failed: %s", e)
        try:
            await bot.delete_message(chat_id, msg.message_id)
        except Exception:
            pass
        return

    source = source_for_chat(chat_id, "group")
    is_new = await db.touch_user(bot_id, user.id, user.username, user.first_name, source)
    if is_new:
        await notify_admin_new_user(bot, cfg, "Group Start Button", user.first_name, user.username, user.id, source)
        reply = (f"✅ <b>Registered!</b>\n\n👤 {esc(user.first_name)}\n🆔 <code>{user.id}</code>\n"
                 f"👥 Group: <code>{esc(source)}</code>")
    else:
        reply = "✅ <b>You are already registered.</b>"
    try:
        await bot.send_message(chat_id, reply)
    except Exception as e:
        log.warning("group reply failed: %s", e)
    try:  # best effort - needs the delete-messages permission
        await bot.delete_message(chat_id, msg.message_id)
    except Exception:
        pass
