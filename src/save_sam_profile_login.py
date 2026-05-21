import argparse
import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


DEFAULT_PROFILE_DIR = ".browser/sam_profile"
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


def save_profile_login(profile_dir, url, headed=True):
    print("")
    print("SAM.gov Persistent Profile Login")
    print("")
    print(f"Profile directory: {profile_dir}")
    print(f"Login URL: {url}")
    print("")
    print("A browser will open inside noVNC.")
    print("Log into SAM.gov manually and make sure SAM.gov itself shows you are signed in.")
    print("")
    print("Do NOT use CTRL+C.")
    print("After SAM.gov shows you are signed in, return to this terminal and press ENTER.")
    print("")

    if headed and not os.environ.get("DISPLAY"):
        print("ERROR: DISPLAY is not set.")
        print("")
        print("Run:")
        print("")
        print("  export DISPLAY=:99")
        print("")
        sys.exit(1)

    Path(profile_dir).mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=not headed,
            viewport={"width": 1440, "height": 1000},
            accept_downloads=True,
        )

        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3000)

        input("Press ENTER only after SAM.gov is fully logged in... ")

        page.goto("https://sam.gov", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)

        screenshot_path = "sam-profile-login-check.png"
        page.screenshot(path=screenshot_path, full_page=True)

        if page_looks_logged_out(page):
            print("")
            print("Profile login check failed.")
            print("SAM.gov still appears logged out or anonymous.")
            print(f"Screenshot saved: {screenshot_path}")
            print("")
            print("Do not proceed yet. In the noVNC browser, make sure SAM.gov shows your account/workspace and no Sign In button.")
            print("")
            context.close()
            sys.exit(1)

        context.close()

    print("")
    print("SAM.gov persistent browser profile saved successfully.")
    print(f"Profile directory: {profile_dir}")
    print(f"Verification screenshot: {screenshot_path}")
    print("")
    print("Next test command:")
    print("")
    print("  python src/save_sam_profile_login.py --test")
    print("")


def test_profile(profile_dir, headed=False):
    print("")
    print("Testing SAM.gov persistent profile...")
    print(f"Profile directory: {profile_dir}")
    print("")

    if not Path(profile_dir).exists():
        print(f"Missing profile directory: {profile_dir}")
        sys.exit(1)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=not headed,
            viewport={"width": 1440, "height": 1000},
            accept_downloads=True,
        )

        page = context.new_page()
        page.goto("https://sam.gov", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)

        screenshot_path = "sam-profile-test.png"
        page.screenshot(path=screenshot_path, full_page=True)

        logged_out = page_looks_logged_out(page)

        context.close()

    if logged_out:
        print("")
        print("Persistent profile test failed.")
        print("SAM.gov still appears logged out.")
        print(f"Screenshot saved: {screenshot_path}")
        print("")
        sys.exit(1)

    print("")
    print("Persistent profile test passed.")
    print("SAM.gov appears logged in.")
    print(f"Screenshot saved: {screenshot_path}")
    print("")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Save/test a persistent SAM.gov browser profile for Codespaces/noVNC."
    )

    parser.add_argument(
        "--profile-dir",
        default=DEFAULT_PROFILE_DIR,
        help="Persistent Chromium profile directory.",
    )

    parser.add_argument(
        "--url",
        default=DEFAULT_URL,
        help="URL to open for login.",
    )

    parser.add_argument(
        "--test",
        action="store_true",
        help="Test existing persistent profile headlessly.",
    )

    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run test headed inside noVNC.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    if args.test:
        test_profile(
            profile_dir=args.profile_dir,
            headed=args.headed,
        )
        return

    save_profile_login(
        profile_dir=args.profile_dir,
        url=args.url,
        headed=True,
    )


if __name__ == "__main__":
    main()