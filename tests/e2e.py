import asyncio, io, json, os, sys, time
import aiohttp
from harness import *  # noqa
from app import backup, config, db, cron  # noqa
import fake_telegram as ft

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond: PASS += 1; print("  PASS", name)
    else: FAIL += 1; print("  FAIL", name, extra)

async def until(fn, t=12, step=0.25):
    end = time.time() + t
    while time.time() < end:
        r = fn()
        if asyncio.iscoroutine(r): r = await r
        if r: return r
        await asyncio.sleep(step)
    return None

BASE = "http://127.0.0.1:8097"
H = {"X-Requested-With": "tgadmin"}
uid = [100]
def upd(token, **kw):
    uid[0] += 1; ft.inject(token, {"update_id": uid[0], **kw})
def msg(token, frm, chat, text, ctype="private", name="U"):
    upd(token, message={"message_id": uid[0] + 1, "date": 1, "from": {"id": frm, "is_bot": False, "first_name": name}, "chat": {"id": chat, "type": ctype}, "text": text})
def cbq(token, frm, data):
    upd(token, callback_query={"id": "c%d" % uid[0], "chat_instance": "x", "from": {"id": frm, "is_bot": False, "first_name": "A"}, "data": data, "message": {"message_id": 5, "date": 1, "chat": {"id": frm, "type": "private"}}})
def calls(token, method=None): return [c for c in ft.LOG if c["token"] == token and (method is None or c["method"] == method)]
def mk(c): return c["p"].get("reply_markup", "") or ""
def user_doc(bid, uid_): return fake.store.get(("bots", bid, "users", str(uid_)))

async def main():
    global fake
    fake, sup, stop = await start_stack()
    s = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))
    async def call(method, path, json_=None, form=None, headers=H):
        async with s.request(method, BASE + path, json=json_, data=form, headers=headers) as r:
            try: return r.status, await r.json()
            except Exception: return r.status, await r.read()
    try:
        print("== Auth & security ==")
        check("API blocked without login", (await call("GET", "/api/bots"))[0] == 401)
        check("wrong password rejected", (await call("POST", "/api/login", {"password": "nope"}))[0] == 401)
        st, _ = await call("POST", "/api/login", {"password": "pw123"})
        check("login OK + cookie set", st == 200)
        check("state-changing request without header refused (CSRF)", (await call("POST", "/api/bots", {"id": "x"}, headers={}))[0] == 403)
        st, html = await call("GET", "/")
        check("index page + static assets served", st == 200 and b"TG Control" in html and (await call("GET", "/static/app.js"))[0] == 200 and (await call("GET", "/static/style.css"))[0] == 200)

        print("\n== Bots CRUD via web ==")
        check("invalid bot id rejected", (await call("POST", "/api/bots", {"id": "bad id!", "token": TOK_A}))[0] == 400)
        check("invalid token rejected", (await call("POST", "/api/bots", {"id": "a", "token": "nonsense"}))[0] == 400)
        for bid, tok in (("alpha", TOK_A), ("beta", TOK_B)):
            st, r = await call("POST", "/api/bots", {"id": bid, "name": bid.title(), "token": tok, "admin_id": "111"})
            check(f"create bot {bid}", st == 200 and r["ok"], r)
        check("duplicate id -> 409", (await call("POST", "/api/bots", {"id": "alpha", "token": TOK_A}))[0] == 409)
        cfg_payload = lambda start: {"telegram": {"api_server": FAKE, "api_server_fallback": FAKE},
            "apis": {"old": {"url": FAKE + "/api/old", "groups": ["-1001234"], "channels": ["@chan1"]}, "vid65": {"url": FAKE + "/api/vid65", "groups": ["-1001234"], "channels": ["@chan1"]}},
            "settings": {"group_start_button": start, "mode": "ON"},
            "messages": {"start": "Hello {first_name}!", "welcome_dm": "Welcome {first_name}", "channel_caption": "CH {title}", "group_caption": "GR <b>{title}</b>"}}
        check("save alpha config (Start ON)", (await call("PUT", "/api/bots/alpha", cfg_payload("ON")))[1]["ok"])
        check("save beta config (Start OFF)", (await call("PUT", "/api/bots/beta", cfg_payload("OFF")))[1]["ok"])
        check("bad API url rejected", (await call("PUT", "/api/bots/alpha", {"apis": {"old": {"url": "ftp://x"}}}))[0] == 400)
        check("bad chat id rejected", (await call("PUT", "/api/bots/alpha", {"apis": {"old": {"groups": ["not a chat"]}}}))[0] == 400)
        ok_running = await until(lambda: len([b for b in sup.runners if sup.info(b)["running"]]) == 2, 20)
        check("both bots started by the supervisor (instant poke)", ok_running)
        st, r = await call("POST", "/api/telegram/check", {"token": TOK_A, "api_server": FAKE})
        check("token check returns @username", st == 200 and r["username"].startswith("tbot_"), r)

        print("\n== Telegram: group Start button ON ==")
        ft.LOG.clear()
        msg(TOK_A, 500, -1001234, "🚀 Start", "supergroup", "Alice")
        msg(TOK_A, 111, -1001234, "🚀 Start", "supergroup", "Admin")
        msg(TOK_A, 501, -1001234, "hello all", "supergroup", "Bob")
        await until(lambda: user_doc("alpha", 111) and len(calls(TOK_A, "deleteMessage")) >= 2 and len([c for c in calls(TOK_A, "sendMessage") if c["p"].get("chat_id") == "111"]) >= 2, 10)
        check("user registered (source group_1234)", (user_doc("alpha", 500) or {}).get("source") == "group_1234", user_doc("alpha", 500))
        check("admin can register too", user_doc("alpha", 111) is not None)
        check("chatter ignored", user_doc("alpha", 501) is None)
        check("'Registered!' posted + tap deleted", any("Registered" in c["p"].get("text", "") for c in calls(TOK_A, "sendMessage")) and len(calls(TOK_A, "deleteMessage")) >= 2)
        check("admin notified of new user", any(c["p"].get("chat_id") == "111" and "New User" in c["p"].get("text", "") for c in calls(TOK_A, "sendMessage")))
        ft.LOG.clear()
        upd(TOK_A, my_chat_member={"chat": {"id": -1009999, "type": "supergroup", "title": "Test Grp"}, "from": {"id": 111, "is_bot": False, "first_name": "Admin"}, "date": 1,
            "old_chat_member": {"status": "left", "user": {"id": 999, "is_bot": True, "first_name": "T"}}, "new_chat_member": {"status": "member", "user": {"id": 999, "is_bot": True, "first_name": "T"}}})
        await until(lambda: any(c["p"].get("chat_id") == "-1009999" for c in calls(TOK_A, "sendMessage")), 8)
        check("Start keyboard posted in new group", any(c["p"].get("chat_id") == "-1009999" and "Start" in mk(c) and "keyboard" in mk(c) for c in calls(TOK_A, "sendMessage")))
        a = await db.find_bot("alpha", fresh=True)
        check("group auto-added to both lists in Firestore", "-1009999" in a["apis"]["old"]["groups"] and "-1009999" in a["apis"]["vid65"]["groups"])
        check("chat saved to chats collection", fake.store.get(("bots", "alpha", "chats", "-1009999"), {}).get("title") == "Test Grp")

        print("\n== Telegram: group Start button OFF ==")
        ft.LOG.clear()
        msg(TOK_B, 500, -1001234, "🚀 Start", "supergroup", "Alice")
        await until(lambda: any("remove_keyboard" in mk(c) for c in calls(TOK_B, "sendMessage")), 8)
        check("OFF: nobody registered", user_doc("beta", 500) is None)
        check("OFF: stale keyboard removed", any("remove_keyboard" in mk(c) for c in calls(TOK_B, "sendMessage")))

        print("\n== Telegram: DM + admin + broadcast ==")
        ft.LOG.clear()
        msg(TOK_A, 600, 600, "/start", "private", "Dan"); msg(TOK_A, 111, 111, "/start")
        await until(lambda: any(c["p"].get("text") == "Hello Dan!" for c in calls(TOK_A, "sendMessage")) and any("Broadcast" in mk(c) for c in calls(TOK_A, "sendMessage")), 8)
        check("DM user registered + welcome with keyboard", user_doc("alpha", 600) is not None and any(c["p"].get("text") == "Hello Dan!" and "Start" in mk(c) for c in calls(TOK_A, "sendMessage")))
        check("admin menu shown", any("Broadcast" in mk(c) for c in calls(TOK_A, "sendMessage")))
        msg(TOK_A, 111, 111, "🟢 Mode: ON")
        await until(lambda: fake.store[("bots", "alpha")]["config"]["settings"]["mode"] == "OFF", 8)
        check("Mode toggle saved to Firestore", fake.store[("bots", "alpha")]["config"]["settings"]["mode"] == "OFF")
        msg(TOK_A, 111, 111, "🔴 Mode: OFF")
        await until(lambda: fake.store[("bots", "alpha")]["config"]["settings"]["mode"] == "ON", 8)
        check("Mode toggled back", fake.store[("bots", "alpha")]["config"]["settings"]["mode"] == "ON")
        ft.LOG.clear()
        await db.set_admin_state("alpha", "waiting_broadcast")
        msg(TOK_A, 111, 111, "📊 Status"); await asyncio.sleep(2)
        check("Status while waiting_broadcast does NOT broadcast", not calls(TOK_A, "copyMessage") and any("Bot Status" in c["p"].get("text", "") for c in calls(TOK_A, "sendMessage")))
        ft.LOG.clear()
        msg(TOK_A, 111, 111, "📢 Broadcast"); await asyncio.sleep(1.5); msg(TOK_A, 111, 111, "Big news!")
        await until(lambda: len(calls(TOK_A, "copyMessage")) >= 3, 10)
        check("Telegram broadcast reaches all 3 active users", len(calls(TOK_A, "copyMessage")) == 3, len(calls(TOK_A, "copyMessage")))
        await until(lambda: any(k[:3] == ("bots", "alpha", "broadcasts") for k in fake.store), 8)
        bc = [v for k, v in fake.store.items() if k[:3] == ("bots", "alpha", "broadcasts")]
        check("broadcast logged", bc and bc[0]["sent"] == 3)

        print("\n== Cron pipeline (triggered from the web panel) ==")
        ft.LOG.clear()
        st, r = await call("POST", "/api/bots/alpha/cron", {"source": "old"})
        check("web: run cron accepted", st == 200, r)
        await until(lambda: ("bots", "alpha", "sent_videos") and any(k[:3] == ("bots", "alpha", "sent_videos") for k in fake.store), 25)
        await asyncio.sleep(1)
        vids = calls(TOK_A, "sendVideo")
        grp = [c for c in vids if c["p"]["chat_id"].startswith("-100")]
        chn = [c for c in vids if c["p"]["chat_id"] == "@chan1"]
        check("video -> 2 groups with Start keyboard", len(grp) == 2 and all("Start" in mk(c) for c in grp), [(c["p"]["chat_id"], mk(c)[:20]) for c in vids])
        check("video -> channel, caption template applied + title HTML-escaped", len(chn) == 1 and chn[0]["p"]["caption"] == "CH Old Two &lt;b&gt;", [c["p"].get("caption") for c in chn])
        check("sent video recorded (duplicate protection)", any(v.get("key") == "slug:s2" for k, v in fake.store.items() if k[:3] == ("bots", "alpha", "sent_videos")))
        a = await db.find_bot("alpha", fresh=True)
        check("tracking advanced (last_slug=s2)", a["apis"]["old"]["last_slug"] == "s2", a["apis"]["old"])
        check("channel history stored", fake.store.get(("bots", "alpha", "channel_history", "@chan1"), {}).get("ids"))
        check("activity log written", any(k[:3] == ("bots", "alpha", "logs") and "Cron" in v["text"] for k, v in fake.store.items()))
        st, r = await call("POST", "/api/bots/alpha/cron", {"source": "old"})
        # second run must not resend s2 (moves on to s1)
        await until(lambda: any(v.get("key") == "slug:s1" for k, v in fake.store.items() if k[:3] == ("bots", "alpha", "sent_videos")), 25)
        keys = sorted(v["key"] for k, v in fake.store.items() if k[:3] == ("bots", "alpha", "sent_videos"))
        check("second cron picks the NEXT video, no repeats", keys == ["slug:s1", "slug:s2"], keys)

        print("\n== Start button switched OFF from the web panel ==")
        ft.LOG.clear()
        st, r = await call("PUT", "/api/bots/alpha", {"settings": {"group_start_button": "OFF"}})
        check("toggle OFF returns sweep result for 2 groups", st == 200 and r.get("sweep", {}).get("cleared") == 2, r)
        check("keyboard removed in every group (send + delete)", len([c for c in calls(TOK_A, "sendMessage") if "remove_keyboard" in mk(c)]) == 2 and len(calls(TOK_A, "deleteMessage")) == 2)
        check("Firestore setting is OFF", fake.store[("bots", "alpha")]["config"]["settings"]["group_start_button"] == "OFF")
        check("'post button' refused while OFF", (await call("POST", "/api/bots/alpha/start-button", {"action": "post"}))[0] == 400)
        ft.LOG.clear()
        await call("POST", "/api/bots/alpha/cron", {"source": "vid65"})
        await until(lambda: any(v.get("key") == "id:7" for k, v in fake.store.items() if k[:3] == ("bots", "alpha", "sent_videos")), 25)
        await asyncio.sleep(1)
        grp = [c for c in calls(TOK_A, "sendVideo") if c["p"]["chat_id"].startswith("-100")]
        check("OFF: cron group videos carry remove_keyboard", len(grp) == 2 and all("remove_keyboard" in mk(c) for c in grp), [mk(c) for c in grp])
        ft.LOG.clear()
        r = (await call("PUT", "/api/bots/alpha", {"settings": {"group_start_button": "ON"}}))[1]
        check("toggle ON works", r["ok"])
        r = (await call("POST", "/api/bots/alpha/start-button", {"action": "post"}))[1]
        check("'post button' now posts to 2 groups", r.get("done") == 2 and len([c for c in calls(TOK_A, "sendMessage") if "Start" in mk(c)]) == 2, r)

        print("\n== Users, CSV, broadcast, logs (web) ==")
        for i in range(120):
            await db.touch_user("beta", 9000 + i, f"user{i:03d}", f"Name{i}", "group_1")
        await db.mark_blocked("beta", 9005)
        r = (await call("GET", "/api/bots/beta/users?limit=50"))[1]
        p1 = [u["id"] for u in r["users"]]
        r2 = (await call("GET", "/api/bots/beta/users?limit=50&after=" + r["next"]))[1]
        r3 = (await call("GET", "/api/bots/beta/users?limit=50&after=" + r2["next"]))[1]
        allids = p1 + [u["id"] for u in r2["users"]] + [u["id"] for u in r3["users"]]
        check("pagination: 3 pages, 120 unique users, no duplicates", len(allids) == 120 and len(set(allids)) == 120 and r3["next"] is None, (len(allids), len(set(allids))))
        check("search by username prefix", len((await call("GET", "/api/bots/beta/users?q=user01"))[1]["users"]) == 10)
        check("search by id", (await call("GET", "/api/bots/beta/users?q=9007"))[1]["users"][0]["username"] == "user007")
        check("filter blocked", [u["id"] for u in (await call("GET", "/api/bots/beta/users?status=blocked"))[1]["users"]] == [9005])
        st, csvb = await call("GET", "/api/bots/beta/users.csv")
        lines = csvb.decode().strip().splitlines()
        check("CSV export has header + 120 rows", st == 200 and len(lines) == 121 and "user_id" in lines[0], len(lines))
        ft.LOG.clear()
        r = (await call("POST", "/api/bots/beta/broadcast", {"text": "<b>Hi</b> everyone"}))[1]
        jid = r["job"]
        await until(lambda: JOBSTATE(jid), 30)
        j = (await call("GET", "/api/jobs/" + jid))[1]["job"]
        check("web broadcast finished: 119 sent (1 blocked user skipped)", j["state"] == "done" and j["sent"] == 119, j)
        check("broadcast uses HTML text", calls(TOK_B, "sendMessage")[0]["p"]["text"] == "<b>Hi</b> everyone")
        r = (await call("GET", "/api/bots/beta/logs"))[1]["logs"]
        check("activity log API", any("broadcast" in l["text"].lower() for l in r))
        st = (await call("PUT", "/api/bots/beta/tracking", {"old_page": 7, "old_last_slug": "zz", "vid65_page": 3, "vid65_index": 2}))[0]
        t = db.get_tracking(await db.find_bot("beta", fresh=True))
        check("cron position editable", st == 200 and t["old_page"] == 7 and t["old_last_slug"] == "zz" and t["vid65_page"] == 3 and t["vid65_index"] == 2, t)
        check("settings save does NOT reset cron position", ((await call("PUT", "/api/bots/beta", {"name": "Beta2"}))[0] == 200) and db.get_tracking(await db.find_bot("beta", fresh=True))["old_page"] == 7)

        print("\n== Dashboard / system ==")
        r = (await call("GET", "/api/overview"))[1]
        check("overview totals", r["totals"]["bots"] == 2 and r["totals"]["running"] == 2 and r["totals"]["users"] >= 120 and len(r["daily"]) == 14, r["totals"])
        check("daily chart sums to new users", sum(d["count"] for d in r["daily"]) >= 120)
        r = (await call("GET", "/api/bots/beta"))[1]
        check("bot detail includes stats/blocked", r["stats"]["blocked"] == 1 and r["stats"]["total"] == 120, r["stats"])
        r = (await call("GET", "/api/system"))[1]
        check("system endpoint", r["firestore"]["ok"] and len(r["running"]) == 2)

        print("\n== BACKUP / RESTORE ==")
        before = json.loads(json.dumps({"|".join(k): v for k, v in fake.store.items() if k[0] == "bots"}, default=str))
        st, r = await call("POST", "/api/backups", {"include_secrets": True, "gz": False})
        check("backup created", st == 200 and r["counts"]["bots"] == 2 and r["counts"]["users"] >= 120, r)
        name = r["name"]
        check("backup file exists on disk", os.path.isfile(os.path.join(TMP, name)))
        st, data = await call("GET", "/api/backups/" + name)
        check("backup downloadable (valid JSON file)", st == 200 and isinstance(data, dict) and data.get("format") == "tgbot-firestore-backup" and len(data["bots"]) == 2)
        st, r = await call("POST", "/api/backups", {"include_secrets": False, "gz": True})
        gzname = r["name"]
        raw = open(os.path.join(TMP, gzname), "rb").read()
        d = backup.parse(raw)
        check("gzip backup w/o secrets masks tokens", all(b["data"]["token"] == "" for b in d["bots"]) and gzname.endswith(".gz"))
        check("path traversal blocked", (await call("GET", "/api/backups/..%2f..%2fetc%2fpasswd"))[0] in (404, 400) and (await call("DELETE", "/api/backups/..%2fx.json"))[0] == 404)
        check("backup list shows both", len((await call("GET", "/api/backups"))[1]["backups"]) == 2)
        # disaster: wipe the database
        for k in [k for k in fake.store if k[0] == "bots"]: del fake.store[k]
        db.invalidate()
        check("database is empty after wipe", not any(k[0] == "bots" for k in fake.store))
        st, r = await call("POST", "/api/restore", {"name": name})
        check("restore from server backup", st == 200 and r["restored"]["bots"] == 2, r)
        after = json.loads(json.dumps({"|".join(k): v for k, v in fake.store.items() if k[0] == "bots"}, default=str))
        check("restored data is IDENTICAL to the original", after == before, f"{len(before)} vs {len(after)} docs")
        check("restore made a safety backup first", any(b["name"].startswith("pre-restore") for b in (await call("GET", "/api/backups"))[1]["backups"]))
        # restore through file upload
        for k in [k for k in fake.store if k[0] == "bots" and len(k) > 2]: del fake.store[k]
        fd = aiohttp.FormData(); fd.add_field("file", open(os.path.join(TMP, name), "rb").read(), filename="x.json")
        st, r = await call("POST", "/api/restore", form=fd)
        check("restore via file upload", st == 200 and len([k for k in fake.store if k[:3] == ("bots", "beta", "users")]) == 120, r)
        fd = aiohttp.FormData(); fd.add_field("file", b"this is not a backup", filename="x.json")
        check("garbage file rejected, nothing touched", (await call("POST", "/api/restore", form=fd))[0] == 400)
        fd = aiohttp.FormData(); fd.add_field("file", raw, filename="x.json.gz")
        st, r = await call("POST", "/api/restore", form=fd)
        check("gzip upload restore (tokens blank -> bots stay stopped safely)", st == 200)
        config.BACKUP_KEEP = 1
        for i in range(3): await backup.create("auto", gz=True); await asyncio.sleep(1.1)
        backup.prune_auto()
        check("auto-backup pruning keeps newest N", len([b for b in backup.list_backups() if b["kind"] == "auto"]) == 1)
        check("manual backups never pruned", len([b for b in backup.list_backups() if b["kind"] == "manual"]) == 2)
        check("delete backup", (await call("DELETE", "/api/backups/" + name))[0] == 200 and not os.path.exists(os.path.join(TMP, name)))

        print("\n== Delete bot ==")
        check("delete needs typed confirmation", (await call("DELETE", "/api/bots/beta?confirm=nope"))[0] == 400)
        st, _ = await call("DELETE", "/api/bots/beta?confirm=beta")
        check("bot + all subcollections removed", st == 200 and not [k for k in fake.store if k[:2] == ("bots", "beta")])
        await until(lambda: "beta" not in sup.runners, 8)
        check("supervisor stopped the deleted bot", "beta" not in sup.runners)
    finally:
        await s.close()
        await stop()
    print(f"\nRESULT: {PASS} passed, {FAIL} failed")
    return FAIL

def JOBSTATE(jid):
    from app.web import server as _s
    j = _s.JOBS.get(jid)
    return bool(j and j["state"] == "done")

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.ERROR)
    logging.getLogger("runner").setLevel(logging.CRITICAL)
    sys.exit(1 if asyncio.run(main()) else 0)
