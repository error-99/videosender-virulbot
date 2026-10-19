"""One-time import of the OLD MySQL data (PHP bot / previous Python version) into Firebase Firestore.

    pip install pymysql
    # put MYSQL_HOST / MYSQL_PORT / MYSQL_DB / MYSQL_USER / MYSQL_PASS in .env (see .env.example)
    python tools/import_from_mysql.py --dry-run      # only shows what would be copied
    python tools/import_from_mysql.py                # copy everything
    python tools/import_from_mysql.py --bot mybot    # copy a single bot

Safe to run more than once: the same documents are simply overwritten. MySQL is only READ, never changed.
MySQL timestamps carry no timezone, so they are treated as UTC.
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import config, db  # noqa: E402


def utc(v):
    if isinstance(v, datetime):
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v
    return None


def jload(s, default):
    try:
        v = json.loads(s) if isinstance(s, (str, bytes)) and s else default
        return v if v is not None else default
    except Exception:
        return default


def read_mysql(params: dict, only_bot: str = None) -> dict:
    import pymysql
    conn = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, charset="utf8mb4", **params)
    out = {"bots": [], "users": [], "chats": [], "history": [], "sent": [], "broadcasts": []}
    where, args = (" WHERE id=%s", (only_bot,)) if only_bot else ("", ())
    bw, ba = (" WHERE bot_id=%s", (only_bot,)) if only_bot else ("", ())
    with conn.cursor() as cur:
        def q(sql, a=()):
            try:
                cur.execute(sql, a)
                return cur.fetchall()
            except Exception as e:  # table missing in old installs
                print(f"  (skipped: {e.args[-1] if e.args else e})")
                return []
        out["bots"] = q("SELECT * FROM bots" + where, args)
        out["users"] = q("SELECT * FROM bot_users" + bw, ba)
        out["chats"] = q("SELECT * FROM bot_chats" + bw, ba)
        out["history"] = q("SELECT bot_id, channel, message_id FROM channel_history" + bw + " ORDER BY id", ba)
        out["sent"] = q("SELECT * FROM cron_sent_videos" + bw, ba)
        out["broadcasts"] = q("SELECT * FROM broadcasts" + bw, ba)
    conn.close()
    return out


def bot_doc(r: dict) -> dict:
    cfg = jload(r.get("config"), {})
    return {"name": r.get("name") or r["id"], "enabled": bool(r.get("enabled", 1)), "token": r.get("token") or "",
            "admin_id": str(r.get("admin_id") or ""), "cron_secret": r.get("cron_secret") or "",
            "admin_state": r.get("admin_state") or "", "created_at": utc(r.get("created_at")) or datetime.now(timezone.utc),
            "config": {k: cfg.get(k, {}) for k in ("telegram", "apis", "settings", "messages")}}


async def write_firestore(data: dict, client, dry_run: bool = False, log=print) -> dict:
    counts = {k: 0 for k in ("bots", "users", "chats", "channels", "sent", "broadcasts", "logs")}
    batch, pending = client.batch(), 0

    async def put(ref, doc):
        nonlocal batch, pending
        if not dry_run:
            batch.set(ref, doc)
            pending += 1
            if pending >= 400:
                await batch.commit()
                batch, pending = client.batch(), 0

    db.client = client  # reuse the app's reference helpers (sub(), safe_id(), ...)
    for r in data["bots"]:
        await put(client.collection(db.BOTS).document(str(r["id"])), bot_doc(r))
        counts["bots"] += 1
        for i, entry in enumerate(jload(r.get("logs"), [])[-100:]):
            text = entry if isinstance(entry, str) else (entry.get("msg") or entry.get("message") or entry.get("text") or json.dumps(entry, ensure_ascii=False))
            ts = utc(datetime.fromtimestamp(entry["time"], timezone.utc)) if isinstance(entry, dict) and isinstance(entry.get("time"), (int, float)) else datetime.now(timezone.utc)
            await put(db.sub(str(r["id"]), "logs").document(f"imported-{i:04d}"), {"text": str(text)[:500], "level": "info", "created_at": ts})
            counts["logs"] += 1
    for u in data["users"]:
        un = u.get("username")
        await put(db.sub(u["bot_id"], "users").document(str(int(u["user_id"]))), {
            "username": un, "username_lc": (un or "").lower(), "first_name": u.get("first_name") or "",
            "joined_at": utc(u.get("joined_at")) or datetime.now(timezone.utc), "last_seen": utc(u.get("last_seen")),
            "status": u.get("status") or "active", "blocked_at": utc(u.get("blocked_at")), "source": u.get("source")})
        counts["users"] += 1
    for c in data["chats"]:
        await put(db.sub(c["bot_id"], "chats").document(db.safe_id(c["chat_id"])), {
            "chat_id": str(c["chat_id"]), "chat_type": c.get("chat_type") or "group", "title": c.get("title"),
            "updated_at": utc(c.get("updated_at")) or datetime.now(timezone.utc)})
        counts["chats"] += 1
    hist: dict = {}
    for h in data["history"]:
        hist.setdefault((h["bot_id"], str(h["channel"])), []).append(int(h["message_id"]))
    for (bid, ch), ids in hist.items():
        await put(db.sub(bid, "channel_history").document(db.safe_id(ch)), {"channel": ch, "ids": ids[-500:], "updated_at": datetime.now(timezone.utc)})
        counts["channels"] += 1
    for s in data["sent"]:
        await put(db.sub(s["bot_id"], "sent_videos").document(db._sent_id(s["source"], s["video_key"])),
                  {"source": s["source"], "key": s["video_key"], "sent_at": utc(s.get("sent_at")) or datetime.now(timezone.utc)})
        counts["sent"] += 1
    for i, b in enumerate(data["broadcasts"]):
        await put(db.sub(b["bot_id"], "broadcasts").document(f"imported-{b.get('id', i)}"), {
            "total": int(b.get("total") or 0), "sent": int(b.get("sent") or 0), "blocked": int(b.get("blocked") or 0),
            "failed": int(b.get("failed") or 0), "created_at": utc(b.get("created_at")) or datetime.now(timezone.utc)})
        counts["broadcasts"] += 1
    if not dry_run and pending:
        await batch.commit()
    return counts


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--bot", help="only this bot id")
    a = ap.parse_args()
    params = dict(host=os.getenv("MYSQL_HOST", "localhost"), port=int(os.getenv("MYSQL_PORT", "3306")),
                  database=os.getenv("MYSQL_DB", ""), user=os.getenv("MYSQL_USER", ""), password=os.getenv("MYSQL_PASS", ""))
    if not params["database"] or not params["user"]:
        sys.exit("Set MYSQL_HOST / MYSQL_DB / MYSQL_USER / MYSQL_PASS in .env first.")
    print("Reading MySQL ...")
    data = read_mysql(params, a.bot)
    print({k: len(v) for k, v in data.items()})
    client = db.make_client()
    counts = await write_firestore(data, client, a.dry_run)
    print(("DRY RUN - nothing written. " if a.dry_run else "Imported into Firestore: ") + str(counts))


if __name__ == "__main__":
    asyncio.run(main())
