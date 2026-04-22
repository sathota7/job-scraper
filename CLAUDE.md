# Job Scraper — Codebase Guide

## What this project does

Automated job search pipeline for an early-career job seeker targeting junior project management roles. It scrapes LinkedIn and Indeed daily, scores each job 1–10 against a resume using Claude, and writes qualifying results to a Google Sheet. For well-matched jobs it generates specific resume tailoring bullets. A reinforcement-learning feedback loop reads manual scores the user enters in the sheet and progressively calibrates the AI scorer to the user's preferences.

---

## Architecture overview

```
main.py
  └── Step 0: feedback.sync_and_maybe_synthesize()   # read manual scores → update calibration
  └── Step 1: scraper.scrape_all()                   # jobspy → LinkedIn + Indeed → DataFrame
  └── Step 2: scorer.score_jobs()                    # Claude Haiku scoring + Sonnet suggestions
  └── Step 3: sheets.write_new_jobs()                # append qualifying rows to Google Sheet
```

External dependencies:
- **python-jobspy** — wraps LinkedIn/Indeed search, returns a pandas DataFrame
- **Anthropic API** — Claude Haiku for scoring (cheap), Claude Sonnet for suggestions (richer)
- **Google Sheets API (gspread)** — result storage and the feedback input channel
- **Google Drive API** — optional log upload after each run

---

## Module breakdown

### `main.py`
Entry point. Sets up loguru logging (stderr + rotating file in `logs/`), then calls `run_pipeline()`. The pipeline runs all four steps in order; a failure in Step 0–2 is caught and logged without crashing the process.

### `config.py`
All tunable parameters in one place. Key sections:
- **JOB_CATEGORIES / ACTIVE_CATEGORIES / SEARCH_QUERIES** — what to search for. Add a new category dict entry and add its key to `ACTIVE_CATEGORIES` to include it.
- **SITES / LOCATIONS** — where to search. Currently `["linkedin", "indeed"]` and `["New York, NY"]`.
- **EXCLUDE_TITLE_KEYWORDS** — pre-score title filter; blocks senior/director/unrelated roles before they reach Claude.
- **SEARCH_SENSITIVITY** (`"strict"` / `"balanced"` / `"liberal"`) — controls `MIN_SCORE_TO_WRITE` and `SUGGESTIONS_MIN_SCORE` thresholds via a lookup table. Currently `"balanced"` → writes jobs ≥ 5, generates suggestions for jobs ≥ 7.
- **SCORING_MODEL** / **SUGGESTIONS_MODEL** — `claude-haiku-4-5-20251001` for scoring, `claude-sonnet-4-6` for suggestions.
- **SHEET_COLUMNS** — defines column order in the Google Sheet. Order matters; `sheets.py` iterates this list.
- **FEEDBACK_CACHE_PATH** / **USER_PREFERENCES_PATH** — RL feedback files (see Feedback Loop section).

### `scraper.py`
Builds every combination of `(site, query, location)` from config and runs them in parallel via `ThreadPoolExecutor` (`SCRAPING_CONCURRENT_WORKERS`, default 4). Results are concatenated and deduplicated in two passes:
1. By `job_id` (site-provided unique ID)
2. By normalized `(title, company)` — catches the same posting returned by multiple queries or sites

After dedup, `EXCLUDE_TITLE_KEYWORDS` is applied as a fast pre-filter. A pre-score log (`logs/scraped_jobs_<timestamp>.log`) is written listing every job that will go to Claude. Jobs get `date_scraped`, `description_snippet` (300 chars), and normalized `date_posted` / `job_url` columns appended.

**Remote search**: if a location string is `"Remote"`, jobspy is called with `location="United States"` and `is_remote=True`.

**Lookback window**: `hours_old=336` (2 weeks). All scrapes use `linkedin_fetch_description=True` so the full description is available for scoring.

### `scorer.py`
Two Claude calls per qualifying job:

1. **Scoring** (`claude-haiku-4-5-20251001`, 300 tokens) — returns `{"score": int, "reasoning": str}` JSON. A regex fallback and a last-resort integer extractor handle malformed responses.
2. **Suggestions** (`claude-sonnet-4-6`, 600 tokens) — only called if `score >= SUGGESTIONS_MIN_SCORE`. Returns 3–5 bullet points, pipe-joined for clean sheet storage.

Both calls use a shared `threading.BoundedSemaphore` (`SCORING_CONCURRENT_REQUESTS`, default 5) and a global `_api_lock` that enforces `SCORING_RATE_LIMIT_DELAY` (1.5s) between API call *initiations* across all threads. Rate-limit errors trigger a 60-second sleep and one retry.

The scoring system prompt is built dynamically: it includes sensitivity-dependent stance text, scoring weights, and the injected feedback context (synthesized preferences + calibration examples).

**Lazy singletons**: the Anthropic client, resume text, brag sheet text, and feedback context are each loaded once per process and cached in module-level globals. `reset_feedback_context()` clears the feedback cache so main.py can force a reload after Step 0 syncs new preferences.

### `sheets.py`
Opens (or creates) the spreadsheet and `Jobs` worksheet. Before writing, reads column A to get all existing `job_id` values — the sheet acts as the cross-run deduplication database. Only rows where `job_id` is new AND `fit_score >= MIN_SCORE_TO_WRITE` are appended, sorted descending by score, capped at `MAX_JOBS_PER_RUN`. Writes in batches of 50 rows.

The `manual_score` and `manual_score_reasoning` columns are always written blank for new rows — they are user-fill-in fields that drive the feedback loop.

### `feedback.py`
The RL calibration layer. Called as Step 0 of every pipeline run.

**`sync_from_sheet()`** — reads the Jobs worksheet, extracts every row where `manual_score` is a valid integer 1–10, and writes them to `data/feedback_cache.json`. Stores `ai_score`, `manual_score`, `diff` (manual − AI), and the user's reasoning text.

**`sync_and_maybe_synthesize()`** — the main entry point:
1. Compares old cache count vs new count.
2. If new scores appeared → calls `synthesize_preferences()` to regenerate `data/user_preferences.txt` via Claude Sonnet.
3. Writes the synthesized text to the `Config` tab of the Google Sheet (key-value store) so it survives stateless runners (GitHub Actions).
4. Always restores preferences from the Config tab to the local file — critical for GitHub Actions where `data/` isn't persisted between runs.

**`synthesize_preferences()`** — sends all rated examples to Claude Sonnet with a prompt asking it to write a directive calibration guide (150–200 words). Output is saved to `data/user_preferences.txt` and upserted into the Config sheet.

**CLI usage** (run independently for inspection):
```bash
python3 feedback.py stats               # cache summary and top misses
python3 feedback.py sync                # pull sheet → cache
python3 feedback.py synthesize          # regenerate preferences from cache
python3 feedback.py sync-and-synthesize # both
```

**Calibration example selection** in `scorer._load_feedback_context()`: picks the examples where the AI was most wrong (`|diff| > 1`), plus up to 2 correct examples, capped at `FEEDBACK_MAX_EXAMPLES` (8 total). Injects them into every scoring prompt so Claude can see where it previously over/underscored.

### `drive_logger.py`
Optional. Creates/finds a Google Drive folder called `"Daily Job Scraper Logs"` and uploads the run log as `SCRAPER-<timestamp>.txt`. Errors are caught and logged as warnings so they never break the pipeline.

---

## Key data files

| File | Purpose |
|---|---|
| `data/resume.txt` | Plain-text resume — required, not in git |
| `data/brag_sheet.txt` | Optional additional achievements/context for suggestions |
| `data/user_preferences.txt` | Synthesized calibration guide, auto-regenerated by feedback loop |
| `data/feedback_cache.json` | Raw scored examples pulled from the sheet |
| `credentials.json` | Google service account JSON — not in git |
| `.env` | `ANTHROPIC_API_KEY`, `GOOGLE_CREDENTIALS_PATH`, `SPREADSHEET_NAME` |
| `logs/job_scraper_<ts>.log` | Full debug log per run (rotated at 10 MB, 7-day retention) |
| `logs/scraped_jobs_<ts>.log` | Pre-score listing of every job seen (title + company only) |
| `logs/cron.log` | stdout/stderr capture from cron/Raspberry Pi runs |

---

## Google Sheet structure

**Jobs tab** — one row per qualifying job, columns defined by `config.SHEET_COLUMNS`:

| Column | Source | User edits? |
|---|---|---|
| `job_id` | jobspy | No |
| `date_scraped` | pipeline | No |
| `title`, `company`, `location`, `site`, `job_url` | jobspy | No |
| `fit_score` | Claude Haiku | No |
| `manual_score` | — (blank) | Yes — drives RL |
| `manual_score_reasoning` | — (blank) | Yes — drives RL |
| `reasoning` | Claude Haiku | No |
| `resume_suggestions` | Claude Sonnet | No |
| `date_posted`, `description_snippet` | jobspy | No |

**Config tab** — key-value store. Row `user_preferences` holds the synthesized calibration text so it survives between stateless runs.

---

## Deployment options

### Local / manual
```bash
python main.py
```

### GitHub Actions (`.github/workflows/scrape.yml`)
Runs at `cron: "0 9 * * *"` (9 AM UTC daily) and on `workflow_dispatch`. Secrets required:
- `ANTHROPIC_API_KEY`
- `GOOGLE_CREDENTIALS_JSON` (full JSON content of credentials.json)
- `SPREADSHEET_NAME`

The workflow writes `credentials.json` from the secret each run. The Config sheet tab is how synthesized preferences survive between stateless Actions runs.

### Raspberry Pi (`pi_setup.md`)
Cron-based daily run at 8 AM local time. The cron line does `git pull --ff-only` before each run so it stays current automatically:
```
0 8 * * * cd /home/sathota/Job-Scraper && git pull --ff-only && /home/sathota/Job-Scraper/.venv/bin/python /home/sathota/Job-Scraper/main.py >> logs/cron.log 2>&1
```
Hostname `saipi`, username `sathota`. SSH: `ssh sathota@saipi.local`.

---

## Cost model

Approximately $0.085 per run (100 jobs, ~30 qualifying for suggestions):
- Scoring: Claude Haiku — ~$0.013
- Suggestions: Claude Sonnet — ~$0.072
- Daily for 30 days ≈ ~$2.55/month

To reduce cost: set `SUGGESTIONS_MODEL = "claude-haiku-4-5-20251001"` in `config.py`.

---

## How the feedback loop learns

1. User fills in `manual_score` (1–10) and `manual_score_reasoning` for any jobs in the sheet.
2. Next pipeline run → `feedback.sync_and_maybe_synthesize()` detects the new count.
3. Claude Sonnet analyzes all scored examples and writes a directive calibration guide to `data/user_preferences.txt`.
4. The guide is also saved to the Config sheet tab (persists across stateless runs).
5. `scorer.reset_feedback_context()` clears the in-process cache so the updated guide is loaded before scoring begins.
6. During scoring, the guide is prepended to every scoring prompt; the worst-miss calibration examples are appended.

The current synthesized preferences (`data/user_preferences.txt`) encode: hard cap at 4 for roles requiring 4+ years experience, no penalty for industry switching, extra credit for explicitly junior titles, strong downgrade for news media roles, no score above 4 for contract/temp/internship roles.

---

## Common tasks

**Add a new search query category:**
1. Add an entry to `JOB_CATEGORIES` in `config.py`.
2. Add its key to `ACTIVE_CATEGORIES`.

**Change the target city:**
Edit `LOCATIONS` in `config.py`. Add `"Remote"` to also search remote roles.

**Tighten or loosen the scoring filter:**
Change `SEARCH_SENSITIVITY` between `"strict"` / `"balanced"` / `"liberal"`.

**Force a preference re-synthesis:**
```bash
python3 feedback.py sync-and-synthesize
```

**Inspect scoring calibration:**
```bash
python3 feedback.py stats
```

**Run one pipeline manually:**
```bash
python main.py
```
