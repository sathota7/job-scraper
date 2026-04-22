import os
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from loguru import logger
from jobspy import scrape_jobs

import config



def _should_exclude(company_name: str) -> bool:
    if not config.EXCLUDE_COMPANY_KEYWORDS:
        return False
    if not company_name or not isinstance(company_name, str):
        return False
    name_lower = company_name.lower()
    return any(kw in name_lower for kw in config.EXCLUDE_COMPANY_KEYWORDS)


def _is_excluded_title(title: str) -> bool:
    if not config.EXCLUDE_TITLE_KEYWORDS:
        return False
    if not title or not isinstance(title, str):
        return False
    title_lower = title.lower()
    return any(kw in title_lower for kw in config.EXCLUDE_TITLE_KEYWORDS)


def _scrape_combination(site: str, query: str, location: str) -> pd.DataFrame:
    is_remote = location.lower() == "remote"
    try:
        df = scrape_jobs(
            site_name=[site],
            search_term=query,
            location=location if not is_remote else "United States",
            results_wanted=config.RESULTS_PER_QUERY,
            distance=config.DISTANCE_MILES,
            linkedin_fetch_description=True,
            is_remote=is_remote,
            hours_old=336,  # 2 weeks
        )
        if df is not None and not df.empty:
            logger.debug(
                f"  {site} | '{query}' | {location} → {len(df)} results"
            )
            return df
    except Exception as e:
        logger.warning(f"  Failed {site} | '{query}' | {location}: {e}")
    return pd.DataFrame()


def _write_prescore_log(df: pd.DataFrame) -> None:
    """Write title + company for all scraped jobs (pre-media-filter) to a dated log file."""
    os.makedirs(config.LOG_DIR, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    log_path = os.path.join(config.LOG_DIR, f"scraped_jobs_{timestamp}.log")
    lines = [f"Scraped jobs — {timestamp} UTC ({len(df)} total, pre-media-filter)\n"]
    lines.append(f"{'#':<5} {'Title':<55} Company")
    lines.append("-" * 90)
    for i, (_, row) in enumerate(df.iterrows(), 1):
        title = str(row.get("title", "")).strip()[:54]
        company = str(row.get("company", "")).strip()
        lines.append(f"{i:<5} {title:<55} {company}")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    logger.info(f"Pre-filter job list written to {log_path}")


def scrape_all() -> pd.DataFrame:
    combinations = [
        (site, query, location)
        for site in config.SITES
        for query in config.SEARCH_QUERIES
        for location in config.LOCATIONS
    ]
    logger.info(
        f"Starting scrape: {len(combinations)} combinations across sites/queries/locations "
        f"({config.SCRAPING_CONCURRENT_WORKERS} workers)"
    )

    all_frames: list[pd.DataFrame] = []
    with ThreadPoolExecutor(max_workers=config.SCRAPING_CONCURRENT_WORKERS) as executor:
        futures = {
            executor.submit(_scrape_combination, s, q, loc): (s, q, loc)
            for s, q, loc in combinations
        }
        for future in as_completed(futures):
            df = future.result()
            if df is not None and not df.empty:
                all_frames.append(df)

    if not all_frames:
        logger.warning("No jobs scraped across all combinations.")
        return pd.DataFrame()

    combined = pd.concat(all_frames, ignore_index=True)
    logger.info(f"Raw scraped rows (before dedup): {len(combined)}")

    # Rename 'id' to 'job_id' if present
    if "id" in combined.columns and "job_id" not in combined.columns:
        combined = combined.rename(columns={"id": "job_id"})

    # Ensure job_id column exists
    if "job_id" not in combined.columns:
        combined["job_id"] = combined.apply(
            lambda r: f"{r.get('site','')}-{r.get('title','')}-{r.get('company','')}".replace(" ", "_")[:80],
            axis=1,
        )

    # Deduplicate by job_id within this run
    combined = combined.drop_duplicates(subset=["job_id"], keep="first").reset_index(drop=True)
    logger.info(f"After dedup by job_id: {len(combined)} unique jobs")

    # Secondary dedup by (title, company) — catches the same job posted on multiple
    # sites or returned by multiple search queries with different job_ids
    before = len(combined)
    combined["_title_norm"] = combined["title"].str.strip().str.lower()
    combined["_company_norm"] = combined["company"].str.strip().str.lower()
    combined = combined.drop_duplicates(subset=["_title_norm", "_company_norm"], keep="first").reset_index(drop=True)
    combined = combined.drop(columns=["_title_norm", "_company_norm"])
    logger.info(f"After dedup by title+company: {len(combined)} unique jobs (removed {before - len(combined)})")

    # Apply exclusion filter
    if config.EXCLUDE_COMPANY_KEYWORDS:
        before = len(combined)
        combined = combined[~combined["company"].apply(_should_exclude)].reset_index(drop=True)
        logger.info(f"After exclusion filter: {len(combined)} jobs (removed {before - len(combined)})")

    # Apply title exclusion filter
    if config.EXCLUDE_TITLE_KEYWORDS:
        before = len(combined)
        combined = combined[~combined["title"].apply(_is_excluded_title)].reset_index(drop=True)
        logger.info(f"After title exclusion filter: {len(combined)} jobs (removed {before - len(combined)})")

    # Log all scraped jobs (pre-media-filter) — title + company only
    _write_prescore_log(combined)

    # Add metadata columns
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    combined["date_scraped"] = now_str

    # Description snippet (300 chars)
    desc_col = "description" if "description" in combined.columns else None
    if desc_col:
        combined["description_snippet"] = combined[desc_col].apply(
            lambda d: str(d)[:300].strip() if pd.notna(d) else ""
        )
    else:
        combined["description_snippet"] = ""

    # Normalize date_posted to string
    if "date_posted" in combined.columns:
        combined["date_posted"] = combined["date_posted"].apply(
            lambda d: str(d) if pd.notna(d) else ""
        )
    else:
        combined["date_posted"] = ""

    # Normalize job_url
    if "job_url" not in combined.columns:
        combined["job_url"] = ""

    # Ensure site column exists
    if "site" not in combined.columns:
        combined["site"] = ""

    # Ensure location/company/title columns exist
    for col in ["title", "company", "location"]:
        if col not in combined.columns:
            combined[col] = ""

    logger.info(f"{len(combined)} jobs passed title/company filters — passing to Claude for scoring")
    return combined
