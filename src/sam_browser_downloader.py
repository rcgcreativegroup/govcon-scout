import argparse
import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_AUTH_STATE = "auth.json"
DEFAULT_PROFILE_DIR = ".browser/sam-profile"
DEFAULT_DOWNLOADS_DIR = "downloads"
DEFAULT_EXPORT_CSV = "exports/govcon_scout_opportunities_latest.csv"

DOWNLOAD_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".zip",
    ".txt",
    ".rtf",
    ".ppt",
    ".pptx",
}

BID_FILE_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".zip",
}

PIEE_ATTACHMENT_KEYWORDS = [
    "sol_",
    "_sol_",
    "pws",
    "sow",
    "pricing",
    "price",
    "schedule",
    "clin",
    "attachment",
    "att_",
    "att1",
    "att2",
    "att3",
    "att4",
    "sf1449",
    "sf 1449",
    "sf30",
    "amendment",
    "facilities",
    "footprint",
    "map",
]

SAM_REFERENCE_TEXT_EXCLUDES = [
    "web based training",
    "training",
    "registration instructions",
    "vendor access instructions",
    "procurement toolbox",
    "user guide",
    "help",
]

SAM_REFERENCE_URL_EXCLUDES = [
    "pieetraining.eb.mil",
    "acq.osd.mil/asda/dpc/ce/cap/docs/piee",
    "public.cyber.mil",
]

PIEE_NAV_TEXT_EXCLUDES = {
    "solicitation",
    "lookup",
    "product/service codes",
    "naics",
    "documentation",
    "exit",
    "close",
    "combined synopsis/solicitation",
    "points of contact",
    "solicitation details",
    "contract information",
    "offer template",
    "volume name",
    "attachments",
}

BAD_DOWNLOAD_NAME_PATTERNS = [
    "installroot",
    "certificate",
    "certificates",
    "pkcs",
    "pki",
    "dod_approved_external_pkis",
    "trust_chains",
    "master_document",
    "unclass-",
]


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def make_safe_folder_name(value):
    text = safe_text(value) or "unknown"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text.strip("_")[:120] or "unknown"


def ensure_folder(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def clean_filename(name, fallback="downloaded_file"):
    name = safe_text(name)

    if not name:
        name = fallback

    name = unquote(name)
    name = name.split("?")[0].split("#")[0]
    name = name.replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^A-Za-z0-9._() -]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip()

    return name or fallback


def unique_path(folder, filename):
    folder = Path(folder)
    base_name = clean_filename(filename)
    candidate = folder / base_name

    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix

    for index in range(2, 999):
        candidate = folder / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate

    return folder / f"{stem}_{int(time.time())}{suffix}"


def get_file_extension_from_text(value):
    text = safe_text(value).lower()
    parsed_path = urlparse(text).path.lower()

    for extension in DOWNLOAD_EXTENSIONS:
        if text.endswith(extension) or parsed_path.endswith(extension):
            return extension

    return ""


def has_bid_file_extension(value):
    extension = get_file_extension_from_text(value)
    return extension in BID_FILE_EXTENSIONS


def is_bad_download_name(filename):
    lower = safe_text(filename).lower()
    return any(pattern in lower for pattern in BAD_DOWNLOAD_NAME_PATTERNS)


def looks_like_bid_attachment_filename(text):
    lower = safe_text(text).lower()

    if not lower:
        return False

    if not has_bid_file_extension(lower):
        return False

    if is_bad_download_name(lower):
        return False

    return any(keyword in lower for keyword in PIEE_ATTACHMENT_KEYWORDS)


def looks_like_download_all(text):
    return safe_text(text).lower() == "download all attachments"


def is_reference_link(text, url):
    lower_text = safe_text(text).lower()
    lower_url = safe_text(url).lower()

    if any(item in lower_text for item in SAM_REFERENCE_TEXT_EXCLUDES):
        return True

    if any(item in lower_url for item in SAM_REFERENCE_URL_EXCLUDES):
        return True

    return False


def is_piee_nav_text(text):
    return safe_text(text).lower() in PIEE_NAV_TEXT_EXCLUDES


def looks_like_piee_link(text, url):
    combined = f"{safe_text(text)} {safe_text(url)}".lower()
    return "piee solicitation module" in combined or "piee.eb.mil" in combined


def filename_from_url_or_text(url, text="", fallback="downloaded_file"):
    parsed = urlparse(safe_text(url))
    name = Path(parsed.path).name

    if name:
        return clean_filename(name, fallback=fallback)

    if text:
        return clean_filename(text, fallback=fallback)

    return fallback


def profile_label(profile_dir):
    return Path(profile_dir).name or "sam-profile"


def is_profile_lock_error(error):
    text = str(error).lower()
    markers = [
        "processsingleton",
        "singletonlock",
        "lock",
        "profile",
        "user data directory is already in use",
        "already in use",
    ]
    return any(marker in text for marker in markers)


def require_auth_state(auth_state):
    path = Path(auth_state)

    if not path.exists():
        print("")
        print(f"Missing auth state file: {auth_state}")
        print("")
        print("Regenerate it through Codespaces/noVNC:")
        print("")
        print("  DISPLAY=:99 npx playwright codegen --save-storage=auth.json https://sam.gov")
        print("")
        print("Log in manually, complete MFA, then stop codegen with CTRL+C.")
        print("Do not commit auth.json.")
        print("")
        sys.exit(1)

    return path


def save_page_debug(page, output_folder, notice_id, label="page"):
    debug_folder = Path(output_folder) / "_debug"
    debug_folder.mkdir(parents=True, exist_ok=True)

    safe_notice = make_safe_folder_name(notice_id)
    safe_label = make_safe_folder_name(label)
    html_path = debug_folder / f"{safe_notice}_{safe_label}.html"
    screenshot_path = debug_folder / f"{safe_notice}_{safe_label}.png"

    try:
        html_path.write_text(page.content(), encoding="utf-8")
        print(f"Saved debug HTML: {html_path}")
    except Exception as error:
        print(f"Could not save debug HTML: {error}")

    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
        print(f"Saved debug screenshot: {screenshot_path}")
    except Exception as error:
        print(f"Could not save debug screenshot: {error}")


def open_browser_context(playwright, auth_state, headless=True, profile_dir=""):
    if profile_dir:
        profile_path = Path(profile_dir)
        profile_path.mkdir(parents=True, exist_ok=True)
        try:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_path),
                headless=headless,
                accept_downloads=True,
                viewport={"width": 1440, "height": 1000},
            )
            return None, context
        except Exception as error:
            if is_profile_lock_error(error):
                raise RuntimeError(
                    "SAM browser profile is already open. Close the login browser, "
                    "then retry the document download."
                ) from error
            raise

    require_auth_state(auth_state)

    browser = playwright.chromium.launch(headless=headless)

    context = browser.new_context(
        storage_state=auth_state,
        accept_downloads=True,
        viewport={"width": 1440, "height": 1000},
    )

    return browser, context


def page_looks_logged_out(page):
    html = page.content().lower()
    # "sign in" is intentionally omitted — SAM.gov's Angular bundle includes that
    # text in every page regardless of auth state, causing false positives.
    logged_out_markers = [
        "role-anonymous",
        'id="signin"',
        "signin-trigger-btn",
        '"uid":0',
        "sign-in-button-current",
    ]
    return any(marker in html for marker in logged_out_markers)


def page_looks_logged_in(page):
    """Returns True if the page contains strong positive authenticated-state markers."""
    html = page.content().lower()
    logged_in_markers = [
        "role-authenticated",
        "sign out",
        "signout",
        "log out",
        "logout",
        "my workspace",
        "sam-workspace",
        "workspace-header",
        "user-menu",
        "account-menu",
    ]
    return any(marker in html for marker in logged_in_markers)


def test_auth(auth_state, headless=True, screenshot_path="sam-session-test.png", profile_dir=""):
    print("")
    if profile_dir:
        print(f"Testing SAM.gov session with profile: {profile_label(profile_dir)}")
    else:
        print(f"Testing SAM.gov session with auth state: {Path(auth_state).name}")
    print("")

    with sync_playwright() as playwright:
        browser, context = open_browser_context(
            playwright=playwright,
            auth_state=auth_state,
            headless=headless,
            profile_dir=profile_dir,
        )

        page = context.new_page()
        page.goto("https://sam.gov", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

        title = page.title()
        print(f"Page title: {title}")

        page.screenshot(path=screenshot_path, full_page=True)
        print(f"Saved screenshot: {screenshot_path}")

        logged_out = page_looks_logged_out(page)

        context.close()
        if browser:
            browser.close()

    if logged_out:
        print("")
        print("SAM.gov session check failed.")
        print("The page loaded, but it appears to be logged out or anonymous.")
        print("")
        print("Refresh auth.json through noVNC:")
        print("")
        print("  DISPLAY=:99 npx playwright codegen --save-storage=auth.json https://sam.gov")
        print("")
        print("Log in manually, complete MFA, then stop codegen with CTRL+C.")
        print("")
        sys.exit(1)

    print("")
    print("Auth test completed.")
    print("SAM.gov appears logged in.")
    print("")


def run_login_instructions():
    print("")
    print("Manual SAM.gov login for Codespaces should use noVNC + a persistent Playwright profile.")
    print("")
    print("Start virtual display services if needed:")
    print("")
    print("  Xvfb :99 -screen 0 1280x900x24 >/tmp/xvfb.log 2>&1 &")
    print("  export DISPLAY=:99")
    print("  fluxbox >/tmp/fluxbox.log 2>&1 &")
    print("  x11vnc -display :99 -nopw -listen localhost -xkb -forever >/tmp/x11vnc.log 2>&1 &")
    print("  websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/novnc.log 2>&1 &")
    print("")
    print("Open forwarded port 6080, click vnc.html, then Connect.")
    print("")
    print("Preferred persistent profile flow:")
    print("")
    print("  python src/sam_browser_downloader.py --test-auth --profile-dir .browser/sam-profile --headed")
    print("")
    print("Fallback auth.json flow:")
    print("")
    print("  DISPLAY=:99 npx playwright codegen --save-storage=auth.json https://sam.gov")
    print("")
    print("Do not commit auth.json or .browser/.")
    print("")


def normalize_href(href, page_url):
    href = safe_text(href)

    if not href:
        return ""

    if href.startswith("javascript:"):
        return href

    return urljoin(page_url, href)


def dedupe_candidates(candidates):
    seen = set()
    unique = []

    for item in candidates:
        key = (item.get("kind"), item.get("href"), item.get("text"))
        if key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return unique


def collect_sam_candidates(page):
    candidates = []
    current_url = page.url
    anchors = page.locator("a")
    count = anchors.count()

    for index in range(count):
        try:
            anchor = anchors.nth(index)
            href = normalize_href(anchor.get_attribute("href"), current_url)
            text = safe_text(anchor.inner_text(timeout=1000))

            if not href:
                continue

            if is_reference_link(text, href):
                continue

            if looks_like_piee_link(text, href):
                candidates.append({
                    "kind": "piee",
                    "index": index,
                    "href": href,
                    "text": text,
                })

        except Exception:
            continue

    return dedupe_candidates(candidates)


def collect_piee_attachment_candidates(page):
    candidates = []
    current_url = page.url
    anchors = page.locator("a")
    count = anchors.count()

    for index in range(count):
        try:
            anchor = anchors.nth(index)
            href = normalize_href(anchor.get_attribute("href"), current_url)
            text = safe_text(anchor.inner_text(timeout=1000))

            if not text:
                continue

            if is_reference_link(text, href):
                continue

            if is_piee_nav_text(text):
                continue

            if looks_like_download_all(text):
                candidates.append({
                    "kind": "download_all",
                    "index": index,
                    "href": href,
                    "text": text,
                })
                continue

            if looks_like_bid_attachment_filename(text):
                candidates.append({
                    "kind": "attachment_file",
                    "index": index,
                    "href": href,
                    "text": text,
                })

        except Exception:
            continue

    return dedupe_candidates(candidates)


def print_candidates(candidates, max_items=80):
    print(f"Candidate link count: {len(candidates)}")

    for item in candidates[:max_items]:
        label = item.get("text")[:120] or "[no text]"
        print(f"- [{item.get('kind')}] {label} | {item.get('href')}")


def wait_for_page_to_settle(page, label="page"):
    page.wait_for_timeout(5000)

    possible_texts = [
        "Attachments",
        "Download All Attachments",
        "Solicitation",
        "Opportunity",
        "Documents",
        "Notice",
        "General",
        "Contact",
    ]

    for text in possible_texts:
        try:
            page.get_by_text(text, exact=False).first.wait_for(timeout=2500)
            return
        except Exception:
            continue

    print(f"Warning: {label} loaded, but expected page text was not detected.")


def save_download(download, output_folder, preferred_filename=""):
    suggested = clean_filename(download.suggested_filename or preferred_filename or "downloaded_file")

    if is_bad_download_name(suggested):
        print(f"Rejected non-solicitation/support download: {suggested}")
        return ""

    target_path = unique_path(output_folder, suggested)
    download.save_as(str(target_path))
    print(f"Downloaded: {target_path}")
    return str(target_path)


def click_exact_text_for_download(page, text, output_folder, timeout=12000):
    text = safe_text(text)

    if not text:
        return ""

    locators = [
        page.get_by_text(text, exact=True),
        page.locator("a", has_text=text),
        page.locator("button", has_text=text),
        page.locator("[role='button']", has_text=text),
    ]

    for locator in locators:
        try:
            target = locator.first
            if target.count() <= 0:
                continue

            target.scroll_into_view_if_needed(timeout=5000)

            with page.expect_download(timeout=timeout) as download_info:
                target.click(timeout=7000)

            download = download_info.value
            return save_download(download, output_folder, preferred_filename=text)

        except PlaywrightTimeoutError:
            continue

        except Exception:
            continue

    return ""


def click_download_all(page, output_folder):
    print("Trying Download All Attachments first...")

    saved = click_exact_text_for_download(
        page=page,
        text="Download All Attachments",
        output_folder=output_folder,
        timeout=15000,
    )

    if saved:
        return [saved]

    print("Download All Attachments did not produce a download event.")
    return []


def click_attachment_filenames(page, candidates, output_folder):
    downloaded_paths = []

    attachment_candidates = [
        item for item in candidates
        if item.get("kind") == "attachment_file"
    ]

    for item in attachment_candidates:
        filename = safe_text(item.get("text"))

        if not looks_like_bid_attachment_filename(filename):
            continue

        print(f"Trying attachment filename: {filename}")

        saved = click_exact_text_for_download(
            page=page,
            text=filename,
            output_folder=output_folder,
            timeout=12000,
        )

        if saved:
            downloaded_paths.append(saved)

    return downloaded_paths


def get_filename_from_content_disposition(header_value):
    text = safe_text(header_value)

    if not text:
        return ""

    match = re.search(r"filename\*=UTF-8''([^;]+)", text, re.IGNORECASE)
    if match:
        return clean_filename(match.group(1))

    match = re.search(r'filename="([^"]+)"', text, re.IGNORECASE)
    if match:
        return clean_filename(match.group(1))

    match = re.search(r"filename=([^;]+)", text, re.IGNORECASE)
    if match:
        return clean_filename(match.group(1))

    return ""


def save_response_to_file(response, output_folder, fallback_filename):
    content_type = safe_text(response.headers.get("content-type", ""))
    disposition = safe_text(response.headers.get("content-disposition", ""))

    filename = get_filename_from_content_disposition(disposition) or fallback_filename

    if "." not in Path(filename).name and "pdf" in content_type.lower():
        filename += ".pdf"

    if is_bad_download_name(filename):
        print(f"Rejected non-solicitation/support response file: {filename}")
        return ""

    target_path = unique_path(output_folder, filename)
    target_path.write_bytes(response.body())

    print(f"Downloaded via request: {target_path}")
    return str(target_path)


def request_direct_file(context, url, output_folder, text=""):
    if not url or url.startswith("javascript:"):
        return ""

    if not looks_like_bid_attachment_filename(text) and not has_bid_file_extension(url):
        return ""

    fallback = filename_from_url_or_text(
        url=url,
        text=text,
        fallback="downloaded_file",
    )

    try:
        response = context.request.get(url, timeout=60000)

        if not response.ok:
            print(f"Request download failed [{response.status}]: {url}")
            return ""

        content_type = safe_text(response.headers.get("content-type", "")).lower()
        disposition = safe_text(response.headers.get("content-disposition", "")).lower()

        file_like = (
            has_bid_file_extension(url)
            or "application/pdf" in content_type
            or "application/octet-stream" in content_type
            or "application/vnd" in content_type
            or "filename=" in disposition
        )

        if not file_like:
            return ""

        return save_response_to_file(
            response=response,
            output_folder=output_folder,
            fallback_filename=fallback,
        )

    except Exception as error:
        print(f"Request download failed for {url}: {error}")
        return ""


def request_candidate_files(context, candidates, output_folder):
    downloaded = []

    for item in candidates:
        if item.get("kind") != "attachment_file":
            continue

        saved = request_direct_file(
            context=context,
            url=item.get("href"),
            output_folder=output_folder,
            text=item.get("text"),
        )

        if saved:
            downloaded.append(saved)

    return downloaded


def open_piee_and_download(context, page, piee_url, notice_id, output_folder, downloads_dir):
    downloaded_paths = []

    print("")
    print(f"Opening PIEE link: {piee_url}")
    print("")

    try:
        page.goto(piee_url, wait_until="domcontentloaded", timeout=90000)
        wait_for_page_to_settle(page, label="PIEE page")
        save_page_debug(page, downloads_dir, notice_id, label="piee")

        piee_candidates = collect_piee_attachment_candidates(page)

        print("PIEE attachment candidates:")
        print_candidates(piee_candidates, max_items=80)

        if not piee_candidates:
            print("No PIEE attachment candidates were detected.")
            return downloaded_paths

        # First try Download All Attachments if present.
        if any(item.get("kind") == "download_all" for item in piee_candidates):
            downloaded_paths.extend(
                click_download_all(
                    page=page,
                    output_folder=output_folder,
                )
            )

        # Then try exact filename clicks.
        if not downloaded_paths:
            downloaded_paths.extend(
                click_attachment_filenames(
                    page=page,
                    candidates=piee_candidates,
                    output_folder=output_folder,
                )
            )

        # Finally, try request downloads for direct file URLs only.
        if not downloaded_paths:
            downloaded_paths.extend(
                request_candidate_files(
                    context=context,
                    candidates=piee_candidates,
                    output_folder=output_folder,
                )
            )

    except Exception as error:
        print(f"PIEE handling failed: {error}")

    return downloaded_paths


def download_for_notice(
    auth_state,
    notice_id,
    url,
    downloads_dir,
    headless=True,
    debug=True,
    profile_dir="",
):
    notice_folder = Path(downloads_dir) / make_safe_folder_name(notice_id)
    ensure_folder(notice_folder)

    print("")
    print(f"Notice ID: {notice_id}")
    print(f"SAM.gov URL: {url}")
    print(f"Download folder: {notice_folder}")
    if profile_dir:
        print(f"Session mode: persistent profile ({profile_label(profile_dir)})")
    else:
        print(f"Auth state: {Path(auth_state).name}")
    print(f"Headless: {headless}")
    print("")

    downloaded_paths = []

    with sync_playwright() as playwright:
        browser, context = open_browser_context(
            playwright=playwright,
            auth_state=auth_state,
            headless=headless,
            profile_dir=profile_dir,
        )

        page = context.new_page()

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=90000)
            wait_for_page_to_settle(page, label="SAM page")
            if page_looks_logged_out(page):
                raise RuntimeError(
                    "SAM.gov session is not logged in. Open SAM.gov Login in noVNC, "
                    "complete login/MFA, then retry document download."
                )

            print("SAM.gov page loaded. Looking for PIEE/attachment candidates...")

            sam_candidates = collect_sam_candidates(page)

            print("SAM.gov candidates:")
            print_candidates(sam_candidates, max_items=80)

            if debug:
                save_page_debug(page, downloads_dir, notice_id, label="sam")

            piee_links = [
                item for item in sam_candidates
                if item.get("kind") == "piee"
            ]

            if not piee_links:
                print("No PIEE link detected from SAM.gov page.")

            for piee_item in piee_links:
                piee_downloads = open_piee_and_download(
                    context=context,
                    page=page,
                    piee_url=piee_item.get("href"),
                    notice_id=notice_id,
                    output_folder=notice_folder,
                    downloads_dir=downloads_dir,
                )
                downloaded_paths.extend(piee_downloads)

        finally:
            context.close()
            if browser:
                browser.close()

    downloaded_paths = list(dict.fromkeys([path for path in downloaded_paths if path]))

    print("")

    if downloaded_paths:
        print("Downloaded files:")
        for path in downloaded_paths:
            print(f"- {path}")
    else:
        print("No solicitation files were downloaded automatically.")
        print("Debug HTML/screenshots were saved if --debug is enabled.")
        print("Next step may require PIEE selector tuning.")

    print("")

    return downloaded_paths


def read_pipeline_or_csv(csv_path):
    path = Path(csv_path)

    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    rows = []

    with open(path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            rows.append(dict(row))

    return rows


def score_int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def select_rows_for_download(rows, limit=5):
    candidates = []

    for row in rows:
        if row.get("notice_actionability") != "actionable":
            continue

        if row.get("ui_link"):
            candidates.append(row)

    candidates = sorted(
        candidates,
        key=lambda row: (
            score_int(row.get("ready_for_bid_no_bid_analysis") == "Yes"),
            score_int(row.get("prime_reality_score")),
            score_int(row.get("fit_score")),
        ),
        reverse=True,
    )

    return candidates[:limit]


def download_from_csv(
    auth_state,
    csv_path,
    downloads_dir,
    limit=5,
    headless=True,
    debug=True,
    profile_dir="",
):
    rows = read_pipeline_or_csv(csv_path)
    selected = select_rows_for_download(rows, limit=limit)

    print(f"Selected {len(selected)} opportunities from CSV for browser download.")

    for index, row in enumerate(selected, start=1):
        notice_id = (
            row.get("notice_id")
            or row.get("solicitation_number")
            or row.get("sam_notice_id")
        )

        url = row.get("ui_link")

        if not notice_id or not url:
            continue

        print(f"\n=== {index}/{len(selected)} ===")

        download_for_notice(
            auth_state=auth_state,
            notice_id=notice_id,
            url=url,
            downloads_dir=downloads_dir,
            headless=headless,
            debug=debug,
            profile_dir=profile_dir,
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="GovCon Scout SAM.gov / PIEE browser attachment downloader."
    )

    parser.add_argument(
        "--login",
        action="store_true",
        help="Print noVNC/codegen login instructions for refreshing auth.json.",
    )

    parser.add_argument(
        "--test-auth",
        action="store_true",
        help="Test auth.json by opening SAM.gov headlessly and saving a screenshot.",
    )

    parser.add_argument(
        "--auth-state",
        default=DEFAULT_AUTH_STATE,
        help="Playwright storage state file created by codegen login.",
    )

    parser.add_argument(
        "--profile-dir",
        default="",
        help="Optional persistent Playwright profile directory. Preferred over --auth-state when provided.",
    )

    parser.add_argument(
        "--notice-id",
        default="",
        help="Notice ID / solicitation number for single opportunity download.",
    )

    parser.add_argument(
        "--url",
        default="",
        help="SAM.gov opportunity URL for single opportunity download.",
    )

    parser.add_argument(
        "--from-csv",
        action="store_true",
        help="Download attachments for top opportunities from latest GovCon Scout CSV.",
    )

    parser.add_argument(
        "--csv",
        default=DEFAULT_EXPORT_CSV,
        help="CSV source for --from-csv mode.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Limit for --from-csv mode.",
    )

    parser.add_argument(
        "--downloads-dir",
        default=DEFAULT_DOWNLOADS_DIR,
        help="Folder where downloaded files will be saved.",
    )

    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser headed. Only use inside noVNC/Xvfb DISPLAY session.",
    )

    parser.add_argument(
        "--no-debug",
        action="store_true",
        help="Disable saving debug HTML/screenshot.",
    )

    return parser.parse_args()


def main():
    args = parse_args()
    headless = not args.headed

    if args.login:
        run_login_instructions()
        return

    if args.test_auth:
        test_auth(
            auth_state=args.auth_state,
            headless=headless,
            profile_dir=args.profile_dir,
        )
        return

    if args.from_csv:
        download_from_csv(
            auth_state=args.auth_state,
            csv_path=args.csv,
            downloads_dir=args.downloads_dir,
            limit=args.limit,
            headless=headless,
            debug=not args.no_debug,
            profile_dir=args.profile_dir,
        )
        return

    if args.notice_id and args.url:
        download_for_notice(
            auth_state=args.auth_state,
            notice_id=args.notice_id,
            url=args.url,
            downloads_dir=args.downloads_dir,
            headless=headless,
            debug=not args.no_debug,
            profile_dir=args.profile_dir,
        )
        return

    print("")
    print("No action selected.")
    print("")
    print("Use one of these:")
    print("")
    print("  python src/sam_browser_downloader.py --login")
    print("")
    print("  python src/sam_browser_downloader.py --test-auth")
    print("")
    print("  python src/sam_browser_downloader.py --notice-id HE125426QE041 --url \"https://sam.gov/workspace/contract/opp/4848111d989545ec9e20b3e6d02ccf2f/view\"")
    print("")
    print("  python src/sam_browser_downloader.py --from-csv --limit 5")
    print("")
    sys.exit(1)


if __name__ == "__main__":
    main()
