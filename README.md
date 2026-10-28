# bot_server — runs the Telegram bots

Reads every enabled bot from Firebase Firestore and runs it with aiogram 3 (long polling).
No web server, **no open port**. Bots are created/edited in the admin panel.

```bash
python3 -m venv venv && source venv/bin/activate      # Python 3.10+
pip install -r requirements.txt
cp .env.example .env              # optional tuning (TIMEZONE=Asia/Dhaka …)
# put firebase_key.json in this folder
python main.py
```
Keep it alive with systemd / pm2 / supervisor, e.g.
```
[Service]
WorkingDirectory=/path/to/bot_server
ExecStart=/path/to/bot_server/venv/bin/python main.py
Restart=always
```

## What it does
* Starts / restarts / stops bots when the `bots` collection changes (token, enabled, API server).
* **Group confirmation**: when the bot is added to a group nothing is saved. It posts ✅ Confirm / ❌ Reject buttons;
  only a Telegram group admin (or the bot owner / Admin ID) can press them. ✅ → the group id is saved to Firestore and
  the bot starts working there. ❌ or no answer within `GROUP_CONFIRM_SECONDS` (default 300) → the bot sends the reject
  message (default `FUCK you group admin`, change it with `GROUP_REJECT_TEXT` in `.env` or `messages.group_reject` in
  the bot's config) and leaves the group. Start taps in unconfirmed groups are ignored.
* Group “🚀 Start” registration (ON/OFF from the panel), DM registration, auto-removal of groups the bot is kicked from,
  admin menu in Telegram (Broadcast, Status, Run Cron, Mode, Browse Videos), the video pipeline
  (Old API + VID65 API, blurred channel previews, channel cleanup, duplicate protection).
* Executes **commands** left by the panel (`_commands`): run cron, broadcast, post/remove Start button.
* Publishes a heartbeat + running bots to `_meta/runtime` every 45 s.

Notes: make the bot a group **admin** (or disable privacy mode in @BotFather) so it sees group “Start” taps.
Files over 50 MB need a local Telegram Bot API server (set in the panel under Settings → Telegram API servers).
Don't run two bot servers on the same bots at the same time.
