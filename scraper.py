import pandas as pd
from datetime import datetime, timezone
from loguru import logger
from jobspy import scrape_jobs

import config


def _is_media_company(company_name: str) -> bool:
    if not company_name or not isinstance(company_name, str):
        return False
    name_lower = company_name.lower()
    return any(kw in name_lower for kw in config.MEDIA_COMPANY_KEYWORDS)


def _should_exclude(company_name: str) -> bool:
    if not config.EXCLUDE_COMPANY_KEYWORDS:
        return False
    if not company_name or not isinstance(company_name, str):
        return False
    name_lower = company_name.lower()
    return any(kw in name_lower for kw in config.EXCLUDE_COMPANY_KEYWORDS)


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
            is_remote=is_remote if is_remote else None,
        )
        if df is not None and not df.empty:
            logger.debug(
                f"  {site} | '{query}' | {location} → {len(df)} results"
            )
            return df
    except Exception as e:
        logger.warning(f"  Failed {site} | '{query}' | {location}: {e}")
    return pd.DataFrame()


def scrape_all() -> pd.DataFrame:
    all_frames: list[pd.DataFrame] = []

    total_combos = len(config.SITES) * len(config.SEARCH_QUERIES) * len(config.LOCATIONS)
    logger.info(f"Starting scrape: {total_combos} combinations across sites/queries/locations")

    for site in config.SITES:
        for query in config.SEARCH_QUERIES:
            for location in config.LOCATIONS:
                df = _scrape_combination(site, query, location)
                if not df.empty:
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

    # Apply exclusion filter
    if config.EXCLUDE_COMPANY_KEYWORDS:
        before = len(combined)
        combined = combined[~combined["company"].apply(_should_exclude)].reset_index(drop=True)
        logger.info(f"After exclusion filter: {len(combined)} jobs (removed {before - len(combined)})")

    # Add metadata columns
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    combined["date_scraped"] = now_str
    combined["is_media_company"] = combined["company"].apply(_is_media_company)

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

    # Prioritize media companies first, then cap
    media_mask = combined["is_media_company"]
    media_jobs = combined[media_mask]
    non_media_jobs = combined[~media_mask]
    combined = pd.concat([media_jobs, non_media_jobs], ignore_index=True)

    if len(combined) > config.MAX_JOBS_PER_RUN:
        logger.info(f"Capping at {config.MAX_JOBS_PER_RUN} jobs (had {len(combined)})")
        combined = combined.iloc[: config.MAX_JOBS_PER_RUN].reset_index(drop=True)

    logger.info(
        f"Final job set: {len(combined)} jobs "
        f"({combined['is_media_company'].sum()} media companies)"
    )
    return combined
