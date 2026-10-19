"""Video pipeline: pick the next unsent video from an API, download it, send to groups/channels."""
import asyncio
import logging
from urllib.parse import quote

from aiogram import Bot
from aiogram.types import FSInputFile, LinkPreviewOptions

from . import db, utils
from .keyboards import cron_source_keyboard, group_reply_markup
from .utils import chat_ref, esc

log = logging.getLogger("cron")
_locks: dict = {}


def _lock(bot_id: str) -> asyncio.Lock:
    return _locks.setdefault(bot_id, asyncio.Lock())


def is_running(bot_id: str) -> bool:
    return bot_id in _locks and _locks[bot_id].locked()


def source_info(source: str, cfg: dict) -> dict:
    apis = cfg.get("apis", {}) or {}
    if source == "vid65":
        return dict(label="VID65 API", api=str((apis.get("vid65", {}) or {}).get("url") or ""),
                    page_key="vid65_page", tracker_key="vid65_index")
    return dict(label="Old API", api=str((apis.get("old", {}) or {}).get("url") or ""),
                page_key="old_page", tracker_key="old_last_slug")


def video_key(source: str, v: dict) -> str:
    if source == "vid65":
        i = str(v.get("id", "")).strip()
        return f"id:{i}" if i else ""
    s = str(v.get("slug", "")).strip()
    return f"slug:{s}" if s else ""


async def progress_edit(bot: Bot, chat_id, msg_id, is_photo: bool, text: str) -> None:
    try:
        if is_photo:
            await bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=text)
        else:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=msg_id)
    except Exception:
        pass  # "message is not modified" and friends are harmless


async def run(bot: Bot, bot_id: str, admin_chat, source) -> None:
    """source=None shows the source picker; otherwise runs old / vid65 / both."""
    if source is None:
        await bot.send_message(admin_chat, "📡 <b>Select API source</b>", reply_markup=cron_source_keyboard())
        return
    lock = _lock(bot_id)
    if lock.locked():
        await bot.send_message(admin_chat, "⚠️ A cron is already running.")
        return
    async with lock:
        for src in (["old", "vid65"] if source == "both" else [source]):
            try:
                await pipeline(bot, bot_id, admin_chat, src)
            except Exception:
                log.exception("[%s] cron pipeline (%s) crashed", bot_id, src)
                try:
                    await bot.send_message(admin_chat, f"❌ Cron ({src}) crashed - see bot_error.log")
                except Exception:
                    pass


async def pipeline(bot: Bot, bot_id: str, admin_chat, source: str) -> None:
    cfg = await db.find_bot(bot_id, fresh=True)
    if not cfg:
        return
    info = source_info(source, cfg)
    tracking = db.get_tracking(cfg)
    api_cfg = (cfg.get("apis", {}) or {}).get(source, {}) or {}
    groups = [str(g) for g in api_cfg.get("groups", []) or []]
    channels = [str(c) for c in api_cfg.get("channels", []) or []]
    settings = cfg.get("settings", {}) or {}
    msgs = cfg.get("messages", {}) or {}
    max_size = int(settings.get("max_size") or 2147483648)
    blur_pct = int(settings.get("blur_percentage") if settings.get("blur_percentage") is not None else 20)
    hist_limit = int(settings.get("max_channel_messages") or 10)
    mode = tracking["mode"]

    page = int(tracking[info["page_key"]] or (1 if source == "vid65" else 203))
    tracker = tracking[info["tracker_key"]]
    v = None
    cur_idx, total = -1, 0

    # ---- look through pages until a real, unsent video is found
    for _ in range(50):
        page = max(1, page)
        url = f"{info['api']}?page={page}" if source == "vid65" else f"{info['api']}?action=home&page={page}"
        home = await utils.fetch_json(url)
        data = home.get("data") if isinstance(home, dict) else None
        if isinstance(data, dict):
            data = list(data.values())
        if not isinstance(data, list):
            await bot.send_message(
                admin_chat, f"⚠️ <b>{info['label']} API error</b>\n\n📄 Page: {page}\n\n"
                "The page position was not changed. Cron will retry this page on the next run.")
            return
        videos, total = data, len(data)

        if total == 0:
            if source == "vid65":
                page, tracker = page + 1, 0
            else:
                page, tracker = max(1, page - 1), ""
            tracking[info["page_key"]], tracking[info["tracker_key"]] = page, tracker
            await db.save_tracking(bot_id, tracking)
            continue

        if source == "vid65":
            index = max(0, int(tracker or 0))
            while index < total:
                cand = videos[index]
                key = video_key("vid65", cand)
                if key and not await db.cron_was_sent(bot_id, "vid65", key):
                    cur_idx, v = index, cand
                    break
                index += 1
            if v is None:
                page, tracker = page + 1, 0
                tracking[info["page_key"]], tracking[info["tracker_key"]] = page, 0
                await db.save_tracking(bot_id, tracking)
                continue
            tracking[info["tracker_key"]] = cur_idx + 1
            if cur_idx + 1 >= total:
                tracking[info["page_key"]], tracking[info["tracker_key"]] = page + 1, 0
        else:
            last = str(tracker or "")
            if last == "":
                cur_idx = total - 1
            else:
                found = next((i for i, x in enumerate(videos) if str(x.get("slug", "")) == last), -1)
                cur_idx = total - 1 if found == -1 else found - 1
            while cur_idx >= 0:
                cand = videos[cur_idx]
                key = video_key("old", cand)
                if key and not await db.cron_was_sent(bot_id, "old", key):
                    v = cand
                    break
                cur_idx -= 1
            if v is None:
                page, tracker = max(1, page - 1), ""
                tracking[info["page_key"]], tracking[info["tracker_key"]] = page, ""
                await db.save_tracking(bot_id, tracking)
                continue
            tracking[info["tracker_key"]] = str(v["slug"])
            if cur_idx == 0:
                tracking[info["page_key"]], tracking[info["tracker_key"]] = max(1, page - 1), ""
        await db.save_tracking(bot_id, tracking)
        break

    if v is None:
        await bot.send_message(admin_chat, f"ℹ️ <b>{info['label']}</b>: no new videos found.")
        return

    if source == "vid65":
        title = esc(v.get("name") or "Untitled")
        slug = "vid65-" + str(v.get("id"))
        vkey = video_key("vid65", v)
        thumb_url, link = v.get("image") or "", v.get("video") or ""
    else:
        title = esc(v.get("title") or "Untitled")
        slug = str(v["slug"])
        vkey = video_key("old", v)
        thumb_url, link = v.get("thumbnail") or "", ""
    if await db.cron_was_sent(bot_id, source, vkey):
        return

    item_num = cur_idx + 1
    mode_label = "Video (Both)" if mode == "ON" else "Video (Group) + Image (Channel)"
    thumb = await utils.fetch_thumbnail(thumb_url)
    init_caption = (f"⏳ <b>Cron started...</b>\n\n📡 <b>Source:</b> {info['label']}\n🎬 <b>{title}</b>\n"
                    f"🆔 <code>{esc(slug)}</code>\n📄 Page: {page} | Item: {item_num}/{total}\n⚙️ Mode: {mode_label}\n"
                    f"👥 Target Groups: {len(groups)}\n📢 Target Channels: {len(channels)}")
    temp_files = [thumb]
    try:
        use_photo = False
        init = None
        if thumb:
            try:
                init = await bot.send_photo(admin_chat, FSInputFile(thumb), caption=init_caption)
                use_photo = True
            except Exception as e:
                log.warning("init photo failed: %s", e)
        if init is None:
            init = await bot.send_message(admin_chat, init_caption)
        msg_id = init.message_id

        async def edit(text: str) -> None:
            await progress_edit(bot, admin_chat, msg_id, use_photo, text)

        if source == "old":
            vd = await utils.fetch_json(f"{info['api']}?action=video&id={quote(slug)}")
            link = ((vd or {}).get("data") or {}).get("downloadLink") or "" if isinstance(vd, dict) else ""
        if not link:
            await edit(f"❌ <b>No download link</b>\n\n📡 {info['label']}\n🎬 <b>{title}</b>\n🆔 <code>{esc(slug)}</code>")
            return

        blur = vthumb = vblur = None
        has_thumb = bool(thumb)
        if has_thumb:
            blur = await utils.make_blurred(thumb, blur_pct)
            vthumb = await utils.video_thumb(thumb)
            vblur = await utils.video_thumb(blur)
            temp_files += [blur, vthumb, vblur]

        ch_caption = str(msgs.get("channel_caption") or "🎬 New video").replace("{title}", title)
        gr_caption = str(msgs.get("group_caption") or "🎬 <b>{title}</b>").replace("{title}", title)
        group_markup = group_reply_markup(cfg)  # Start keyboard, or remove_keyboard when switched OFF

        tmp = utils.tmp_path("dl", ".mp4")
        temp_files.append(tmp)
        stats = dict(last=-1)

        async def on_prog(done: int, tot: int) -> None:
            pct = int(done * 100 / tot)
            if pct != stats["last"] and pct < 100:
                stats["last"] = pct
                await edit(f"📥 <b>Downloading...</b> {pct}%\n\n📡 {info['label']}\n🎬 <b>{title}</b>\n"
                           f"📄 Page: {page} | Item: {item_num}/{total}\n📦 {done/1048576:.1f} MB / {tot/1048576:.1f} MB")

        ok, err, size = await utils.download_file(link, tmp, max_size, on_prog)
        sent_as_video = False
        if not ok and err == "too_large":
            await edit(f"⚠️ <b>File too large ({size/1048576:.1f}MB > {max_size/1048576:.1f}MB). Falling back to photo/link.</b>")
        elif not ok:
            await edit("⚠️ <b>Download failed. Falling back to photo/link.</b>")
        else:
            await edit(f"📤 <b>Uploading to groups...</b>\n\n🎬 <b>{title}</b>\n📄 Page: {page} | Item: {item_num}/{total}")
            for g in groups:
                await _send_video(bot, g, tmp, vthumb if has_thumb else None, gr_caption, protect=True, markup=group_markup)
                await asyncio.sleep(1)
            if mode == "ON":
                await edit(f"📤 <b>Uploading to channels...</b>\n\n🎬 <b>{title}</b>\n📄 Page: {page} | Item: {item_num}/{total}")
                for c in channels:
                    r = await _send_video(bot, c, tmp, vblur if has_thumb else None, ch_caption, protect=False)
                    await _record_channel(bot, bot_id, c, r, hist_limit)
            else:
                await edit(f"📤 <b>Sending Image to Channels (Mode OFF)...</b>\n\n🎬 <b>{title}</b>")
                await _channel_images(bot, bot_id, channels, blur, ch_caption, hist_limit)
            sent_as_video = True

        if not sent_as_video:
            await edit(f"📤 <b>Sending Fallback (Image + Link)...</b>\n\n📡 {info['label']}\n🎬 <b>{title}</b>\n🆔 <code>{esc(slug)}</code>")
            for g in groups:
                cap = gr_caption + f'\n\n📥 <a href="{esc(link)}">Watch / Download Video</a>'
                try:
                    if has_thumb:
                        await bot.send_photo(chat_ref(g), FSInputFile(thumb), caption=cap, protect_content=True, reply_markup=group_markup)
                    else:
                        await bot.send_message(chat_ref(g), cap, protect_content=True, reply_markup=group_markup,
                                               link_preview_options=LinkPreviewOptions(is_disabled=True))
                except Exception as e:
                    log.warning("[%s] fallback to group %s failed: %s", bot_id, g, e)
                await asyncio.sleep(1)
            await _channel_images(bot, bot_id, channels, blur, ch_caption, hist_limit)

        await db.cron_mark_sent(bot_id, source, vkey)
        await db.log_event(bot_id, f"Cron ({source}): sent '{v.get('title') or v.get('name') or slug}' to {len(groups)} group(s), {len(channels)} channel(s)")
        await edit(f"✅ <b>Cron finished!</b>\n\n📡 <b>{info['label']}</b>\n🎬 <b>{title}</b>\n"
                   f"📄 Page: {page} | Item: {item_num}/{total}\n👥 Groups Dispatched: {len(groups)}\n"
                   f"📢 Channels Dispatched: {len(channels)}")
    finally:
        utils.safe_unlink(*set(p for p in temp_files if p))


async def _send_video(bot: Bot, chat, path: str, thumb, caption: str, protect: bool, markup=None):
    try:
        return await bot.send_video(
            chat_ref(chat), FSInputFile(path), caption=caption, supports_streaming=True, protect_content=protect,
            thumbnail=FSInputFile(thumb) if thumb else None, reply_markup=markup,
            request_timeout=1800)
    except Exception as e:
        log.warning("send_video to %s failed: %s", chat, e)
        return None


async def _record_channel(bot: Bot, bot_id: str, channel: str, result, limit: int) -> None:
    if result is None:
        return
    try:
        for old_id in await db.add_channel_post(bot_id, channel, result.message_id, limit):
            try:
                await bot.delete_message(chat_ref(channel), old_id)
            except Exception:
                pass
    except Exception:
        log.exception("channel history failed")


async def _channel_images(bot: Bot, bot_id: str, channels: list, blur, caption: str, limit: int) -> None:
    if not blur:
        return
    for c in channels:
        try:
            r = await bot.send_photo(chat_ref(c), FSInputFile(blur), caption=caption)
            await _record_channel(bot, bot_id, c, r, limit)
        except Exception as e:
            log.warning("channel image to %s failed: %s", c, e)
