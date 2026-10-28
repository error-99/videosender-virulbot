from aiogram import Bot

from . import db
from .keyboards import admin_keyboard
from .utils import esc


async def send(bot: Bot, bot_id: str, chat_id, cfg: dict) -> None:
    t = db.get_tracking(cfg)
    us = await db.user_stats(bot_id)
    apis = cfg.get("apis", {}) or {}

    def n(src: str, key: str) -> int:
        return len((apis.get(src, {}) or {}).get(key, []) or [])

    gs = "ON" if str((cfg.get("settings", {}) or {}).get("group_start_button", "ON")).upper() != "OFF" else "OFF"
    text = (
        f"📊 <b>Bot Status</b>\n\n🤖 <b>Bot:</b> {esc(cfg.get('name') or bot_id)}\n🆔 <b>ID:</b> <code>{esc(bot_id)}</code>\n"
        f"⚙️ <b>Mode:</b> {t['mode']}\n🚀 <b>Group Start button:</b> {gs}\n"
        f"👥 <b>Users:</b> {us['total']} (🟢 {us['active']} active · 🚫 {us['blocked']} blocked · 🆕 {us['new_today']} today)\n\n"
        f"📡 <b>Old API:</b>\n  • Groups: {n('old','groups')} | Channels: {n('old','channels')}\n"
        f"  • Page: {t['old_page']} | Last: <code>{esc(t['old_last_slug'] or 'None')}</code>\n\n"
        f"📡 <b>VID65 API:</b>\n  • Groups: {n('vid65','groups')} | Channels: {n('vid65','channels')}\n"
        f"  • Page: {t['vid65_page']} | Index: {t['vid65_index']}\n\n"
        f"🆔 <b>Admin:</b> <code>{esc(cfg.get('admin_id'))}</code>")
    await bot.send_message(chat_id, text, reply_markup=admin_keyboard(t["mode"]))
