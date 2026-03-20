import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# --- Google Sheets ---
GOOGLE_CREDENTIALS_PATH = os.getenv("GOOGLE_CREDENTIALS_PATH", "credentials.json")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "Job Scraper Results")
WORKSHEET_NAME = "Jobs"
CONFIG_SHEET_NAME = "Config"  # stores synthesized preferences between stateless runs

# --- Job Search Profiles ---
# Comment out entries in ACTIVE_CATEGORIES to disable a group.
JOB_CATEGORIES = {
    "media_buying": [
        "media buyer",
        "programmatic manager",
        "paid media manager",
        "digital media manager",
        "media planner",
    ],
    "account_management": [
        "media account manager",
        "client services manager",
        "account manager advertising",
    ],
    "project_management": [
        "project manager",
        "campaign manager",
        "marketing project manager",
    ],
}

ACTIVE_CATEGORIES = ["media_buying", "account_management", "project_management"]

# Derived — scraper.py reads this unchanged
SEARCH_QUERIES = [
    query
    for cat in ACTIVE_CATEGORIES
    for query in JOB_CATEGORIES.get(cat, [])
]

# --- Scraping ---

SITES = ["linkedin", "indeed"]

LOCATIONS = [
    "New York, NY",
    # "Los Angeles, CA",
    # "Chicago, IL",
    # "Remote",
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

EXCLUDE_TITLE_KEYWORDS: list[str] = [
    "director",
    "vp",
    "vice president",
    "head of",
    "svp",
    "evp",
    "chief",
    "president",
    "lead",
    "senior manager",
]

# --- Search Sensitivity ---
# "strict"   — high-confidence matches only
# "balanced" — current default
# "liberal"  — wider net, generous scoring
SEARCH_SENSITIVITY = "balanced"

_SENSITIVITY_THRESHOLDS = {
    "strict":   (7, 8),
    "balanced": (5, 7),
    "liberal":  (4, 6),
}

assert SEARCH_SENSITIVITY in _SENSITIVITY_THRESHOLDS, (
    f"Invalid SEARCH_SENSITIVITY '{SEARCH_SENSITIVITY}'. "
    f"Must be one of: {list(_SENSITIVITY_THRESHOLDS.keys())}"
)

MIN_SCORE_TO_WRITE, SUGGESTIONS_MIN_SCORE = _SENSITIVITY_THRESHOLDS[SEARCH_SENSITIVITY]

# --- Scoring ---
SCORING_MODEL = "claude-haiku-4-5-20251001"
SUGGESTIONS_MODEL = "claude-sonnet-4-6"
SCORING_MAX_TOKENS = 300
SUGGESTIONS_MAX_TOKENS = 600
SCORING_RATE_LIMIT_DELAY = 1.5  # seconds between Claude API calls (globally enforced across all workers)
SCORING_CONCURRENT_REQUESTS = int(os.getenv("SCORING_CONCURRENT_REQUESTS", "5"))

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
    "manual_score",           # user fills in — bot always writes blank for new rows
    "manual_score_reasoning", # user fills in — bot always writes blank for new rows
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

# --- Feedback / RL ---
FEEDBACK_CACHE_PATH = "data/feedback_cache.json"
USER_PREFERENCES_PATH = "data/user_preferences.txt"
# Max calibration examples injected into the scoring prompt (controls token cost)
FEEDBACK_MAX_EXAMPLES = 8
