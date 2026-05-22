"""
MyBidMatch Session Setup — Gmail-assisted login flow.

Opens Gmail in a headed Chromium browser (via noVNC), pre-fills the email
address, and waits for you to log in and click the MyBidMatch directory link
from your inbox. Once the MyBidMatch directory page is detected, the session
is saved automatically to mybidmatch_auth.json.

Optionally runs the intake immediately after saving.

Usage:
  python src/mybidmatch_session_setup.py
  python src/mybidmatch_session_setup.py --email travisrobinsonlive@gmail.com
  python src/mybidmatch_session_setup.py --run-intake
  python src/mybidmatch_session_setup.py --run-intake --limit-days 1 --limit-articles 10

Prerequisites:
  source scripts/novnc_reset.sh
  export DISPLAY=:99
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright


DEFAULT_EMAIL        = "travisrobinsonlive@gmail.com"
DEFAULT_AUTH_STATE   = "mybidmatch_auth.json"
DEFAULT_GMAIL_URL    = "https://accounts.google.com/v3/signin/identifier?email={email}&flowName=GlifWebSignIn&flowEntry=ServiceLogin"
DEFAULT_DIRECTORY_URL = (
    "https://mybidmatch.outreachsystems.com/go?sub=0FCE00BD-0DBB-4438-A624-DE3BE05AC6D1"
)

MYBIDMATCH_MARKERS   = ["mybidmatch", "outreachsystems", "search profile", "articles"]
MYBIDMATCH_URL_HINTS = ["mybidmatch.outreachsystems.com", "outreachsystems.com/go"]
POLL_INTERVAL_MS     = 1500
MAX_WAIT_SECONDS     = 300  # 5 minutes max before prompting


def check_display():
    if not os.environ.get("DISPLAY"):
        print("")
        print("ERROR: DISPLAY is not set.")
        print("")
        print("Start the noVNC desktop first:")
        print("  source scripts/novnc_reset.sh")
        print("  export DISPLAY=:99")
        print("")
        print("Then open the noVNC viewer at:  http://localhost:6080/vnc.html")
        print("")
        sys.exit(1)


def is_mybidmatch_page(page):
    try:
        url = page.url.lower()
        if any(hint in url for hint in MYBIDMATCH_URL_HINTS):
            html = page.content().lower()
            if any(m in html for m in MYBIDMATCH_MARKERS) and "403" not in html:
                return True
    except Exception:
        pass
    return False


def wait_for_mybidmatch(context, max_seconds=MAX_WAIT_SECONDS):
    """Poll all open pages until one shows the MyBidMatch directory."""
    elapsed = 0
    while elapsed < max_seconds:
        try:
            for pg in context.pages:
                if is_mybidmatch_page(pg):
                    return pg
        except Exception:
            pass
        time.sleep(POLL_INTERVAL_MS / 1000)
        elapsed += POLL_INTERVAL_MS / 1000
        if elapsed % 30 < (POLL_INTERVAL_MS / 1000):
            remaining = max_seconds - elapsed
            print(f"  Waiting for MyBidMatch directory... ({int(remaining)}s remaining)")
    return None


def run_intake(auth_state, limit_days, limit_articles, debug):
    cmd = [
        sys.executable, "src/mybidmatch_browser_intake.py",
        "--storage-state", auth_state,
        "--limit-days", str(limit_days),
    ]
    if limit_articles > 0:
        cmd += ["--limit-articles", str(limit_articles)]
    if debug:
        cmd.append("--debug")

    print("")
    print("Running intake:")
    print("  " + " ".join(cmd))
    print("")
    result = subprocess.run(cmd)
    return result.returncode


def setup_session(
    email,
    auth_state,
    directory_url,
    run_intake_after,
    limit_days,
    limit_articles,
    debug,
):
    check_display()

    print("")
    print("=" * 58)
    print("  MyBidMatch Session Setup")
    print("=" * 58)
    print(f"  Gmail account : {email}")
    print(f"  Session file  : {auth_state}")
    print(f"  Directory URL : {directory_url}")
    print("=" * 58)
    print("")
    print("A Chromium browser will open.")
    print("")
    print("Steps:")
    print("  1. Gmail will load with your email pre-filled.")
    print("     Enter your password and complete sign-in.")
    print("")
    print("  2. Find the MyBidMatch / OutreachSystems email in your inbox.")
    print("     (Search for: from:mybidmatch OR outreachsystems)")
    print("")
    print("  3. Click the link in the email to open your MyBidMatch directory.")
    print("     You should see a list of dates with article counts.")
    print("")
    print("  The session will be saved automatically once the directory loads.")
    print("  If auto-detection times out, you will be prompted to press ENTER.")
    print("")
    input("Press ENTER to open the browser... ")
    print("")

    gmail_url = DEFAULT_GMAIL_URL.format(email=email)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=False,
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

        print(f"Opening Gmail for: {email}")
        try:
            page.goto(gmail_url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
        except Exception as exc:
            print(f"[warn] Gmail load issue (may be normal): {exc}")

        print("")
        print("Browser is open. Follow the steps above.")
        print("Auto-detecting when the MyBidMatch directory is loaded...")
        print("")

        mybidmatch_page = wait_for_mybidmatch(context)

        if mybidmatch_page:
            print("  [OK] MyBidMatch directory detected automatically.")
            mybidmatch_page.wait_for_timeout(1000)
        else:
            print("")
            print("  [!] Auto-detection timed out.")
            print("      Make sure you can see your MyBidMatch date list in the browser.")
            input("  Press ENTER once you are on the MyBidMatch directory page... ")
            # Re-check after manual prompt
            for pg in context.pages:
                if is_mybidmatch_page(pg):
                    mybidmatch_page = pg
                    break

        screenshot_path = "mybidmatch-session-check.png"
        target_page = mybidmatch_page or page
        try:
            target_page.screenshot(path=screenshot_path, full_page=True)
            print(f"  Screenshot: {screenshot_path}")
        except Exception:
            pass

        # Validate before saving
        if mybidmatch_page and is_mybidmatch_page(mybidmatch_page):
            context.storage_state(path=auth_state)
            print(f"  Session saved: {auth_state}")
            context.close()
            browser.close()
        else:
            print("")
            print("  [FAIL] Could not confirm a valid MyBidMatch session.")
            print(f"  Check screenshot: {screenshot_path}")
            print("  Session NOT saved.")
            print("")
            print("  Tip: Make sure you can see the date list with article counts,")
            print("  not just the Gmail inbox or a 403 page.")
            context.close()
            browser.close()
            sys.exit(1)

    print("")
    print("=" * 58)
    print("  Session saved successfully.")
    print(f"  File: {auth_state}")
    print("=" * 58)
    print("")

    if run_intake_after:
        code = run_intake(auth_state, limit_days, limit_articles, debug)
        sys.exit(code)
    else:
        print("Next — run the intake:")
        print(f"  python src/mybidmatch_browser_intake.py \\")
        print(f"    --storage-state {auth_state} \\")
        print(f"    --limit-days 1")
        print("")


def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "Gmail-assisted MyBidMatch session setup. "
            "Opens Gmail, lets you log in and click the MyBidMatch link, "
            "then saves the OutreachSystems session automatically."
        )
    )
    p.add_argument(
        "--email", default=DEFAULT_EMAIL,
        help=f"Gmail address (default: {DEFAULT_EMAIL})",
    )
    p.add_argument(
        "--auth-state", default=DEFAULT_AUTH_STATE,
        help="Output session file (default: mybidmatch_auth.json)",
    )
    p.add_argument(
        "--directory-url", default=DEFAULT_DIRECTORY_URL,
        help="MyBidMatch directory URL",
    )
    p.add_argument(
        "--run-intake", action="store_true",
        help="Run the intake immediately after saving the session",
    )
    p.add_argument(
        "--limit-days", type=int, default=1,
        help="Days to pull when --run-intake is set (default 1)",
    )
    p.add_argument(
        "--limit-articles", type=int, default=0,
        help="Articles per day when --run-intake is set (0 = no limit)",
    )
    p.add_argument(
        "--debug", action="store_true",
        help="Pass --debug to intake run",
    )
    return p.parse_args()


def main():
    args = parse_args()
    setup_session(
        email=args.email,
        auth_state=args.auth_state,
        directory_url=args.directory_url,
        run_intake_after=args.run_intake,
        limit_days=args.limit_days,
        limit_articles=args.limit_articles,
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
