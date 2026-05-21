import argparse
import csv
import subprocess
import sys
from datetime import datetime
from pathlib import Path


DEFAULT_CSV_PATH = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_BATCH_REPORT_DIR = "reports/batch_runs"
DEFAULT_AUTH_STATE = "auth.json"


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def score_int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def load_rows(csv_path):
    path = Path(csv_path)

    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    with open(path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return [dict(row) for row in reader]


def run_command(command, stop_on_error=False):
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


def auth_ready(auth_state):
    if not Path(auth_state).exists():
        print("")
        print(f"Missing auth file: {auth_state}")
        print("Use live mode instead:")
        print("")
        print("  python src/process_shortlist.py --limit 3 --live")
        print("")
        return False

    result = run_command([
        sys.executable,
        "src/sam_browser_downloader.py",
        "--test-auth",
        "--auth-state",
        auth_state,
        "--no-debug",
    ])

    return result == 0


def already_processed(notice_id):
    required_outputs = [
        Path("reports/opportunity_reviews") / f"{notice_id}_bid_no_bid.md",
        Path("reports/opportunity_reviews") / f"{notice_id}_decision_report.md",
        Path("reports/opportunity_reviews") / f"{notice_id}_compliance_matrix.md",
    ]

    return all(path.exists() for path in required_outputs)


def manual_review_exists(notice_id):
    return (Path("reports/manual_review") / f"{notice_id}_manual_review.md").exists()


def select_candidates(
    rows,
    limit,
    include_teaming=False,
    include_blocked=False,
    min_prime_reality=50,
    notice_ids=None,
):
    notice_ids = set(notice_ids or [])
    candidates = []

    for row in rows:
        notice_id = safe_text(
            row.get("notice_id")
            or row.get("solicitation_number")
            or row.get("sam_notice_id")
        )
        url = safe_text(row.get("ui_link"))

        if not notice_id or not url:
            continue

        if notice_ids and notice_id not in notice_ids:
            continue

        if row.get("notice_actionability") != "actionable":
            continue

        if not include_blocked and row.get("set_aside_hard_gate") == "Yes":
            continue

        if not include_teaming:
            recommendation = safe_text(row.get("conditional_recommendation"))
            if recommendation.startswith("Teaming/Subcontractor Target"):
                continue

        if score_int(row.get("prime_reality_score")) < min_prime_reality and not notice_ids:
            continue

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


def write_batch_report(results, output_dir):
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    report_path = Path(output_dir) / f"process_shortlist_{timestamp}.md"

    lines = []
    lines.append("# GovCon Scout Batch Processing Report")
    lines.append("")
    lines.append(f"**Run Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("| Notice ID | Status | Return Code | Mode | Title | Output |")
    lines.append("|---|---:|---:|---:|---|---|")

    for result in results:
        notice_id = result.get("notice_id", "")
        status = result.get("status", "")
        code = result.get("return_code", "")
        mode = result.get("mode", "")
        title = result.get("title", "").replace("|", "\\|")
        output = result.get("output", "").replace("|", "\\|")

        lines.append(f"| {notice_id} | {status} | {code} | {mode} | {title} | {output} |")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    return str(report_path)


def build_manual_review(notice_id, url, reason, details, downloads_dir):
    return run_command([
        sys.executable,
        "src/manual_review_report.py",
        "--notice-id",
        notice_id,
        "--url",
        url,
        "--reason",
        reason,
        "--details",
        details,
        "--downloads-dir",
        downloads_dir,
    ])


def process_candidate(row, args):
    notice_id = safe_text(
        row.get("notice_id")
        or row.get("solicitation_number")
        or row.get("sam_notice_id")
    )
    url = safe_text(row.get("ui_link"))
    title = safe_text(row.get("title"))

    if already_processed(notice_id) and not args.force:
        print("")
        print(f"Skipping already processed opportunity: {notice_id}")
        print("")
        return {
            "notice_id": notice_id,
            "title": title,
            "status": "Skipped — Already Processed",
            "return_code": 0,
            "mode": "live" if args.live else "saved-auth",
            "output": f"reports/opportunity_reviews/{notice_id}_compliance_matrix.md",
        }

    if manual_review_exists(notice_id) and not args.force and not args.retry_manual:
        print("")
        print(f"Skipping opportunity already marked for manual review: {notice_id}")
        print("")
        return {
            "notice_id": notice_id,
            "title": title,
            "status": "Skipped — Manual Review Exists",
            "return_code": 0,
            "mode": "live" if args.live else "saved-auth",
            "output": f"reports/manual_review/{notice_id}_manual_review.md",
        }

    if args.live:
        command = [
            sys.executable,
            "src/process_opportunity_live.py",
            "--notice-id",
            notice_id,
            "--url",
            url,
            "--downloads-dir",
            args.downloads_dir,
        ]

        if args.skip_analysis:
            command.append("--skip-analysis")

    else:
        command = [
            sys.executable,
            "src/process_opportunity.py",
            "--notice-id",
            notice_id,
            "--url",
            url,
            "--auth-state",
            args.auth_state,
            "--downloads-dir",
            args.downloads_dir,
            "--skip-auth-check",
        ]

        if args.skip_download:
            command.append("--skip-download")

        if args.skip_unzip:
            command.append("--skip-unzip")

        if args.skip_extract:
            command.append("--skip-extract")

        if args.skip_decision:
            command.append("--skip-decision")

        if args.skip_compliance:
            command.append("--skip-compliance")

    return_code = run_command(command)

    if return_code == 0:
        status = "Processed"
        output = f"reports/opportunity_reviews/{notice_id}_compliance_matrix.md"
    else:
        status = "Manual Review Required"
        output = f"reports/manual_review/{notice_id}_manual_review.md"

        build_manual_review(
            notice_id=notice_id,
            url=url,
            reason="Batch processing could not complete automatically.",
            details=(
                "GovCon Scout attempted to process this opportunity, but download/extraction/analysis "
                "did not complete. Review SAM.gov and debug files."
            ),
            downloads_dir=args.downloads_dir,
        )

    return {
        "notice_id": notice_id,
        "title": title,
        "status": status,
        "return_code": return_code,
        "mode": "live" if args.live else "saved-auth",
        "output": output,
    }


def parse_notice_ids(value):
    text = safe_text(value)

    if not text:
        return []

    return [
        item.strip()
        for item in text.split(",")
        if item.strip()
    ]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Batch process top GovCon Scout shortlist opportunities."
    )

    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV_PATH,
        help="GovCon Scout CSV source.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=3,
        help="Number of opportunities to process.",
    )

    parser.add_argument(
        "--notice-ids",
        default="",
        help="Comma-separated specific notice IDs to process instead of automatic shortlist selection.",
    )

    parser.add_argument(
        "--auth-state",
        default=DEFAULT_AUTH_STATE,
        help="Playwright auth state file.",
    )

    parser.add_argument(
        "--downloads-dir",
        default="downloads",
        help="Downloads directory.",
    )

    parser.add_argument(
        "--live",
        action="store_true",
        help="Use live noVNC browser processing instead of saved auth.json processing.",
    )

    parser.add_argument(
        "--include-teaming",
        action="store_true",
        help="Include teaming/subcontractor targets.",
    )

    parser.add_argument(
        "--include-blocked",
        action="store_true",
        help="Include set-aside hard-gated opportunities.",
    )

    parser.add_argument(
        "--min-prime-reality",
        type=int,
        default=50,
        help="Minimum prime reality score for automatic selection.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess opportunities even if reports already exist.",
    )

    parser.add_argument(
        "--retry-manual",
        action="store_true",
        help="Retry opportunities even if a manual-review report already exists.",
    )
    
    parser.add_argument(
        "--skip-auth-check",
        action="store_true",
        help="Skip the batch-level SAM.gov auth check.",
    )

    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download step for each opportunity in saved-auth mode.",
    )

    parser.add_argument(
        "--skip-unzip",
        action="store_true",
        help="Skip unzip step for each opportunity in saved-auth mode.",
    )

    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip text extraction step for each opportunity in saved-auth mode.",
    )

    parser.add_argument(
        "--skip-decision",
        action="store_true",
        help="Skip decision report generation in saved-auth mode.",
    )

    parser.add_argument(
        "--skip-compliance",
        action="store_true",
        help="Skip compliance matrix generation in saved-auth mode.",
    )

    parser.add_argument(
        "--skip-analysis",
        action="store_true",
        help="Skip analysis pipeline in live mode after download.",
    )

    parser.add_argument(
        "--report-dir",
        default=DEFAULT_BATCH_REPORT_DIR,
        help="Output folder for batch report.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    notice_ids = parse_notice_ids(args.notice_ids)
    rows = load_rows(args.csv)

    candidates = select_candidates(
        rows=rows,
        limit=args.limit if not notice_ids else len(notice_ids),
        include_teaming=args.include_teaming,
        include_blocked=args.include_blocked,
        min_prime_reality=args.min_prime_reality,
        notice_ids=notice_ids,
    )

    if not candidates:
        print("")
        print("No candidates selected for batch processing.")
        print("")
        print("Try one of these:")
        print("")
        print("  python src/process_shortlist.py --limit 3 --include-teaming")
        print("  python src/process_shortlist.py --limit 3 --include-blocked")
        print("  python src/process_shortlist.py --notice-ids HE125426QE041")
        print("  python src/process_shortlist.py --limit 3 --live")
        print("")
        sys.exit(1)

    print("")
    print(f"Selected {len(candidates)} candidate(s) for batch processing:")
    print("")

    for index, row in enumerate(candidates, start=1):
        print(
            f"{index}. {row.get('notice_id')} — "
            f"Prime Reality: {row.get('prime_reality_score')} — "
            f"Fit: {row.get('fit_score')} — "
            f"{row.get('title')}"
        )

    if not args.live and not args.skip_auth_check:
        if not auth_ready(args.auth_state):
            print("")
            print("Stopping batch before processing because SAM.gov auth is not ready.")
            print("Use live mode if saved auth is unreliable:")
            print("")
            print("  python src/process_shortlist.py --limit 3 --live")
            print("")
            sys.exit(1)

    if args.live:
        print("")
        print("Live mode enabled.")
        print("Make sure noVNC is running on forwarded port 6080 and DISPLAY=:99 is set.")
        print("Each selected opportunity may open in the live browser session.")
        print("")

    results = []

    for row in candidates:
        result = process_candidate(row, args)
        results.append(result)

    report_path = write_batch_report(
        results=results,
        output_dir=args.report_dir,
    )

    print("")
    print("Batch processing complete.")
    print(f"Batch report: {report_path}")
    print("")


if __name__ == "__main__":
    main()