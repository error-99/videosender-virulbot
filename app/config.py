import os
import secrets
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

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
GENERATED_PASSWORD = False
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD, GENERATED_PASSWORD = secrets.token_urlsafe(9), True
SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip() or ("tgbot-" + ADMIN_PASSWORD)
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

TIMEZONE_NAME = os.getenv("TIMEZONE", "UTC")
BACKUP_DIR = _p("BACKUP_DIR", "backups")
AUTO_BACKUP_HOURS = float(os.getenv("AUTO_BACKUP_HOURS", "24"))
BACKUP_KEEP = int(os.getenv("BACKUP_KEEP", "14"))

SUPERVISOR_INTERVAL = int(os.getenv("SUPERVISOR_INTERVAL", "30"))
CONFIG_CACHE_TTL = float(os.getenv("CONFIG_CACHE_TTL", "10"))
VERIFY_SSL = os.getenv("VERIFY_SSL", "1") not in ("0", "false", "False", "no")

LOG_FILE = BASE_DIR / "bot_error.log"
STATIC_DIR = BASE_DIR / "app" / "web" / "static"
VERSION = "2.0.0"
