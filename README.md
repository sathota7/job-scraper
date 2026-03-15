# Job Scraper

Scrapes LinkedIn and Indeed for project management and media buying roles at media companies, scores each job 1–10 against your resume using the Anthropic API, and writes results to Google Sheets. For strong-fit jobs (score ≥ 7), a second Claude call generates specific resume tailoring suggestions.

Runs on a configurable daily schedule via APScheduler.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Add your resume

Create `data/resume.txt` and paste your full resume as plain text:

```bash
cp /path/to/your/resume.txt data/resume.txt
```

### 3. Google Cloud setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project (or select an existing one)
3. Enable these two APIs:
   - **Google Sheets API**
   - **Google Drive API**
4. Go to **IAM & Admin → Service Accounts → Create Service Account**
5. Give it a name (e.g., `job-scraper`), click **Create and Continue**
6. Skip optional role/user steps, click **Done**
7. Click the service account → **Keys → Add Key → Create new key → JSON**
8. Download the JSON file and save it as `credentials.json` in the project root
9. Note the service account email (format: `name@project.iam.gserviceaccount.com`)

### 4. Share your Google Sheet

1. Create a new Google Sheet (or let the scraper create it automatically)
2. Click **Share** and add the service account email with **Editor** access
3. Set the spreadsheet name in `.env` (see below) — it must match exactly

### 5. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_CREDENTIALS_PATH=credentials.json
SPREADSHEET_NAME=Job Scraper Results
# SCHEDULE_INTERVAL_HOURS=24  # uncomment to change from default 24h
```

---

## Run

```bash
python main.py
```

The scraper runs immediately on start, then repeats every 24 hours (configurable).

Press **Ctrl+C** to stop.

---

## Google Sheets output columns

| Column | Description |
|--------|-------------|
| `job_id` | Unique job identifier (used for deduplication across runs) |
| `date_scraped` | UTC timestamp when the job was scraped |
| `title` | Job title |
| `company` | Company name |
| `location` | Job location |
| `site` | Source site (linkedin / indeed) |
| `job_url` | Direct link to the job posting |
| `is_media_company` | TRUE if company name matches media/advertising keywords |
| `fit_score` | Claude fit score 1–10 |
| `reasoning` | One-sentence explanation of the score |
| `resume_suggestions` | Pipe-separated resume tailoring bullets (for score ≥ 7 only) |
| `date_posted` | Job posting date |
| `description_snippet` | First 300 characters of the job description |

---

## Configuration

Edit `config.py` to customize:

- `SEARCH_QUERIES` — job titles to search for
- `LOCATIONS` — cities/regions to search
- `MEDIA_COMPANY_KEYWORDS` — keywords that flag a company as media-related
- `EXCLUDE_COMPANY_KEYWORDS` — companies to skip entirely
- `MIN_SCORE_TO_WRITE` — minimum score to write to Sheets (default: 5)
- `SUGGESTIONS_MIN_SCORE` — minimum score to generate resume suggestions (default: 7)
- `MAX_JOBS_PER_RUN` — max jobs per pipeline run (default: 100)

---

## Cost estimate

~$0.085 per run (100 jobs, ~30 with resume suggestions):
- Scoring: Claude Haiku (~$0.013)
- Resume suggestions: Claude Sonnet (~$0.072)
- Daily for 30 days ≈ **~$2.55/month**

To reduce cost, set `SUGGESTIONS_MODEL = "claude-haiku-4-5-20251001"` in `config.py`.

---

## Deduplication

Two-layer deduplication prevents duplicate work:
1. **Within a run**: `scraper.py` deduplicates by `job_id` before scoring
2. **Across runs**: `sheets.py` reads existing job IDs from column A before writing — the sheet is the database
