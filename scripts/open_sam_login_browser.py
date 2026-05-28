#!/usr/bin/env python3
"""
Launch a visible Chromium window for SAM.gov login using the persistent profile.

Usage:
    python scripts/open_sam_login_browser.py [profile_dir]

The browser stays open until the operator closes it manually.
Window is sized and positioned to fit inside the noVNC viewport (1280x900).
Does not print cookies, session data, or profile contents.
"""

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = ROOT / ".browser" / "sam-profile"
profile_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PROFILE
profile_dir.mkdir(parents=True, exist_ok=True)

DISPLAY = os.environ.get("DISPLAY", ":99")
os.environ["DISPLAY"] = DISPLAY

# Check for profile lock before launching
lock_file = profile_dir / "SingletonLock"
if lock_file.exists():
    print(
        f"SAM browser profile is already open (SingletonLock exists).\n"
        f"Close the Chromium/SAM.gov window inside noVNC first,\n"
        f"or click 'Close SAM Login Browser / Release Profile' in Streamlit.\n"
        f"Profile: {profile_dir}"
    )
    sys.exit(1)

try:
    from playwright.sync_api import sync_playwright, Error as PlaywrightError
except ImportError:
    print("playwright is not installed. Run: pip install playwright && playwright install chromium")
    sys.exit(1)

print(f"Opening SAM.gov login browser. DISPLAY={DISPLAY} PROFILE={profile_dir}")

try:
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=False,
            args=[
                "--window-size=1150,760",
                "--window-position=40,40",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-features=Translate",
                "--disable-extensions",
            ],
            viewport={"width": 1150, "height": 700},
            no_viewport=False,
        )

        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://sam.gov", wait_until="domcontentloaded", timeout=30000)
        print("Browser open. Complete SAM.gov/Login.gov login, then close this window.")

        # Block until the operator closes the browser
        ctx.wait_for_event("close")
        print("Browser closed by operator. Profile released.")

except PlaywrightError as exc:
    msg = str(exc)
    if "SingletonLock" in msg or "user data directory is already in use" in msg.lower():
        print(
            "SAM browser profile is already in use by another Chromium process.\n"
            "Close the existing SAM.gov window inside noVNC,\n"
            "or click 'Close SAM Login Browser / Release Profile' in Streamlit."
        )
        sys.exit(1)
    print(f"Playwright error: {msg}")
    sys.exit(1)
except KeyboardInterrupt:
    print("Interrupted.")
    sys.exit(0)
