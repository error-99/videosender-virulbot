"""Firestore data layer of the BOT server (see schema.py for the layout)."""
import asyncio
import copy
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from google.api_core.exceptions import AlreadyExists
from google.cloud.firestore_v1 import ArrayRemove, ArrayUnion
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.transforms import SERVER_TIMESTAMP

from . import config
from .schema import BOTS, COMMANDS, META, row_to_bot, safe_id, sent_id

log = logging.getLogger("db")
client: Any = None
_cache: dict = {}
_seen: dict = {}
_stats_cache: dict = {}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(config.TIMEZONE_NAME)
    except Exception:
        return timezone.utc


async def init(c: Any = None) -> None:
    global client
    if c is None:
        from .firebase import make_client
        c = make_client()
    client = c
    await client.collection(META).document("bot_server").set(
        {"last_start": SERVER_TIMESTAMP, "version": config.VERSION}, merge=True)
    log.info("Firestore connected (project=%s)", getattr(client, "project", "?"))


async def close() -> None:
    try:
        if client is not None and hasattr(client, "close"):
            r = client.close()
            if hasattr(r, "__await__"):
                await r
    except Exception:
        pass


def bot_ref(bot_id: str):
    return client.collection(BOTS).document(str(bot_id))


def sub(bot_id: str, name: str):
    return bot_ref(bot_id).collection(name)


# ------------------------------------------------------------------ bots
def invalidate(bot_id: Optional[str] = None) -> None:
    _cache.clear() if bot_id is None else _cache.pop(str(bot_id), None)


async def find_bot(bot_id: str, fresh: bool = False) -> Optional[dict]:
    bot_id = str(bot_id)
    hit = _cache.get(bot_id)
    if hit and not fresh and time.monotonic() - hit[0] < config.CONFIG_CACHE_TTL:
        return copy.deepcopy(hit[1])
    snap = await bot_ref(bot_id).get()
    if not snap.exists:
        _cache.pop(bot_id, None)
        return None
    cfg = row_to_bot(snap.id, snap.to_dict() or {})
    _cache[bot_id] = (time.monotonic(), cfg)
    return copy.deepcopy(cfg)


async def list_bots() -> list:
    out = []
    async for snap in client.collection(BOTS).stream():
        cfg = row_to_bot(snap.id, snap.to_dict() or {})
        _cache[cfg["id"]] = (time.monotonic(), cfg)
        out.append(copy.deepcopy(cfg))
    return out


async def update_bot(bot_id: str, updates: dict) -> None:
    """Dotted-path update: only the named fields change (cron tracking etc. stay intact)."""
    await bot_ref(bot_id).update(dict(updates, updated_at=SERVER_TIMESTAMP))
    invalidate(bot_id)


def get_tracking(cfg: dict) -> dict:
    apis = cfg.get("apis", {}) or {}
    old, v65 = apis.get("old", {}) or {}, apis.get("vid65", {}) or {}
    return {
        "old_page": int(old.get("current_page") or 203),
        "old_last_slug": str(old.get("last_slug") or ""),
        "vid65_page": int(v65.get("current_page") or 1),
        "vid65_index": int(v65.get("current_index") or 0),
        "mode": str((cfg.get("settings", {}) or {}).get("mode") or "ON"),
    }


async def save_tracking(bot_id: str, t: dict) -> bool:
    try:
        await update_bot(bot_id, {
            "config.apis.old.current_page": int(t.get("old_page", 203)),
            "config.apis.old.last_slug": str(t.get("old_last_slug", "")),
            "config.apis.vid65.current_page": int(t.get("vid65_page", 1)),
            "config.apis.vid65.current_index": int(t.get("vid65_index", 0)),
        })
        return True
    except Exception:
        log.exception("save_tracking(%s) failed", bot_id)
        return False


async def set_mode(bot_id: str, mode: str) -> bool:
    try:
        await update_bot(bot_id, {"config.settings.mode": mode})
        return True
    except Exception:
        log.exception("set_mode failed")
        return False


async def add_group(bot_id: str, chat_id: str) -> bool:
    try:
        await update_bot(bot_id, {"config.apis.old.groups": ArrayUnion([str(chat_id)]),
                                  "config.apis.vid65.groups": ArrayUnion([str(chat_id)])})
        return True
    except Exception:
        log.exception("add_group failed")
        return False


async def remove_group(bot_id: str, chat_id: str) -> bool:
    try:
        await update_bot(bot_id, {"config.apis.old.groups": ArrayRemove([str(chat_id)]),
                                  "config.apis.vid65.groups": ArrayRemove([str(chat_id)])})
        return True
    except Exception:
        log.exception("remove_group failed")
        return False


async def add_pending_group(bot_id: str, chat_id, title: str, by_id, by_name: str, ttl: int) -> None:
    await sub(bot_id, "pending_groups").document(safe_id(chat_id)).set({
        "chat_id": str(chat_id), "title": title, "added_by_id": str(by_id or ""), "added_by": by_name,
        "created_at": SERVER_TIMESTAMP, "expires_at": utcnow() + timedelta(seconds=ttl)})


async def claim_pending_group(bot_id: str, chat_id) -> Optional[dict]:
    """Take (and delete) the pending request. Only the caller that gets a dict may act on it."""
    ref = sub(bot_id, "pending_groups").document(safe_id(chat_id))
    snap = await ref.get()
    if not snap.exists:
        return None
    await ref.delete()
    return {"chat_id": str(chat_id), **(snap.to_dict() or {})}


async def expired_pending_groups(bot_id: str) -> list:
    q = sub(bot_id, "pending_groups").where(filter=FieldFilter("expires_at", "<", utcnow()))
    return [{"chat_id": (s.to_dict() or {}).get("chat_id") or s.id} async for s in q.stream()]


async def get_admin_state(bot_id: str) -> str:
    snap = await bot_ref(bot_id).get()
    return str((snap.to_dict() or {}).get("admin_state") or "") if snap.exists else ""


async def set_admin_state(bot_id: str, value: str) -> None:
    await bot_ref(bot_id).update({"admin_state": value})


# ------------------------------------------------------------------ users
async def touch_user(bot_id: str, user_id: int, username: Optional[str], first_name: Optional[str],
                     source: Optional[str] = None) -> bool:
    """Register / refresh a user. Returns True only when the user is new."""
    username = None if username in (None, "", "None") else str(username)
    first_name = first_name or ""
    key = (str(bot_id), int(user_id))
    c = _seen.get(key)
    if source is None and c and time.monotonic() - c[0] < 60 and c[1] == username and c[2] == first_name:
        return False
    ref = sub(bot_id, "users").document(str(int(user_id)))
    snap = await ref.get()
    is_new = False
    if not snap.exists:
        try:
            await ref.create({"username": username, "username_lc": (username or "").lower(), "first_name": first_name,
                              "joined_at": SERVER_TIMESTAMP, "last_seen": SERVER_TIMESTAMP, "status": "active",
                              "blocked_at": None, "source": source[:64] if source else None})
            is_new = True
        except AlreadyExists:
            is_new = False
    else:
        d = snap.to_dict() or {}
        upd = {"last_seen": SERVER_TIMESTAMP}
        if d.get("username") != username:
            upd["username"], upd["username_lc"] = username, (username or "").lower()
        if d.get("first_name") != first_name:
            upd["first_name"] = first_name
        if d.get("status") != "active":
            upd["status"], upd["blocked_at"] = "active", None
        if source and not d.get("source"):
            upd["source"] = source[:64]
        await ref.update(upd)
    if len(_seen) > 50000:
        _seen.clear()
    _seen[key] = (time.monotonic(), username, first_name)
    return is_new


async def _set_status(bot_id: str, user_id: int, status: str) -> bool:
    ref = sub(bot_id, "users").document(str(int(user_id)))
    snap = await ref.get()
    if not snap.exists or (snap.to_dict() or {}).get("status") == status:
        return False
    await ref.update({"status": status, "blocked_at": SERVER_TIMESTAMP if status == "blocked" else None})
    _seen.pop((str(bot_id), int(user_id)), None)
    return True


async def mark_blocked(bot_id: str, user_id: int) -> bool:
    return await _set_status(bot_id, user_id, "blocked")


async def mark_active(bot_id: str, user_id: int) -> bool:
    return await _set_status(bot_id, user_id, "active")


async def active_user_ids(bot_id: str) -> list:
    q = sub(bot_id, "users").where(filter=FieldFilter("status", "==", "active")).select(["status"])
    return sorted([int(d.id) async for d in q.stream()])


async def _count(q) -> int:
    res = await q.count().get()
    return int(res[0][0].value)


async def user_stats(bot_id: str, ttl: float = 20) -> dict:
    ck = f"stats:{bot_id}"
    hit = _stats_cache.get(ck)
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    users = sub(bot_id, "users")
    t = utcnow()
    today = datetime.now(tz()).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    f = lambda field, op, v: users.where(filter=FieldFilter(field, op, v))
    total, blocked, new_today, a24 = await asyncio.gather(
        _count(users), _count(f("status", "==", "blocked")), _count(f("joined_at", ">=", today)),
        _count(f("last_seen", ">=", t - timedelta(days=1))))
    out = dict(total=total, active=max(0, total - blocked), blocked=blocked, new_today=new_today, active_24h=a24)
    _stats_cache[ck] = (time.monotonic(), out)
    return out


# ------------------------------------------------------------------ chats / history / sent / broadcasts / logs
async def upsert_chat(bot_id: str, chat_id, chat_type: str, title: Optional[str]) -> None:
    await sub(bot_id, "chats").document(safe_id(chat_id)).set(
        {"chat_id": str(chat_id), "chat_type": chat_type, "title": title, "updated_at": SERVER_TIMESTAMP}, merge=True)


async def add_channel_post(bot_id: str, channel: str, message_id: int, limit: int) -> list:
    ref = sub(bot_id, "channel_history").document(safe_id(channel))
    snap = await ref.get()
    ids = [int(x) for x in ((snap.to_dict() or {}).get("ids") or [])] if snap.exists else []
    ids.append(int(message_id))
    limit = max(1, int(limit))
    excess, ids = (ids[:-limit], ids[-limit:]) if len(ids) > limit else ([], ids)
    await ref.set({"channel": str(channel), "ids": ids, "updated_at": SERVER_TIMESTAMP})
    return excess


async def cron_was_sent(bot_id: str, source: str, key: str) -> bool:
    if not key:
        return False
    return (await sub(bot_id, "sent_videos").document(sent_id(source, key)).get()).exists


async def cron_mark_sent(bot_id: str, source: str, key: str) -> None:
    if key:
        await sub(bot_id, "sent_videos").document(sent_id(source, key)).set(
            {"source": source, "key": key, "sent_at": SERVER_TIMESTAMP})


async def log_broadcast(bot_id: str, total: int, sent: int, blocked: int, failed: int) -> None:
    await sub(bot_id, "broadcasts").add({"total": total, "sent": sent, "blocked": blocked, "failed": failed,
                                         "created_at": SERVER_TIMESTAMP})


async def log_event(bot_id: str, text: str, level: str = "info") -> None:
    try:
        await sub(bot_id, "logs").add({"text": str(text)[:500], "level": level, "created_at": SERVER_TIMESTAMP})
    except Exception:
        log.warning("log_event failed", exc_info=True)


# ------------------------------------------------------------------ panel -> bot commands, bot -> panel runtime
async def pending_commands() -> list:
    q = client.collection(COMMANDS).where(filter=FieldFilter("status", "==", "pending")).limit(25)
    rows = [{"id": s.id, **(s.to_dict() or {})} async for s in q.stream()]
    rows.sort(key=lambda r: r.get("created_at") or utcnow())
    return rows


async def update_command(cmd_id: str, **fields) -> None:
    fields["updated_at"] = SERVER_TIMESTAMP
    await client.collection(COMMANDS).document(cmd_id).update(fields)


async def cleanup_commands(hours: int = 24) -> None:
    cutoff = utcnow() - timedelta(hours=hours)
    q = client.collection(COMMANDS).where(filter=FieldFilter("created_at", "<", cutoff)).limit(200)
    async for s in q.stream():
        await s.reference.delete()


async def write_runtime(bots: dict) -> None:
    await client.collection(META).document("runtime").set(
        {"heartbeat": SERVER_TIMESTAMP, "version": config.VERSION, "bots": bots})
