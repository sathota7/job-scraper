import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

# Load .env BEFORE any project imports so config picks up env vars
load_dotenv()

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

import config

# --- Logging setup ---
os.makedirs(config.LOG_DIR, exist_ok=True)

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    colorize=True,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
)
logger.add(
    os.path.join(config.LOG_DIR, "job_scraper_{time:YYYY-MM-DD}.log"),
    level="DEBUG",
    rotation="10 MB",
    retention="7 days",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
)


def run_pipeline() -> None:
    logger.info("=" * 60)
    logger.info("Pipeline started")
    logger.info("=" * 60)

    try:
        # Step 1: Scrape
        from scraper import scrape_all
        logger.info("Step 1/3 — Scraping jobs...")
        jobs_df = scrape_all()

        if jobs_df.empty:
            logger.warning("No jobs scraped. Pipeline complete (nothing to score/write).")
            return

        logger.info(f"Step 1 complete: {len(jobs_df)} jobs scraped")

        # Step 2: Score
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


def main() -> None:
    logger.info("Job Scraper starting up")
    logger.info(f"Schedule: every {config.SCHEDULE_INTERVAL_HOURS} hour(s)")

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(
        run_pipeline,
        trigger=IntervalTrigger(hours=config.SCHEDULE_INTERVAL_HOURS),
        next_run_time=datetime.now(timezone.utc),  # run immediately on start
        max_instances=1,
        coalesce=True,
        id="job_scraper_pipeline",
    )

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Shutting down scheduler (KeyboardInterrupt)")
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    main()
