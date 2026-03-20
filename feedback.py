"""feedback.py — Sync manual scores from the main sheet into a calibration cache,
and optionally re-synthesize user preferences via Claude when new scores appear.

The pipeline calls sync_and_maybe_synthesize() automatically before each run.
Run manually for inspection or to force a re-synthesize:

  python3 feedback.py stats              # print cache stats
  python3 feedback.py sync               # pull scores from sheet → cache
  python3 feedback.py synthesize         # regenerate user_preferences.txt from cache
  python3 feedback.py sync-and-synthesize  # force both
"""

import datetime
import json
import os
import sys

import gspread
from google.oauth2.service_account import Credentials
from loguru import logger

import config

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_sheets_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(config.CREDENTIALS_PATH, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_config_sheet(spreadsheet: gspread.Spreadsheet) -> gspread.Worksheet:
    """Get or create the Config tab, seeding it with a header row if new."""
    try:
        return spreadsheet.worksheet(config.CONFIG_SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=config.CONFIG_SHEET_NAME, rows=50, cols=2)
        ws.append_row(["key", "value"], value_input_option="USER_ENTERED")
        logger.info(f"Created '{config.CONFIG_SHEET_NAME}' tab in spreadsheet")
        return ws


def _read_preferences_from_sheet(spreadsheet: gspread.Spreadsheet) -> str:
    """Read synthesized preferences from the Config tab. Returns '' if not set."""
    ws = _get_config_sheet(spreadsheet)
    rows = ws.get_all_records()
    for row in rows:
        if str(row.get("key", "")).strip() == "user_preferences":
            return str(row.get("value", "")).strip()
    return ""


def _write_preferences_to_sheet(spreadsheet: gspread.Spreadsheet, text: str) -> None:
    """Upsert the user_preferences row in the Config tab."""
    ws = _get_config_sheet(spreadsheet)
    rows = ws.get_all_records()
    for i, row in enumerate(rows, start=2):  # row 1 is header
        if str(row.get("key", "")).strip() == "user_preferences":
            ws.update(f"B{i}", [[text]], value_input_option="USER_ENTERED")
            logger.debug("Updated user_preferences in Config sheet")
            return
    ws.append_row(["user_preferences", text], value_input_option="USER_ENTERED")
    logger.debug("Inserted user_preferences into Config sheet")


def _safe_int(val) -> int:
    try:
        return int(float(val or 0))
    except (ValueError, TypeError):
        return 0


def _load_cache() -> dict:
    if os.path.exists(config.FEEDBACK_CACHE_PATH):
        with open(config.FEEDBACK_CACHE_PATH) as f:
            return json.load(f)
    return {"examples": [], "count": 0}


def sync_from_sheet(spreadsheet: gspread.Spreadsheet) -> list[dict]:
    """Read manually-scored rows from the main worksheet → data/feedback_cache.json.
    Returns the list of examples found."""
    try:
        ws = spreadsheet.worksheet(config.WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        logger.info(f"Worksheet '{config.WORKSHEET_NAME}' not found yet — skipping feedback sync")
        return []

    rows = ws.get_all_records()
    examples = []
    for row in rows:
        raw = str(row.get("manual_score", "")).strip()
        if not raw:
            continue
        try:
            manual_score = int(float(raw))
            if not (1 <= manual_score <= 10):
                continue
        except (ValueError, TypeError):
            continue

        ai_score = _safe_int(row.get("fit_score"))
        examples.append({
            "job_id": str(row.get("job_id", "")),
            "title": str(row.get("title", "")),
            "company": str(row.get("company", "")),
            "is_media_company": str(row.get("is_media_company", "")).lower() in ("true", "1", "yes"),
            "description_snippet": str(row.get("description_snippet", ""))[:600],
            "ai_score": ai_score,
            "manual_score": manual_score,
            "diff": manual_score - ai_score,
            "manual_score_reasoning": str(row.get("manual_score_reasoning", "")),
        })

    os.makedirs("data", exist_ok=True)
    cache = {
        "examples": examples,
        "count": len(examples),
        "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(config.FEEDBACK_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)

    logger.info(f"Feedback sync: {len(examples)} manually-scored jobs found in '{config.WORKSHEET_NAME}'")
    return examples


def sync_and_maybe_synthesize() -> bool:
    """Called automatically by the pipeline before each run.

    1. Opens the spreadsheet once (reused for both Jobs and Config tabs).
    2. Reads manual scores from the Jobs tab → rebuilds feedback_cache.json.
    3. If new scores appeared → re-synthesizes preferences via Claude →
       writes to both Config tab and local file.
    4. If count unchanged → restores preferences from Config tab to local file
       (critical for stateless runners like GitHub Actions where local files
       don't persist between runs).

    Returns True if preferences were re-synthesized."""
    gc = _get_sheets_client()
    spreadsheet = gc.open(config.SPREADSHEET_NAME)

    prev_count = _load_cache().get("count", 0)
    examples = sync_from_sheet(spreadsheet)
    new_count = len(examples)

    if new_count > prev_count:
        logger.info(
            f"New manual scores detected ({prev_count} → {new_count}) — re-synthesizing preferences"
        )
        prefs = synthesize_preferences(examples)
        _write_preferences_to_sheet(spreadsheet, prefs)
        return True

    # Always restore preferences from the Config tab to the local file.
    # Count unchanged is fine — we just need the text on disk before scoring starts.
    # This is a no-op when running locally with the file present, but essential for
    # stateless runners (GitHub Actions) where local files don't persist between runs.
    prefs = _read_preferences_from_sheet(spreadsheet)
    if prefs:
        os.makedirs("data", exist_ok=True)
        with open(config.USER_PREFERENCES_PATH, "w") as f:
            f.write(prefs)
        logger.debug(
            "Preferences restored from Config sheet"
            + ("" if new_count > 0 else " (no manual scores yet, using prior synthesis)")
        )
    else:
        if new_count == 0:
            logger.debug("No manual scores and no prior preferences — scoring with defaults")
        else:
            logger.debug("Preferences not yet synthesized — run 'python3 feedback.py synthesize'")

    return False


def synthesize_preferences(examples: list[dict] | None = None) -> str:
    """Use Claude to analyze feedback → write data/user_preferences.txt.
    Pass examples directly (from a fresh sync) or leave None to load from cache."""
    if examples is None:
        cache = _load_cache()
        examples = cache.get("examples", [])
    if not examples:
        logger.warning("No feedback examples available — cannot synthesize preferences")
        return ""

    examples_text = "\n\n".join(
        f'Job: "{e["title"]}" at {e["company"]} (media={e["is_media_company"]})\n'
        f'AI score: {e["ai_score"]} | Manual score: {e["manual_score"]} | Diff: {e["diff"]:+d}\n'
        f'User reasoning: {e["manual_score_reasoning"]}'
        for e in examples
    )

    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    response = client.messages.create(
        model=config.SUGGESTIONS_MODEL,
        max_tokens=800,
        system="""You are analyzing job scoring feedback to produce a calibration guide for an AI job-fit scorer.

Your output will be injected verbatim into every future scoring prompt.

Write a directive, second-person calibration guide (150–200 words) covering:
1. Industries/sectors to score up or down (specific)
2. Role types to penalize (contract, temp, freelance, seasonal, internship — hard caps)
3. Seniority calibration (what level the user is targeting)
4. Factors the user consistently values or devalues
5. Overall bias note (e.g., if the AI tends to over/underscore)

Use directive language: "Score lower if...", "Do not score above X if...", "Give extra weight to..."
Output ONLY the guide text. No headers, no preamble.""",
        messages=[{
            "role": "user",
            "content": (
                f"Here are {len(examples)} manually-scored jobs with the user's reasoning:\n\n"
                f"{examples_text}\n\n"
                "Write the calibration guide."
            ),
        }],
    )

    preferences = response.content[0].text.strip()
    os.makedirs("data", exist_ok=True)
    with open(config.USER_PREFERENCES_PATH, "w") as f:
        f.write(preferences)
    logger.info(f"User preferences written to '{config.USER_PREFERENCES_PATH}'")
    return preferences


def _cli_open_spreadsheet() -> gspread.Spreadsheet:
    return _get_sheets_client().open(config.SPREADSHEET_NAME)


def _print_stats() -> None:
    cache = _load_cache()
    examples = cache.get("examples", [])
    if not examples:
        print("No rated examples in cache.")
        return
    diffs = [e["diff"] for e in examples]
    avg_diff = sum(diffs) / len(diffs)
    overscored = sum(1 for d in diffs if d < -1)
    underscored = sum(1 for d in diffs if d > 1)
    print(f"\nLast updated : {cache.get('last_updated', 'unknown')}")
    print(f"Examples     : {len(examples)}")
    print(f"Avg diff (manual − AI) : {avg_diff:+.2f}")
    print(f"AI overscored (diff < −1) : {overscored}")
    print(f"AI underscored (diff > +1) : {underscored}")
    print("\nTop misses (|diff| > 1):")
    for e in sorted(examples, key=lambda e: abs(e["diff"]), reverse=True)[:8]:
        sign = "↑" if e["diff"] > 0 else "↓"
        print(f"  {sign}{abs(e['diff'])}  {e['title'][:42]:<42} @ {e['company'][:22]:<22}  AI={e['ai_score']} → {e['manual_score']}")
    print()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "stats":
        _print_stats()
    elif cmd == "sync":
        ss = _cli_open_spreadsheet()
        sync_from_sheet(ss)
        _print_stats()
    elif cmd == "synthesize":
        prefs = synthesize_preferences()
        ss = _cli_open_spreadsheet()
        _write_preferences_to_sheet(ss, prefs)
        print("\n--- SYNTHESIZED PREFERENCES ---")
        print(prefs)
    elif cmd == "sync-and-synthesize":
        ss = _cli_open_spreadsheet()
        examples = sync_from_sheet(ss)
        _print_stats()
        prefs = synthesize_preferences(examples)
        _write_preferences_to_sheet(ss, prefs)
        print("\n--- SYNTHESIZED PREFERENCES ---")
        print(prefs)
    else:
        print(__doc__)
