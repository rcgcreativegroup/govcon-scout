import argparse
import csv
from datetime import datetime
from pathlib import Path


DEFAULT_OUTPUT_DIR = "reports/manual_review"
DEFAULT_CSV_PATH = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_DOWNLOADS_DIR = "downloads"


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def load_opportunity_from_csv(notice_id, csv_path):
    path = Path(csv_path)

    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            if row.get("notice_id") == notice_id:
                return dict(row)

    return {}


def file_exists(path):
    return Path(path).exists()


def build_manual_review_report(
    notice_id,
    url,
    reason,
    details,
    output_dir,
    csv_path,
    downloads_dir,
):
    ensure_dir(output_dir)

    opportunity = load_opportunity_from_csv(notice_id, csv_path)

    debug_html = Path(downloads_dir) / "_debug" / f"{notice_id}_sam.html"
    debug_png = Path(downloads_dir) / "_debug" / f"{notice_id}_sam.png"
    piee_html = Path(downloads_dir) / "_debug" / f"{notice_id}_piee.html"
    piee_png = Path(downloads_dir) / "_debug" / f"{notice_id}_piee.png"
    possible_logged_out_html = Path(downloads_dir) / "_debug" / f"{notice_id}_possible_logged_out.html"
    possible_logged_out_png = Path(downloads_dir) / "_debug" / f"{notice_id}_possible_logged_out.png"

    output_path = Path(output_dir) / f"{notice_id}_manual_review.md"

    title = safe_text(opportunity.get("title") or notice_id)
    agency = safe_text(opportunity.get("department_ind_agency"))
    deadline = safe_text(opportunity.get("due_date_user_local") or opportunity.get("response_deadline"))
    fit_score = safe_text(opportunity.get("fit_score"))
    prime_reality = safe_text(opportunity.get("prime_reality_score"))
    recommendation = safe_text(opportunity.get("conditional_recommendation") or opportunity.get("recommendation"))

    lines = []
    lines.append(f"# Manual Review Required — {notice_id}")
    lines.append("")
    lines.append(f"**Created:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## Opportunity Summary")
    lines.append("")
    lines.append(f"- **Title:** {title}")
    lines.append(f"- **Agency:** {agency}")
    lines.append(f"- **Deadline:** {deadline}")
    lines.append(f"- **Fit Score:** {fit_score}")
    lines.append(f"- **Prime Reality Score:** {prime_reality}")
    lines.append(f"- **GovCon Scout Recommendation:** {recommendation}")
    lines.append(f"- **SAM.gov URL:** {url}")
    lines.append("")
    lines.append("## Manual Review Reason")
    lines.append("")
    lines.append(f"- **Reason:** {reason}")
    lines.append(f"- **Details:** {details or 'No additional details provided.'}")
    lines.append("")
    lines.append("## What GovCon Scout Tried")
    lines.append("")
    lines.append("- Opened the SAM.gov opportunity URL.")
    lines.append("- Looked for PIEE Solicitation Module links.")
    lines.append("- Looked for downloadable solicitation/package files.")
    lines.append("- Checked the local downloads folder after download/unzip.")
    lines.append("")
    lines.append("## Debug Files")
    lines.append("")
    lines.append("| File | Exists | Purpose |")
    lines.append("|---|---:|---|")
    lines.append(f"| `{debug_html}` | {'Yes' if file_exists(debug_html) else 'No'} | SAM.gov page HTML |")
    lines.append(f"| `{debug_png}` | {'Yes' if file_exists(debug_png) else 'No'} | SAM.gov page screenshot |")
    lines.append(f"| `{piee_html}` | {'Yes' if file_exists(piee_html) else 'No'} | PIEE page HTML, if reached |")
    lines.append(f"| `{piee_png}` | {'Yes' if file_exists(piee_png) else 'No'} | PIEE page screenshot, if reached |")
    lines.append(f"| `{possible_logged_out_html}` | {'Yes' if file_exists(possible_logged_out_html) else 'No'} | Possible logged-out SAM.gov page HTML |")
    lines.append(f"| `{possible_logged_out_png}` | {'Yes' if file_exists(possible_logged_out_png) else 'No'} | Possible logged-out SAM.gov screenshot |")
    lines.append("")
    lines.append("## Recommended Next Action")
    lines.append("")
    lines.append("1. Open the SAM.gov debug screenshot and confirm whether the opportunity page loaded correctly.")
    lines.append("2. If there is no PIEE link, check whether the notice is RFI-only, text-only, or uses another external portal.")
    lines.append("3. If attachments are visible manually but not detected, update `sam_browser_downloader.py` selectors.")
    lines.append("4. If SAM.gov appears logged out, rerun using the live noVNC processor.")
    lines.append("5. If the notice has no attachments, analyze the notice description directly and classify it as `Notice Text Only`.")
    lines.append("")
    lines.append("## Suggested Classification")
    lines.append("")
    lines.append("- **Manual Review — No Downloadable Package Found**")
    lines.append("")
    lines.append("## Follow-Up Prompt")
    lines.append("")
    lines.append("Review this opportunity manually using the SAM.gov page and debug files. Determine whether it is worth pursuing, whether attachments exist, and whether it should be classified as RFI-only, text-only, external portal, or no-bid.")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")

    print("")
    print(f"Manual review report written to: {output_path}")
    print("")

    return str(output_path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a manual-review report when GovCon Scout cannot download opportunity documents."
    )

    parser.add_argument("--notice-id", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--reason", default="No downloadable solicitation files found.")
    parser.add_argument("--details", default="")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--csv", default=DEFAULT_CSV_PATH)
    parser.add_argument("--downloads-dir", default=DEFAULT_DOWNLOADS_DIR)

    return parser.parse_args()


def main():
    args = parse_args()

    build_manual_review_report(
        notice_id=args.notice_id,
        url=args.url,
        reason=args.reason,
        details=args.details,
        output_dir=args.output_dir,
        csv_path=args.csv,
        downloads_dir=args.downloads_dir,
    )


if __name__ == "__main__":
    main()