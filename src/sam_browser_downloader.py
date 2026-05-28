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
    # Match "Download All", "Download All Attachments", "DOWNLOAD ALL", etc.
    return "download all" in safe_text(text).lower()


def is_direct_attachment_filename(text, href=""):
    """
    Returns True for links that look like solicitation attachment files on a SAM.gov
    workspace/opportunity page.  Does NOT require PIEE keyword matching — the Attachments
    section context is sufficient signal; we only filter by extension and bad-name lists.
    """
    lower_text = safe_text(text).lower()
    lower_href  = safe_text(href).lower()
    if not lower_text and not lower_href:
        return False
    has_ext = any(
        lower_text.endswith(ext) or lower_href.endswith(ext)
        for ext in DOWNLOAD_EXTENSIONS
    )
    if not has_ext:
        return False
    if is_bad_download_name(lower_text):
        return False
    if is_reference_link(text, href):
        return False
    return True


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


def save_rendered_debug(page, downloads_dir, notice_id, label="rendered"):
    """Save the live rendered DOM (post-Angular) as HTML + full-page screenshot."""
    save_page_debug(page, downloads_dir, notice_id, label=label)


FILE_LIKE_CONTENT_TYPES = {
    "application/pdf",
    "application/zip",
    "application/octet-stream",
    "application/msword",
    "application/vnd",
    "text/plain",
    "text/csv",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats",
}

FILE_LIKE_URL_MARKERS = [
    "/download",
    "response-cont",
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".zip",
    ".txt",
    ".rtf",
    ".csv",
]


def is_file_like_url(url):
    lower = safe_text(url).lower().split("?")[0]
    return any(m in lower for m in FILE_LIKE_URL_MARKERS)


def is_file_like_response(response, preferred_filename=""):
    """Return True if the HTTP response looks like a downloadable file."""
    try:
        if not response.ok:
            return False
        ct = response.headers.get("content-type", "").lower()
        disposition = response.headers.get("content-disposition", "").lower()
        url = safe_text(response.url)

        if "text/html" in ct and "filename=" not in disposition and not is_file_like_url(url):
            return False  # skip plain HTML pages

        if any(ft in ct for ft in FILE_LIKE_CONTENT_TYPES):
            return True
        if "filename=" in disposition:
            return True
        if is_file_like_url(url):
            return True
        if preferred_filename and has_bid_file_extension(preferred_filename):
            return True
        return False
    except Exception:
        return False


def save_response_body_to_file(body, output_folder, filename, fallback="downloaded_file"):
    """Save raw bytes to a file in output_folder using existing helpers."""
    if not body:
        return ""
    fname = clean_filename(filename or fallback, fallback=fallback)
    if is_bad_download_name(fname):
        print(f"  Rejected bad download name: {fname}")
        return ""
    target = unique_path(output_folder, fname)
    try:
        target.write_bytes(body)
        print(f"  Saved: {target}")
        return str(target)
    except Exception as exc:
        print(f"  Could not save response body: {exc}")
        return ""


def click_and_capture_file(page, locator, output_folder, preferred_filename="", timeout=30000):
    """
    Click a locator and capture the resulting file via whichever mechanism fires:
      A) Playwright download event (browser download dialog)
      B) Response body (SAM.gov returns 200 document with Content-Disposition or file URL)
      C) New-tab URL fetch (link opens in new window)

    Returns (saved_path, mechanism) or ("", "") if nothing was captured.
    Does not log cookies, tokens, auth headers, or profile data.
    """
    # Cache file-like responses as they arrive (read body in-handler)
    file_captures = []

    def _on_response(resp):
        try:
            if not is_file_like_response(resp, preferred_filename):
                return
            body = resp.body()
            if not body:
                return
            ct   = resp.headers.get("content-type", "")
            disp = resp.headers.get("content-disposition", "")
            fname = (get_filename_from_content_disposition(disp)
                     or preferred_filename
                     or filename_from_url_or_text(resp.url, preferred_filename))
            # Log safe summary — no tokens, no full tokenized URL
            url_safe = resp.url.split("?")[0][-80:]
            print(f"  Response captured: ct={ct[:40]}  url=...{url_safe}  fname={fname[:60]}")
            file_captures.append((body, fname))
        except Exception:
            pass

    pre_url = page.url
    page.on("response", _on_response)

    try:
        # Approach A: Playwright download event
        try:
            with page.expect_download(timeout=min(timeout, 15000)) as dl_info:
                locator.scroll_into_view_if_needed(timeout=5000)
                locator.click(timeout=7000)
            dl = dl_info.value
            page.remove_listener("response", _on_response)
            saved = save_download(dl, output_folder, preferred_filename=preferred_filename)
            if saved:
                print(f"  Captured via download event: {Path(saved).name}")
                return saved, "download_event"
        except PlaywrightTimeoutError:
            pass  # no download event — fall through to response/navigation checks
        except Exception as exc:
            print(f"  Download event error: {exc}")

        # Give the response handler time to fire
        page.wait_for_timeout(2000)

        # Approach B: cached response body
        if file_captures:
            body, fname = file_captures[-1]
            saved = save_response_body_to_file(body, output_folder, fname, preferred_filename)
            if saved:
                return saved, "response_body"

        # Approach C: check if page navigated to a file-like URL
        post_url = page.url
        if post_url != pre_url and is_file_like_url(post_url):
            print(f"  Page navigated to file URL — fetching via context.request...")
            try:
                resp = page.context.request.get(post_url, timeout=60000)
                if resp.ok:
                    disp  = resp.headers.get("content-disposition", "")
                    fname = (get_filename_from_content_disposition(disp)
                             or preferred_filename
                             or filename_from_url_or_text(post_url, preferred_filename))
                    saved = save_response_body_to_file(resp.body(), output_folder, fname, preferred_filename)
                    if saved:
                        return saved, "navigation_url"
            except Exception as exc:
                print(f"  Navigation URL fetch failed: {exc}")

        return "", ""

    finally:
        try:
            page.remove_listener("response", _on_response)
        except Exception:
            pass


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


# ── Direct SAM workspace/opportunity attachment download ──────────────────────

def collect_sam_direct_attachment_candidates(page):
    """
    Collects attachment candidates directly from a SAM.gov workspace/opportunity page.
    Used when the page shows inline attachments (no PIEE redirect).
    Kinds returned: 'download_all', 'attachment_file'.
    """
    candidates = []
    current_url = page.url

    # Download All button / link (any element whose text contains "download all")
    for loc in [
        page.locator("button").filter(has_text=re.compile(r"download all", re.I)),
        page.locator("a").filter(has_text=re.compile(r"download all", re.I)),
        page.locator("[role='button']").filter(has_text=re.compile(r"download all", re.I)),
    ]:
        try:
            for i in range(min(loc.count(), 3)):
                el = loc.nth(i)
                text = safe_text(el.inner_text(timeout=1000))
                href = normalize_href(el.get_attribute("href") or "", current_url)
                candidates.append({
                    "kind": "download_all",
                    "index": i,
                    "href": href,
                    "text": text or "Download All",
                })
        except Exception:
            continue

    # Individual file links via <a> tags
    anchors = page.locator("a")
    for i in range(anchors.count()):
        try:
            el = anchors.nth(i)
            text = safe_text(el.inner_text(timeout=1000))
            href = normalize_href(el.get_attribute("href") or "", current_url)
            if is_direct_attachment_filename(text, href):
                candidates.append({
                    "kind": "attachment_file",
                    "index": i,
                    "href": href,
                    "text": text,
                })
        except Exception:
            continue

    # Individual file buttons (Angular SPAs sometimes render download triggers as <button>)
    buttons = page.locator("button")
    for i in range(buttons.count()):
        try:
            el = buttons.nth(i)
            text = safe_text(el.inner_text(timeout=1000))
            if is_direct_attachment_filename(text):
                candidates.append({
                    "kind": "attachment_file",
                    "index": i,
                    "href": "",
                    "text": text,
                })
        except Exception:
            continue

    return dedupe_candidates(candidates)


def debug_log_page_attachments(page, notice_id):
    """Log page attachment-detection state. Never logs cookies, tokens, or profile data."""
    try:
        lower = page.content().lower()
        print(f"  URL          : {page.url}")
        try:
            print(f"  Title        : {page.title()}")
        except Exception:
            pass
        print(f"  'attachments': {'yes' if 'attachments' in lower else 'no'}")
        print(f"  'download all': {'yes' if 'download all' in lower else 'no'}")
        print(f"  'request access': {'yes' if 'request access' in lower else 'no'}")
        try:
            a_n = page.locator("a").count()
            b_n = page.locator("button").count()
            print(f"  Anchors: {a_n}  Buttons: {b_n}")
        except Exception:
            pass
        try:
            anchors = page.locator("a")
            sample = []
            for i in range(min(anchors.count(), 40)):
                try:
                    t = safe_text(anchors.nth(i).inner_text(timeout=300))
                    if t:
                        sample.append(t[:80])
                except Exception:
                    continue
            if sample:
                print(f"  Anchor texts : {sample[:20]}")
        except Exception:
            pass
    except Exception as exc:
        print(f"  (debug_log_page_attachments error: {exc})")


def wait_for_attachments_section(page, timeout=30000):
    """Wait for the SAM.gov Attachments section to appear (Angular SPA renders async)."""
    targets = ["Attachments/Links", "Attachments", "Download All", "Request Access"]
    for target in targets:
        try:
            page.get_by_text(target, exact=False).first.wait_for(
                state="visible", timeout=timeout
            )
            print(f"Attachments section visible (found: '{target}').")
            return True
        except Exception:
            continue
    print("Warning: Attachments section not detected within timeout; proceeding.")
    return False


def click_download_all_sam(page, output_folder):
    """Try every reasonable selector for a SAM.gov 'Download All' button.
    Uses click_and_capture_file to handle both download events and document responses."""
    print("Trying SAM 'Download All' button...")
    selectors = [
        page.get_by_role("button", name=re.compile(r"download all", re.I)),
        page.locator("button").filter(has_text=re.compile(r"download all", re.I)),
        page.locator("a").filter(has_text=re.compile(r"download all", re.I)),
        page.locator("[role='button']").filter(has_text=re.compile(r"download all", re.I)),
    ]
    for loc in selectors:
        try:
            el = loc.first
            if el.count() <= 0:
                continue
            saved, mechanism = click_and_capture_file(
                page, el, output_folder,
                preferred_filename="solicitation_attachments.zip",
                timeout=30000,
            )
            if saved:
                print(f"Download All captured ({mechanism}): {Path(saved).name}")
                return [saved]
        except Exception as exc:
            print(f"Download All selector error: {exc}")
            continue
    print("Download All: no file captured.")
    return []


def click_direct_attachment_files(page, candidates, output_folder):
    """Click individual attachment file links on a SAM.gov workspace page.
    Handles both Playwright download events and document responses."""
    downloaded = []
    file_candidates = [c for c in candidates if c.get("kind") == "attachment_file"]
    print(f"Trying {len(file_candidates)} individual attachment link(s)...")
    for item in file_candidates:
        text = safe_text(item.get("text"))
        href  = item.get("href", "")
        print(f"  Candidate: {text[:80]}")

        # Build a locator for this specific element
        locators = [
            page.get_by_text(text, exact=True).first,
            page.locator("a", has_text=text).first,
            page.locator("button", has_text=text).first,
        ]
        saved_path = ""
        for loc in locators:
            try:
                if loc.count() <= 0:
                    continue
                saved, mechanism = click_and_capture_file(
                    page, loc, output_folder,
                    preferred_filename=text,
                    timeout=25000,
                )
                if saved:
                    downloaded.append(saved)
                    print(f"    Saved ({mechanism}): {Path(saved).name}")
                    saved_path = saved
                    break
            except Exception as exc:
                print(f"    Locator attempt error: {exc}")
                continue

        # Final fallback: direct HTTP request via authenticated context
        if not saved_path and href and not href.startswith("javascript:"):
            saved = request_direct_file(
                context=page.context,
                url=href,
                output_folder=output_folder,
                text=text,
            )
            if saved:
                downloaded.append(saved)
    return downloaded


def download_for_notice(
    auth_state,
    notice_id,
    url,
    downloads_dir,
    headless=True,
    debug=True,
    profile_dir="",
    skip_session_check=False,
    _meta=None,
):
    """
    Download solicitation documents for a single notice.

    _meta (optional dict) is populated with:
      attachment_ui_found   – True if any Attachments section / Download All was detected
      download_all_found    – True if a "Download All" button was found
      direct_candidates     – count of direct attachment link candidates
    This lets callers distinguish "no link found" from "UI found but download failed".
    """
    if _meta is None:
        _meta = {}
    _meta.update(attachment_ui_found=False, download_all_found=False, direct_candidates=0)

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
            if not skip_session_check and page_looks_logged_out(page):
                raise RuntimeError(
                    "SAM.gov session is not logged in. Open SAM.gov Login in noVNC, "
                    "complete login/MFA, then retry document download. "
                    "(Session checker is advisory — if you can see SAM.gov is logged in, "
                    "use the manual login override.)"
                )

            # Save debug artifacts immediately after page settles (always, not just on failure)
            if debug:
                save_page_debug(page, downloads_dir, notice_id, label="sam")

            print("SAM.gov page loaded. Looking for PIEE/attachment candidates...")

            sam_candidates = collect_sam_candidates(page)

            print("SAM.gov candidates:")
            print_candidates(sam_candidates, max_items=80)

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

            # ── Direct SAM workspace/opportunity attachment strategy ───────────
            # Triggered when no PIEE redirect was found (workspace/contract/opp pages
            # serve attachments directly) or when PIEE produced no files.
            if not downloaded_paths:
                if not piee_links:
                    print("No PIEE link found — trying direct SAM attachment detection...")
                else:
                    print("PIEE produced no files — trying direct SAM attachment strategy...")

                # Step 1: wait for Angular to finish rendering the Attachments section
                wait_for_attachments_section(page, timeout=30000)

                # Step 2: capture rendered DOM (after Angular — the useful debug artifact)
                if debug:
                    save_rendered_debug(page, downloads_dir, notice_id, label="sam_rendered")

                # Step 3: log safe page state for diagnostics
                print("--- Page state for attachment detection ---")
                debug_log_page_attachments(page, notice_id)

                # Step 4: collect candidates from rendered DOM
                direct_candidates = collect_sam_direct_attachment_candidates(page)
                print("Direct SAM attachment candidates:")
                print_candidates(direct_candidates, max_items=40)

                _meta["attachment_ui_found"]  = len(direct_candidates) > 0
                _meta["download_all_found"]   = any(c.get("kind") == "download_all" for c in direct_candidates)
                _meta["direct_candidates"]    = len(direct_candidates)

                if direct_candidates:
                    # Strategy A — Download All button (handles download event + doc response)
                    if _meta["download_all_found"]:
                        downloaded_paths.extend(
                            click_download_all_sam(page, notice_folder)
                        )

                    # Strategy B — individual attachment file links
                    if not downloaded_paths:
                        downloaded_paths.extend(
                            click_direct_attachment_files(page, direct_candidates, notice_folder)
                        )

                    # Strategy C — direct HTTP requests for any anchor href
                    if not downloaded_paths:
                        downloaded_paths.extend(
                            request_candidate_files(context, direct_candidates, notice_folder)
                        )

                # Step 7: post-attempt debug (regardless of whether candidates were found)
                if debug:
                    save_rendered_debug(page, downloads_dir, notice_id, label="sam_post_dl")

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
        print(f"  attachment_ui_found={_meta.get('attachment_ui_found')}  "
              f"download_all_found={_meta.get('download_all_found')}  "
              f"direct_candidates={_meta.get('direct_candidates')}")
        print("  Debug HTML/screenshots saved to downloads/_debug/")

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
