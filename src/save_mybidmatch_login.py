"""
Save an OutreachSystems / MyBidMatch session to a Playwright storage-state file.

The MyBidMatch directory page requires an active OutreachSystems session cookie.
Run this script once (in headed mode via noVNC), log in manually, then press
ENTER — the session is saved and reused by mybidmatch_browser_intake.py.

Usage:
  python src/save_mybidmatch_login.py
  python src/save_mybidmatch_login.py --auth-state mybidmatch_auth.json
  python src/save_mybidmatch_login.py --url "https://mybidmatch.outreachsystems.com/..."

After saving:
  python src/mybidmatch_browser_intake.py --storage-state mybidmatch_auth.json
"""

import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


DEFAULT_AUTH_STATE    = "mybidmatch_auth.json"
DEFAULT_LOGIN_URL     = "https://mybidmatch.outreachsystems.com"
MYBIDMATCH_MARKERS    = ["mybidmatch", "outreachsystems", "search profile", "articles"]


def page_looks_authenticated(page):
    html = page.content().lower()
    return any(m in html for m in MYBIDMATCH_MARKERS) and "403" not in html


def save_mybidmatch_login(auth_state, url, headed=True):
    print("")
    print("MyBidMatch / OutreachSystems Session Saver")
    print("")
    print(f"Target URL:   {url}")
    print(f"Output file:  {auth_state}")
    print("")

    if headed and not os.environ.get("DISPLAY"):
        print("ERROR: DISPLAY is not set.")
        print("")
        print("Start your noVNC/Xvfb session first, then re-run:")
        print("  source scripts/novnc_reset.sh")
        print("  python src/save_mybidmatch_login.py")
        print("")
        sys.exit(1)

    print("A browser window will open.")
    print("Log in to OutreachSystems / MyBidMatch manually.")
    print("Navigate to the MyBidMatch directory page so you can see the date list.")
    print("")
    print("When fully logged in, return here and press ENTER.")
    print("")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = context.new_page()
        page.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(2000)
        except Exception as exc:
            print(f"[warn] Page load issue (may be normal): {exc}")

        input("Press ENTER after you are fully logged in to MyBidMatch... ")

        screenshot_path = "mybidmatch-session-check.png"
        page.screenshot(path=screenshot_path, full_page=True)
        print(f"Screenshot saved: {screenshot_path}")

        if not page_looks_authenticated(page):
            print("")
            print("Session save failed — the page does not appear to be authenticated.")
            print("The browser may still be on the login page or returned 403.")
            print(f"Check screenshot: {screenshot_path}")
            print("Session NOT saved.")
            print("")
            context.close()
            browser.close()
            sys.exit(1)

        context.storage_state(path=auth_state)
        context.close()
        browser.close()

    print("")
    print("MyBidMatch session saved successfully.")
    print(f"Session file: {auth_state}")
    print("")
    print("Run intake with:")
    print(f"  python src/mybidmatch_browser_intake.py --storage-state {auth_state}")
    print("")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Save a logged-in OutreachSystems/MyBidMatch Playwright session."
    )
    parser.add_argument(
        "--auth-state", default=DEFAULT_AUTH_STATE,
        help="Output storage state file (default: mybidmatch_auth.json)",
    )
    parser.add_argument(
        "--url", default=DEFAULT_LOGIN_URL,
        help="URL to open for login",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="Run headless (not recommended for manual login)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    save_mybidmatch_login(
        auth_state=args.auth_state,
        url=args.url,
        headed=not args.headless,
    )


if __name__ == "__main__":
    main()
