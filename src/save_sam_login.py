import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


DEFAULT_AUTH_STATE = "auth.json"
DEFAULT_URL = "https://sam.gov"


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


def save_sam_login(auth_state, url, headed=True):
    print("")
    print("SAM.gov Login Saver")
    print("")
    print(f"Target URL: {url}")
    print(f"Auth state output: {auth_state}")
    print("")
    print("A browser will open inside your Codespaces/noVNC desktop.")
    print("Log into SAM.gov manually, complete MFA, and confirm you are fully logged in.")
    print("")
    print("Do NOT press CTRL+C.")
    print("When login is complete, return to this terminal and press ENTER.")
    print("")

    if headed and not os.environ.get("DISPLAY"):
        print("ERROR: DISPLAY is not set.")
        print("")
        print("Start your noVNC/Xvfb session first:")
        print("")
        print("  Xvfb :99 -screen 0 1280x900x24 >/tmp/xvfb.log 2>&1 &")
        print("  export DISPLAY=:99")
        print("  fluxbox >/tmp/fluxbox.log 2>&1 &")
        print("  x11vnc -display :99 -nopw -listen localhost -xkb -forever >/tmp/x11vnc.log 2>&1 &")
        print("  websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/novnc.log 2>&1 &")
        print("")
        sys.exit(1)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not headed,
        )

        context = browser.new_context(
            viewport={"width": 1440, "height": 1000},
            accept_downloads=True,
        )

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3000)

        input("Press ENTER only after SAM.gov is fully logged in... ")

        page.goto("https://sam.gov", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)

        screenshot_path = "sam-login-save-check.png"
        page.screenshot(path=screenshot_path, full_page=True)

        if page_looks_logged_out(page):
            print("")
            print("Login save failed.")
            print("SAM.gov still appears logged out or anonymous.")
            print(f"Screenshot saved: {screenshot_path}")
            print("")
            print("Check the noVNC browser and make sure you are actually signed into SAM.gov, not just Login.gov.")
            print("auth.json was NOT saved.")
            print("")
            context.close()
            browser.close()
            sys.exit(1)

        context.storage_state(path=auth_state)

        context.close()
        browser.close()

    print("")
    print("SAM.gov login saved successfully.")
    print(f"Saved: {auth_state}")
    print(f"Verification screenshot: {screenshot_path}")
    print("")
    print("Now test it:")
    print("")
    print("  python src/sam_browser_downloader.py --test-auth")
    print("")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Save a logged-in SAM.gov Playwright auth.json session without using CTRL+C."
    )

    parser.add_argument(
        "--auth-state",
        default=DEFAULT_AUTH_STATE,
        help="Output auth state file.",
    )

    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="URL to open for login.",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless. Not recommended for manual login.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    save_sam_login(
        auth_state=args.auth_state,
        url=args.url,
        headed=not args.headless,
    )


if __name__ == "__main__":
    main()