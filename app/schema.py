"""Data model shared by bot_server and admin_server.
KEEP THIS FILE IDENTICAL IN BOTH PROJECTS (it is the contract between them).

Firestore layout
  bots/{bot_id}                       name, enabled, token, admin_id, cron_secret, admin_state, created_at,
                                      config{telegram, apis{old,vid65}, settings, messages}
  bots/{bot_id}/users|chats|channel_history|sent_videos|broadcasts|logs|pending_groups/{id}
  _commands/{id}                      admin_server -> bot_server requests (run cron, broadcast, ...)
  _meta/runtime                       bot_server -> admin_server live status (heartbeat, running bots)
"""
import copy
import hashlib
import re
from datetime import datetime
from typing import Optional

BOTS = "bots"
META = "_meta"
COMMANDS = "_commands"
SUBCOLLECTIONS = ("users", "chats", "channel_history", "sent_videos", "broadcasts", "logs", "pending_groups")


def safe_id(s) -> str:
    return re.sub(r"[/\\\s]", "_", str(s)) or "_"


def sent_id(source: str, key: str) -> str:
    return hashlib.sha1(f"{source}|{key}".encode()).hexdigest()[:40]


def iso(v) -> Optional[str]:
    return v.isoformat() if isinstance(v, datetime) else (None if v is None else str(v))


def groups_of(cfg: dict) -> list:
    seen: list = []
    for src in ("old", "vid65"):
        for g in (cfg.get("apis", {}).get(src, {}) or {}).get("groups", []) or []:
            if str(g) not in seen:
                seen.append(str(g))
    return seen


def normalize(b: dict) -> dict:
    """Make sure every section the code reads exists."""
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


def row_to_bot(doc_id: str, d: dict) -> dict:
    cfg = copy.deepcopy(d.get("config") or {})
    cfg.update({
        "id": str(doc_id), "name": str(d.get("name") or doc_id), "enabled": bool(d.get("enabled", True)),
        "token": str(d.get("token") or ""), "admin_id": str(d.get("admin_id") or ""),
        "cron_secret": str(d.get("cron_secret") or ""), "admin_state": str(d.get("admin_state") or ""),
        "created_at": d.get("created_at"),
    })
    return normalize(cfg)
