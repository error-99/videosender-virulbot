"""Bot added to / removed from chats, and private-chat block/unblock tracking."""
import asyncio
import logging

from aiogram import Bot
from aiogram.types import CallbackQuery, ChatMemberUpdated, ReplyKeyboardRemove

from . import config, db
from .keyboards import group_confirm_keyboard, group_reply_markup, group_start_enabled
from .utils import chat_ref, esc

log = logging.getLogger("group_manager")
IN = {"member", "administrator", "creator", "restricted"}


async def send_group_start_button(bot: Bot, cfg: dict, chat_id) -> None:
    if not group_start_enabled(cfg):
        return
    await bot.send_message(
        chat_id, "👋 <b>Welcome!</b>\n\nPress <b>🚀 Start</b> below to register yourself with the bot.",
        reply_markup=group_reply_markup(cfg))


def _who(user) -> str:
    n = f"{user.first_name or ''} {user.last_name or ''}".strip()
    return n + (f" (@{user.username})" if user.username else "")


async def _notify_owner(bot: Bot, cfg: dict, text: str) -> None:
    if cfg.get("admin_id"):
        try:
            await bot.send_message(cfg["admin_id"], text)
        except Exception as e:
            log.warning("admin notify failed: %s", e)


async def handle_my_chat_member(upd: ChatMemberUpdated, bot: Bot, cfg: dict, bot_id: str) -> None:
    chat = upd.chat
    chat_id = str(chat.id)
    title = chat.title or chat.username or chat_id
    ctype = chat.type

    if ctype == "private":
        st = upd.new_chat_member.status
        if st == "kicked":
            await db.mark_blocked(bot_id, chat.id)
        elif st == "member":
            await db.mark_active(bot_id, chat.id)
        return

    if ctype == "channel":
        await db.upsert_chat(bot_id, chat_id, "channel", str(title))
    if ctype not in ("group", "supergroup"):
        return
    if chat_id in groups_of(cfg):  # already confirmed earlier - keep the title fresh
        await db.upsert_chat(bot_id, chat_id, "group", str(title))

    was_in = upd.old_chat_member.status in IN
    is_in = upd.new_chat_member.status in IN
    actor = upd.from_user

    if not was_in and is_in:
        # NOT saved yet: a group admin has to confirm first (see handle_group_decision).
        mins = max(1, round(config.GROUP_CONFIRM_SECONDS / 60))
        await db.add_pending_group(bot_id, chat_id, str(title), actor.id, _who(actor), config.GROUP_CONFIRM_SECONDS)
        log.info("[%s] Group %s (%s) added by %s - waiting for confirmation", bot_id, chat_id, title, _who(actor))
        await db.log_event(bot_id, f"Bot added to group '{title}' ({chat_id}) by {_who(actor)} - waiting for admin confirmation")
        try:
            await bot.send_message(
                chat.id, f"👋 <b>Hello!</b>\n\nA <b>group admin</b> must confirm that this bot may work here.\n"
                         f"Press <b>✅ Confirm</b> within {mins} min, otherwise the bot will leave.",
                reply_markup=group_confirm_keyboard())
        except Exception as e:
            log.warning("confirmation post failed in %s: %s", chat_id, e)
        await _notify_owner(bot, cfg, f"⏳ <b>Bot added to a group - waiting for confirmation</b>\n\n👥 <b>{esc(title)}</b>\n"
                                      f"🆔 <code>{chat_id}</code>\n👤 Added by: {esc(_who(actor))}")
    elif was_in and not is_in:
        pend = await db.claim_pending_group(bot_id, chat_id)
        await db.remove_group(bot_id, chat_id)
        log.info("[%s] Group %s (%s) removed%s", bot_id, chat_id, title, " before confirmation" if pend else "")
        await db.log_event(bot_id, f"Bot removed from group '{title}' ({chat_id})", "warn")
        await _notify_owner(bot, cfg, f"⚠️ <b>Bot removed from a group</b>\n\n👥 {esc(title)}\n🆔 <code>{chat_id}</code>\n\n"
                                      + ("It had not been confirmed yet." if pend else
                                         "It has been automatically removed from both target lists."))


async def _may_decide(bot: Bot, cfg: dict, chat_id: int, user_id: int) -> bool:
    """Telegram group admins/creator, or the bot owner (admin_id), may confirm or reject."""
    if str(user_id) == str(cfg.get("admin_id", "")):
        return True
    try:
        m = await bot.get_chat_member(chat_id, user_id)
        return m.status in ("administrator", "creator")
    except Exception as e:
        log.warning("get_chat_member(%s, %s) failed: %s", chat_id, user_id, e)
        return False


async def handle_group_decision(cq: CallbackQuery, bot: Bot, cfg: dict, bot_id: str) -> None:
    msg = cq.message
    if not msg or msg.chat.type not in ("group", "supergroup"):
        await cq.answer()
        return
    chat = msg.chat
    if not await _may_decide(bot, cfg, chat.id, cq.from_user.id):
        await cq.answer("Only a group admin can answer this.", show_alert=True)
        return
    pend = await db.claim_pending_group(bot_id, chat.id)
    if not pend:
        await cq.answer("This request is no longer pending.", show_alert=True)
        return
    await cq.answer()
    try:  # remove the buttons so it can't be pressed twice
        await bot.edit_message_reply_markup(chat_id=chat.id, message_id=msg.message_id, reply_markup=None)
    except Exception:
        pass
    if cq.data == "grp_ok":
        await approve_group(bot, cfg, bot_id, pend, cq.from_user)
    else:
        await reject_group(bot, cfg, bot_id, pend, by=cq.from_user)


async def approve_group(bot: Bot, cfg: dict, bot_id: str, pend: dict, by) -> None:
    chat_id, title = pend["chat_id"], pend.get("title") or pend["chat_id"]
    await db.add_group(bot_id, chat_id)
    await db.upsert_chat(bot_id, chat_id, "group", str(title))
    await db.log_event(bot_id, f"Group '{title}' ({chat_id}) confirmed by {_who(by)} and saved")
    log.info("[%s] Group %s (%s) confirmed by %s", bot_id, chat_id, title, _who(by))
    note = ("the <b>🚀 Start</b> registration button was posted." if group_start_enabled(cfg)
            else "the <b>🚀 Start</b> button is switched OFF in the admin panel, so it was not posted.")
    try:
        await bot.send_message(chat_ref(chat_id), f"✅ <b>Confirmed by {esc(_who(by))}.</b> The bot is now active in this group.")
    except Exception as e:
        log.warning("confirm message failed in %s: %s", chat_id, e)
    try:
        await send_group_start_button(bot, cfg, chat_ref(chat_id))
    except Exception as e:
        log.warning("start button post failed in %s: %s", chat_id, e)
    await _notify_owner(bot, cfg, f"✅ <b>Group confirmed and saved!</b>\n\n👥 <b>{esc(title)}</b>\n🆔 <code>{chat_id}</code>\n"
                                  f"👤 Confirmed by: {esc(_who(by))}\n\nIt was added to both API target lists and {note}")


def reject_text(cfg: dict) -> str:
    return str((cfg.get("messages", {}) or {}).get("group_reject") or config.GROUP_REJECT_TEXT)


async def reject_group(bot: Bot, cfg: dict, bot_id: str, pend: dict, by=None, timed_out: bool = False) -> None:
    """Not confirmed: nothing is saved, the reject message is sent, and the bot leaves the group."""
    chat_id, title = pend["chat_id"], pend.get("title") or pend["chat_id"]
    await db.remove_group(bot_id, chat_id)  # make sure it is in no target list
    try:
        await bot.send_message(chat_ref(chat_id), esc(reject_text(cfg)))
    except Exception as e:
        log.warning("reject message failed in %s: %s", chat_id, e)
    try:
        await bot.leave_chat(chat_ref(chat_id))
    except Exception as e:
        log.warning("leave_chat %s failed: %s", chat_id, e)
    why = "no confirmation in time" if timed_out else f"rejected by {_who(by)}"
    await db.log_event(bot_id, f"Left group '{title}' ({chat_id}): {why}", "warn")
    log.info("[%s] Left group %s (%s): %s", bot_id, chat_id, title, why)
    await _notify_owner(bot, cfg, f"🚪 <b>Bot left a group</b>\n\n👥 <b>{esc(title)}</b>\n🆔 <code>{chat_id}</code>\nReason: {esc(why)}")


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
