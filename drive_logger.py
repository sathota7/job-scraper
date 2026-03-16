"""Upload run log files to Google Drive under a shared folder."""

from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
from google.oauth2.service_account import Credentials
from loguru import logger

import config

_FOLDER_NAME = "Daily Job Scraper Logs"
_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_or_create_folder(service, name: str) -> str:
    results = (
        service.files()
        .list(
            q=f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id)",
        )
        .execute()
    )
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    folder = (
        service.files()
        .create(
            body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
        )
        .execute()
    )
    logger.info(f"Created Google Drive folder: '{name}'")
    return folder["id"]


def upload_log(log_content: str, run_timestamp: str) -> None:
    """Upload log_content as SCRAPER-{run_timestamp}.txt to the logs folder."""
    try:
        creds = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_PATH,
            scopes=_SCOPES,
        )
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        folder_id = _get_or_create_folder(service, _FOLDER_NAME)
        filename = f"SCRAPER-{run_timestamp}.txt"

        media = MediaInMemoryUpload(
            log_content.encode("utf-8"), mimetype="text/plain", resumable=False
        )
        service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id",
        ).execute()

        logger.info(f"Log uploaded to Drive: '{_FOLDER_NAME}/{filename}'")
    except Exception as e:
        logger.warning(f"Could not upload log to Drive: {e}")
