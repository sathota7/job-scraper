import io
import os
import sys
from datetime import datetime

from dotenv import load_dotenv

# Load .env BEFORE any project imports so config picks up env vars
load_dotenv()

from loguru import logger

import config

# --- Logging setup ---
os.makedirs(config.LOG_DIR, exist_ok=True)

_run_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
_log_buffer = io.StringIO()

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
)
logger.add(
    os.path.join(config.LOG_DIR, "job_scraper_{time:YYYY-MM-DD_HH-mm-ss}.log"),
    level="DEBUG",
    rotation="10 MB",
    retention="7 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
)
logger.add(
    _log_buffer,
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
)


def run_pipeline() -> None:
    logger.info("=" * 60)
    logger.info("Pipeline started")
    logger.info("=" * 60)

    try:
        # Step 0: Sync feedback — reads manual scores from the sheet and
        # re-synthesizes user preferences if new scores have appeared since last run
        import feedback
        import scorer
        logger.info("Step 0/3 — Syncing feedback from sheet...")
        feedback.sync_and_maybe_synthesize()
        scorer.reset_feedback_context()  # ensure scorer picks up any updated preferences

        # Step 1: Scrape
        from scraper import scrape_all
        logger.info("Step 1/3 — Scraping jobs...")
        jobs_df = scrape_all()

        if jobs_df.empty:
            logger.warning("No jobs scraped. Pipeline complete (nothing to score/write).")
            return

        logger.info(f"Step 1 complete: {len(jobs_df)} jobs scraped")

        # Step 2: Score (uses updated feedback context loaded in Step 0)
        from scorer import score_jobs
        logger.info("Step 2/3 — Scoring jobs with Claude...")
        scored_df = score_jobs(jobs_df)
        logger.info("Step 2 complete: scoring done")

        # Step 3: Write to Sheets
        from sheets import write_new_jobs
        logger.info("Step 3/3 — Writing qualifying jobs to Google Sheets...")
        written = write_new_jobs(scored_df)
        logger.info(f"Step 3 complete: {written} new rows written to Sheets")

    except FileNotFoundError as e:
        logger.error(f"Setup error: {e}")
    except Exception as e:
        logger.exception(f"Unexpected pipeline error: {e}")

    logger.info("Pipeline finished")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_pipeline()

    from drive_logger import upload_log
    upload_log(_log_buffer.getvalue(), _run_timestamp)
