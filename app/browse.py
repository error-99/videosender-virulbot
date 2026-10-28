"""📥 Browse Videos: flip through an API's video list and download one on demand."""
import logging
from urllib.parse import quote

from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from . import db, utils
from .keyboards import admin_keyboard
from .utils import esc

log = logging.getLogger("browse")


def _api(cfg: dict, src: str) -> str:
    return str(((cfg.get("apis", {}) or {}).get(src, {}) or {}).get("url") or "")


def _home_url(cfg: dict, src: str, page: int) -> str:
    base = _api(cfg, src)
    return f"{base}?page={page}" if src == "vid65" else f"{base}?action=home&page={page}"


def _videos(data) -> list:
    d = (data or {}).get("data") if isinstance(data, dict) else None
    if isinstance(d, dict):
        d = list(d.values())
    return d if isinstance(d, list) else []


def _fields(src: str, v: dict) -> tuple:
    if src == "vid65":
        return esc(v.get("name") or "Untitled"), "vid65-" + str(v.get("id", "")), v.get("image") or ""
    return esc(v.get("title") or "Untitled"), str(v.get("slug", "")), v.get("thumbnail") or ""


async def _show(bot: Bot, chat_id, src: str, p: int, i: int, vids: list) -> None:
    total = len(vids)
    v = vids[i]
    title, slug, thumb = _fields(src, v)
    caption = f"🎬 <b>{title}</b>\n\n📄 Page: {p} | Item: {i + 1}/{total}\n🆔 <code>{esc(slug)}</code>\n📡 {src.upper()}"
    row = []
    if i > 0:
        row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"abrowse_{src}_{p}_{i - 1}"))
    row.append(InlineKeyboardButton(text="⬇️ Download", callback_data=f"aget_{src}_{p}_{i}"))
    if i < total - 1:
        row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"abrowse_{src}_{p}_{i + 1}"))
    kb = InlineKeyboardMarkup(inline_keyboard=[row])
    if thumb:
        try:
            await bot.send_photo(chat_id, thumb, caption=caption, reply_markup=kb)
            return
        except Exception as e:
            log.debug("send_photo by url failed (%s) - sending text instead", e)
    await bot.send_message(chat_id, caption, reply_markup=kb)


async def page(bot: Bot, bot_id: str, chat_id, p: int, src: str, cfg: dict) -> None:
    vids = _videos(await utils.fetch_json(_home_url(cfg, src, p)))
    mode = db.get_tracking(cfg)["mode"]
    if not vids:
        await bot.send_message(chat_id, f"❌ No videos on page {p} for {src.upper()}.", reply_markup=admin_keyboard(mode))
        return
    await _show(bot, chat_id, src, p, 0, vids)
    await bot.send_message(chat_id, "Use buttons below or ❌ Cancel above.", reply_markup=admin_keyboard(mode))


async def render(bot: Bot, chat_id, data: str, del_msg_id: int, cfg: dict) -> None:
    parts = data.split("_")
    if len(parts) < 4:
        return
    src, p, i = parts[1], int(parts[2]), int(parts[3])
    vids = _videos(await utils.fetch_json(_home_url(cfg, src, p)))
    if not vids:
        await bot.send_message(chat_id, f"❌ No videos on page {p}.")
        return
    if i >= len(vids) or i < 0:
        i = 0
    try:
        await bot.delete_message(chat_id, del_msg_id)
    except Exception:
        pass
    await _show(bot, chat_id, src, p, i, vids)


async def download_by_index(bot: Bot, bot_id: str, chat_id, src: str, page_no: str, index: str, cfg: dict) -> None:
    vids = _videos(await utils.fetch_json(_home_url(cfg, src, int(page_no))))
    idx = int(index)
    if idx < 0 or idx >= len(vids):
        await bot.send_message(chat_id, "❌ Video not found.")
        return
    v = vids[idx]
    title, slug, thumb_url = _fields(src, v)
    if src == "vid65":
        link = v.get("video") or ""
    else:
        vd = await utils.fetch_json(f"{_api(cfg, 'old')}?action=video&id={quote(slug)}")
        link = ((vd or {}).get("data") or {}).get("downloadLink") or "" if isinstance(vd, dict) else ""
    if not link:
        await bot.send_message(chat_id, f"❌ No download link.\n\n🎬 <b>{title}</b>\n🆔 <code>{esc(slug)}</code>")
        return

    max_size = int((cfg.get("settings", {}) or {}).get("max_size") or 2147483648)
    tmp = utils.tmp_path("browse", ".mp4")
    thumb = vthumb = None
    try:
        await bot.send_message(chat_id, "📥 Downloading...")
        ok, err, _ = await utils.download_file(link, tmp, max_size)
        if not ok:
            await bot.send_message(chat_id, "❌ File is larger than the limit." if err == "too_large" else "❌ Download failed.")
            return
        thumb = await utils.fetch_thumbnail(thumb_url)
        vthumb = await utils.video_thumb(thumb) if thumb else None
        await bot.send_video(chat_id, FSInputFile(tmp), caption=f"🎬 <b>{title}</b>\n🆔 <code>{esc(slug)}</code>",
                             thumbnail=FSInputFile(vthumb) if vthumb else None, supports_streaming=True)
    except Exception as e:
        log.error("browse download failed: %s", e)
        await bot.send_message(chat_id, f"❌ Failed: {esc(str(e)[:200])}")
    finally:
        utils.safe_unlink(tmp, thumb, vthumb if vthumb != thumb else None)
