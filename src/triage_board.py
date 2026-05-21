import argparse
import csv
import re
from datetime import datetime
from pathlib import Path


DEFAULT_CSV_PATH = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_OUTPUT_PATH = "reports/triage/govcon_triage_board.md"

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


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def score_int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def yes(value):
    return safe_text(value).lower() == "yes"


def read_rows(csv_path):
    path = Path(csv_path)
    if not path.exists():
        raise FileNotFoundError(f"CSV not found: {csv_path}")

    with path.open("r", encoding="utf-8", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def notice_id_for(row):
    return safe_text(
        row.get("notice_id")
        or row.get("solicitation_number")
        or row.get("sam_notice_id")
    )


def is_sources_sought(row):
    text = " ".join([
        safe_text(row.get("notice_type")),
        safe_text(row.get("title")),
        safe_text(row.get("short_description")),
    ]).lower()
    return any(keyword in text for keyword in EARLY_STAGE_KEYWORDS)


def latest_batch_report(batch_dir="reports/batch_runs"):
    reports = sorted(Path(batch_dir).glob("process_shortlist_*.md"))
    return reports[-1] if reports else None


def parse_latest_batch_statuses(batch_path):
    statuses = {}

    if not batch_path or not batch_path.exists():
        return statuses

    for line in batch_path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| "):
            continue
        if "Notice ID" in line or line.startswith("|---"):
            continue

        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 7:
            continue

        notice_id, status, return_code, mode, route, _title, output = cells[:7]
        statuses[notice_id] = {
            "status": status,
            "return_code": return_code,
            "mode": mode,
            "route": route,
            "output": output,
        }

    return statuses


def processed_paths(notice_id):
    return [
        Path("reports/opportunity_reviews") / f"{notice_id}_bid_no_bid.md",
        Path("reports/opportunity_reviews") / f"{notice_id}_decision_report.md",
        Path("reports/opportunity_reviews") / f"{notice_id}_compliance_matrix.md",
    ]


def processed_successfully(notice_id):
    return all(path.exists() for path in processed_paths(notice_id))


def sources_sought_plan_path(notice_id):
    return Path("reports/sources_sought") / f"{notice_id}_sources_sought_plan.md"


def manual_review_path(notice_id):
    return Path("reports/manual_review") / f"{notice_id}_manual_review.md"


def debug_path(notice_id, label, suffix):
    return Path("downloads/_debug") / f"{notice_id}_{label}.{suffix}"


def first_matching_line(text, patterns):
    for line in text.splitlines():
        lower = line.lower()
        if any(pattern in lower for pattern in patterns):
            return line.strip("- ").strip()
    return ""


def classify_manual_review(row, notice_id):
    manual_path = manual_review_path(notice_id)
    text = manual_path.read_text(encoding="utf-8") if manual_path.exists() else ""
    lower = text.lower()

    external_count = score_int(row.get("external_portal_link_count"))
    external_links = safe_text(row.get("external_portal_links"))
    discovery = safe_text(row.get("attachment_discovery_method")).lower()
    possible_logged_out = debug_path(notice_id, "possible_logged_out", "html").exists()
    piee_debug = debug_path(notice_id, "piee", "html").exists()

    if external_count > 0 or external_links or "external" in discovery:
        return "Manual Review - Likely External Portal/No Attachment"

    if possible_logged_out and not piee_debug:
        return "Manual Review - Retry Candidate"

    if "no downloadable package found" in lower or "no piee links detected" in lower:
        return "Manual Review - No Link/No Download"

    if manual_path.exists():
        reason = first_matching_line(lower, ["reason:", "details:"])
        if "logged out" in reason:
            return "Manual Review - Retry Candidate"
        return "Manual Review - No Link/No Download"

    return ""


def document_readiness_label(row, notice_id):
    if processed_successfully(notice_id):
        return "Processed Successfully"

    if sources_sought_plan_path(notice_id).exists():
        return "Sources Sought Plan Generated"

    if manual_review_path(notice_id).exists():
        return classify_manual_review(row, notice_id)

    if yes(row.get("ready_for_bid_no_bid_analysis")) or yes(row.get("local_attachments_found")):
        return "Pass/Not Ready"

    return "Pass/Not Ready"


def output_path_for_status(notice_id, status):
    if status == "Processed Successfully":
        return f"reports/opportunity_reviews/{notice_id}_compliance_matrix.md"
    if status == "Sources Sought Plan Generated":
        return str(sources_sought_plan_path(notice_id))
    if status.startswith("Manual Review"):
        return str(manual_review_path(notice_id))
    packet = Path("reports/analysis_packets") / f"{notice_id}.md"
    return str(packet) if packet.exists() else ""


def action_recommendation(row, status):
    if status == "Processed Successfully":
        return "Review decision packet; decide bid/no-bid."
    if status == "Sources Sought Plan Generated":
        return "Draft response or teaming outreach."
    if status == "Manual Review - Retry Candidate":
        return "Retry live after confirming SAM.gov login; inspect debug screenshot."
    if status == "Manual Review - Likely External Portal/No Attachment":
        return "Inspect portal manually; only add selectors if high value."
    if status == "Manual Review - No Link/No Download":
        return "Inspect SAM/debug files; classify text-only/no-bid or selector gap."
    if yes(row.get("set_aside_hard_gate")):
        return "Pass for prime; consider teaming only."
    if not yes(row.get("ready_for_bid_no_bid_analysis")):
        return "Do not pursue until attachments/details are available."
    return "Review manually before spending more automation time."


def pursue_score(row, status):
    score = score_int(row.get("fit_score")) + score_int(row.get("prime_reality_score"))

    if status == "Processed Successfully":
        score += 40
    elif status == "Sources Sought Plan Generated":
        score += 25
    elif status == "Manual Review - Retry Candidate":
        score += 10
    elif status.startswith("Manual Review"):
        score -= 10

    if yes(row.get("set_aside_hard_gate")):
        score -= 80

    if safe_text(row.get("conditional_recommendation")).startswith("Teaming/Subcontractor Target"):
        score -= 20

    if safe_text(row.get("deadline_status")) == "expired":
        score -= 100

    return score


def board_item(row, batch_statuses):
    notice_id = notice_id_for(row)
    status = document_readiness_label(row, notice_id)
    batch = batch_statuses.get(notice_id, {})

    return {
        "notice_id": notice_id,
        "title": safe_text(row.get("title")),
        "agency": safe_text(row.get("department_ind_agency")),
        "deadline": safe_text(row.get("due_date_user_local") or row.get("response_deadline")),
        "fit": score_int(row.get("fit_score")),
        "prime": score_int(row.get("prime_reality_score")),
        "lane": safe_text(row.get("matched_lane")),
        "recommendation": safe_text(row.get("conditional_recommendation") or row.get("recommendation")),
        "set_aside": safe_text(row.get("set_aside")),
        "status": status,
        "route": batch.get("route") or ("sources_sought" if is_sources_sought(row) else "solicitation"),
        "last_batch_status": batch.get("status", ""),
        "output": output_path_for_status(notice_id, status),
        "action": action_recommendation(row, status),
        "pursue_score": pursue_score(row, status),
    }


def markdown_link(path):
    if not path:
        return ""
    return f"[open]({path})"


def table(items):
    lines = [
        "| Notice | Status | Fit | Prime | Deadline | Action | Output |",
        "|---|---:|---:|---:|---|---|---|",
    ]

    for item in items:
        notice = f"{item['notice_id']} - {item['title']}".replace("|", "\\|")
        action = item["action"].replace("|", "\\|")
        lines.append(
            f"| {notice} | {item['status']} | {item['fit']} | {item['prime']} | "
            f"{item['deadline']} | {action} | {markdown_link(item['output'])} |"
        )

    return "\n".join(lines)


def summarize_counts(items):
    counts = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return counts


def build_board(items, batch_path):
    actionable = [
        item for item in items
        if item["status"] in {
            "Processed Successfully",
            "Sources Sought Plan Generated",
            "Manual Review - Retry Candidate",
        }
        and item["prime"] >= 50
    ]
    pursue_now = sorted(actionable, key=lambda item: item["pursue_score"], reverse=True)[:5]

    sections = {
        "Processed Successfully": [],
        "Sources Sought Plans Generated": [],
        "Manual Review - No Link/No Download": [],
        "Manual Review - Likely External Portal/No Attachment": [],
        "Manual Review - Retry Candidate": [],
        "Pass/Not Ready": [],
    }

    for item in sorted(items, key=lambda item: item["pursue_score"], reverse=True):
        if item["status"] == "Sources Sought Plan Generated":
            sections["Sources Sought Plans Generated"].append(item)
        else:
            sections.setdefault(item["status"], []).append(item)

    counts = summarize_counts(items)

    lines = [
        "# GovCon Scout Triage Board",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Source CSV:** `{DEFAULT_CSV_PATH}`",
        f"**Latest Batch Report:** `{batch_path}`" if batch_path else "**Latest Batch Report:** None found",
        "",
        "## Summary",
        "",
    ]

    for status in sorted(counts):
        lines.append(f"- **{status}:** {counts[status]}")

    lines.extend([
        "",
        "## Pursue Now",
        "",
    ])

    lines.append(table(pursue_now) if pursue_now else "No pursue-now items found.")

    for title, section_items in sections.items():
        lines.extend(["", f"## {title}", ""])
        lines.append(table(section_items) if section_items else "None.")

    return "\n".join(lines) + "\n"


def parse_args():
    parser = argparse.ArgumentParser(description="Build the GovCon Scout triage board.")
    parser.add_argument("--csv", default=DEFAULT_CSV_PATH)
    parser.add_argument("--output", default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum actionable rows to include from the CSV before artifact grouping.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    rows = [
        row for row in read_rows(args.csv)
        if safe_text(row.get("notice_actionability")) == "actionable"
    ]
    rows = sorted(
        rows,
        key=lambda row: (
            score_int(row.get("prime_reality_score")),
            score_int(row.get("fit_score")),
        ),
        reverse=True,
    )[:args.limit]

    batch_path = latest_batch_report()
    batch_statuses = parse_latest_batch_statuses(batch_path)
    items = [board_item(row, batch_statuses) for row in rows if notice_id_for(row)]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_board(items, batch_path), encoding="utf-8")

    print(f"Triage board written to: {output_path}")


if __name__ == "__main__":
    main()
