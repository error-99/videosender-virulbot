"""Starts the whole app against FAKE Firestore/Telegram with demo data (for UI testing)."""
import asyncio, os
os.environ.setdefault("TEST_WEB_PORT", "8095")
from harness import *  # noqa
from app import db


async def main():
    fake, sup, stop = await start_stack()
    for bid, tok, start in (("alpha", TOK_A, "ON"), ("beta", TOK_B, "OFF")):
        b = db.new_bot(bid)
        b.update(name=bid.title() + " Bot", token=tok, admin_id="111", cron_secret="sekret" + bid)
        b["telegram"] = {"api_server": FAKE, "api_server_fallback": FAKE}
        b["apis"]["old"].update(url=FAKE + "/api/old", groups=["-1001234"], channels=["@chan1"])
        b["apis"]["vid65"].update(url=FAKE + "/api/vid65", groups=["-1001234"])
        b["settings"]["group_start_button"] = start
        await db.create_bot(b)
    for i in range(75):
        await db.touch_user("alpha", 7000 + i, f"user{i}" if i % 3 else None, f"Person <{i}>", "group_1001234")
    await db.mark_blocked("alpha", 7003)
    await db.log_broadcast("alpha", 70, 66, 3, 1)
    await db.log_event("alpha", "Cron (old): sent 'Demo <video>' to 1 group(s), 1 channel(s)")
    sup.poke()
    print("DEMO READY", flush=True)
    await asyncio.sleep(10 ** 6)

asyncio.run(main())
