import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
import pandas as pd
from loguru import logger

import config

_client: anthropic.Anthropic | None = None
_resume_text: str | None = None
_brag_sheet_text: str | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _load_resume() -> str:
    global _resume_text
    if _resume_text is None:
        try:
            with open(config.RESUME_PATH, "r", encoding="utf-8") as f:
                _resume_text = f.read().strip()
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Resume not found at '{config.RESUME_PATH}'. "
                "Please create data/resume.txt with your resume text before running."
            )
    return _resume_text


def _load_brag_sheet() -> str:
    global _brag_sheet_text
    if _brag_sheet_text is None:
        try:
            with open(config.BRAG_SHEET_PATH, "r", encoding="utf-8") as f:
                _brag_sheet_text = f.read().strip()
            logger.info(f"Brag sheet loaded from '{config.BRAG_SHEET_PATH}'")
        except FileNotFoundError:
            _brag_sheet_text = ""  # optional — silently skip
    return _brag_sheet_text


def _build_scoring_system() -> str:
    sensitivity = config.SEARCH_SENSITIVITY

    if sensitivity == "strict":
        stance = (
            "Be conservative in your scoring. Only award high scores (7+) when the candidate "
            "clearly meets the majority of stated requirements with direct, demonstrable experience. "
            "Penalize role mismatch, missing tools, or insufficient seniority."
        )
        weight_note = (
            "- Extra weight for roles at media/entertainment/advertising companies (+1 if strong otherwise)\n"
            "- Prioritize exact title match and specific platform experience (DV360, The Trade Desk, etc.)\n"
            "- Penalize more than one major skills gap"
        )
    elif sensitivity == "liberal":
        stance = (
            "Be generous in your scoring. Award credit for transferable skills and adjacent experience. "
            "A candidate with 2–3 years of broadly relevant experience and strong growth indicators "
            "should score in the 6–7 range even with some gaps. Cast a wide net."
        )
        weight_note = (
            "- Extra weight for roles at media/entertainment/advertising companies (+1 if strong otherwise)\n"
            "- Give credit for adjacent roles (e.g., general project management counts toward media PM roles)\n"
            "- Do not penalize for missing niche tools if core skills are present"
        )
    else:  # balanced
        stance = (
            "Apply balanced judgment. Score based on genuine fit accounting for both strengths and gaps. "
            "3 years of focused experience in the domain should score in the 6–8 range for well-matched roles."
        )
        weight_note = (
            "- Extra weight for roles at media/entertainment/advertising companies (+1 if strong otherwise)\n"
            "- Extra weight for project management and media buying roles specifically\n"
            "- Consider years of experience, specific tools/platforms mentioned, and industry background"
        )

    return f"""You are a job fit evaluator. Given a resume and a job description, score how well the candidate fits the role on a scale of 1–10.

Scoring rubric:
- 9–10: Exceptional fit — candidate meets nearly all requirements, role is in their core domain
- 7–8: Strong fit — candidate meets most requirements, minor gaps
- 5–6: Moderate fit — some relevant experience, notable gaps
- 3–4: Weak fit — limited relevant experience, significant gaps
- 1–2: Poor fit — role is misaligned with candidate's background

Scoring stance:
{stance}

Scoring weights:
{weight_note}

You MUST respond with ONLY valid JSON in this exact format, no other text:
{{"score": <integer 1-10>, "reasoning": "<one concise sentence explaining the score>"}}\""""


SUGGESTIONS_SYSTEM = """You are an expert career coach specializing in media, advertising, and project management roles.
Given a resume and a job description, provide 3–5 specific, actionable bullet points on how the candidate should tailor their resume to this specific job.

Each bullet point must:
- Reference a specific requirement from the job description
- Suggest a concrete change to the resume (e.g., add a specific keyword, reframe an achievement, highlight a specific project)
- Be practical and immediately actionable

Format: Return ONLY the bullet points, one per line, each starting with "• ". No preamble or conclusion."""


def _parse_score_response(text: str) -> dict:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Regex fallback — extract JSON object
    match = re.search(r'\{[^{}]*"score"\s*:\s*(\d+)[^{}]*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Last resort: just extract score integer
    score_match = re.search(r'"score"\s*:\s*(\d+)', text)
    reasoning_match = re.search(r'"reasoning"\s*:\s*"([^"]+)"', text)
    if score_match:
        return {
            "score": int(score_match.group(1)),
            "reasoning": reasoning_match.group(1) if reasoning_match else "Parsed from partial response.",
        }

    logger.warning(f"Could not parse score response: {text[:200]}")
    return {"score": 0, "reasoning": "Failed to parse response."}


def _score_job(job: pd.Series, resume: str, brag_sheet: str) -> dict:
    client = _get_client()
    title = str(job.get("title", ""))
    company = str(job.get("company", ""))
    description = str(job.get("description", job.get("description_snippet", "")))[:2000]
    is_media = job.get("is_media_company", False)

    brag_section = f"\nPAST WORK EXPERIENCE / BRAG SHEET:\n{brag_sheet}" if brag_sheet else ""

    user_message = f"""RESUME:
{resume}{brag_section}

JOB TITLE: {title}
COMPANY: {company}
IS MEDIA COMPANY: {is_media}
JOB DESCRIPTION:
{description}

Score this job fit and respond with JSON only."""

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=config.SCORING_MODEL,
                max_tokens=config.SCORING_MAX_TOKENS,
                system=_build_scoring_system(),
                messages=[{"role": "user", "content": user_message}],
            )
            return _parse_score_response(response.content[0].text)
        except anthropic.RateLimitError:
            if attempt == 0:
                logger.warning("Rate limit hit during scoring — waiting 60s before retry")
                time.sleep(60)
            else:
                logger.error("Rate limit persists after retry — skipping job")
                return {"score": 0, "reasoning": "Rate limit exceeded."}
        except Exception as e:
            logger.error(f"Scoring error for '{title}' at '{company}': {e}")
            return {"score": 0, "reasoning": f"Error: {e}"}

    return {"score": 0, "reasoning": "Max retries reached."}


def _get_resume_suggestions(job: pd.Series, resume: str, brag_sheet: str) -> str:
    client = _get_client()
    title = str(job.get("title", ""))
    company = str(job.get("company", ""))
    description = str(job.get("description", job.get("description_snippet", "")))[:2000]

    brag_section = (
        f"\nPAST WORK EXPERIENCE / BRAG SHEET (use this to find specific achievements to highlight):\n{brag_sheet}"
        if brag_sheet else ""
    )

    user_message = f"""RESUME:
{resume}{brag_section}

JOB TITLE: {title}
COMPANY: {company}
JOB DESCRIPTION:
{description}

Provide 3–5 specific bullet points on how to tailor this resume to this job. Where relevant, reference specific achievements or projects from the brag sheet."""

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=config.SUGGESTIONS_MODEL,
                max_tokens=config.SUGGESTIONS_MAX_TOKENS,
                system=SUGGESTIONS_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text.strip()
            # Convert newlines to pipe-separated for clean Sheets storage
            bullets = [line.strip() for line in raw.splitlines() if line.strip()]
            return " | ".join(bullets)
        except anthropic.RateLimitError:
            if attempt == 0:
                logger.warning("Rate limit hit during suggestions — waiting 60s before retry")
                time.sleep(60)
            else:
                logger.error("Rate limit persists — skipping suggestions")
                return ""
        except Exception as e:
            logger.error(f"Suggestions error for '{title}' at '{company}': {e}")
            return ""

    return ""


def _process_job(
    idx: int,
    total: int,
    job: pd.Series,
    resume: str,
    brag_sheet: str,
    semaphore: threading.BoundedSemaphore,
) -> tuple[int, int, str, str]:
    title = job.get("title", "unknown")
    company = job.get("company", "unknown")
    logger.debug(f"  Scoring [{idx + 1}/{total}]: {title} @ {company}")

    with semaphore:
        result = _score_job(job, resume, brag_sheet)
        time.sleep(config.SCORING_RATE_LIMIT_DELAY)

    score = int(result.get("score", 0))
    reasoning = result.get("reasoning", "")

    suggestion = ""
    if score >= config.SUGGESTIONS_MIN_SCORE:
        logger.debug(f"    Score {score} >= {config.SUGGESTIONS_MIN_SCORE} — generating resume suggestions")
        with semaphore:
            suggestion = _get_resume_suggestions(job, resume, brag_sheet)
            time.sleep(config.SCORING_RATE_LIMIT_DELAY)

    return idx, score, reasoning, suggestion


def score_jobs(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        logger.warning("No jobs to score.")
        df["fit_score"] = []
        df["reasoning"] = []
        df["resume_suggestions"] = []
        return df

    resume = _load_resume()
    brag_sheet = _load_brag_sheet()
    if brag_sheet:
        logger.info("Brag sheet will be included in scoring and suggestions.")

    total = len(df)
    workers = config.SCORING_CONCURRENT_REQUESTS
    logger.info(f"Scoring {total} jobs with {config.SCORING_MODEL} ({workers} parallel workers)...")

    semaphore = threading.BoundedSemaphore(workers)
    jobs_list = [(i, row) for i, (_, row) in enumerate(df.iterrows())]

    results: dict[int, tuple[int, str, str]] = {}

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_process_job, idx, total, job, resume, brag_sheet, semaphore): idx
            for idx, job in jobs_list
        }
        for future in as_completed(futures):
            idx, score, reasoning, suggestion = future.result()
            results[idx] = (score, reasoning, suggestion)

    scores = [results[i][0] for i in range(total)]
    reasonings = [results[i][1] for i in range(total)]
    suggestions = [results[i][2] for i in range(total)]

    df = df.copy()
    df["fit_score"] = scores
    df["reasoning"] = reasonings
    df["resume_suggestions"] = suggestions

    above_threshold = sum(1 for s in scores if s >= config.MIN_SCORE_TO_WRITE)
    logger.info(
        f"Scoring complete. "
        f"Avg score: {sum(scores)/len(scores):.1f} | "
        f"Score >= {config.MIN_SCORE_TO_WRITE}: {above_threshold} jobs | "
        f"Score >= {config.SUGGESTIONS_MIN_SCORE} (with suggestions): {sum(1 for s in scores if s >= config.SUGGESTIONS_MIN_SCORE)} jobs"
    )
    return df
