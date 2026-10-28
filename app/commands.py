"""Executes requests that the admin panel leaves in Firestore (_commands collection) and
publishes this server's live status (_meta/runtime) for the panel to show."""
import asyncio
import logging
import time

from . import admin, broadcast, config, cron, db, group_manager
from .utils import esc  # noqa: F401

log = logging.getLogger("commands")
STALE_SECONDS = 120


class CmdError(Exception):
    pass


async def run_loop(sup) -> None:
    hot_until = 0.0
    while True:
        try:
            cmds = await db.pending_commands()
            for c in cmds:
                age = (db.utcnow() - c["created_at"]).total_seconds() if c.get("created_at") else 0
                if age > STALE_SECONDS:  # bot server was offline - don't run old requests by surprise
                    await db.update_command(c["id"], status="expired", error="The bot server was offline - request expired.")
                    continue
                await db.update_command(c["id"], status="running")  # claim before the next poll
                admin.spawn(handle(c, sup))
                hot_until = time.monotonic() + 90
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("command poll failed")
        await asyncio.sleep(config.COMMAND_POLL_HOT if time.monotonic() < hot_until else config.COMMAND_POLL_IDLE)


async def handle(c: dict, sup) -> None:
    cid, kind, bid = c["id"], c.get("type"), c.get("bot_id", "")
    try:
        result = await execute(kind, bid, c.get("payload") or {}, sup, cid)
        await db.update_command(cid, status="done", result=result or {})
    except CmdError as e:
        await db.update_command(cid, status="error", error=str(e))
    except Exception as e:
        log.exception("command %s (%s) failed", cid, kind)
        await db.update_command(cid, status="error", error=f"Unexpected error: {e}")


async def execute(kind: str, bid: str, p: dict, sup, cid: str) -> dict:
    if kind == "sync":
        sup.poke()
        return {"synced": True}
    cfg = await db.find_bot(bid, fresh=True)
    if not cfg:
        raise CmdError("Bot not found")
    bot = sup.get_bot(bid)
    if not bot:
        raise CmdError("The bot is not running (check its token and that it is enabled).")

    if kind == "cron":
        if not cfg["admin_id"]:
            raise CmdError("Set the Admin ID first - cron progress is sent to the admin's Telegram chat.")
        if cron.is_running(bid):
            raise CmdError("A cron is already running for this bot.")
        if p.get("source") not in ("old", "vid65", "both"):
            raise CmdError("Unknown source")
        admin.spawn(cron.run(bot, bid, cfg["admin_id"], p["source"]))
        return {"started": True}

    if kind in ("start_button_post", "start_button_clear"):
        groups = group_manager.groups_of(cfg)
        if kind == "start_button_post":
            if cfg["settings"]["group_start_button"] == "OFF":
                raise CmdError("The Group Start button is switched OFF - turn it ON first.")
            done, failed = await group_manager.post_group_start_buttons(bot, cfg, groups)
        else:
            done, failed = await group_manager.clear_group_keyboards(bot, groups)
        return {"done": done, "failed": failed}

    if kind == "broadcast":
        text = str(p.get("text", "")).strip()
        if not text:
            raise CmdError("Empty message")
        job = {"state": "starting", "total": 0, "sent": 0, "blocked": 0, "failed": 0}

        async def report():
            while job["state"] != "done":
                await asyncio.sleep(2)
                try:
                    await db.update_command(cid, progress=dict(job))
                except Exception:
                    pass

        rep = asyncio.create_task(report())
        try:
            await broadcast.run_text(bot, bid, text, job)
        finally:
            job["state"] = "done"
            rep.cancel()
        return dict(job)

    raise CmdError(f"Unknown command: {kind}")


async def runtime_loop(sup) -> None:
    """Heartbeat + which bots run, so the panel can show live status (and 'bot server offline')."""
    while True:
        try:
            bots = {}
            for bid in list(sup.runners):
                i = sup.info(bid)
                bots[bid] = {"running": i["running"], "username": i["username"] or "", "error": i["error"] or "",
                             "started_at": i["started_at"] or 0, "cron_running": cron.is_running(bid)}
            await db.write_runtime(bots)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("runtime write failed")
        await asyncio.sleep(config.RUNTIME_INTERVAL)


async def housekeeping_loop() -> None:
    while True:
        try:
            await db.cleanup_commands(24)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("housekeeping failed")
        await asyncio.sleep(3600)


async def pending_groups_loop(sup) -> None:
    """Groups nobody confirmed in time: send the reject message and leave."""
    while True:
        try:
            for bid in list(sup.runners):
                bot = sup.get_bot(bid)
                if not bot:
                    continue
                rows = await db.expired_pending_groups(bid)
                if not rows:
                    continue
                cfg = await db.find_bot(bid)
                for r in rows:
                    pend = await db.claim_pending_group(bid, r["chat_id"])
                    if pend and cfg:
                        await group_manager.reject_group(bot, cfg, bid, pend, timed_out=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("pending groups sweep failed")
        await asyncio.sleep(config.GROUP_SWEEP_INTERVAL)
