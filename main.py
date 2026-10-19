"""Telegram multi-bot (aiogram 3) + web admin panel, backed by Firebase Firestore."""
import asyncio
import logging
import signal
import sys

from app import backup, config, db
from app.runner import Supervisor
from app.web import server


def setup_logging() -> None:
    fmt = logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s - %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(config.LOG_FILE, encoding="utf-8")):
        h.setFormatter(fmt)
        root.addHandler(h)
    for noisy in ("aiogram", "google", "grpc", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


async def main() -> None:
    setup_logging()
    log = logging.getLogger("main")
    if config.GENERATED_PASSWORD:
        log.warning("ADMIN_PASSWORD is not set - temporary panel password for this run: %s", config.ADMIN_PASSWORD)
    try:
        await db.init()
    except Exception as e:
        log.error("Could not connect to Firebase: %s", e)
        return

    sup = Supervisor()
    web_runner = await server.start(sup)
    tasks = [asyncio.create_task(sup.run_forever()), asyncio.create_task(backup.auto_loop())]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    log.info("Running. Open the admin panel on port %s.", config.WEB_PORT)
    try:
        await stop.wait()
    except asyncio.CancelledError:
        pass
    log.info("Shutting down...")
    await sup.shutdown()
    for t in tasks:
        t.cancel()
    await web_runner.cleanup()
    await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
