"""Creates the Firestore client. KEEP IDENTICAL IN BOTH PROJECTS."""
import json

from google.cloud.firestore_v1 import AsyncClient

from . import config


def make_client():
    from google.oauth2 import service_account
    if config.FIREBASE_CREDENTIALS_JSON:
        info = json.loads(config.FIREBASE_CREDENTIALS_JSON)
    elif config.FIREBASE_CREDENTIALS.is_file():
        info = json.loads(config.FIREBASE_CREDENTIALS.read_text(encoding="utf-8"))
    else:
        raise RuntimeError(
            f"Firebase key not found: {config.FIREBASE_CREDENTIALS}. Download a service-account JSON from "
            "Firebase console -> Project settings -> Service accounts, or set FIREBASE_CREDENTIALS_JSON.")
    creds = service_account.Credentials.from_service_account_info(info)
    return AsyncClient(project=info["project_id"], credentials=creds, database=config.FIRESTORE_DATABASE)
