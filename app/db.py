"""Firestore data layer.

Data model
----------
bots/{bot_id}                         name, enabled, token, admin_id, cron_secret, admin_state,
                                      config{telegram, apis{old,vid65}, settings, messages}, created_at
bots/{bot_id}/users/{user_id}         username, username_lc, first_name, joined_at, last_seen,
                                      status(active|blocked), blocked_at, source
bots/{bot_id}/chats/{chat_id}         chat_type, title, updated_at
bots/{bot_id}/channel_history/{key}   channel, ids[]            (last N message ids per channel)
bots/{bot_id}/sent_videos/{hash}      source, key, sent_at      (duplicate-video protection)
bots/{bot_id}/broadcasts/{auto}       total, sent, blocked, failed, created_at
bots/{bot_id}/logs/{auto}             text, level, created_at

Only single-field queries are used, so NO composite indexes have to be created.
"""
import copy
import hashlib
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from google.api_core.exceptions import AlreadyExists, NotFound
from google.cloud.firestore_v1 import ArrayRemove, ArrayUnion, AsyncClient
from google.cloud.firestore_v1.base_query import FieldFilter
from google.cloud.firestore_v1.transforms import SERVER_TIMESTAMP

from . import config

log = logging.getLogger("db")
client: Any = None
BOTS = "bots"
SUBCOLLECTIONS = ("users", "chats", "channel_history", "sent_videos", "broadcasts", "logs")

_cache: dict = {}          # bot_id -> (monotonic_ts, cfg)
_seen: dict = {}           # (bot_id, user_id) -> (monotonic_ts, username, first_name)
_stats_cache: dict = {}    # key -> (monotonic_ts, value)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(config.TIMEZONE_NAME)
    except Exception:
        return timezone.utc


def iso(v) -> Optional[str]:
    return v.isoformat() if isinstance(v, datetime) else (None if v is None else str(v))


# ------------------------------------------------------------------ connection
def make_client():
    from google.oauth2 import service_account
    if config.FIREBASE_CREDENTIALS_JSON:
        info = json.loads(config.FIREBASE_CREDENTIALS_JSON)
        creds = service_account.Credentials.from_service_account_info(info)
    elif config.FIREBASE_CREDENTIALS.is_file():
        info = json.loads(config.FIREBASE_CREDENTIALS.read_text(encoding="utf-8"))
        creds = service_account.Credentials.from_service_account_info(info)
    else:
        raise RuntimeError(
            f"Firebase key not found: {config.FIREBASE_CREDENTIALS}. Download a service-account JSON from "
            "Firebase console -> Project settings -> Service accounts, or set FIREBASE_CREDENTIALS_JSON.")
    return AsyncClient(project=info["project_id"], credentials=creds, database=config.FIRESTORE_DATABASE)


async def init(c: Any = None) -> None:
    """Connect and verify read + write access. `c` lets tests inject a fake client."""
    global client
    client = c or make_client()
    await client.collection("_meta").document("app").set(
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


def safe_id(s: str) -> str:
    return re.sub(r"[/\\\s]", "_", str(s)) or "_"


# ------------------------------------------------------------------ bots
def new_bot(bot_id: str) -> dict:
    return normalize({
        "id": bot_id, "name": bot_id, "enabled": True, "token": "", "admin_id": "", "cron_secret": "",
        "telegram": {"api_server": "", "api_server_fallback": "https://api.telegram.org"},
        "apis": {"old": {"url": "", "current_page": 203, "last_slug": "", "groups": [], "channels": []},
                 "vid65": {"url": "", "current_page": 1, "current_index": 0, "groups": [], "channels": []}},
        "settings": {"mode": "ON", "group_start_button": "ON", "max_channel_messages": 10,
                     "blur_percentage": 20, "max_size": 2147483648},
        "messages": {"start": "👋 Hello {first_name}!\n\nPress 🚀 Start to continue.",
                     "welcome_dm": "👋 Welcome {first_name}!\n\nPress 🚀 Start to register with the bot.",
                     "channel_caption": "🎬 <b>{title}</b>", "group_caption": "🎬 <b>{title}</b>"},
    })


def normalize(b: dict) -> dict:
    """Make sure every section the bot code reads exists."""
    b.setdefault("telegram", {})
    b["telegram"].setdefault("api_server", "")
    b["telegram"].setdefault("api_server_fallback", "https://api.telegram.org")
    apis = b.setdefault("apis", {})
    for src, extra in (("old", {"current_page": 203, "last_slug": ""}), ("vid65", {"current_page": 1, "current_index": 0})):
        a = apis.setdefault(src, {})
        a.setdefault("url", "")
        a.setdefault("groups", [])
        a.setdefault("channels", [])
        for k, v in extra.items():
            a.setdefault(k, v)
        a["groups"] = [str(x) for x in a["groups"] or []]
        a["channels"] = [str(x) for x in a["channels"] or []]
    s = b.setdefault("settings", {})
    s.setdefault("mode", "ON")
    s.setdefault("group_start_button", "ON")
    s.setdefault("max_channel_messages", 10)
    s.setdefault("blur_percentage", 20)
    s.setdefault("max_size", 2147483648)
    b.setdefault("messages", {})
    return b


def row_to_bot(doc_id: str, d: dict) -> dict:
    cfg = copy.deepcopy(d.get("config") or {})
    cfg.update({
        "id": str(doc_id), "name": str(d.get("name") or doc_id), "enabled": bool(d.get("enabled", True)),
        "token": str(d.get("token") or ""), "admin_id": str(d.get("admin_id") or ""),
        "cron_secret": str(d.get("cron_secret") or ""), "admin_state": str(d.get("admin_state") or ""),
        "created_at": d.get("created_at"),
    })
    return normalize(cfg)


def invalidate(bot_id: Optional[str] = None) -> None:
    if bot_id is None:
        _cache.clear()
    else:
        _cache.pop(str(bot_id), None)


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
    out.sort(key=lambda b: (b["created_at"] or utcnow(), b["id"]))
    return out


async def create_bot(bot: dict) -> None:
    """Raises ValueError if the id already exists."""
    b = normalize(copy.deepcopy(bot))
    data = {"name": b["name"], "enabled": bool(b["enabled"]), "token": b["token"], "admin_id": b["admin_id"],
            "cron_secret": b["cron_secret"], "admin_state": "", "created_at": SERVER_TIMESTAMP,
            "config": {k: b[k] for k in ("telegram", "apis", "settings", "messages")}}
    try:
        await bot_ref(b["id"]).create(data)
    except AlreadyExists:
        raise ValueError("A bot with this ID already exists")
    invalidate(b["id"])


async def update_bot(bot_id: str, updates: dict) -> None:
    """`updates` uses Firestore dotted field paths, e.g. {'config.settings.mode': 'OFF'}.
    Only the named fields change - cron tracking and anything else stay untouched."""
    updates = dict(updates)
    updates["updated_at"] = SERVER_TIMESTAMP
    await bot_ref(bot_id).update(updates)
    invalidate(bot_id)


async def delete_collection(coll, batch_size: int = 300) -> int:
    n = 0
    while True:
        docs = [d async for d in coll.limit(batch_size).stream()]
        if not docs:
            return n
        batch = client.batch()
        for d in docs:
            batch.delete(d.reference)
        await batch.commit()
        n += len(docs)


async def delete_bot(bot_id: str) -> None:
    for name in SUBCOLLECTIONS:
        await delete_collection(sub(bot_id, name))
    await bot_ref(bot_id).delete()
    invalidate(bot_id)


# ------------------------------------------------------------------ tracking / mode / groups
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
        return False  # seen a moment ago, nothing changed - save a Firestore read+write
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


def _cached(key: str, ttl: float):
    hit = _stats_cache.get(key)
    return hit[1] if hit and time.monotonic() - hit[0] < ttl else None


async def user_stats(bot_id: str, ttl: float = 20) -> dict:
    ck = f"stats:{bot_id}"
    hit = _cached(ck, ttl)
    if hit is not None:
        return hit
    import asyncio
    users = sub(bot_id, "users")
    t = utcnow()
    today = datetime.now(tz()).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    f = lambda field, op, v: users.where(filter=FieldFilter(field, op, v))
    total, blocked, new_today, new_7d, a24, a7 = await asyncio.gather(
        _count(users), _count(f("status", "==", "blocked")), _count(f("joined_at", ">=", today)),
        _count(f("joined_at", ">=", t - timedelta(days=7))), _count(f("last_seen", ">=", t - timedelta(days=1))),
        _count(f("last_seen", ">=", t - timedelta(days=7))))
    out = dict(total=total, active=max(0, total - blocked), blocked=blocked, new_today=new_today,
               new_7d=new_7d, active_24h=a24, active_7d=a7)
    _stats_cache[ck] = (time.monotonic(), out)
    return out


async def daily_new_users(bot_id: str, days: int = 14, ttl: float = 60) -> list:
    """[{date, count}] for the last `days` days (oldest first), in the configured timezone."""
    ck = f"daily:{bot_id}:{days}"
    hit = _cached(ck, ttl)
    if hit is not None:
        return hit
    import asyncio
    zone = tz()
    start = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    users = sub(bot_id, "users")

    async def one(i: int) -> dict:
        a = start + timedelta(days=i)
        b = start + timedelta(days=i + 1)
        n = await _count(users.where(filter=FieldFilter("joined_at", ">=", a.astimezone(timezone.utc)))
                         .where(filter=FieldFilter("joined_at", "<", b.astimezone(timezone.utc))))
        return {"date": a.strftime("%Y-%m-%d"), "count": n}

    out = list(await asyncio.gather(*[one(i) for i in range(days)]))
    _stats_cache[ck] = (time.monotonic(), out)
    return out


def user_row(snap) -> dict:
    d = snap.to_dict() or {}
    return {"id": int(snap.id) if snap.id.isdigit() else snap.id, "username": d.get("username"),
            "first_name": d.get("first_name"), "status": d.get("status", "active"), "source": d.get("source"),
            "joined_at": iso(d.get("joined_at")), "last_seen": iso(d.get("last_seen")),
            "blocked_at": iso(d.get("blocked_at"))}


async def list_users(bot_id: str, limit: int = 50, after: Optional[str] = None, q: str = "", status: str = "") -> dict:
    users = sub(bot_id, "users")
    limit = max(1, min(int(limit), 200))
    q = (q or "").strip().lstrip("@")
    if q:
        if q.isdigit():
            snap = await users.document(q).get()
            rows = [user_row(snap)] if snap.exists else []
        else:
            ql = q.lower()
            query = (users.where(filter=FieldFilter("username_lc", ">=", ql))
                     .where(filter=FieldFilter("username_lc", "<", ql + "\uf8ff")).limit(limit))
            rows = [user_row(s) async for s in query.stream()]
        if status:
            rows = [r for r in rows if r["status"] == status]
        return {"users": rows, "next": None}
    if status in ("active", "blocked"):
        # equality filter only (no ordering) -> no composite index needed; sorted here instead
        query = users.where(filter=FieldFilter("status", "==", status)).limit(limit)
        rows = [user_row(s) async for s in query.stream()]
        rows.sort(key=lambda r: r["joined_at"] or "", reverse=True)
        return {"users": rows, "next": None}
    query = users.order_by("joined_at", direction="DESCENDING")
    if after:
        snap = await users.document(str(after)).get()
        if snap.exists:
            query = query.start_after(snap)
    docs = [s async for s in query.limit(limit + 1).stream()]
    more = len(docs) > limit
    docs = docs[:limit]
    return {"users": [user_row(s) for s in docs], "next": docs[-1].id if more and docs else None}


async def iter_users(bot_id: str):
    async for s in sub(bot_id, "users").stream():
        yield user_row(s)


# ------------------------------------------------------------------ chats / history / sent / broadcasts / logs
async def upsert_chat(bot_id: str, chat_id, chat_type: str, title: Optional[str]) -> None:
    await sub(bot_id, "chats").document(safe_id(chat_id)).set(
        {"chat_id": str(chat_id), "chat_type": chat_type, "title": title, "updated_at": SERVER_TIMESTAMP}, merge=True)


async def add_channel_post(bot_id: str, channel: str, message_id: int, limit: int) -> list:
    """Remember a channel post; returns the OLD message ids that exceed `limit` (caller deletes them)."""
    ref = sub(bot_id, "channel_history").document(safe_id(channel))
    snap = await ref.get()
    ids = [int(x) for x in ((snap.to_dict() or {}).get("ids") or [])] if snap.exists else []
    ids.append(int(message_id))
    limit = max(1, int(limit))
    excess, ids = (ids[:-limit], ids[-limit:]) if len(ids) > limit else ([], ids)
    await ref.set({"channel": str(channel), "ids": ids, "updated_at": SERVER_TIMESTAMP})
    return excess


def _sent_id(source: str, key: str) -> str:
    return hashlib.sha1(f"{source}|{key}".encode()).hexdigest()[:40]


async def cron_was_sent(bot_id: str, source: str, key: str) -> bool:
    if not key:
        return False
    return (await sub(bot_id, "sent_videos").document(_sent_id(source, key)).get()).exists


async def cron_mark_sent(bot_id: str, source: str, key: str) -> None:
    if key:
        await sub(bot_id, "sent_videos").document(_sent_id(source, key)).set(
            {"source": source, "key": key, "sent_at": SERVER_TIMESTAMP})


async def log_broadcast(bot_id: str, total: int, sent: int, blocked: int, failed: int) -> None:
    await sub(bot_id, "broadcasts").add({"total": total, "sent": sent, "blocked": blocked, "failed": failed,
                                         "created_at": SERVER_TIMESTAMP})
    _stats_cache.pop(f"bc:{bot_id}", None)


async def list_broadcasts(bot_id: str, limit: int = 5) -> list:
    q = sub(bot_id, "broadcasts").order_by("created_at", direction="DESCENDING").limit(limit)
    out = []
    async for s in q.stream():
        d = s.to_dict() or {}
        out.append({**{k: d.get(k, 0) for k in ("total", "sent", "blocked", "failed")}, "created_at": iso(d.get("created_at"))})
    return out


async def log_event(bot_id: str, text: str, level: str = "info") -> None:
    try:
        await sub(bot_id, "logs").add({"text": str(text)[:500], "level": level, "created_at": SERVER_TIMESTAMP})
    except Exception:
        log.warning("log_event failed", exc_info=True)


async def list_logs(bot_id: str, limit: int = 60) -> list:
    q = sub(bot_id, "logs").order_by("created_at", direction="DESCENDING").limit(limit)
    return [{"text": (s.to_dict() or {}).get("text", ""), "level": (s.to_dict() or {}).get("level", "info"),
             "created_at": iso((s.to_dict() or {}).get("created_at"))} async for s in q.stream()]


async def prune_logs(days: int = 30) -> int:
    cutoff = utcnow() - timedelta(days=days)
    total = 0
    async for b in client.collection(BOTS).select([]).stream():
        total += await delete_collection(sub(b.id, "logs").where(filter=FieldFilter("created_at", "<", cutoff)))
    return total
