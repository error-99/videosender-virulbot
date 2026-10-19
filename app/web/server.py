"""Admin web panel backend (aiohttp). Serves the single-page UI in ./static and a JSON API."""
import asyncio
import csv
import hashlib
import hmac
import io
import json
import logging
import platform
import re
import secrets
import time
import uuid
from typing import Optional

import aiohttp
from aiohttp import web

from .. import admin, backup, config, cron, db, group_manager
from ..runner import pick_server

log = logging.getLogger("web")
COOKIE = "tgadmin"
SESSION_DAYS = 7
STARTED = time.time()
JOBS: dict = {}
FAILS: dict = {}

BOT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,50}$")
TOKEN_RE = re.compile(r"^\d{1,20}:[A-Za-z0-9_-]{20,}$")
CHAT_RE = re.compile(r"^(-?\d+|@[A-Za-z0-9_]{3,})$")
URL_RE = re.compile(r"^https?://\S+$")


# ------------------------------------------------------------------ helpers
def jdumps(o) -> str:
    return json.dumps(o, default=lambda x: x.isoformat() if hasattr(x, "isoformat") else str(x), ensure_ascii=False)


def ok(**kw) -> web.Response:
    return web.json_response({"ok": True, **kw}, dumps=jdumps)


def fail(msg: str, status: int = 400, **kw) -> web.Response:
    return web.json_response({"ok": False, "error": msg, **kw}, status=status, dumps=jdumps)


def _sign(exp: int) -> str:
    return hmac.new(config.SESSION_SECRET.encode(), str(exp).encode(), hashlib.sha256).hexdigest()


def make_session() -> str:
    exp = int(time.time()) + SESSION_DAYS * 86400
    return f"{exp}.{_sign(exp)}"


def valid_session(tok: Optional[str]) -> bool:
    try:
        exp, sig = (tok or "").split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, _sign(int(exp)))
    except Exception:
        return False


def client_ip(request: web.Request) -> str:
    return request.headers.get("X-Forwarded-For", request.remote or "?").split(",")[0].strip()


async def body(request: web.Request) -> dict:
    try:
        d = await request.json()
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def groups_count(cfg: dict) -> int:
    return len(group_manager.groups_of(cfg))


def channels_count(cfg: dict) -> int:
    s = set()
    for src in ("old", "vid65"):
        s.update(str(c) for c in (cfg["apis"][src].get("channels") or []))
    return len(s)


def bot_public(cfg: dict, sup) -> dict:
    info = sup.info(cfg["id"])
    return {
        "id": cfg["id"], "name": cfg["name"], "enabled": cfg["enabled"], "token": cfg["token"],
        "admin_id": cfg["admin_id"], "cron_secret": cfg["cron_secret"], "telegram": cfg["telegram"],
        "apis": cfg["apis"], "settings": cfg["settings"], "messages": cfg["messages"],
        "created_at": cfg.get("created_at"), "running": info["running"], "username": info["username"],
        "error": info["error"], "cron_running": cron.is_running(cfg["id"]),
    }


# ------------------------------------------------------------------ middlewares
@web.middleware
async def errors_mw(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception as e:
        log.exception("API error on %s %s", request.method, request.path)
        return fail(f"Server error: {e}", 500)


@web.middleware
async def auth_mw(request: web.Request, handler):
    p = request.path
    if p.startswith("/api/") and p != "/api/login":
        if not valid_session(request.cookies.get(COOKIE)):
            return fail("Not logged in", 401)
        if request.method not in ("GET", "HEAD") and request.headers.get("X-Requested-With") != "tgadmin":
            return fail("Bad request origin", 403)
    return await handler(request)


# ------------------------------------------------------------------ auth
async def login(request: web.Request) -> web.Response:
    ip = client_ip(request)
    n, until = FAILS.get(ip, (0, 0))
    if until > time.time():
        return fail("Too many attempts. Try again in a few minutes.", 429)
    pw = str((await body(request)).get("password", ""))
    if not hmac.compare_digest(pw.encode(), config.ADMIN_PASSWORD.encode()):
        n += 1
        FAILS[ip] = (n, time.time() + 300 if n >= 8 else 0)
        await asyncio.sleep(1)
        return fail("Wrong password", 401)
    FAILS.pop(ip, None)
    r = ok()
    secure = request.secure or request.headers.get("X-Forwarded-Proto") == "https"
    r.set_cookie(COOKIE, make_session(), max_age=SESSION_DAYS * 86400, httponly=True, samesite="Strict", secure=secure)
    return r


async def logout(request: web.Request) -> web.Response:
    r = ok()
    r.del_cookie(COOKIE)
    return r


async def me(request: web.Request) -> web.Response:
    return ok(version=config.VERSION, timezone=config.TIMEZONE_NAME)


# ------------------------------------------------------------------ dashboard / bots
def make_handlers(sup):
    async def summary(cfg: dict) -> dict:
        st = await db.user_stats(cfg["id"])
        info = sup.info(cfg["id"])
        return {"id": cfg["id"], "name": cfg["name"], "enabled": cfg["enabled"], "running": info["running"],
                "username": info["username"], "error": info["error"], "users": st,
                "mode": cfg["settings"]["mode"], "group_start_button": cfg["settings"]["group_start_button"],
                "groups": groups_count(cfg), "channels": channels_count(cfg)}

    async def overview(request):
        bots = await db.list_bots()
        rows = await asyncio.gather(*[summary(b) for b in bots])
        dailies = await asyncio.gather(*[db.daily_new_users(b["id"], 14) for b in bots]) if bots else []
        days: dict = {}
        for d in dailies:
            for x in d:
                days[x["date"]] = days.get(x["date"], 0) + x["count"]
        if not days:
            from datetime import datetime, timedelta
            today = datetime.now(db.tz())
            days = {(today - timedelta(days=i)).strftime("%Y-%m-%d"): 0 for i in range(13, -1, -1)}
        tot = lambda k: sum(r["users"][k] for r in rows)
        return ok(bots=rows, daily=[{"date": k, "count": v} for k, v in sorted(days.items())],
                  totals={"bots": len(rows), "running": sum(1 for r in rows if r["running"]), "users": tot("total"),
                          "new_today": tot("new_today"), "blocked": tot("blocked"), "active_24h": tot("active_24h")})

    async def bots_list(request):
        bots = await db.list_bots()
        return ok(bots=await asyncio.gather(*[summary(b) for b in bots]))

    async def bot_get(request):
        cfg = await db.find_bot(request.match_info["id"], fresh=True)
        if not cfg:
            return fail("Bot not found", 404)
        st, daily, bcs = await asyncio.gather(db.user_stats(cfg["id"]), db.daily_new_users(cfg["id"], 14),
                                              db.list_broadcasts(cfg["id"], 5))
        return ok(bot=bot_public(cfg, sup), stats=st, daily=daily, broadcasts=bcs, tracking=db.get_tracking(cfg),
                  groups_total=groups_count(cfg))

    async def bot_create(request):
        d = await body(request)
        bid = str(d.get("id", "")).strip()
        if not BOT_ID_RE.match(bid):
            return fail("Bot ID may contain only letters, numbers, _ and - (max 50).")
        token = str(d.get("token", "")).strip()
        if token and not TOKEN_RE.match(token):
            return fail("That does not look like a Telegram bot token.")
        admin_id = str(d.get("admin_id", "")).strip()
        if admin_id and not re.fullmatch(r"-?\d+", admin_id):
            return fail("Admin ID must be a number (your Telegram user id).")
        b = db.new_bot(bid)
        b.update(name=str(d.get("name") or bid)[:80], token=token, admin_id=admin_id,
                 cron_secret=secrets.token_urlsafe(9), enabled=bool(d.get("enabled", True)))
        try:
            await db.create_bot(b)
        except ValueError as e:
            return fail(str(e), 409)
        await db.log_event(bid, "Bot created from the web panel")
        sup.poke()
        return ok(id=bid)

    def build_updates(d: dict) -> tuple:
        """Validate a (partial) settings payload -> Firestore dotted-path updates."""
        u: dict = {}
        if "name" in d:
            u["name"] = str(d["name"]).strip()[:80] or "Bot"
        if "enabled" in d:
            u["enabled"] = bool(d["enabled"])
        if "token" in d:
            t = str(d["token"]).strip()
            if t and not TOKEN_RE.match(t):
                return None, "That does not look like a Telegram bot token."
            u["token"] = t
        if "admin_id" in d:
            a = str(d["admin_id"]).strip()
            if a and not re.fullmatch(r"-?\d+", a):
                return None, "Admin ID must be a number."
            u["admin_id"] = a
        if "cron_secret" in d:
            u["cron_secret"] = str(d["cron_secret"]).strip()[:100]
        for k in ("api_server", "api_server_fallback"):
            if k in (d.get("telegram") or {}):
                v = str(d["telegram"][k]).strip().rstrip("/")
                if v and not URL_RE.match(v):
                    return None, "Telegram API server must start with http:// or https://"
                u[f"config.telegram.{k}"] = v
        s = d.get("settings") or {}
        if "mode" in s:
            u["config.settings.mode"] = "OFF" if str(s["mode"]).upper() == "OFF" else "ON"
        if "group_start_button" in s:
            u["config.settings.group_start_button"] = "OFF" if str(s["group_start_button"]).upper() == "OFF" else "ON"
        try:
            if "max_channel_messages" in s:
                u["config.settings.max_channel_messages"] = max(1, min(500, int(s["max_channel_messages"])))
            if "blur_percentage" in s:
                u["config.settings.blur_percentage"] = max(0, min(100, int(s["blur_percentage"])))
            if "max_size_mb" in s:
                u["config.settings.max_size"] = max(1, min(4096, int(float(s["max_size_mb"])))) * 1048576
        except (TypeError, ValueError):
            return None, "Numbers expected in the behaviour settings."
        for k, v in (d.get("messages") or {}).items():
            if k in ("start", "welcome_dm", "channel_caption", "group_caption"):
                u[f"config.messages.{k}"] = str(v)[:4000]
        for src in ("old", "vid65"):
            a = (d.get("apis") or {}).get(src) or {}
            if "url" in a:
                v = str(a["url"]).strip()
                if v and not URL_RE.match(v):
                    return None, f"{src.upper()} API URL must start with http:// or https://"
                u[f"config.apis.{src}.url"] = v
            for lk in ("groups", "channels"):
                if lk in a:
                    items = a[lk] if isinstance(a[lk], list) else re.split(r"[\s,]+", str(a[lk]))
                    items = [str(i).strip() for i in items if str(i).strip()]
                    bad = [i for i in items if not CHAT_RE.match(i)]
                    if bad:
                        return None, f"Invalid chat id/username in {src} {lk}: {bad[0]}"
                    u[f"config.apis.{src}.{lk}"] = list(dict.fromkeys(items))
        return u, ""

    async def bot_update(request):
        bid = request.match_info["id"]
        before = await db.find_bot(bid, fresh=True)
        if not before:
            return fail("Bot not found", 404)
        updates, msg = build_updates(await body(request))
        if updates is None:
            return fail(msg)
        if updates:
            await db.update_bot(bid, updates)
        extra: dict = {}
        was_on = before["settings"]["group_start_button"] != "OFF"
        now_on = updates.get("config.settings.group_start_button", before["settings"]["group_start_button"]) != "OFF"
        if was_on and not now_on:
            bot = sup.get_bot(bid)
            groups = group_manager.groups_of(before)
            if bot and groups:
                okc, bad = await group_manager.clear_group_keyboards(bot, groups)
                extra["sweep"] = {"cleared": okc, "failed": bad}
            await db.log_event(bid, "Group Start button switched OFF")
        elif not was_on and now_on:
            await db.log_event(bid, "Group Start button switched ON")
        if updates:
            await db.log_event(bid, "Settings saved from the web panel")
        sup.poke()
        return ok(**extra)

    async def bot_delete(request):
        bid = request.match_info["id"]
        if request.query.get("confirm") != bid:
            return fail("Type the bot ID to confirm deletion.")
        if not await db.find_bot(bid, fresh=True):
            return fail("Bot not found", 404)
        await db.delete_bot(bid)
        sup.poke()
        return ok()

    async def tracking_put(request):
        bid = request.match_info["id"]
        d = await body(request)
        try:
            u = {"config.apis.old.current_page": max(1, int(d["old_page"])),
                 "config.apis.old.last_slug": str(d.get("old_last_slug", "")).strip(),
                 "config.apis.vid65.current_page": max(1, int(d["vid65_page"])),
                 "config.apis.vid65.current_index": max(0, int(d["vid65_index"]))}
        except (KeyError, TypeError, ValueError):
            return fail("Page numbers must be numbers.")
        await db.update_bot(bid, u)
        await db.log_event(bid, "Cron position changed from the web panel")
        return ok()

    async def reset_sent(request):
        bid = request.match_info["id"]
        n = await db.delete_collection(db.sub(bid, "sent_videos"))
        await db.log_event(bid, f"Sent-video history cleared ({n} entries)")
        return ok(deleted=n)

    async def check_token(request):
        d = await body(request)
        token = str(d.get("token", "")).strip()
        if not TOKEN_RE.match(token):
            return fail("That does not look like a Telegram bot token.")
        server = await pick_server(token, str(d.get("api_server", "")), str(d.get("api_server_fallback", "")))
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
                async with s.get(f"{server}/bot{token}/getMe") as r:
                    j = await r.json(content_type=None)
        except Exception as e:
            return fail(f"Could not reach Telegram: {e}")
        if not j.get("ok"):
            return fail(j.get("description", "Telegram rejected the token"))
        r = j["result"]
        return ok(id=r.get("id"), username=r.get("username"), name=r.get("first_name"))

    async def run_cron(request):
        bid = request.match_info["id"]
        d = await body(request)
        source = d.get("source", "both")
        if source not in ("old", "vid65", "both"):
            return fail("Unknown source")
        cfg, bot = await db.find_bot(bid), sup.get_bot(bid)
        if not cfg:
            return fail("Bot not found", 404)
        if not bot:
            return fail("The bot is not running (check token / Enabled).", 409)
        if not cfg["admin_id"]:
            return fail("Set the Admin ID first - cron progress is sent to the admin's Telegram chat.", 409)
        if cron.is_running(bid):
            return fail("A cron is already running for this bot.", 409)
        admin.spawn(cron.run(bot, bid, cfg["admin_id"], source))
        return ok()

    async def start_button(request):
        bid = request.match_info["id"]
        act = (await body(request)).get("action")
        cfg, bot = await db.find_bot(bid, fresh=True), sup.get_bot(bid)
        if not cfg:
            return fail("Bot not found", 404)
        if not bot:
            return fail("The bot is not running.", 409)
        groups = group_manager.groups_of(cfg)
        if act == "post":
            if cfg["settings"]["group_start_button"] == "OFF":
                return fail("The Group Start button is switched OFF - turn it ON first.")
            okc, bad = await group_manager.post_group_start_buttons(bot, cfg, groups)
        elif act == "clear":
            okc, bad = await group_manager.clear_group_keyboards(bot, groups)
        else:
            return fail("Unknown action")
        return ok(done=okc, failed=bad)

    async def users_get(request):
        bid = request.match_info["id"]
        q = request.query
        res = await db.list_users(bid, int(q.get("limit", 50)), q.get("after") or None, q.get("q", ""), q.get("status", ""))
        return ok(**res)

    async def users_csv(request):
        bid = request.match_info["id"]
        resp = web.StreamResponse(headers={"Content-Type": "text/csv; charset=utf-8",
                                           "Content-Disposition": f'attachment; filename="{bid}-users.csv"'})
        await resp.prepare(request)
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["user_id", "username", "first_name", "status", "source", "joined_at", "last_seen"])
        await resp.write(("\ufeff" + buf.getvalue()).encode())
        async for u in db.iter_users(bid):
            buf.seek(0), buf.truncate()
            w.writerow([u["id"], u["username"] or "", u["first_name"] or "", u["status"], u["source"] or "",
                        u["joined_at"] or "", u["last_seen"] or ""])
            await resp.write(buf.getvalue().encode())
        await resp.write_eof()
        return resp

    async def broadcast(request):
        bid = request.match_info["id"]
        text = str((await body(request)).get("text", "")).strip()
        if not text:
            return fail("Write the message first.")
        bot = sup.get_bot(bid)
        if not bot:
            return fail("The bot is not running.", 409)
        from .. import broadcast as bc
        jid = uuid.uuid4().hex[:10]
        JOBS[jid] = {"state": "starting", "total": 0, "sent": 0, "blocked": 0, "failed": 0, "bot": bid, "t": time.time()}
        for k in [k for k, v in JOBS.items() if time.time() - v["t"] > 3600]:
            JOBS.pop(k, None)
        admin.spawn(bc.run_text(bot, bid, text, JOBS[jid]))
        return ok(job=jid)

    async def job_get(request):
        j = JOBS.get(request.match_info["job"])
        return ok(job=j) if j else fail("Unknown job", 404)

    async def logs_get(request):
        return ok(logs=await db.list_logs(request.match_info["id"], 80))

    # ------------------------------------------------------------ backups
    async def backups_list(request):
        return ok(backups=backup.list_backups(), auto_hours=config.AUTO_BACKUP_HOURS, keep=config.BACKUP_KEEP)

    async def backups_create(request):
        d = await body(request)
        meta = await backup.create("backup", include_secrets=bool(d.get("include_secrets", True)), gz=bool(d.get("gz", False)))
        return ok(**meta)

    async def backup_download(request):
        p = backup.safe_name(request.match_info["name"])
        if not p or not p.is_file():
            return fail("Backup not found", 404)
        return web.FileResponse(p, headers={"Content-Disposition": f'attachment; filename="{p.name}"'})

    async def backup_delete(request):
        return ok() if backup.delete_backup(request.match_info["name"]) else fail("Backup not found", 404)

    async def restore(request):
        raw: Optional[bytes] = None
        if request.content_type.startswith("multipart/"):
            form = await request.post()
            f = form.get("file")
            if f is None or not hasattr(f, "file"):
                return fail("Choose a backup file first.")
            raw = f.file.read()
        else:
            name = str((await body(request)).get("name", ""))
            p = backup.safe_name(name)
            if not p or not p.is_file():
                return fail("Backup not found", 404)
            raw = p.read_bytes()
        try:
            backup.parse(raw)  # validate BEFORE touching anything
        except Exception as e:
            return fail(f"Invalid backup file: {e}")
        safety = await backup.create("pre-restore", include_secrets=True, gz=True)
        counts = await backup.restore(raw)
        sup.poke()
        return ok(restored=counts, safety_backup=safety["name"])

    async def system(request):
        t0 = time.perf_counter()
        status, err = True, ""
        try:
            await db.client.collection("_meta").document("app").get()
        except Exception as e:
            status, err = False, str(e)
        bk = backup.list_backups()
        return ok(version=config.VERSION, python=platform.python_version(), uptime=int(time.time() - STARTED),
                  firestore={"ok": status, "error": err, "project": getattr(db.client, "project", ""),
                             "latency_ms": int((time.perf_counter() - t0) * 1000)},
                  running=[b for b in sup.runners if sup.info(b)["running"]], timezone=config.TIMEZONE_NAME,
                  auto_backup_hours=config.AUTO_BACKUP_HOURS, last_backup=bk[0] if bk else None)

    # ------------------------------------------------------------ public endpoints
    async def run_cron_external(request):
        bid = request.query.get("bot", "")
        cfg = await db.find_bot(bid, fresh=True) if bid else None
        secret = request.query.get("secret", "")
        if not cfg or not cfg.get("cron_secret") or not hmac.compare_digest(secret, cfg["cron_secret"]):
            return web.json_response({"ok": False}, status=403)
        bot = sup.get_bot(bid)
        if not bot:
            return web.json_response({"ok": False, "error": "bot not running"}, status=503)
        source = request.query.get("source", "both")
        if source not in ("old", "vid65", "both"):
            return web.json_response({"ok": False, "error": "bad source"}, status=400)
        admin.spawn(cron.run(bot, bid, cfg["admin_id"], source))
        return web.json_response({"ok": True})

    async def health(request):
        return web.json_response({"ok": True, "bots": sorted(sup.runners)})

    async def index(request):
        return web.FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    return dict(overview=overview, bots_list=bots_list, bot_get=bot_get, bot_create=bot_create, bot_update=bot_update,
                bot_delete=bot_delete, tracking_put=tracking_put, reset_sent=reset_sent, check_token=check_token,
                run_cron=run_cron, start_button=start_button, users_get=users_get, users_csv=users_csv,
                broadcast=broadcast, job_get=job_get, logs_get=logs_get, backups_list=backups_list,
                backups_create=backups_create, backup_download=backup_download, backup_delete=backup_delete,
                restore=restore, system=system, run_cron_external=run_cron_external, health=health, index=index)


def make_app(sup) -> web.Application:
    h = make_handlers(sup)
    app = web.Application(middlewares=[errors_mw, auth_mw], client_max_size=512 * 1024 * 1024)
    app.add_routes([
        web.get("/", h["index"]), web.get("/health", h["health"]), web.get("/run_cron", h["run_cron_external"]),
        web.static("/static", config.STATIC_DIR, show_index=False),
        web.post("/api/login", login), web.post("/api/logout", logout), web.get("/api/me", me),
        web.get("/api/overview", h["overview"]), web.get("/api/bots", h["bots_list"]), web.post("/api/bots", h["bot_create"]),
        web.get("/api/bots/{id}", h["bot_get"]), web.put("/api/bots/{id}", h["bot_update"]),
        web.delete("/api/bots/{id}", h["bot_delete"]), web.put("/api/bots/{id}/tracking", h["tracking_put"]),
        web.post("/api/bots/{id}/reset-sent", h["reset_sent"]), web.post("/api/bots/{id}/cron", h["run_cron"]),
        web.post("/api/bots/{id}/start-button", h["start_button"]), web.get("/api/bots/{id}/users", h["users_get"]),
        web.get("/api/bots/{id}/users.csv", h["users_csv"]), web.post("/api/bots/{id}/broadcast", h["broadcast"]),
        web.get("/api/bots/{id}/logs", h["logs_get"]), web.get("/api/jobs/{job}", h["job_get"]),
        web.post("/api/telegram/check", h["check_token"]),
        web.get("/api/backups", h["backups_list"]), web.post("/api/backups", h["backups_create"]),
        web.get("/api/backups/{name}", h["backup_download"]), web.delete("/api/backups/{name}", h["backup_delete"]),
        web.post("/api/restore", h["restore"]), web.get("/api/system", h["system"]),
    ])
    return app


async def start(sup) -> web.AppRunner:
    runner = web.AppRunner(make_app(sup), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT).start()
    log.info("Admin panel: http://%s:%s", "localhost" if config.WEB_HOST in ("0.0.0.0", "::") else config.WEB_HOST, config.WEB_PORT)
    return runner
