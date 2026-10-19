"""Full backup / restore of everything the app stores in Firestore (all bots + their subcollections)."""
import asyncio
import gzip
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from . import config, db

log = logging.getLogger("backup")
FORMAT = "tgbot-firestore-backup"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.json(\.gz)?$")
_lock = asyncio.Lock()


# ---------------------------------------------------------------- (de)serialisation
def enc(v):
    if isinstance(v, datetime):
        return {"__ts__": v.astimezone(timezone.utc).isoformat()}
    if isinstance(v, dict):
        return {k: enc(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [enc(x) for x in v]
    return v


def dec(v):
    if isinstance(v, dict):
        if set(v) == {"__ts__"}:
            return datetime.fromisoformat(v["__ts__"])
        return {k: dec(x) for k, x in v.items()}
    if isinstance(v, list):
        return [dec(x) for x in v]
    return v


def safe_name(name: str) -> Optional[Path]:
    if not NAME_RE.match(name or ""):
        return None
    return config.BACKUP_DIR / name


# ---------------------------------------------------------------- create
async def create(prefix: str = "backup", include_secrets: bool = True, gz: bool = False) -> dict:
    async with _lock:
        config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        name = f"{prefix}-{stamp}.json" + (".gz" if gz else "")
        path = config.BACKUP_DIR / name
        tmp = path.with_suffix(path.suffix + ".part")
        opener = (lambda p: gzip.open(p, "wt", encoding="utf-8")) if gz else (lambda p: open(p, "w", encoding="utf-8"))
        counts = {"bots": 0}
        f = opener(tmp)
        try:
            header = {"format": FORMAT, "version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                      "project": getattr(db.client, "project", ""), "app_version": config.VERSION,
                      "include_secrets": include_secrets}
            f.write(json.dumps(header, ensure_ascii=False)[:-1] + ',"bots":[')
            first = True
            async for snap in db.client.collection(db.BOTS).stream():
                data = snap.to_dict() or {}
                if not include_secrets:
                    data["token"], data["cron_secret"] = "", ""
                cols = {}
                for c in db.SUBCOLLECTIONS:
                    rows = []
                    async for d in db.sub(snap.id, c).stream():
                        rows.append({"id": d.id, "data": enc(d.to_dict() or {})})
                    cols[c] = rows
                    counts[c] = counts.get(c, 0) + len(rows)
                chunk = json.dumps({"id": snap.id, "data": enc(data), "collections": cols}, ensure_ascii=False)
                await asyncio.to_thread(f.write, ("" if first else ",") + chunk)
                first = False
                counts["bots"] += 1
            f.write("]}")
        finally:
            f.close()
        tmp.replace(path)
        log.info("backup created: %s %s", name, counts)
        return {"name": name, "size": path.stat().st_size, "counts": counts, "created_at": header["created_at"]}


# ---------------------------------------------------------------- list / delete / prune
def list_backups() -> list:
    out = []
    if config.BACKUP_DIR.is_dir():
        for p in config.BACKUP_DIR.iterdir():
            if p.is_file() and NAME_RE.match(p.name):
                st = p.stat()
                kind = p.name.split("-")[0] if p.name.split("-")[0] in ("auto", "pre") else "manual"
                out.append({"name": p.name, "size": st.st_size, "kind": kind,
                            "modified": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    out.sort(key=lambda x: x["modified"], reverse=True)
    return out


def delete_backup(name: str) -> bool:
    p = safe_name(name)
    if p and p.is_file():
        p.unlink()
        return True
    return False


def prune_auto() -> int:
    autos = [b for b in list_backups() if b["kind"] == "auto"]
    n = 0
    for b in autos[max(1, config.BACKUP_KEEP):]:
        n += bool(delete_backup(b["name"]))
    return n


# ---------------------------------------------------------------- restore
def parse(raw: bytes) -> dict:
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or data.get("format") != FORMAT or not isinstance(data.get("bots"), list):
        raise ValueError("This file is not a backup made by this app")
    return data


async def restore(raw: bytes) -> dict:
    """Merge a backup into Firestore: documents in the file overwrite the same documents in the
    database; documents that are NOT in the file are left alone."""
    data = parse(raw)
    async with _lock:
        counts = {"bots": 0}
        batch, pending = db.client.batch(), 0

        async def flush():
            nonlocal batch, pending
            if pending:
                await batch.commit()
                batch, pending = db.client.batch(), 0

        async def put(ref, doc):
            nonlocal pending
            batch.set(ref, doc)
            pending += 1
            if pending >= 400:
                await flush()

        for b in data["bots"]:
            bid = str(b["id"])
            await put(db.bot_ref(bid), dec(b.get("data") or {}))
            counts["bots"] += 1
            for cname, rows in (b.get("collections") or {}).items():
                if cname not in db.SUBCOLLECTIONS:
                    continue
                for r in rows:
                    await put(db.sub(bid, cname).document(str(r["id"])), dec(r.get("data") or {}))
                counts[cname] = counts.get(cname, 0) + len(rows)
        await flush()
        db.invalidate()
        log.info("restore finished: %s", counts)
        return counts


async def auto_loop() -> None:
    """Background task: periodic automatic backup + housekeeping."""
    if config.AUTO_BACKUP_HOURS <= 0:
        log.info("automatic backups are OFF (AUTO_BACKUP_HOURS=0)")
    while True:
        try:
            if config.AUTO_BACKUP_HOURS > 0:
                await asyncio.sleep(120)  # let the app settle after start
                autos = [b for b in list_backups() if b["kind"] == "auto"]
                due = True
                if autos:
                    last = datetime.fromisoformat(autos[0]["modified"])
                    due = (datetime.now(timezone.utc) - last).total_seconds() >= config.AUTO_BACKUP_HOURS * 3600
                if due:
                    await create("auto", include_secrets=True, gz=True)
                    prune_auto()
            else:
                await asyncio.sleep(3600)
            await db.prune_logs(30)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("auto backup / housekeeping failed")
        await asyncio.sleep(max(600, config.AUTO_BACKUP_HOURS * 3600 / 6) if config.AUTO_BACKUP_HOURS > 0 else 3600)
