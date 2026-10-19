import asyncio, os, sys
from harness import *  # noqa
from fake_firestore import FakeClient
sys.path.insert(0, os.path.join(ROOT, "tools"))
import import_from_mysql as imp
from app import db

PASS = FAIL = 0
def check(n, c, e=""):
    global PASS, FAIL
    if c: PASS += 1; print("  PASS", n)
    else: FAIL += 1; print("  FAIL", n, e)

async def main():
    data = imp.read_mysql(dict(host="localhost", database="t", user="t", password="tp"))
    check("read from MySQL", len(data["bots"]) == 1 and len(data["users"]) == 3, {k: len(v) for k, v in data.items()})
    fake = FakeClient()
    dry = await imp.write_firestore(data, fake, dry_run=True, log=lambda *a: None)
    check("dry run writes nothing", not fake.store and dry["users"] == 3)
    c = await imp.write_firestore(data, fake)
    check("counts", c == {"bots": 1, "users": 3, "chats": 1, "channels": 1, "sent": 2, "broadcasts": 1, "logs": 2}, c)
    db.client = fake
    b = await db.find_bot("oldbot", fresh=True)
    check("bot fields + token + admin", b["name"] == "Old Bot" and b["token"].startswith("7777:") and b["admin_id"] == "111" and b["cron_secret"] == "sec1")
    check("config sections kept, junk dropped", b["apis"]["old"]["groups"] == ["-100111", "-100222"] and b["settings"]["group_start_button"] == "OFF" and "webhook_junk" not in fake.store[("bots", "oldbot")]["config"])
    t = db.get_tracking(b)
    check("cron position preserved", t["old_page"] == 150 and t["old_last_slug"] == "abc" and t["vid65_page"] == 4 and t["vid65_index"] == 2 and t["mode"] == "OFF")
    u = fake.store[("bots", "oldbot", "users", "1002")]
    check("blocked user + timestamps (UTC aware)", u["status"] == "blocked" and u["blocked_at"].tzinfo is not None and u["joined_at"].year == 2026)
    check("username_lc for search", fake.store[("bots", "oldbot", "users", "1001")]["username_lc"] == "alice")
    check("channel history ids", fake.store[("bots", "oldbot", "channel_history", "@c1")]["ids"] == [10, 11, 12])
    check("duplicate-protection keys work with the new code", await db.cron_was_sent("oldbot", "old", "slug:abc") and await db.cron_was_sent("oldbot", "vid65", "id:9") and not await db.cron_was_sent("oldbot", "old", "slug:zzz"))
    check("old logs imported", [v["text"] for k, v in fake.store.items() if k[2:3] == ("logs",)] and len([k for k in fake.store if k[2:3] == ("logs",)]) == 2)
    check("migrated users usable by the web API", (await db.user_stats("oldbot", ttl=0))["total"] == 3 and (await db.user_stats("oldbot", ttl=0))["blocked"] == 1)
    check("active_user_ids excludes blocked", await db.active_user_ids("oldbot") == [1001, 1003])
    c2 = await imp.write_firestore(data, fake)
    check("re-running is idempotent", c2 == c and len([k for k in fake.store if k[:3] == ("bots", "oldbot", "users")]) == 3)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    return FAIL
sys.exit(1 if asyncio.run(main()) else 0)
