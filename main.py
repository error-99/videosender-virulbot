"""BOT SERVER - runs all Telegram bots (aiogram 3) from the bots stored in Firebase Firestore.
Needs no open port: it only talks outward to Telegram and Firebase."""
import asyncio
import logging
import signal
import sys

from app import commands, config, db
from app.runner import Supervisor


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
    try:
        await db.init()
    except Exception as e:
        log.error("Could not connect to Firebase: %s", e)
        return
    sup = Supervisor()
    tasks = [asyncio.create_task(sup.run_forever()), asyncio.create_task(commands.run_loop(sup)),
             asyncio.create_task(commands.runtime_loop(sup)), asyncio.create_task(commands.housekeeping_loop()),
             asyncio.create_task(commands.pending_groups_loop(sup))]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:  # Windows
            pass
    log.info("Bot server running. Bots are read from Firebase every %ss; panel commands every %ss.",
             config.SUPERVISOR_INTERVAL, config.COMMAND_POLL_IDLE)
    try:
        await stop.wait()
    except asyncio.CancelledError:
        pass
    log.info("Shutting down...")
    for t in tasks:
        t.cancel()
    await sup.shutdown()
    try:
        await db.write_runtime({})
    except Exception:
        pass
    await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
