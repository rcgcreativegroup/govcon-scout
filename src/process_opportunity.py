import argparse
import subprocess
import sys
import zipfile
from pathlib import Path


DEFAULT_DOWNLOADS_DIR = "downloads"
DEFAULT_AUTH_STATE = "auth.json"


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def run_command(command, stop_on_error=True):
    print("")
    print("Running:")
    print(" ".join(command))
    print("")

    result = subprocess.run(command)

    if result.returncode != 0:
        print("")
        print(f"Command failed with exit code {result.returncode}:")
        print(" ".join(command))
        print("")

        if stop_on_error:
            sys.exit(result.returncode)

    return result.returncode


def ensure_auth_state(auth_state):
    path = Path(auth_state)

    if path.exists():
        return True

    print_login_refresh_instructions(auth_state)
    return False


def print_login_refresh_instructions(auth_state="auth.json"):
    print("")
    print("SAM.gov login session is missing or expired.")
    print("")
    print(f"Expected auth file: {auth_state}")
    print("")
    print("Refresh your SAM.gov login in Codespaces using noVNC:")
    print("")
    print("1. Start the virtual display services if they are not already running:")
    print("")
    print("   Xvfb :99 -screen 0 1280x900x24 >/tmp/xvfb.log 2>&1 &")
    print("   export DISPLAY=:99")
    print("   fluxbox >/tmp/fluxbox.log 2>&1 &")
    print("   x11vnc -display :99 -nopw -listen localhost -xkb -forever >/tmp/x11vnc.log 2>&1 &")
    print("   websockify --web=/usr/share/novnc/ 6080 localhost:5900 >/tmp/novnc.log 2>&1 &")
    print("")
    print("2. Open the forwarded Codespaces port:")
    print("")
    print("   6080")
    print("")
    print("3. In noVNC:")
    print("")
    print("   Click vnc.html")
    print("   Click Connect")
    print("")
    print("4. In the Codespaces terminal, run:")
    print("")
    print(f"   DISPLAY=:99 npx playwright codegen --save-storage={auth_state} https://sam.gov")
    print("")
    print("5. Log into SAM.gov manually, complete MFA, then stop codegen with:")
    print("")
    print("   CTRL + C")
    print("")
    print("6. Confirm the file exists:")
    print("")
    print(f"   ls -la {auth_state}")
    print("")
    print("Then rerun this process command.")
    print("")
    print("Security reminder: do not commit auth.json.")
    print("")


def test_auth_session(auth_state):
    if not ensure_auth_state(auth_state):
        return False

    print("")
    print("Checking SAM.gov login session before download...")
    print("")

    result = subprocess.run([
        sys.executable,
        "src/sam_browser_downloader.py",
        "--test-auth",
        "--auth-state",
        auth_state,
        "--no-debug",
    ])

    if result.returncode != 0:
        print_login_refresh_instructions(auth_state)
        return False

    screenshot_path = Path("sam-session-test.png")

    if not screenshot_path.exists():
        print("")
        print("Auth test ran, but no screenshot was created.")
        print("This may mean the SAM.gov session test failed.")
        print_login_refresh_instructions(auth_state)
        return False

    print("")
    print("Auth test completed.")
    print("A screenshot was created at sam-session-test.png.")
    print("If downloads fail, open that screenshot and confirm SAM.gov still appears logged in.")
    print("")

    return True


def unzip_downloads_for_notice(notice_id, downloads_dir):
    folder = Path(downloads_dir) / notice_id

    if not folder.exists():
        print(f"No downloads folder found yet: {folder}")
        return

    zip_files = sorted(folder.glob("*.zip"))

    if not zip_files:
        print(f"No ZIP files found to extract in: {folder}")
        return

    for zip_path in zip_files:
        print("")
        print(f"Inspecting ZIP: {zip_path}")
        print("")

        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                names = archive.namelist()

                print("ZIP contents:")
                for name in names:
                    print(f"- {name}")

                print("")
                print(f"Extracting ZIP into: {folder}")
                archive.extractall(folder)

        except zipfile.BadZipFile:
            print(f"Skipping invalid ZIP file: {zip_path}")


def count_local_files(notice_id, downloads_dir):
    folder = Path(downloads_dir) / notice_id

    if not folder.exists():
        return 0

    return len([path for path in folder.iterdir() if path.is_file()])


def process_opportunity(args):
    notice_id = safe_text(args.notice_id)
    url = safe_text(args.url)

    if not notice_id:
        print("Missing --notice-id")
        sys.exit(1)

    if not url:
        print("Missing --url")
        sys.exit(1)

    print("")
    print("GovCon Scout Opportunity Processor")
    print("")
    print(f"Notice ID: {notice_id}")
    print(f"URL: {url}")
    print(f"Downloads directory: {args.downloads_dir}")
    print(f"Auth state: {args.auth_state}")
    print("")

    if not args.skip_auth_check:
        auth_ok = test_auth_session(args.auth_state)

        if not auth_ok:
            print("")
            print("Stopping before download because SAM.gov login is not ready.")
            print("")
            sys.exit(1)

    if not args.skip_download:
        run_command([
            sys.executable,
            "src/sam_browser_downloader.py",
            "--notice-id",
            notice_id,
            "--url",
            url,
            "--auth-state",
            args.auth_state,
            "--downloads-dir",
            args.downloads_dir,
        ])

    if not args.skip_unzip:
        unzip_downloads_for_notice(
            notice_id=notice_id,
            downloads_dir=args.downloads_dir,
        )

    local_file_count = count_local_files(
        notice_id=notice_id,
        downloads_dir=args.downloads_dir,
    )

    if local_file_count == 0:
        print("")
        print("No local files were found after download/unzip.")
        print("Stopping before extraction and analysis.")
        print("")
        print("Check:")
        print(f"- downloads folder: {Path(args.downloads_dir) / notice_id}")
        print("- downloads/_debug screenshots and HTML")
        print("- whether SAM.gov/PIEE session expired")
        print("")
        sys.exit(1)

    print("")
    print(f"Local file count for {notice_id}: {local_file_count}")
    print("")

    if not args.skip_local_scan:
        run_command([
            sys.executable,
            "src/main.py",
            "--offline",
            "--scan-local-attachments",
            "--downloads-dir",
            args.downloads_dir,
        ])

    if not args.skip_extract:
        run_command([
            sys.executable,
            "src/local_document_extractor.py",
            "--notice-id",
            notice_id,
            "--downloads-dir",
            args.downloads_dir,
        ])

    if not args.skip_decision:
        run_command([
            sys.executable,
            "src/bid_no_bid_analyzer.py",
            "--notice-id",
            notice_id,
        ])

    if not args.skip_compliance:
        run_command([
            sys.executable,
            "src/solicitation_parser.py",
            "--notice-id",
            notice_id,
        ])

    print("")
    print("Opportunity processing complete.")
    print("")
    print("Key outputs:")
    print(f"- Downloads: {Path(args.downloads_dir) / notice_id}")
    print(f"- Analysis packet: reports/analysis_packets/{notice_id}.md")
    print(f"- Document extracts: reports/document_extracts/{notice_id}/")
    print(f"- Bid/no-bid review: reports/opportunity_reviews/{notice_id}_bid_no_bid.md")
    print(f"- Decision report: reports/opportunity_reviews/{notice_id}_decision_report.md")
    print(f"- Compliance matrix: reports/opportunity_reviews/{notice_id}_compliance_matrix.md")
    print("")


def parse_args():
    parser = argparse.ArgumentParser(
        description="One-button GovCon Scout opportunity processor."
    )

    parser.add_argument(
        "--notice-id",
        required=True,
        help="Notice ID to process, e.g. HE125426QE041.",
    )

    parser.add_argument(
        "--url",
        required=True,
        help="SAM.gov opportunity URL.",
    )

    parser.add_argument(
        "--auth-state",
        default=DEFAULT_AUTH_STATE,
        help="Playwright storage state file, usually auth.json.",
    )

    parser.add_argument(
        "--downloads-dir",
        default=DEFAULT_DOWNLOADS_DIR,
        help="Downloads directory.",
    )

    parser.add_argument(
        "--skip-auth-check",
        action="store_true",
        help="Skip SAM.gov auth session check.",
    )

    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip browser download step.",
    )

    parser.add_argument(
        "--skip-unzip",
        action="store_true",
        help="Skip ZIP extraction step.",
    )

    parser.add_argument(
        "--skip-local-scan",
        action="store_true",
        help="Skip local attachment scan.",
    )

    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip document text extraction.",
    )

    parser.add_argument(
        "--skip-decision",
        action="store_true",
        help="Skip decision report generation.",
    )

    parser.add_argument(
        "--skip-compliance",
        action="store_true",
        help="Skip compliance matrix generation.",
    )

    return parser.parse_args()


def main():
    args = parse_args()
    process_opportunity(args)


if __name__ == "__main__":
    main()