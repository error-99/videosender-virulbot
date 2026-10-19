"""Fake Telegram Bot API + fake video API, used by the tests only."""
import asyncio, io, itertools, os
from aiohttp import web
from PIL import Image

LOG, UPDATES = [], {}
mid = itertools.count(1000)
BASE = "http://127.0.0.1:8099"


def chat_obj(cid):
    try:
        i = int(cid)
    except Exception:
        return {"id": 1, "type": "channel", "title": str(cid)}
    return {"id": i, "type": "private" if i > 0 else "supergroup", "title": "g" if i < 0 else None}


async def api(request):
    token, method = request.match_info["token"], request.match_info["method"]
    form = {}
    if request.method == "POST":
        data = await request.post()
        for k in data:
            v = data[k]
            form[k] = "<file>" if hasattr(v, "filename") else v
    if method != "getUpdates":
        LOG.append({"token": token, "method": method, "p": form})
    if method == "getMe":
        return web.json_response({"ok": True, "result": {"id": 999, "is_bot": True, "first_name": "T", "username": "tbot_" + token.split(":")[0]}})
    if method == "getUpdates":
        q = UPDATES.setdefault(token, [])
        if not q:
            await asyncio.sleep(0.4)
            return web.json_response({"ok": True, "result": []})
        out, UPDATES[token] = q[:], []
        return web.json_response({"ok": True, "result": out})
    if method in ("sendMessage", "sendPhoto", "sendVideo"):
        return web.json_response({"ok": True, "result": {"message_id": next(mid), "date": 1, "chat": chat_obj(form.get("chat_id", 1)), "text": form.get("text")}})
    if method == "copyMessage":
        return web.json_response({"ok": True, "result": {"message_id": next(mid)}})
    if method in ("editMessageCaption", "editMessageText"):
        return web.json_response({"ok": True, "result": {"message_id": 1, "date": 1, "chat": chat_obj(form.get("chat_id", 1))}})
    return web.json_response({"ok": True, "result": True})


async def video_api(request):
    q = request.query
    if request.match_info["src"] == "old":
        if q.get("action") == "home":
            return web.json_response({"data": [{"slug": "s1", "title": "Old One", "thumbnail": BASE + "/thumb.jpg"},
                                                 {"slug": "s2", "title": "Old Two <b>", "thumbnail": BASE + "/thumb.jpg"}]})
        if q.get("action") == "video":
            return web.json_response({"data": {"downloadLink": BASE + "/file.mp4"}})
    else:
        return web.json_response({"data": [{"id": 7, "name": "V65 Seven", "image": BASE + "/thumb.jpg", "video": BASE + "/file.mp4"}]})
    return web.json_response({"data": []})


async def thumb(request):
    b = io.BytesIO(); Image.new("RGB", (640, 360), (200, 60, 60)).save(b, "JPEG")
    return web.Response(body=b.getvalue(), content_type="image/jpeg")


async def mp4(request):
    return web.Response(body=os.urandom(60000), content_type="video/mp4")


def build_app():
    app = web.Application()
    app.add_routes([web.route("*", "/bot{token}/{method}", api), web.get("/api/{src}", video_api),
                    web.get("/thumb.jpg", thumb), web.get("/file.mp4", mp4)])
    return app


def inject(token, update):
    UPDATES.setdefault(token, []).append(update)
