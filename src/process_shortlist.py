import argparse
import csv
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


DEFAULT_CSV_PATH = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_BATCH_REPORT_DIR = "reports/batch_runs"
DEFAULT_AUTH_STATE = "auth.json"
NOVNC_CHECK_SCRIPT = "scripts/novnc_check.sh"
LIVE_INFRASTRUCTURE_EXIT_CODE = 86


EARLY_STAGE_KEYWORDS = [
    "sources sought",
    "source sought",
    "request for information",
    "rfi",
    "market research",
    "special notice",
    "presolicitation",
    "pre-solicitation",
]

SOLICITATION_KEYWORDS = [
    "combined synopsis/solicitation",
    "combinedsynopsissolicitation",
    "solicitation",
    "rfq",
    "rfp",
    "request for quote",
    "request for proposal",
    "invitation for bid",
    "ifb",
]


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


def live_environment_ready():
    if not os.environ.get("DISPLAY"):
        print("")
        print("DISPLAY is not set, so live noVNC processing cannot start.")
        print("")
        print("Reset the live desktop with:")
        print("")
        print("  scripts/novnc_reset.sh")
        print("  export DISPLAY=:99")
        print("")
        return False

    check_script = Path(NOVNC_CHECK_SCRIPT)

    if not check_script.exists():
        print("")
        print(f"Missing noVNC check script: {NOVNC_CHECK_SCRIPT}")
        print("")
        return False

    print("")
    print("Checking live noVNC environment before batch processing...")
    print("")

    result = subprocess.run(["bash", str(check_script)])

    if result.returncode == 0:
        return True

    print("")
    print("Stopping batch before processing because noVNC is not ready.")
    print("No manual-review reports were created for this infrastructure failure.")
    print("")
    return False


def live_infrastructure_failed(return_code):
    return return_code == LIVE_INFRASTRUCTURE_EXIT_CODE


def already_processed(notice_id):
    required_outputs = [
        Path("reports/opportunity_reviews") / f"{notice_id}_bid_no_bid.md",
        Path("reports/opportunity_reviews") / f"{notice_id}_decision_report.md",
        Path("reports/opportunity_reviews") / f"{notice_id}_compliance_matrix.md",
    ]

    return all(path.exists() for path in required_outputs)


def pricing_processed(notice_id):
    return (
        (Path("reports/pricing") / f"{notice_id}_pricing_schedule.md").exists()
        and (Path("reports/pricing") / f"{notice_id}_pricing_table.csv").exists()
    )


def sources_sought_processed(notice_id):
    return (Path("reports/sources_sought") / f"{notice_id}_sources_sought_plan.md").exists()


def manual_review_exists(notice_id):
    return (Path("reports/manual_review") / f"{notice_id}_manual_review.md").exists()


def combined_row_text(row):
    fields = [
        "title",
        "description",
        "notice_type",
        "type",
        "solicitation_type",
        "base_type",
        "archive_type",
        "conditional_recommendation",
        "recommendation",
        "matched_keywords",
        "matched_core_strengths",
    ]

    return " ".join(safe_text(row.get(field)) for field in fields).lower()


def classify_route(row):
    text = combined_row_text(row)
    title = safe_text(row.get("title")).lower()
    notice_type = safe_text(row.get("notice_type") or row.get("type") or row.get("solicitation_type")).lower()
    combined = f"{notice_type} {title} {text}"

    if any(keyword in combined for keyword in EARLY_STAGE_KEYWORDS):
        return "sources_sought"

    if any(keyword in combined for keyword in SOLICITATION_KEYWORDS):
        return "solicitation"

    if safe_text(row.get("ready_for_bid_no_bid_analysis")).lower() == "yes":
        return "solicitation"

    return "solicitation"


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
    lines.append("| Notice ID | Status | Return Code | Mode | Route | Title | Output |")
    lines.append("|---|---:|---:|---:|---:|---|---|")

    for result in results:
        notice_id = result.get("notice_id", "")
        status = result.get("status", "")
        code = result.get("return_code", "")
        mode = result.get("mode", "")
        route = result.get("route", "")
        title = result.get("title", "").replace("|", "\\|")
        output = result.get("output", "").replace("|", "\\|")

        lines.append(f"| {notice_id} | {status} | {code} | {mode} | {route} | {title} | {output} |")

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


def run_sources_sought_planner(notice_id):
    return run_command([
        sys.executable,
        "src/sources_sought_planner.py",
        "--notice-id",
        notice_id,
    ])


def process_sources_sought_candidate(row, args):
    notice_id = safe_text(
        row.get("notice_id")
        or row.get("solicitation_number")
        or row.get("sam_notice_id")
    )
    url = safe_text(row.get("ui_link"))
    title = safe_text(row.get("title"))

    if sources_sought_processed(notice_id) and not args.force and not args.retry_manual:
        print("")
        print(f"Skipping already planned sources sought/RFI opportunity: {notice_id}")
        print("")
        return {
            "notice_id": notice_id,
            "title": title,
            "status": "Skipped — Sources Sought Plan Exists",
            "return_code": 0,
            "mode": "smart",
            "route": "sources_sought",
            "output": f"reports/sources_sought/{notice_id}_sources_sought_plan.md",
        }

    return_code = run_sources_sought_planner(notice_id)

    if return_code == 0:
        status = "Processed — Sources Sought Plan"
        output = f"reports/sources_sought/{notice_id}_sources_sought_plan.md"
    else:
        status = "Manual Review Required"
        output = f"reports/manual_review/{notice_id}_manual_review.md"

        build_manual_review(
            notice_id=notice_id,
            url=url,
            reason="Sources sought planner could not complete.",
            details="GovCon Scout routed this as an early-stage notice, but sources_sought_planner.py failed.",
            downloads_dir=args.downloads_dir,
        )

    return {
        "notice_id": notice_id,
        "title": title,
        "status": status,
        "return_code": return_code,
        "mode": "smart",
        "route": "sources_sought",
        "output": output,
    }


def process_solicitation_candidate(row, args):
    notice_id = safe_text(
        row.get("notice_id")
        or row.get("solicitation_number")
        or row.get("sam_notice_id")
    )
    url = safe_text(row.get("ui_link"))
    title = safe_text(row.get("title"))

    if already_processed(notice_id) and pricing_processed(notice_id) and not args.force:
        print("")
        print(f"Skipping already fully processed solicitation: {notice_id}")
        print("")
        return {
            "notice_id": notice_id,
            "title": title,
            "status": "Skipped — Fully Processed",
            "return_code": 0,
            "mode": "live" if args.live else "saved-auth",
            "route": "solicitation",
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
            "route": "solicitation",
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

        if args.skip_pricing:
            command.append("--skip-pricing")

    return_code = run_command(command)

    if return_code == 0:
        status = "Processed — Solicitation"
        output = f"reports/opportunity_reviews/{notice_id}_compliance_matrix.md"
    elif args.live and live_infrastructure_failed(return_code):
        status = "Live Infrastructure Failure"
        output = "scripts/novnc_check.sh"
    else:
        status = "Manual Review Required"
        output = f"reports/manual_review/{notice_id}_manual_review.md"

        build_manual_review(
            notice_id=notice_id,
            url=url,
            reason="Solicitation processing could not complete automatically.",
            details=(
                "GovCon Scout attempted to process this solicitation, but download/extraction/analysis "
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
        "route": "solicitation",
        "output": output,
    }


def process_candidate(row, args):
    if args.smart:
        route = classify_route(row)

        notice_id = safe_text(
            row.get("notice_id")
            or row.get("solicitation_number")
            or row.get("sam_notice_id")
        )

        print("")
        print(f"Smart route for {notice_id}: {route}")
        print("")

        if route == "sources_sought":
            return process_sources_sought_candidate(row, args)

        return process_solicitation_candidate(row, args)

    return process_solicitation_candidate(row, args)


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
        "--smart",
        action="store_true",
        help="Automatically route sources sought/RFI notices to the sources sought planner and solicitations to document processing.",
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
        "--skip-pricing",
        action="store_true",
        help="Skip pricing schedule extraction in saved-auth mode.",
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
        print("  python src/process_shortlist.py --limit 3 --smart")
        print("  python src/process_shortlist.py --limit 3 --smart --live")
        print("  python src/process_shortlist.py --limit 3 --include-teaming")
        print("  python src/process_shortlist.py --limit 3 --include-blocked")
        print("  python src/process_shortlist.py --notice-ids HE125426QE041 --smart")
        print("")
        sys.exit(1)

    print("")
    print(f"Selected {len(candidates)} candidate(s) for batch processing:")
    print("")

    for index, row in enumerate(candidates, start=1):
        route = classify_route(row) if args.smart else "solicitation"
        print(
            f"{index}. {row.get('notice_id')} — "
            f"Route: {route} — "
            f"Prime Reality: {row.get('prime_reality_score')} — "
            f"Fit: {row.get('fit_score')} — "
            f"{row.get('title')}"
        )

    needs_saved_auth = False

    if args.live and not live_environment_ready():
        sys.exit(LIVE_INFRASTRUCTURE_EXIT_CODE)

    if not args.live and not args.skip_auth_check:
        for row in candidates:
            notice_id = safe_text(
                row.get("notice_id")
                or row.get("solicitation_number")
                or row.get("sam_notice_id")
            )

            route = classify_route(row) if args.smart else "solicitation"

            if route == "sources_sought":
                continue

            if already_processed(notice_id) and pricing_processed(notice_id) and not args.force:
                continue

            if manual_review_exists(notice_id) and not args.force and not args.retry_manual:
                continue

            needs_saved_auth = True
            break

    if needs_saved_auth:
        if not auth_ready(args.auth_state):
            print("")
            print("Stopping batch before processing because SAM.gov auth is not ready.")
            print("Use live mode if saved auth is unreliable:")
            print("")
            print("  python src/process_shortlist.py --limit 3 --smart --live")
            print("")
            sys.exit(1)

    if args.live:
        print("")
        print("Live mode enabled.")
        print("Make sure noVNC is running on forwarded port 6080 and DISPLAY=:99 is set.")
        print("Each selected solicitation may open in the live browser session.")
        print("Sources sought/RFI items do not need browser download unless manually reviewed.")
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
