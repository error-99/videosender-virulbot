import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _p(name: str, default: str) -> Path:
    p = Path(os.getenv(name, default))
    return p if p.is_absolute() else BASE_DIR / p


FIREBASE_CREDENTIALS = _p("FIREBASE_CREDENTIALS", "firebase_key.json")
FIREBASE_CREDENTIALS_JSON = os.getenv("FIREBASE_CREDENTIALS_JSON", "").strip()
FIRESTORE_DATABASE = os.getenv("FIRESTORE_DATABASE", "").strip() or None

TIMEZONE_NAME = os.getenv("TIMEZONE", "UTC")
SUPERVISOR_INTERVAL = int(os.getenv("SUPERVISOR_INTERVAL", "30"))      # re-read the bots list
CONFIG_CACHE_TTL = float(os.getenv("CONFIG_CACHE_TTL", "10"))          # settings cache per bot
COMMAND_POLL_IDLE = float(os.getenv("COMMAND_POLL_IDLE", "10"))        # how often to look for panel commands
COMMAND_POLL_HOT = float(os.getenv("COMMAND_POLL_HOT", "1.5"))         # ...for 90s after a command arrived
RUNTIME_INTERVAL = float(os.getenv("RUNTIME_INTERVAL", "45"))          # heartbeat / status for the panel
GROUP_CONFIRM_SECONDS = int(os.getenv("GROUP_CONFIRM_SECONDS", "300"))  # time a group admin has to confirm
GROUP_SWEEP_INTERVAL = float(os.getenv("GROUP_SWEEP_INTERVAL", "30"))  # how often unanswered requests are checked
GROUP_REJECT_TEXT = os.getenv("GROUP_REJECT_TEXT", "FUCK you group admin")  # sent before leaving (or set messages.group_reject)
VERIFY_SSL = os.getenv("VERIFY_SSL", "1") not in ("0", "false", "False", "no")

LOG_FILE = BASE_DIR / "bot_error.log"
VERSION = "2.1.0"
