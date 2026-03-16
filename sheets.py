import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from loguru import logger

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_gc: gspread.Client | None = None
_worksheet: gspread.Worksheet | None = None


def _get_client() -> gspread.Client:
    global _gc
    if _gc is None:
        creds = Credentials.from_service_account_file(config.CREDENTIALS_PATH, scopes=SCOPES)
        _gc = gspread.authorize(creds)
    return _gc


def _get_or_create_worksheet() -> gspread.Worksheet:
    global _worksheet
    if _worksheet is not None:
        return _worksheet

    gc = _get_client()
    try:
        spreadsheet = gc.open(config.SPREADSHEET_NAME)
        logger.debug(f"Opened existing spreadsheet: '{config.SPREADSHEET_NAME}'")
    except gspread.SpreadsheetNotFound:
        logger.info(f"Spreadsheet '{config.SPREADSHEET_NAME}' not found — creating it")
        spreadsheet = gc.create(config.SPREADSHEET_NAME)

    try:
        ws = spreadsheet.worksheet(config.WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        logger.info(f"Worksheet '{config.WORKSHEET_NAME}' not found — creating with headers")
        ws = spreadsheet.add_worksheet(
            title=config.WORKSHEET_NAME, rows=10000, cols=len(config.SHEET_COLUMNS)
        )
        ws.append_row(config.SHEET_COLUMNS, value_input_option="USER_ENTERED")

    _worksheet = ws
    return ws


def _get_existing_job_ids() -> set[str]:
    ws = _get_or_create_worksheet()
    try:
        col_values = ws.col_values(1)  # Column A = job_id
        # Skip header row
        existing = set(v.strip() for v in col_values[1:] if v.strip())
        logger.debug(f"Found {len(existing)} existing job_ids in sheet")
        return existing
    except Exception as e:
        logger.warning(f"Could not read existing job_ids: {e}")
        return set()


def _job_to_row(job: pd.Series) -> list:
    row = []
    for col in config.SHEET_COLUMNS:
        val = job.get(col, "")
        if pd.isna(val) if not isinstance(val, str) else False:
            val = ""
        row.append(str(val) if val is not None else "")
    return row


def write_new_jobs(scored_df: pd.DataFrame) -> int:
    if scored_df.empty:
        logger.info("No jobs to write to Sheets.")
        return 0

    existing_ids = _get_existing_job_ids()

    # Filter: not already in sheet AND score >= MIN_SCORE_TO_WRITE
    mask = (
        ~scored_df["job_id"].astype(str).isin(existing_ids)
        & (scored_df["fit_score"] >= config.MIN_SCORE_TO_WRITE)
    )
    new_jobs = (
        scored_df[mask]
        .sort_values("fit_score", ascending=False)
        .head(config.MAX_JOBS_PER_RUN)
        .reset_index(drop=True)
    )

    if new_jobs.empty:
        logger.info(
            f"No new qualifying jobs to write "
            f"(all either already in sheet or score < {config.MIN_SCORE_TO_WRITE})"
        )
        return 0

    logger.info(f"Writing {len(new_jobs)} new jobs to Google Sheets...")

    ws = _get_or_create_worksheet()
    rows = [_job_to_row(job) for _, job in new_jobs.iterrows()]

    # Write in batches of 50
    batch_size = 50
    written = 0
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        ws.append_rows(batch, value_input_option="USER_ENTERED")
        written += len(batch)
        logger.debug(f"  Wrote batch {i // batch_size + 1}: {len(batch)} rows")

    logger.info(f"Successfully wrote {written} rows to '{config.SPREADSHEET_NAME}'")
    return written
