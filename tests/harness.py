"""Boots the complete app (bots + admin panel) in-process against fake Firestore + fake Telegram."""
import asyncio, os, sys, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="tgbk_")
os.environ.update(ADMIN_PASSWORD="pw123", WEB_PORT=os.environ.get("TEST_WEB_PORT", "8097"), WEB_HOST="127.0.0.1",
                  BACKUP_DIR=TMP, SUPERVISOR_INTERVAL="3", CONFIG_CACHE_TTL="0.5", AUTO_BACKUP_HOURS="0",
                  TIMEZONE="Asia/Dhaka", FIREBASE_CREDENTIALS_JSON="")

from aiohttp import web  # noqa: E402
from app import db  # noqa: E402
from app.runner import Supervisor  # noqa: E402
from app.web import server  # noqa: E402
import fake_telegram as ft  # noqa: E402
from fake_firestore import FakeClient  # noqa: E402

FAKE = "http://127.0.0.1:8099"
TOK_A = "1111:AAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
TOK_B = "2222:BBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


async def start_stack():
    fake = FakeClient()
    await db.init(fake)
    tg = web.AppRunner(ft.build_app(), access_log=None)
    await tg.setup()
    await web.TCPSite(tg, "127.0.0.1", 8099).start()
    sup = Supervisor()
    web_runner = await server.start(sup)
    task = asyncio.create_task(sup.run_forever())

    async def stop():
        await sup.shutdown()
        task.cancel()
        await web_runner.cleanup()
        await tg.cleanup()
    return fake, sup, stop
