"""One Router per bot (aiogram routers can only belong to a single Dispatcher)."""
from aiogram import Bot, F, Router
from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, ChatMemberUpdated, Message

from . import admin, group_manager, user


class IsAdmin(BaseFilter):
    async def __call__(self, event, cfg: dict) -> bool:
        u = getattr(event, "from_user", None)
        return bool(u) and str(u.id) == str(cfg.get("admin_id", ""))


def build_router() -> Router:
    r = Router()

    @r.my_chat_member()
    async def my_chat_member(upd: ChatMemberUpdated, bot: Bot, cfg: dict, bot_id: str):
        await group_manager.handle_my_chat_member(upd, bot, cfg, bot_id)

    @r.chat_member()
    async def chat_member(upd: ChatMemberUpdated, bot: Bot, cfg: dict):
        await user.group_join_via_chat_member(upd, bot, cfg)

    @r.message(F.new_chat_members)
    async def new_members(msg: Message, bot: Bot, cfg: dict):
        await user.group_new_chat_members(msg, bot, cfg)

    # Group traffic ALWAYS goes to the user side - even for the admin
    # (so the admin can press the group Start button and is never deleted).
    @r.message(F.chat.type.in_({"group", "supergroup"}))
    async def group_message(msg: Message, bot: Bot, cfg: dict, bot_id: str):
        await user.handle_group_message(msg, bot, cfg, bot_id)

    @r.message(F.chat.type == "private", IsAdmin())
    async def admin_message(msg: Message, bot: Bot, cfg: dict, bot_id: str):
        await admin.handle_message(msg, bot, cfg, bot_id)

    @r.message(F.chat.type == "private")
    async def private_message(msg: Message, bot: Bot, cfg: dict, bot_id: str):
        await user.handle_private_message(msg, bot, cfg, bot_id)

    @r.callback_query(IsAdmin())
    async def admin_callback(cq: CallbackQuery, bot: Bot, cfg: dict, bot_id: str):
        await admin.handle_callback(cq, bot, cfg, bot_id)

    @r.callback_query()
    async def user_callback(cq: CallbackQuery):
        await cq.answer()

    return r
