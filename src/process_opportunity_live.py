import argparse
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_DOWNLOADS_DIR = "downloads"
DEFAULT_PROFILE_DIR = ".browser/live_sam_profile"
NOVNC_CHECK_SCRIPT = "scripts/novnc_check.sh"
LIVE_INFRASTRUCTURE_EXIT_CODE = 86

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

REFERENCE_TEXT_EXCLUDES = [
    "web based training",
    "training",
    "registration instructions",
    "vendor access instructions",
    "procurement toolbox",
    "user guide",
    "help",
    "documentation",
]

REFERENCE_URL_EXCLUDES = [
    "pieetraining.eb.mil",
    "acq.osd.mil",
    "public.cyber.mil",
]


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def make_safe_folder_name(value):
    text = safe_text(value) or "unknown"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text.strip("_")[:120] or "unknown"


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


def page_looks_logged_out(page):
    html = page.content().lower()

    logged_out_markers = [
        "role-anonymous",
        'id="signin"',
        "signin-trigger-btn",
        "sign in",
        '"uid":0',
        "sign-in-button-current",
    ]

    return any(marker in html for marker in logged_out_markers)


def print_novnc_help():
    print("")
    print("Make sure noVNC is open on forwarded port 6080.")
    print("")
    print("If needed, start it with:")
    print("")
    print("  Xvfb :99 -screen 0 1280x900x24 >/tmp/xvfb.log 2>&1 &")
    print("  export DISPLAY=:99")
    print("  fluxbox >/tmp/fluxbox.log 2>&1 &")
    print("  x11vnc -display :99 -nopw -listen localhost -xkb -forever >/tmp/x11vnc.log 2>&1 &")
    print("  websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/novnc.log 2>&1 &")
    print("")
    print("Or run:")
    print("")
    print("  scripts/novnc_reset.sh")
    print("")


def live_environment_ready():
    if not os.environ.get("DISPLAY"):
        print("DISPLAY is not set.")
        print_novnc_help()
        return False

    check_script = Path(NOVNC_CHECK_SCRIPT)

    if not check_script.exists():
        print(f"Missing noVNC check script: {NOVNC_CHECK_SCRIPT}")
        return False

    result = subprocess.run(["bash", str(check_script)])
    return result.returncode == 0


def save_page_debug(page, downloads_dir, notice_id, label):
    debug_folder = Path(downloads_dir) / "_debug"
    debug_folder.mkdir(parents=True, exist_ok=True)

    html_path = debug_folder / f"{notice_id}_{label}.html"
    screenshot_path = debug_folder / f"{notice_id}_{label}.png"

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


def get_extension(value):
    text = safe_text(value).lower()
    parsed = urlparse(text).path.lower()

    for ext in BID_FILE_EXTENSIONS:
        if text.endswith(ext) or parsed.endswith(ext):
            return ext

    return ""


def has_bid_file_extension(value):
    return get_extension(value) in BID_FILE_EXTENSIONS


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

    if any(item in lower_text for item in REFERENCE_TEXT_EXCLUDES):
        return True

    if any(item in lower_url for item in REFERENCE_URL_EXCLUDES):
        return True

    return False


def looks_like_piee_link(text, url):
    combined = f"{safe_text(text)} {safe_text(url)}".lower()
    return "piee solicitation module" in combined or "piee.eb.mil" in combined


def normalize_href(href, page_url):
    href = safe_text(href)

    if not href:
        return ""

    if href.startswith("javascript:"):
        return href

    return urljoin(page_url, href)


def collect_sam_candidates(page):
    candidates = []
    anchors = page.locator("a")
    count = anchors.count()

    for index in range(count):
        try:
            anchor = anchors.nth(index)
            text = safe_text(anchor.inner_text(timeout=1000))
            href = normalize_href(anchor.get_attribute("href"), page.url)

            if not href:
                continue

            if is_reference_link(text, href):
                continue

            if looks_like_piee_link(text, href):
                candidates.append({
                    "kind": "piee",
                    "text": text,
                    "href": href,
                })

        except Exception:
            continue

    return dedupe_candidates(candidates)


def collect_piee_attachment_candidates(page):
    candidates = []
    anchors = page.locator("a")
    count = anchors.count()

    for index in range(count):
        try:
            anchor = anchors.nth(index)
            text = safe_text(anchor.inner_text(timeout=1000))
            href = normalize_href(anchor.get_attribute("href"), page.url)

            if not text:
                continue

            if is_reference_link(text, href):
                continue

            if looks_like_download_all(text):
                candidates.append({
                    "kind": "download_all",
                    "text": text,
                    "href": href,
                })
                continue

            if looks_like_bid_attachment_filename(text):
                candidates.append({
                    "kind": "attachment_file",
                    "text": text,
                    "href": href,
                })

        except Exception:
            continue

    return dedupe_candidates(candidates)


def dedupe_candidates(candidates):
    seen = set()
    unique = []

    for item in candidates:
        key = (item.get("kind"), item.get("text"), item.get("href"))
        if key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return unique


def print_candidates(title, candidates):
    print("")
    print(title)
    print(f"Candidate count: {len(candidates)}")

    for item in candidates[:80]:
        print(f"- [{item.get('kind')}] {item.get('text')[:120]} | {item.get('href')}")

    print("")


def wait_for_page(page):
    page.wait_for_timeout(5000)


def click_exact_text_for_download(page, text, output_folder, timeout=15000):
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
            filename = clean_filename(download.suggested_filename or text)

            if is_bad_download_name(filename):
                print(f"Rejected non-solicitation/support download: {filename}")
                return ""

            target_path = unique_path(output_folder, filename)
            download.save_as(str(target_path))
            print(f"Downloaded: {target_path}")
            return str(target_path)

        except PlaywrightTimeoutError:
            continue
        except Exception:
            continue

    return ""


def download_from_piee(page, notice_id, downloads_dir):
    output_folder = Path(downloads_dir) / notice_id
    ensure_dir(output_folder)

    save_page_debug(page, downloads_dir, notice_id, "piee")

    candidates = collect_piee_attachment_candidates(page)
    print_candidates("PIEE attachment candidates", candidates)

    downloaded = []

    if any(item["kind"] == "download_all" for item in candidates):
        print("Trying Download All Attachments...")
        saved = click_exact_text_for_download(page, "Download All Attachments", output_folder)
        if saved:
            downloaded.append(saved)

    if not downloaded:
        for item in candidates:
            if item["kind"] != "attachment_file":
                continue

            print(f"Trying attachment filename: {item['text']}")
            saved = click_exact_text_for_download(page, item["text"], output_folder)
            if saved:
                downloaded.append(saved)

    return downloaded


def unzip_downloads(notice_id, downloads_dir):
    folder = Path(downloads_dir) / notice_id

    if not folder.exists():
        return

    for zip_path in sorted(folder.glob("*.zip")):
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                print("")
                print(f"ZIP contents for {zip_path}:")
                for name in archive.namelist():
                    print(f"- {name}")
                archive.extractall(folder)
                print(f"Extracted ZIP into: {folder}")
        except zipfile.BadZipFile:
            print(f"Bad ZIP skipped: {zip_path}")


def run_command(command):
    print("")
    print("Running:")
    print(" ".join(command))
    print("")

    result = subprocess.run(command)

    if result.returncode != 0:
        print(f"Command failed: {' '.join(command)}")
        sys.exit(result.returncode)


def run_analysis_pipeline(notice_id, downloads_dir):
    run_command([
        sys.executable,
        "src/main.py",
        "--offline",
        "--scan-local-attachments",
        "--downloads-dir",
        downloads_dir,
    ])

    run_command([
        sys.executable,
        "src/local_document_extractor.py",
        "--notice-id",
        notice_id,
        "--downloads-dir",
        downloads_dir,
    ])

    run_command([
        sys.executable,
        "src/bid_no_bid_analyzer.py",
        "--notice-id",
        notice_id,
    ])

    run_command([
        sys.executable,
        "src/solicitation_parser.py",
        "--notice-id",
        notice_id,
    ])


def process_live(notice_id, url, downloads_dir, profile_dir, skip_analysis):
    if not live_environment_ready():
        sys.exit(LIVE_INFRASTRUCTURE_EXIT_CODE)

    notice_id = make_safe_folder_name(notice_id)
    ensure_dir(Path(downloads_dir) / notice_id)
    ensure_dir(profile_dir)

    print("")
    print("GovCon Scout Live SAM.gov Processor")
    print("")
    print(f"Notice ID: {notice_id}")
    print(f"URL: {url}")
    print(f"Downloads: {downloads_dir}")
    print(f"Profile: {profile_dir}")
    print("")
    print("A browser will open in noVNC.")
    print("Log into SAM.gov manually if needed.")
    print("Navigate/confirm you are signed in, then return here and press ENTER.")
    print("Do NOT press CTRL+C.")
    print("")

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=False,
            viewport={"width": 1440, "height": 1000},
            accept_downloads=True,
        )

        page = context.new_page()
        page.goto("https://sam.gov", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3000)

        input("Press ENTER only after SAM.gov shows you are fully signed in... ")

                # Do not force the browser back to SAM.gov home after manual login.
        # SAM.gov can render the public home page as anonymous even when the user
        # just completed Login.gov/SAM.gov auth in another route. The real test is
        # whether the opportunity page and attachment/PIEE links are accessible.
        print("Continuing to the opportunity page...")

        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(7000)

        if page_looks_logged_out(page):
            print("")
            print("Warning: this page still contains logged-out markers.")
            print("Continuing anyway because SAM.gov may expose public opportunity links.")
            print("If download fails, use the debug screenshot to verify login/page state.")
            print("")
            save_page_debug(page,
                            


 downloads_dir, notice_id, "possible_logged_out")
        wait_for_page(page)
        save_page_debug(page, downloads_dir, notice_id, "sam")

        sam_candidates = collect_sam_candidates(page)
        print_candidates("SAM.gov candidates", sam_candidates)

        downloaded = []

        piee_links = [item for item in sam_candidates if item["kind"] == "piee"]

        if not piee_links:
            print("No PIEE links detected on this SAM page.")
        else:
            for item in piee_links:
                print(f"Opening PIEE: {item['href']}")
                page.goto(item["href"], wait_until="domcontentloaded", timeout=90000)
                wait_for_page(page)
                downloaded.extend(download_from_piee(page, notice_id, downloads_dir))

        context.close()

    if not downloaded:
        print("")
        print("No files downloaded.")
        print(f"Check debug files in: {downloads_dir}/_debug")
        print("")
        sys.exit(1)

    unzip_downloads(notice_id, downloads_dir)

    if not skip_analysis:
        run_analysis_pipeline(notice_id, downloads_dir)

    print("")
    print("Live opportunity processing complete.")
    print("")
    print(f"- Downloads: {Path(downloads_dir) / notice_id}")
    print(f"- Bid/no-bid: reports/opportunity_reviews/{notice_id}_bid_no_bid.md")
    print(f"- Decision report: reports/opportunity_reviews/{notice_id}_decision_report.md")
    print(f"- Compliance matrix: reports/opportunity_reviews/{notice_id}_compliance_matrix.md")
    print("")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Live interactive SAM.gov processor using the current noVNC browser session."
    )

    parser.add_argument("--notice-id", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--downloads-dir", default=DEFAULT_DOWNLOADS_DIR)
    parser.add_argument("--profile-dir", default=DEFAULT_PROFILE_DIR)
    parser.add_argument("--skip-analysis", action="store_true")

    return parser.parse_args()


def main():
    args = parse_args()

    process_live(
        notice_id=args.notice_id,
        url=args.url,
        downloads_dir=args.downloads_dir,
        profile_dir=args.profile_dir,
        skip_analysis=args.skip_analysis,
    )


if __name__ == "__main__":
    main()
