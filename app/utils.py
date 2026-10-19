import asyncio
import html
import logging
import os
import re
import tempfile
import time
from typing import Awaitable, Callable, Optional

import aiohttp

from . import config

log = logging.getLogger("utils")


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=False)


def chat_ref(v):
    """Numeric chat ids become int, @usernames stay str."""
    s = str(v).strip()
    return int(s) if re.fullmatch(r"-?\d+", s) else s


def chat_key(chat_id) -> str:
    s = str(chat_id)
    if s.startswith("-100"):
        return s[4:]
    return s.lstrip("-")


def source_for_chat(chat_id, kind: str = "group") -> str:
    return ("channel_" if kind == "channel" else "group_") + chat_key(chat_id)


def tmp_path(prefix: str, suffix: str) -> str:
    fd, p = tempfile.mkstemp(prefix=f"tgbot_{prefix}_", suffix=suffix)
    os.close(fd)
    return p


def safe_unlink(*paths) -> None:
    for p in paths:
        if p:
            try:
                os.unlink(p)
            except OSError:
                pass


def _ssl():
    return None if config.VERIFY_SSL else False


async def fetch_json(url: str, timeout: int = 60):
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as s:
            async with s.get(url, ssl=_ssl()) as r:
                return await r.json(content_type=None)
    except Exception as e:
        log.error("fetch_json(%s): %s", url, e)
        return None


async def download_file(url: str, dest: str, max_size: int,
                        on_progress: Optional[Callable[[int, int], Awaitable[None]]] = None) -> tuple:
    """Stream url -> dest. Returns (ok, error_text, size)."""
    size = 0
    try:
        timeout = aiohttp.ClientTimeout(total=1800, connect=15)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.get(url, ssl=_ssl(), allow_redirects=True) as r:
                if r.status != 200:
                    return False, f"HTTP {r.status}", 0
                total = int(r.headers.get("Content-Length") or 0)
                if total and total > max_size:
                    return False, "too_large", total
                last = 0.0
                with open(dest, "wb") as f:
                    async for chunk in r.content.iter_chunked(262144):
                        size += len(chunk)
                        if size > max_size:
                            return False, "too_large", size
                        f.write(chunk)
                        now = time.time()
                        if on_progress and total and now - last >= 2:
                            last = now
                            try:
                                await on_progress(size, total)
                            except Exception:
                                pass
        return (size > 0), ("" if size > 0 else "empty file"), size
    except Exception as e:
        log.error("download_file(%s): %s", url, e)
        return False, str(e), size


def _make_jpeg(raw_path: str) -> Optional[str]:
    """Validate an image and re-save it as JPEG. Returns the new path or None."""
    from PIL import Image
    try:
        im = Image.open(raw_path)
        im.load()
        out = tmp_path("thumb", ".jpg")
        im.convert("RGB").save(out, "JPEG", quality=90)
        return out
    except Exception:
        return None


async def fetch_thumbnail(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.get(url, ssl=_ssl()) as r:
                raw = await r.read()
        if len(raw) <= 100:
            return None
        rawp = tmp_path("raw", ".img")
        with open(rawp, "wb") as f:
            f.write(raw)
        out = await asyncio.to_thread(_make_jpeg, rawp)
        safe_unlink(rawp)
        return out
    except Exception as e:
        log.error("fetch_thumbnail(%s): %s", url, e)
        return None


def _blur(src: str, dst: str, pct: int) -> bool:
    from PIL import Image, ImageFilter
    try:
        im = Image.open(src).convert("RGB")
        radius = max(1, round(pct / 3.33))
        im.filter(ImageFilter.GaussianBlur(radius)).save(dst, "JPEG", quality=90)
        return True
    except Exception as e:
        log.error("blur failed: %s", e)
        return False


async def make_blurred(src: str, pct: int) -> str:
    dst = tmp_path("blur", ".jpg")
    if not await asyncio.to_thread(_blur, src, dst, pct):
        import shutil
        shutil.copyfile(src, dst)
    return dst


def _small(src: str, dst: str) -> bool:
    """Telegram wants video thumbnails <= 320px JPEG."""
    from PIL import Image
    try:
        im = Image.open(src).convert("RGB")
        im.thumbnail((320, 320))
        im.save(dst, "JPEG", quality=85)
        return True
    except Exception:
        return False


async def video_thumb(src: str) -> str:
    dst = tmp_path("vthumb", ".jpg")
    return dst if await asyncio.to_thread(_small, src, dst) else src
