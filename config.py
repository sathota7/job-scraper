import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# --- Google Sheets ---
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "Job Scraper Results")
WORKSHEET_NAME = "Jobs"

# --- Scraping ---
SEARCH_QUERIES = [
    "project manager",
    "media buyer",
    "digital media manager",
    "media planner",
    "campaign manager",
    "programmatic manager",
    "paid media manager",
    "media account manager",
]

SITES = ["linkedin", "indeed"]

LOCATIONS = [
    "New York, NY",
    "Los Angeles, CA",
    "Chicago, IL",
    "Remote",
]

RESULTS_PER_QUERY = 20
DISTANCE_MILES = 50
MAX_JOBS_PER_RUN = 100

MEDIA_COMPANY_KEYWORDS = [
    "media",
    "entertainment",
    "advertising",
    "agency",
    "digital",
    "broadcast",
    "publishing",
    "content",
    "marketing",
    "creative",
    "production",
    "film",
    "television",
    "radio",
    "streaming",
    "pr ",
    "communications",
    "public relations",
    "branding",
    "omnicom",
    "wpp",
    "publicis",
    "interpublic",
    "dentsu",
    "havas",
]

EXCLUDE_COMPANY_KEYWORDS: list[str] = []

# --- Scoring ---
SCORING_MODEL = "claude-haiku-4-5-20251001"
SUGGESTIONS_MODEL = "claude-sonnet-4-6"
SUGGESTIONS_MIN_SCORE = 7
MIN_SCORE_TO_WRITE = 5
SCORING_MAX_TOKENS = 300
SUGGESTIONS_MAX_TOKENS = 600
SCORING_RATE_LIMIT_DELAY = 0.5  # seconds between Claude API calls

# --- Scheduler ---
SCHEDULE_INTERVAL_HOURS = int(os.getenv("SCHEDULE_INTERVAL_HOURS", "24"))

# --- Google Sheets columns (in order) ---
SHEET_COLUMNS = [
    "job_id",
    "date_scraped",
    "title",
    "company",
    "location",
    "site",
    "job_url",
    "is_media_company",
    "fit_score",
    "reasoning",
    "resume_suggestions",
    "date_posted",
    "description_snippet",
]

# --- Paths ---
RESUME_PATH = "data/resume.txt"
BRAG_SHEET_PATH = os.getenv("BRAG_SHEET_PATH", "data/brag_sheet.txt")  # optional
CREDENTIALS_PATH = GOOGLE_CREDENTIALS_PATH
LOG_DIR = "logs"
