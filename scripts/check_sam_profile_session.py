#!/usr/bin/env python3
"""
CLI diagnostic for SAM.gov persistent-profile session verification.

Usage:
    python scripts/check_sam_profile_session.py
    python scripts/check_sam_profile_session.py --profile-dir /path/to/profile
    python scripts/check_sam_profile_session.py --display :99
"""
import argparse
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR / "src"))

os.environ.setdefault("DISPLAY", ":99")


def main():
    parser = argparse.ArgumentParser(description="Check SAM.gov persistent profile session")
    parser.add_argument("--profile-dir", default=None, help="Path to Chromium user-data-dir")
    parser.add_argument("--display", default=None, help="X display (default: :99)")
    args = parser.parse_args()

    display = args.display or os.environ.get("DISPLAY", ":99")
    os.environ["DISPLAY"] = display

    from batch_download_docs import DEFAULT_PROFILE_DIR, check_sam_profile_session

    profile_dir = args.profile_dir or DEFAULT_PROFILE_DIR

    print()
    print("SAM.gov Profile Session Check")
    print("=" * 40)
    print(f"  Profile dir : {profile_dir}")
    print(f"  Display     : {display}")
    print(f"  Check URL   : https://sam.gov/workspace")
    print()
    print("Running check (headless Chromium)…")
    print()

    result = check_sam_profile_session(profile_dir=profile_dir, display=display)

    status_sym = "OK" if result["ok"] else "FAIL"
    print(f"  Result      : [{status_sym}] {result.get('code', '?')}")
    print(f"  Message     : {result.get('message', '')}")
    if result.get("debug_html"):
        print(f"  Debug HTML  : {result['debug_html']}")
    if result.get("debug_png"):
        print(f"  Debug PNG   : {result['debug_png']}")
    print()

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
