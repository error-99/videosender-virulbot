# TG Control — Telegram bots (aiogram 3) + Admin panel, on Firebase

One Python app that does three things:

1. runs all your Telegram bots (aiogram 3, long polling),
2. stores everything in **Firebase Firestore**,
3. serves a modern **admin web panel** with a one-click **Backup** button.

No PHP, no MySQL, no separate web host. Bots are created and edited in the panel;
the app starts, restarts and stops them by itself within seconds.

---------------------------------------------------------------------------
## 1. Set up Firebase (5 minutes, free plan is enough)

1. Go to https://console.firebase.google.com → **Add project**.
2. **Build → Firestore Database → Create database** → choose **Production mode** and a region near you.
3. **Project settings (gear) → Service accounts → Generate new private key** → save the file as
   `firebase_key.json` next to `main.py`.
4. (Recommended) **Firestore → Rules**: only this server talks to the database, so lock the public door:
   ```
   rules_version = '2';
   service cloud.firestore { match /databases/{db}/documents { match /{document=**} { allow read, write: if false; } } }
   ```
   The server uses the service account and is not affected by these rules.

No indexes have to be created — the app only uses simple queries.

## 2. Install and run

```bash
python3 -m venv venv && source venv/bin/activate      # Python 3.10+
pip install -r requirements.txt
cp .env.example .env          # set ADMIN_PASSWORD (and TIMEZONE, e.g. Asia/Dhaka)
python main.py
```
Open **http://YOUR_SERVER:8080** and sign in with `ADMIN_PASSWORD`.
Put it behind HTTPS (Caddy / nginx / Cloudflare Tunnel) if it is reachable from the internet.

Keep it running with systemd, e.g. `/etc/systemd/system/tgcontrol.service`:
```
[Service]
WorkingDirectory=/path/to/tgbot_firebase
ExecStart=/path/to/tgbot_firebase/venv/bin/python main.py
Restart=always
[Install]
WantedBy=multi-user.target
```

## 3. Using the panel

| Page | What you can do |
|------|-----------------|
| **Dashboard** | totals across all bots, 14-day growth chart, bot status |
| **Bots → Add bot** | paste the @BotFather token, your Telegram ID → the bot starts automatically |
| **Bot → Overview** | **Group “🚀 Start” button ON/OFF**, Mode ON/OFF, post/remove the button in all groups, cron position, external cron URL |
| **Bot → Settings** | name, token (with “Check token”), admin ID, behaviour numbers, Telegram API servers, delete bot |
| **Bot → Sources & targets** | API URLs, target groups and channels, reset cron position |
| **Bot → Messages** | start / welcome / caption texts |
| **Bot → Users** | search, filter blocked, load more, **export CSV** |
| **Bot → Broadcast** | message to all active users with live progress |
| **Bot → Activity** | log of important events |
| **💾 Backups** | **Create backup & download**, save on server, restore from file/server, delete |
| **System** | Firebase connection and latency, uptime |

### Group Start button OFF
Turning it OFF in the panel removes the button from every group immediately, stops posting it
(new groups, videos) and ignores old button taps. Turn it ON again and press
“Post button in all groups” (or it comes back with the next video).

## 4. Backups
* **Create backup & download** gives you one `.json` file with *everything*: bots, settings, users,
  groups, history, logs. “Include bot tokens & secrets” should stay ON for a full restore — keep such files private.
* **Automatic backups**: every `AUTO_BACKUP_HOURS` (default 24) a compressed copy is saved in `backups/`;
  the newest `BACKUP_KEEP` (default 14) are kept. Set `AUTO_BACKUP_HOURS=0` to turn off.
* **Restore** (file upload or from the server list) merges the backup into Firebase: items in the file
  overwrite the same items, anything else is left alone. A `pre-restore-…` safety backup is made first.
* Restoring onto a brand-new Firebase project works too (it recreates all bots).

## 5. Moving your old MySQL data to Firebase (one time)
```bash
pip install pymysql
# add MYSQL_HOST / MYSQL_DB / MYSQL_USER / MYSQL_PASS to .env
python tools/import_from_mysql.py --dry-run     # see what would be copied
python tools/import_from_mysql.py               # copy bots, users, groups, history
```
MySQL is only read, never changed. Run it once, then stop the old PHP/MySQL bot.
Don't run two bots with the same token at the same time.

## 6. Data layout in Firestore
```
bots/{bot_id}                        settings, token, admin_id, config{telegram, apis, settings, messages}
bots/{bot_id}/users/{user_id}        username, first_name, joined_at, last_seen, status, source
bots/{bot_id}/chats/{chat_id}        groups / channels the bot knows
bots/{bot_id}/channel_history/{ch}   last message ids per channel (auto-cleanup)
bots/{bot_id}/sent_videos/{hash}     duplicate-video protection
bots/{bot_id}/broadcasts/{id}        broadcast history
bots/{bot_id}/logs/{id}              activity log (pruned after 30 days)
```

## 7. Good to know
* Telegram sends group "Start" taps only if the bot can see them: make the bot a group **admin**
  (or disable privacy mode in @BotFather).
* Files over 50 MB need a local Telegram Bot API server — enter it under *Settings → Telegram API servers*.
* External cron trigger (optional): `GET /run_cron?bot=BOT_ID&secret=CRON_SECRET&source=old|vid65|both`.
* Firestore free plan: 50k reads / 20k writes per day. The app caches settings and user activity to stay far below that.
  "Active 24h" counts users whose last message was in the last 24 hours.
* A Bot ID can't be renamed after creation (it is the Firestore document id).

## 8. Tests (optional)
`python tests/e2e.py` runs the whole app against an in-memory fake of Firestore and a fake Telegram server
(81 checks). `python tests/test_import.py` tests the MySQL importer (needs a MySQL server).
