import asyncio
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter

from . import db
from .keyboards import admin_keyboard

log = logging.getLogger("broadcast")
PERMANENT = ("can't initiate conversation", "bot was blocked by the user", "user is deactivated",
             "chat not found", "peer_id_invalid", "user not found")


def is_permanent(e: Exception) -> bool:
    if isinstance(e, (TelegramForbiddenError, TelegramBadRequest)):
        m = str(e).lower()
        return any(p in m for p in PERMANENT)
    return False


async def run(bot: Bot, bot_id: str, chat_id, msg_id: int, admin_id: str, mode: str) -> None:
    users = await db.active_user_ids(bot_id)
    await bot.send_message(chat_id, f"🚀 Broadcasting to {len(users)} users...")
    ok = blocked = failed = 0
    for uid in users:
        for attempt in (1, 2):
            try:
                await bot.copy_message(uid, from_chat_id=int(admin_id), message_id=msg_id)
                ok += 1
            except TelegramRetryAfter as e:
                if attempt == 1:
                    await asyncio.sleep(e.retry_after + 1)
                    continue
                failed += 1
            except TelegramAPIError as e:
                if is_permanent(e):
                    await db.mark_blocked(bot_id, uid)
                    blocked += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            break
        await asyncio.sleep(0.05)
    try:
        await db.log_broadcast(bot_id, len(users), ok, blocked, failed)
    except Exception:
        log.exception("log_broadcast failed")
    s = f"✅ Broadcast done. Sent to {ok} users."
    if blocked:
        s += f"\n🚫 {blocked} unreachable user(s) marked as blocked (skipped in future broadcasts)."
    if failed:
        s += f"\n⚠️ {failed} temporary failure(s)."
    await bot.send_message(chat_id, s, reply_markup=admin_keyboard(mode))


async def run_text(bot: Bot, bot_id: str, text: str, job: dict) -> None:
    """Broadcast typed text (HTML allowed) from the web panel. `job` is updated live for progress display."""
    users = await db.active_user_ids(bot_id)
    job.update(state="running", total=len(users), sent=0, blocked=0, failed=0)
    for uid in users:
        for attempt in (1, 2):
            try:
                await bot.send_message(uid, text)
                job["sent"] += 1
            except TelegramRetryAfter as e:
                if attempt == 1:
                    await asyncio.sleep(e.retry_after + 1)
                    continue
                job["failed"] += 1
            except TelegramAPIError as e:
                if is_permanent(e):
                    await db.mark_blocked(bot_id, uid)
                    job["blocked"] += 1
                else:
                    job["failed"] += 1
            except Exception:
                job["failed"] += 1
            break
        await asyncio.sleep(0.05)
    try:
        await db.log_broadcast(bot_id, job["total"], job["sent"], job["blocked"], job["failed"])
        await db.log_event(bot_id, f"Web broadcast: {job['sent']}/{job['total']} delivered, {job['blocked']} blocked")
    except Exception:
        log.exception("log_broadcast failed")
    job["state"] = "done"
