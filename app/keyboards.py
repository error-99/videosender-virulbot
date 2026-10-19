from aiogram.types import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                           ReplyKeyboardMarkup, ReplyKeyboardRemove)

START_TEXT = "🚀 Start"


def group_start_enabled(cfg: dict) -> bool:
    """Group 🚀 Start button switch (web panel → Settings). Default ON."""
    v = (cfg.get("settings", {}) or {}).get("group_start_button", "ON")
    if isinstance(v, str):
        return v.strip().upper() != "OFF"
    return bool(v)


def group_start_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=START_TEXT)]],
                               resize_keyboard=True, one_time_keyboard=True)


def group_reply_markup(cfg: dict):
    """ON -> Start keyboard. OFF -> remove_keyboard (hides any old button in the group)."""
    return group_start_keyboard() if group_start_enabled(cfg) else ReplyKeyboardRemove()


def dm_start_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=START_TEXT)]], resize_keyboard=True)


def admin_keyboard(mode: str) -> ReplyKeyboardMarkup:
    mode_txt = "🟢 Mode: ON" if mode == "ON" else "🔴 Mode: OFF"
    return ReplyKeyboardMarkup(resize_keyboard=True, keyboard=[
        [KeyboardButton(text="📢 Broadcast"), KeyboardButton(text="📊 Status")],
        [KeyboardButton(text="▶️ Run Cron"), KeyboardButton(text=mode_txt)],
        [KeyboardButton(text="📥 Browse Videos")],
        [KeyboardButton(text="⌨️ Hide Keyboard")],
    ])


def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="❌ Cancel")]], resize_keyboard=True)


def hidden_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


def cron_source_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ Old API", callback_data="cron_source_old"),
         InlineKeyboardButton(text="2️⃣ VID65 API", callback_data="cron_source_vid65")],
        [InlineKeyboardButton(text="▶️ Run Both", callback_data="cron_source_both")],
    ])


def browse_source_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ Old API", callback_data="browse_src_old")],
        [InlineKeyboardButton(text="2️⃣ VID65 API", callback_data="browse_src_vid65")],
    ])
