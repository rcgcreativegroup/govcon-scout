"""
Batch document download for opportunities already kept by the operator.

Selects candidates from kept post-intake stages and downloads
SAM.gov / PIEE attachment packages using the persistent SAM browser profile
when available, with auth.json retained as a fallback.
"""

import argparse
import csv as csv_module
import json
import os
import re
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_AUTH_STATE = str(BASE_DIR / "auth.json")
DEFAULT_PROFILE_DIR = str(BASE_DIR / ".browser/sam-profile")
DEFAULT_DOWNLOADS_DIR = str(BASE_DIR / "downloads")
DEFAULT_EXTRACTS_DIR = str(BASE_DIR / "reports/document_extracts")
DEFAULT_BATCH_RUNS_DIR = str(BASE_DIR / "reports/batch_runs")
DEFAULT_STATE_CSV  = str(BASE_DIR / "data/opportunity_state.csv")
DEFAULT_STATE_PATH = DEFAULT_STATE_CSV  # legacy alias — kept for CLI arg compat
NOVNC_CHECK_SCRIPT = str(BASE_DIR / "scripts/novnc_check.sh")

BATCH_STAGES = {"Manual Review", "AI Review", "Development", "Ready to Submit", "Execution"}

SOURCE_URL_FIELDS = [
    "source_url",
    "ui_link",
    "url",
    "link",
    "sam_url",
    "notice_url",
]

STATUS_DOWNLOADED = "downloaded"
STATUS_SKIPPED_STAGE = "skipped_stage_not_eligible"
STATUS_SKIPPED_WATCH = "skipped_watch_list_only"
STATUS_SKIPPED_PASS_ARCHIVE = "skipped_pass_or_archive"
STATUS_SKIPPED_MYBIDMATCH_NO_SAM = "skipped_mybidmatch_no_sam_url"
STATUS_SKIPPED_HAS_DOCS = "skipped_already_has_docs"
STATUS_SKIPPED_NO_URL = "skipped_no_url"
STATUS_SKIPPED_ROUTE = "skipped_sources_sought_route"
STATUS_SKIPPED_DUPLICATE = "skipped_duplicate_secondary"
STATUS_FAILED_INFRA = "failed_login_or_live_infra"
STATUS_FAILED_NO_LINK = "failed_no_download_link"
STATUS_FAILED_ATTACHMENT_CLICK = "failed_attachment_click_or_download"
STATUS_FAILED_OTHER = "failed_other"

NEXT_ACTION_AI_REVIEW = "Run AI Review"
NEXT_ACTION_MANUAL = "Upload Documents Manually"
NEXT_ACTION_RETRY = "Retry Login"
NEXT_ACTION_SKIP = "Skip/Pass"


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def safe_notice_id(value):
    text = safe_text(value)
    if not text:
        return ""
    if text.startswith("/") or "\\" in text or "/" in text:
        return ""
    if text in {".", ".."} or ".." in text:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", text):
        return ""
    return text


def get_source_url(row):
    for field in SOURCE_URL_FIELDS:
        value = safe_text(row.get(field))
        if value and (value.startswith("http://") or value.startswith("https://")):
            return value
    return ""


def boolish(value):
    return safe_text(value).lower() in {"1", "true", "yes", "y", "watch", "watched", "on"}


def has_existing_downloads(notice_id, downloads_dir, extracts_dir):
    if not notice_id:
        return False
    downloads_folder = Path(downloads_dir) / notice_id
    if downloads_folder.exists():
        non_debug = [
            f for f in downloads_folder.rglob("*")
            if f.is_file() and "_debug" not in f.parts and f.name != ".gitkeep"
        ]
        if non_debug:
            return True
    extracts_folder = Path(extracts_dir) / notice_id
    if extracts_folder.exists() and any(f.is_file() for f in extracts_folder.rglob("*")):
        return True
    return False


def is_sources_sought_only(row):
    route = safe_text(row.get("route")).lower()
    return route == "sources_sought"


def is_duplicate_secondary(row):
    route = safe_text(row.get("route")).lower()
    resolution = safe_text(row.get("mybidmatch_resolution_status")).lower()
    status_text = " ".join([
        safe_text(row.get("triage_status")),
        safe_text(row.get("processed_status")),
        safe_text(row.get("operator_status")),
    ]).lower()
    return (
        route == "duplicate"
        or "duplicate" in resolution
        or "already covered" in resolution
        or "duplicate" in status_text
    )


def is_mybidmatch_only(row):
    source = safe_text(row.get("source")).lower()
    return "mybidmatch" in source and "govcon" not in source


def is_watch_list_only(row, eligible_stages):
    if not boolish(row.get("watch_list")):
        return False
    stage = safe_text(row.get("macro_stage"))
    operator_status = safe_text(row.get("operator_status")).lower()
    return stage not in eligible_stages or operator_status in {"", "watch", "watched", "hold"}


def is_pass_or_archive(row):
    stage = safe_text(row.get("macro_stage")).lower()
    operator_status = safe_text(row.get("operator_status")).lower()
    last_action = safe_text(row.get("last_operator_action")).lower()
    if stage in {"pass", "archive", "archived", "done"}:
        return True
    if operator_status in {"pass", "passed", "archive", "archived", "done", "disqualified"}:
        return True
    return "passed/archived" in last_action or "auto_archived" in last_action


def skipped_candidate(row, notice_id, url, reason):
    return {
        "row": row,
        "notice_id": notice_id,
        "url": url,
        "skip": True,
        "skip_reason": reason,
    }


def count_downloads(notice_id, downloads_dir):
    folder = Path(downloads_dir) / notice_id
    if not folder.exists():
        return 0
    return len([
        f for f in folder.rglob("*")
        if f.is_file() and "_debug" not in f.parts and f.name != ".gitkeep"
    ])


def extract_zips(notice_id, downloads_dir):
    folder = Path(downloads_dir) / notice_id
    if not folder.exists():
        return 0, ""
    extracted = 0
    for zip_path in sorted(folder.rglob("*.zip")):
        target_dir = folder / "extracted"
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                for member in archive.infolist():
                    member_path = Path(member.filename)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        return extracted, f"Unsafe ZIP entry rejected: {member.filename}"
                    target_path = (target_dir / member.filename).resolve()
                    try:
                        target_path.relative_to(target_dir.resolve())
                    except ValueError:
                        return extracted, f"Unsafe ZIP path traversal rejected: {member.filename}"
                archive.extractall(target_dir)
                extracted += 1
        except zipfile.BadZipFile:
            return extracted, f"Bad ZIP skipped: {zip_path.name}"
        except OSError as err:
            return extracted, f"ZIP extraction error: {err}"
    return extracted, ""


def live_preflight_check():
    if not os.environ.get("DISPLAY"):
        return False, (
            "DISPLAY is not set. Start noVNC (scripts/novnc_reset.sh), "
            "set DISPLAY=:99, then retry."
        )
    check_script = Path(NOVNC_CHECK_SCRIPT)
    if not check_script.exists():
        return False, f"noVNC check script not found: {NOVNC_CHECK_SCRIPT}"
    result = subprocess.run(["bash", str(check_script)], capture_output=True)
    if result.returncode != 0:
        output = result.stdout.decode("utf-8", errors="replace").strip()
        return False, f"noVNC preflight failed:\n{output}\nRun scripts/novnc_reset.sh, then retry."
    return True, ""


def auth_state_ready(auth_state_path):
    return Path(auth_state_path).exists()


def profile_ready(profile_dir):
    path = Path(profile_dir)
    return path.exists() and path.is_dir()


def is_profile_filesystem_locked(profile_dir=None):
    """Pure filesystem check — returns True if Chromium's SingletonLock file exists.

    Chromium writes <user-data-dir>/SingletonLock while it holds the profile open.
    This check does not launch Playwright and is safe to call from any thread.
    """
    lock = Path(profile_dir or DEFAULT_PROFILE_DIR) / "SingletonLock"
    return lock.exists()


def select_candidates(rows, stages, limit, force, downloads_dir, extracts_dir):
    candidates = []
    kept = 0
    stages = set(stages)
    for row in rows:
        stage = safe_text(row.get("macro_stage"))
        notice_id = safe_notice_id(safe_text(row.get("notice_id")))
        if not notice_id:
            continue
        url = get_source_url(row)

        if is_duplicate_secondary(row):
            candidates.append(skipped_candidate(row, notice_id, url, STATUS_SKIPPED_DUPLICATE))
            continue
        if is_watch_list_only(row, stages):
            candidates.append(skipped_candidate(row, notice_id, url, STATUS_SKIPPED_WATCH))
            continue
        if is_pass_or_archive(row):
            candidates.append(skipped_candidate(row, notice_id, url, STATUS_SKIPPED_PASS_ARCHIVE))
            continue
        if is_sources_sought_only(row):
            candidates.append(skipped_candidate(row, notice_id, url, STATUS_SKIPPED_ROUTE))
            continue
        if is_mybidmatch_only(row) and not url:
            candidates.append(skipped_candidate(row, notice_id, url, STATUS_SKIPPED_MYBIDMATCH_NO_SAM))
            continue
        if stage not in stages:
            candidates.append(skipped_candidate(row, notice_id, url, STATUS_SKIPPED_STAGE))
            continue
        if not url:
            candidates.append(skipped_candidate(row, notice_id, url, STATUS_SKIPPED_NO_URL))
            continue
        if not force and has_existing_downloads(notice_id, downloads_dir, extracts_dir):
            candidates.append(skipped_candidate(row, notice_id, url, STATUS_SKIPPED_HAS_DOCS))
            continue
        if kept >= limit:
            continue
        candidates.append({
            "row": row, "notice_id": notice_id, "url": url,
            "skip": False, "skip_reason": "",
        })
        kept += 1
    return candidates


def _next_action_for_skip(skip_reason):
    if skip_reason == STATUS_SKIPPED_HAS_DOCS:
        return NEXT_ACTION_AI_REVIEW
    if skip_reason in {STATUS_SKIPPED_NO_URL, STATUS_SKIPPED_MYBIDMATCH_NO_SAM}:
        return NEXT_ACTION_MANUAL
    return NEXT_ACTION_SKIP


def process_candidate(notice_id, url, auth_state, downloads_dir, headless, profile_dir=None, skip_session_check=False):
    try:
        from sam_browser_downloader import download_for_notice
    except ImportError as err:
        return {
            "status": STATUS_FAILED_OTHER,
            "error": f"sam_browser_downloader not available: {err}",
            "downloads_folder": str(Path(downloads_dir) / notice_id),
            "attachment_count": 0,
            "extracted_zips": 0,
            "next_action": NEXT_ACTION_MANUAL,
        }

    Path(downloads_dir, notice_id).mkdir(parents=True, exist_ok=True)

    _meta = {}
    try:
        downloaded = download_for_notice(
            auth_state=auth_state,
            notice_id=notice_id,
            url=url,
            downloads_dir=downloads_dir,
            headless=headless,
            debug=True,
            profile_dir=profile_dir or "",
            skip_session_check=skip_session_check,
            _meta=_meta,
        )
    except SystemExit as err:
        code = getattr(err, "code", 1)
        if code == 1:
            return {
                "status": STATUS_FAILED_INFRA,
                "error": (
                    "SAM.gov login/live browser required. "
                    "Open SAM.gov Login in noVNC, complete login, then retry."
                ),
                "downloads_folder": str(Path(downloads_dir) / notice_id),
                "attachment_count": 0,
                "extracted_zips": 0,
                "next_action": NEXT_ACTION_RETRY,
            }
        return {
            "status": STATUS_FAILED_OTHER,
            "error": f"Download process exited with code {code}.",
            "downloads_folder": str(Path(downloads_dir) / notice_id),
            "attachment_count": 0,
            "extracted_zips": 0,
            "next_action": NEXT_ACTION_MANUAL,
        }
    except Exception as err:
        message = str(err)
        if "SAM browser profile is already open" in message or "SAM.gov session is not logged in" in message:
            return {
                "status": STATUS_FAILED_INFRA,
                "error": message,
                "downloads_folder": str(Path(downloads_dir) / notice_id),
                "attachment_count": 0,
                "extracted_zips": 0,
                "next_action": NEXT_ACTION_RETRY,
            }
        return {
            "status": STATUS_FAILED_OTHER,
            "error": message,
            "downloads_folder": str(Path(downloads_dir) / notice_id),
            "attachment_count": 0,
            "extracted_zips": 0,
            "next_action": NEXT_ACTION_MANUAL,
        }

    if not downloaded:
        # Distinguish: UI was found but click/download failed vs. no UI at all
        if _meta.get("attachment_ui_found"):
            fail_status = STATUS_FAILED_ATTACHMENT_CLICK
            fail_error  = (
                f"Attachment UI detected (Download All={_meta.get('download_all_found')}, "
                f"candidates={_meta.get('direct_candidates', 0)}) but no files downloaded. "
                "Check debug HTML/PNG in downloads/_debug/."
            )
        else:
            fail_status = STATUS_FAILED_NO_LINK
            fail_error  = "No solicitation files or attachment UI found. Check debug screenshots."
        return {
            "status": fail_status,
            "error": fail_error,
            "downloads_folder": str(Path(downloads_dir) / notice_id),
            "attachment_count": 0,
            "extracted_zips": 0,
            "next_action": NEXT_ACTION_MANUAL,
        }

    extracted_count, _zip_err = extract_zips(notice_id, downloads_dir)
    attachment_count = count_downloads(notice_id, downloads_dir)

    return {
        "status": STATUS_DOWNLOADED,
        "error": "",
        "downloads_folder": str(Path(downloads_dir) / notice_id),
        "attachment_count": attachment_count,
        "extracted_zips": extracted_count,
        "next_action": NEXT_ACTION_AI_REVIEW,
    }


def needs_login_gate(live, auth_state=None, profile_dir=None):
    """
    Returns (needs_gate: bool, message: str).
    Live mode always gates — operator must confirm SAM.gov login via noVNC.
    Headless mode gates only if neither the persistent profile nor auth.json exists.
    """
    if live:
        return True, (
            "SAM.gov login required. Open noVNC, complete login, "
            "then click I'm Logged In — Close Login & Continue Downloads."
        )
    profile = profile_dir or DEFAULT_PROFILE_DIR
    if profile_ready(profile):
        return False, ""
    auth = auth_state or DEFAULT_AUTH_STATE
    if not auth_state_ready(auth):
        return True, (
            "No SAM.gov browser session found. Open SAM.gov Login in noVNC "
            "or regenerate auth.json."
        )
    return False, ""


def check_sam_session_live(live=True, auth_state=None, profile_dir=None):
    """
    Post-login verification called when the operator clicks
    'I'm Logged In — Continue Downloads'.
    Live mode: verifies DISPLAY is set. Headless mode verifies that either the
    persistent profile or auth.json fallback exists.
    Returns (ok: bool, message: str).
    """
    if not live:
        profile = profile_dir or DEFAULT_PROFILE_DIR
        if profile_ready(profile):
            return True, ""
        auth = auth_state or DEFAULT_AUTH_STATE
        if auth_state_ready(auth):
            return True, ""
        return False, (
            f"No SAM.gov session found at {auth}. "
            "Regenerate auth.json, then retry."
        )
    if not os.environ.get("DISPLAY"):
        return False, (
            "DISPLAY is not set. Run scripts/novnc_reset.sh and "
            "export DISPLAY=:99, then retry."
        )
    return True, ""


def check_sam_profile_session(profile_dir=None, display=None):
    """
    Performs a fresh Playwright session check entirely within the calling thread.
    Creates, uses, and closes its own sync_playwright instance. Never reuses
    Playwright objects across threads.

    Navigates to https://sam.gov/workspace (requires auth) and evaluates:
      1. URL after navigation — redirect to login.gov = definitive logged-out
      2. Positive logged-in markers (role-authenticated, sign out, my workspace…)
      3. Strong negative markers (role-anonymous, id="signin"…)
      4. If neither set of markers fires, assumes logged in (benefit of the doubt
         after the operator completed manual login).

    Always saves debug artifacts to downloads/_debug/sam_profile_check.{html,png}.

    Returns dict: {ok, code, message, debug_html, debug_png}
    Codes: logged_in | not_logged_in | profile_locked | no_session |
           playwright_thread_error | playwright_unavailable | auth_json
    """
    prof_path = Path(profile_dir or DEFAULT_PROFILE_DIR).resolve()
    debug_dir = BASE_DIR / "downloads" / "_debug"
    debug_html_path = debug_dir / "sam_profile_check.html"
    debug_png_path = debug_dir / "sam_profile_check.png"
    debug_html_rel = str(debug_html_path.relative_to(BASE_DIR))
    debug_png_rel = str(debug_png_path.relative_to(BASE_DIR))

    if not prof_path.exists():
        auth = DEFAULT_AUTH_STATE
        if auth_state_ready(auth):
            return {
                "ok": True,
                "code": "auth_json",
                "message": "Using existing auth.json session.",
                "debug_html": "",
                "debug_png": "",
            }
        return {
            "ok": False,
            "code": "no_session",
            "message": (
                "No SAM browser profile or auth.json found. "
                "Open SAM.gov Login in noVNC, complete login, then retry."
            ),
            "debug_html": "",
            "debug_png": "",
        }

    # Fast filesystem lock check — avoid launching Playwright against a locked profile.
    # Chromium holds SingletonLock while a persistent context is open.
    lock_file = prof_path / "SingletonLock"
    if lock_file.exists():
        return {
            "ok": False,
            "code": "profile_locked",
            "message": (
                "Login browser is open and the profile is locked. "
                "After completing Login.gov/MFA, click 'Release Profile' in the sidebar "
                "to save your session to disk. Then click 'Check SAM Session' again."
            ),
            "debug_html": "",
            "debug_png": "",
        }

    resolved_display = display or os.environ.get("DISPLAY", ":99")
    os.environ["DISPLAY"] = resolved_display

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "ok": False,
            "code": "playwright_unavailable",
            "message": "Playwright is not installed. Cannot verify SAM.gov session.",
            "debug_html": "",
            "debug_png": "",
        }

    try:
        with sync_playwright() as pw:
            try:
                # headless=False: SAM.gov's Angular SPA requires full JS rendering to
                # emit role-authenticated/role-anonymous markers reliably.
                # DISPLAY=:99 (Xvfb) provides the display; the browser is briefly
                # visible in noVNC during the check.
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(prof_path),
                    headless=False,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
                    viewport={"width": 1280, "height": 900},
                )
            except Exception as lock_err:
                try:
                    from sam_browser_downloader import is_profile_lock_error
                    locked = is_profile_lock_error(lock_err)
                except ImportError:
                    locked = any(
                        m in str(lock_err).lower()
                        for m in ["lock", "already in use", "processsingleton"]
                    )
                if locked:
                    return {
                        "ok": False,
                        "code": "profile_locked",
                        "message": (
                            "SAM browser profile is already open. "
                            "Click 'Release Profile' in the sidebar to close the login browser "
                            "and save your session, then retry."
                        ),
                        "debug_html": "",
                        "debug_png": "",
                    }
                raise

            try:
                page = context.pages[0] if context.pages else context.new_page()

                # /workspace requires authentication and redirects to Login.gov when
                # the session is absent — more reliable than the home page.
                page.goto(
                    "https://sam.gov/workspace",
                    wait_until="load",
                    timeout=30000,
                )
                # Allow Angular SPA to finish rendering auth state
                page.wait_for_timeout(4000)

                # Save debug artifacts before evaluation
                debug_dir.mkdir(parents=True, exist_ok=True)
                try:
                    debug_html_path.write_text(page.content(), encoding="utf-8")
                except Exception:
                    pass
                try:
                    page.screenshot(path=str(debug_png_path))
                except Exception:
                    pass

                current_url = page.url.lower()

                # URL is the strongest signal: Login.gov redirect = definitive logged-out
                if "login.gov" in current_url or "idp.int.identitysandbox" in current_url:
                    return {
                        "ok": False,
                        "code": "not_logged_in",
                        "message": (
                            "SAM.gov redirected to Login.gov — session not active. "
                            "Complete login in noVNC, close the login browser, then try again. "
                            f"Debug screenshot: {debug_png_rel}"
                        ),
                        "debug_html": debug_html_rel,
                        "debug_png": debug_png_rel,
                    }

                try:
                    from sam_browser_downloader import page_looks_logged_in, page_looks_logged_out
                    is_logged_in = page_looks_logged_in(page)
                    is_logged_out = page_looks_logged_out(page)
                except ImportError:
                    is_logged_in = False
                    is_logged_out = False

                if is_logged_in:
                    return {
                        "ok": True,
                        "code": "logged_in",
                        "message": "SAM.gov session is active.",
                        "debug_html": debug_html_rel,
                        "debug_png": debug_png_rel,
                    }

                if is_logged_out:
                    return {
                        "ok": False,
                        "code": "not_logged_in",
                        "message": (
                            "SAM.gov still appears logged out. "
                            "Check the debug screenshot or reopen SAM.gov Login. "
                            f"Debug screenshot: {debug_png_rel}"
                        ),
                        "debug_html": debug_html_rel,
                        "debug_png": debug_png_rel,
                    }

                # No clear markers in either direction — give benefit of the doubt
                # after the operator manually completed login.
                return {
                    "ok": True,
                    "code": "logged_in",
                    "message": (
                        "SAM.gov session verification inconclusive "
                        "(no login prompt detected — proceeding). "
                        f"Debug screenshot: {debug_png_rel}"
                    ),
                    "debug_html": debug_html_rel,
                    "debug_png": debug_png_rel,
                }

            finally:
                try:
                    context.close()
                except Exception:
                    pass

    except Exception as err:
        text = str(err).lower()
        is_thread_err = "cannot switch" in text or "thread" in text or "event loop" in text
        return {
            "ok": False,
            "code": "playwright_thread_error",
            "message": (
                "SAM browser session check could not run cleanly. "
                "Close the login browser and retry."
                + (f" Detail: {str(err)[:120]}" if not is_thread_err else "")
            ),
            "debug_html": debug_html_rel if debug_html_path.exists() else "",
            "debug_png": debug_png_rel if debug_png_path.exists() else "",
        }


def load_and_select(stages, limit, force, downloads_dir=None, extracts_dir=None, state_csv=None):
    """
    Reads the state CSV, applies candidate selection rules, and returns
    (skipped_results, active_candidates) without running any downloads.

    skipped_results  — list of result dicts (status already determined)
    active_candidates — list of {"notice_id", "url", "title", "stage", "source"}
    """
    downloads_dir = downloads_dir or DEFAULT_DOWNLOADS_DIR
    extracts_dir = extracts_dir or DEFAULT_EXTRACTS_DIR
    state_csv = state_csv or DEFAULT_STATE_CSV

    state_path = Path(state_csv)
    if not state_path.exists():
        raise FileNotFoundError(f"State CSV not found: {state_csv}")

    rows = []
    with state_path.open("r", encoding="utf-8", newline="") as f:
        rows = [dict(row) for row in csv_module.DictReader(f)]

    candidates = select_candidates(rows, set(stages), limit, force, downloads_dir, extracts_dir)

    skipped_results = []
    active_candidates = []
    for c in candidates:
        row = c["row"]
        if c["skip"]:
            skipped_results.append({
                "notice_id": c["notice_id"],
                "title": safe_text(row.get("title"))[:80],
                "stage": safe_text(row.get("macro_stage")),
                "source": safe_text(row.get("source")),
                "url": c["url"],
                "status": c["skip_reason"],
                "error": "",
                "downloads_folder": "",
                "attachment_count": 0,
                "extracted_zips": 0,
                "next_action": _next_action_for_skip(c["skip_reason"]),
            })
        else:
            active_candidates.append({
                "notice_id": c["notice_id"],
                "url": c["url"],
                "title": safe_text(row.get("title"))[:80],
                "stage": safe_text(row.get("macro_stage")),
                "source": safe_text(row.get("source")),
            })

    return skipped_results, active_candidates


def run_batch(
    stages=None,
    limit=10,
    force=False,
    live=True,
    auth_state=None,
    profile_dir=None,
    downloads_dir=None,
    extracts_dir=None,
    state_path=None,
    progress_callback=None,
    skip_session_check=False,
):
    """
    Main batch routine. Returns (results, error_message).
    error_message is non-empty when the batch could not start at all.
    progress_callback(step, total, results) is called after each active candidate.
    """
    stages = set(stages) if stages else BATCH_STAGES
    auth_state = auth_state or DEFAULT_AUTH_STATE
    profile_dir = profile_dir or DEFAULT_PROFILE_DIR
    downloads_dir = downloads_dir or DEFAULT_DOWNLOADS_DIR
    extracts_dir = extracts_dir or DEFAULT_EXTRACTS_DIR
    state_path = state_path or DEFAULT_STATE_PATH

    if live:
        ready, msg = live_preflight_check()
        if not ready:
            return [], msg

    if not profile_ready(profile_dir) and not auth_state_ready(auth_state):
        return [], (
            "No SAM.gov session found. Open SAM.gov Login in noVNC to create "
            "the persistent browser profile, or regenerate auth.json."
        )

    path = Path(state_path)
    if not path.exists():
        return [], f"State CSV not found: {state_path}"

    with path.open("r", encoding="utf-8", newline="") as _f:
        rows = [dict(r) for r in csv_module.DictReader(_f)]

    candidates = select_candidates(rows, stages, limit, force, downloads_dir, extracts_dir)
    headless = not live

    results = []

    for c in candidates:
        if not c["skip"]:
            continue
        row = c["row"]
        results.append({
            "notice_id": c["notice_id"],
            "title": safe_text(row.get("title"))[:80],
            "stage": safe_text(row.get("macro_stage")),
            "source": safe_text(row.get("source")),
            "url": c["url"],
            "status": c["skip_reason"],
            "error": "",
            "downloads_folder": "",
            "attachment_count": 0,
            "extracted_zips": 0,
            "next_action": _next_action_for_skip(c["skip_reason"]),
        })

    active = [c for c in candidates if not c["skip"]]
    total = len(active)

    for step, c in enumerate(active, start=1):
        row = c["row"]
        download_result = process_candidate(
            notice_id=c["notice_id"],
            url=c["url"],
            auth_state=auth_state,
            downloads_dir=downloads_dir,
            headless=headless,
            profile_dir=profile_dir if profile_ready(profile_dir) else "",
            skip_session_check=skip_session_check,
        )
        result = {
            "notice_id": c["notice_id"],
            "title": safe_text(row.get("title"))[:80],
            "stage": safe_text(row.get("macro_stage")),
            "source": safe_text(row.get("source")),
            "url": c["url"],
            **download_result,
        }
        results.append(result)
        if progress_callback:
            progress_callback(step, total, list(results))

    return results, ""


def write_batch_report(results, report_path, stages, limit, force, live):
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    downloaded = [r for r in results if r["status"] == STATUS_DOWNLOADED]
    skipped = [r for r in results if r["status"].startswith("skipped_")]
    failed = [r for r in results if r["status"].startswith("failed_")]

    lines = [
        "# Batch Document Download",
        "",
        f"**Generated:** {now}",
        f"**Stages:** {', '.join(sorted(stages))}",
        f"**Limit:** {limit}  **Force:** {force}  **Live (headed):** {live}",
        "",
        "## Summary",
        "",
        f"- **Downloaded:** {len(downloaded)}",
        f"- **Skipped:** {len(skipped)}",
        f"- **Failed:** {len(failed)}",
        f"- **Total candidates evaluated:** {len(results)}",
        "",
        "## Results",
        "",
        "| notice_id | title | stage | status | attachments | next action |",
        "|---|---|---|---|---|---|",
    ]

    for r in results:
        count = r.get("attachment_count") or ""
        lines.append(
            f"| {r['notice_id']} | {r['title'][:50]} | {r['stage']} "
            f"| {r['status']} | {count} | {r['next_action']} |"
        )

    lines += ["", "## Detail", ""]

    for r in results:
        if r["status"] == STATUS_DOWNLOADED:
            lines += [
                f"### {r['notice_id']} — downloaded",
                "",
                f"- **Title:** {r['title']}",
                f"- **Stage:** {r['stage']}  **Source:** {r['source']}",
                f"- **URL:** {r['url']}",
                f"- **Downloads folder:** `{r['downloads_folder']}`",
                f"- **Attachment count:** {r.get('attachment_count', 0)}",
                f"- **ZIPs extracted:** {r.get('extracted_zips', 0)}",
                f"- **Next action:** {r['next_action']}",
                "",
            ]
        elif r["status"].startswith("failed_"):
            lines += [
                f"### {r['notice_id']} — {r['status']}",
                "",
                f"- **Title:** {r['title']}",
                f"- **Stage:** {r['stage']}  **Source:** {r['source']}",
                f"- **URL:** {r['url'] or '(none)'}",
                f"- **Error:** {r.get('error', '')}",
                f"- **Next action:** {r['next_action']}",
                "",
            ]

    lines += [
        "## Next Steps",
        "",
        "1. For downloaded cards: use Run AI Review in the dashboard.",
        "2. For skipped (no URL / sources-sought): upload documents manually.",
        "3. For failed (login/infra): open SAM.gov Login in noVNC, then retry.",
        "",
    ]

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(report_path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Batch download SAM.gov documents for kept opportunities."
    )
    parser.add_argument(
        "--stages", nargs="+", default=list(BATCH_STAGES),
        help="Stages to include (default: kept post-intake stages).",
    )
    parser.add_argument("--limit", type=int, default=10, help="Max candidates to download.")
    parser.add_argument("--force", action="store_true", help="Re-download even if docs exist.")
    parser.add_argument("--live", action="store_true", default=True, help="Use headed browser (requires noVNC).")
    parser.add_argument("--headless", action="store_true", help="Use headless browser (overrides --live).")
    parser.add_argument("--auth-state", default=DEFAULT_AUTH_STATE, help="Path to auth.json.")
    parser.add_argument(
        "--profile-dir",
        default=DEFAULT_PROFILE_DIR,
        help="Persistent SAM browser profile directory. Preferred when present.",
    )
    parser.add_argument("--downloads-dir", default=DEFAULT_DOWNLOADS_DIR)
    parser.add_argument("--extracts-dir", default=DEFAULT_EXTRACTS_DIR)
    parser.add_argument("--state-path", default=DEFAULT_STATE_PATH)
    return parser.parse_args()


def main():
    args = parse_args()
    live = not args.headless

    print("")
    print("GovCon Scout — Batch Document Download")
    print(f"Stages: {', '.join(args.stages)}")
    print(f"Limit: {args.limit}  Force: {args.force}  Live: {live}")
    print("")

    def progress(step, total, results_so_far):
        last = results_so_far[-1] if results_so_far else {}
        print(f"[{step}/{total}] {last.get('notice_id', '?')} — {last.get('status', '?')}")

    results, error = run_batch(
        stages=args.stages,
        limit=args.limit,
        force=args.force,
        live=live,
        auth_state=args.auth_state,
        profile_dir=args.profile_dir,
        downloads_dir=args.downloads_dir,
        extracts_dir=args.extracts_dir,
        state_path=args.state_path,
        progress_callback=progress,
    )

    if error:
        print(f"Batch could not start: {error}")
        sys.exit(1)

    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    report_path = Path(DEFAULT_BATCH_RUNS_DIR) / f"batch_download_docs_{stamp}.md"
    report_path = write_batch_report(results, report_path, set(args.stages), args.limit, args.force, live)

    downloaded = sum(1 for r in results if r["status"] == STATUS_DOWNLOADED)
    skipped = sum(1 for r in results if r["status"].startswith("skipped_"))
    failed = sum(1 for r in results if r["status"].startswith("failed_"))

    print("")
    print(f"Done. Downloaded: {downloaded}  Skipped: {skipped}  Failed: {failed}")
    print(f"Report: {report_path}")
    print("")


if __name__ == "__main__":
    main()
