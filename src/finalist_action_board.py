import argparse
import csv
import re
from datetime import datetime
from pathlib import Path


DEFAULT_OUTPUT = "reports/triage/finalist_action_board.md"
DEFAULT_REVIEW_PACK = "reports/triage/govcon_triage_review_pack.md"
DEFAULT_TRIAGE_BOARD = "reports/triage/govcon_triage_board.md"
DEFAULT_GOVCON_CSV = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_MYBIDMATCH_RESOLVED = "data/mybidmatch/mybidmatch_resolved.csv"

BOARD_SECTIONS = [
    "Pursue Now",
    "Processed Successfully",
    "Sources Sought Plans Generated",
    "Manual Review - No Link/No Download",
    "Manual Review - Retry Candidate",
    "Pass/Not Ready",
]


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def read_text(path):
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_csv_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def split_markdown_row(line):
    line = line.strip()
    if not line.startswith("|") or not line.endswith("|"):
        return []
    return [cell.strip() for cell in line.strip("|").split("|")]


def extract_link(value):
    match = re.search(r"\(([^)]+)\)", safe_text(value))
    return match.group(1) if match else ""


def parse_notice_cell(value):
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", safe_text(value))
    if " - " not in text:
        return text, ""
    notice_id, title = text.split(" - ", 1)
    return notice_id.strip(), title.strip()


def parse_board_table(lines, section):
    rows = []
    in_section = False
    headers = []
    for line in lines:
        if line.startswith("## "):
            current = line.replace("## ", "", 1).strip()
            if in_section and current != section:
                break
            in_section = current == section
            headers = []
            continue
        if not in_section or not line.startswith("|"):
            continue
        cells = split_markdown_row(line)
        if not cells or all(set(cell) <= {"-", ":"} for cell in cells):
            continue
        if cells[0] == "Notice":
            headers = cells
            continue
        if not headers or len(cells) < len(headers):
            continue
        row = dict(zip(headers, cells))
        notice_id, title = parse_notice_cell(row.get("Notice"))
        if notice_id:
            rows.append({
                "notice_id": notice_id,
                "title": title,
                "status": row.get("Status", section),
                "fit": row.get("Fit", ""),
                "prime": row.get("Prime", ""),
                "deadline": row.get("Deadline", ""),
                "action": row.get("Action", ""),
                "output": extract_link(row.get("Output", "")),
                "section": section,
            })
    return rows


def parse_triage_board(path):
    text = read_text(path)
    if not text:
        return []
    lines = text.splitlines()
    by_notice = {}
    for section in BOARD_SECTIONS:
        for row in parse_board_table(lines, section):
            existing = by_notice.get(row["notice_id"])
            if not existing or section == "Pursue Now":
                by_notice[row["notice_id"]] = row
            if section == "Pursue Now":
                by_notice[row["notice_id"]]["pursue_now"] = True
    return list(by_notice.values())


def path_for(notice_id, kind):
    paths = {
        "decision": Path("reports/opportunity_reviews") / f"{notice_id}_decision_report.md",
        "compliance": Path("reports/opportunity_reviews") / f"{notice_id}_compliance_matrix.md",
        "bid_no_bid": Path("reports/opportunity_reviews") / f"{notice_id}_bid_no_bid.md",
        "sources_sought": Path("reports/sources_sought") / f"{notice_id}_sources_sought_plan.md",
        "manual_review": Path("reports/manual_review") / f"{notice_id}_manual_review.md",
        "pricing": Path("reports/pricing") / f"{notice_id}_pricing_schedule.md",
        "pricing_csv": Path("reports/pricing") / f"{notice_id}_pricing_table.csv",
        "usaspending": Path("reports/market_intel") / f"{notice_id}_usaspending_intel.md",
        "bid_price_sanity": Path("reports/pricing") / f"{notice_id}_bid_price_sanity.md",
        "analysis_packet": Path("reports/analysis_packets") / f"{notice_id}.md",
    }
    return paths[kind]


def artifact_links(notice_id):
    labels = [
        ("decision", "decision"),
        ("compliance", "compliance"),
        ("bid_no_bid", "bid/no-bid"),
        ("pricing", "pricing"),
        ("pricing_csv", "pricing csv"),
        ("usaspending", "USAspending"),
        ("bid_price_sanity", "price sanity"),
        ("sources_sought", "sources sought"),
        ("manual_review", "manual review"),
        ("analysis_packet", "analysis packet"),
    ]
    links = []
    for kind, label in labels:
        path = path_for(notice_id, kind)
        if path.exists():
            links.append(f"[{label}]({path})")
    return ", ".join(links) if links else "No local output linked."


def has_artifact(notice_id, kind):
    return path_for(notice_id, kind).exists()


def first_match(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


def bid_price_action(notice_id):
    text = read_text(path_for(notice_id, "bid_price_sanity"))
    if not text:
        return ""
    return first_match(text, [r"\*\*Recommended next action:\*\*\s*(.+)", r"## Recommended Next Action\s+\*\*(.+?)\*\*"])


def evidence_summary(notice_id):
    parts = []
    if all(has_artifact(notice_id, kind) for kind in ["decision", "compliance", "bid_no_bid"]):
        parts.append("review package")
    if has_artifact(notice_id, "pricing") or has_artifact(notice_id, "pricing_csv"):
        parts.append("pricing extracted")
    parts.append("USAspending ready" if has_artifact(notice_id, "usaspending") else "USAspending not run")
    if has_artifact(notice_id, "bid_price_sanity"):
        parts.append("price sanity ready")
    if has_artifact(notice_id, "sources_sought"):
        parts.append("sources-sought plan")
    if has_artifact(notice_id, "manual_review"):
        parts.append("manual-review report")
    return ", ".join(parts)


def action_for_govcon(row):
    notice_id = row["notice_id"]
    status = row.get("status", "")
    sanity = bid_price_action(notice_id)
    title_lower = row.get("title", "").lower()

    if sanity == "Proceed Only After Vendor/Subcontractor Quote":
        if "pest" in title_lower:
            return "Get pest-control subcontractor quote before pricing", "Bid price sanity requires vendor/subcontractor quote coverage before building a worksheet."
        return "Validate vendor availability", "Bid price sanity requires quote support before pricing."
    if sanity == "Pass / Not Priceable Yet":
        return "Park/pass until pricing package or local vendor quote exists", "Bid price sanity found no priceable CLIN package."
    if sanity == "Proceed to Pricing Worksheet":
        return "Build pricing worksheet", "Pricing artifacts and sanity check are ready for worksheet development."
    if sanity == "Market Intel Needs More Validation":
        return "Validate market comparables before pricing", "Price sanity flagged historical award context as broad or weak."
    if sanity == "Teaming Recommended Before Pricing":
        return "Validate teaming path before pricing", "Price sanity recommends a partner path before pricing."

    if status == "Sources Sought Plan Generated":
        if "rfi" in row.get("title", "").lower() or "sources" in row.get("title", "").lower():
            return "Prepare sources-sought response", "Sources-sought/RFI planner exists; response or teaming outreach is the next human step."
        return "Draft agency questions", "Early-stage item needs response shaping and agency validation."
    if status.startswith("Manual Review"):
        if "Retry Candidate" in status:
            return "Retry live download", "Manual-review report indicates this may be recoverable after live/session validation."
        return "Locate SAM.gov notice ID manually", "Automation could not complete; manual source review is needed."
    if status == "Pass/Not Ready":
        return "Pass for now", "Item is currently marked pass/not-ready."
    if row.get("status") == "Processed Successfully":
        if has_artifact(notice_id, "usaspending"):
            return "Review decision and pricing gate", "Processed package has market intel; choose pricing, quote, or park."
        return "Run finalist market intel later", "Processed package is missing USAspending, but external calls are not part of this board run."
    return row.get("action") or "Review candidate", "Existing triage action is the best available local signal."


def priority_for_govcon(row, action):
    if action in {"Get pest-control subcontractor quote before pricing", "Build pricing worksheet"}:
        return 1
    if row.get("status") == "Sources Sought Plan Generated":
        return 2
    if row.get("status") == "Processed Successfully":
        return 3
    if row.get("status", "").startswith("Manual Review"):
        return 7
    if row.get("status") == "Pass/Not Ready":
        return 9
    return 5


def make_govcon_item(row):
    action, why = action_for_govcon(row)
    return {
        "priority": priority_for_govcon(row, action),
        "id": row["notice_id"],
        "title": row["title"],
        "source": "GovCon Scout",
        "status": row.get("status", ""),
        "evidence": evidence_summary(row["notice_id"]),
        "action": action,
        "why": why,
        "owner": "Operator",
        "links": artifact_links(row["notice_id"]),
        "bucket": bucket_for_action(row.get("status", ""), action),
    }


def bucket_for_action(status, action):
    if action in {
        "Get pest-control subcontractor quote before pricing",
        "Build pricing worksheet",
        "Validate vendor availability",
        "Review decision and pricing gate",
    }:
        return "Pursue / Price Next"
    if status == "Sources Sought Plan Generated":
        return "Sources Sought / RFI Actions"
    if status.startswith("Manual Review") or action in {"Retry live download", "Locate SAM.gov notice ID manually"}:
        return "Manual Review / Retry"
    if status == "Pass/Not Ready" or action.startswith("Park") or action.startswith("Pass"):
        return "Park / Pass"
    return "Pursue / Price Next"


def make_mybidmatch_item(row):
    status = row.get("resolution_status", "")
    matched_id = safe_text(row.get("matched_notice_id"))
    if status == "Confirmed GovCon Scout Match":
        action = "Confirm MyBidMatch/SAM match"
        why = "Resolver found a confirmed existing GovCon Scout/SAM candidate; validate title and agency before using the matched record."
        priority = 4
        bucket = "Validate MyBidMatch Matches"
    elif status == "Possible GovCon Scout Match":
        action = "Confirm MyBidMatch/SAM match"
        why = "Resolver found a possible match that requires manual title/agency validation."
        priority = 5
        bucket = "Validate MyBidMatch Matches"
    elif status == "Needs Manual Lookup":
        action = "Locate SAM.gov notice ID manually"
        why = "Resolver could not confirm a reliable local match."
        priority = 8
        bucket = "Validate MyBidMatch Matches"
    elif status == "State/Local/Non-SAM Lead":
        action = "Park for state/local workflow"
        why = "Lead appears state/local or non-SAM and is not ready for SAM automation."
        priority = 9
        bucket = "Park / Pass"
    elif status == "Duplicate / Already Covered":
        action = "Park unless new details are present"
        why = "Resolver says this is already represented in GovCon Scout."
        priority = 10
        bucket = "Park / Pass"
    else:
        action = "Review MyBidMatch source"
        why = "Resolution status is missing or unknown."
        priority = 8
        bucket = "Validate MyBidMatch Matches"

    title = row.get("matched_govcon_title") if status == "Confirmed GovCon Scout Match" and row.get("matched_govcon_title") else row.get("mybidmatch_title")
    lead_id = matched_id or "MyBidMatch"
    source_url = safe_text(row.get("mybidmatch_source_url"))
    links = f"[source]({source_url})" if source_url else "No source link."
    if matched_id:
        links = f"{links}, matched notice `{matched_id}`"

    return {
        "priority": priority,
        "id": lead_id,
        "title": safe_text(title),
        "source": "MyBidMatch",
        "status": status,
        "evidence": f"resolution: {status}",
        "action": action,
        "why": why,
        "owner": "Operator",
        "links": links,
        "bucket": bucket,
    }


def priority_sort(item):
    return (item["priority"], item["source"], item["id"], item["title"])


def table(items):
    if not items:
        return "None."
    lines = [
        "| Priority | Notice / Lead ID | Title | Source | Status | Evidence Available | Recommended Next Action | Why | Owner | Output Links |",
        "|---:|---|---|---|---|---|---|---|---|---|",
    ]
    for index, item in enumerate(sorted(items, key=priority_sort), start=1):
        lines.append(
            f"| {index} | {escape(item['id'])} | {escape(item['title'])} | {escape(item['source'])} "
            f"| {escape(item['status'])} | {escape(item['evidence'])} | {escape(item['action'])} "
            f"| {escape(item['why'])} | {escape(item['owner'])} | {escape(item['links'])} |"
        )
    return "\n".join(lines)


def escape(value):
    return safe_text(value).replace("|", "/").replace("\n", " ")


def source_lines(paths):
    lines = []
    for label, path in paths:
        path_obj = Path(path)
        status = "reviewed" if path_obj.exists() else "not available"
        lines.append(f"- **{label}:** `{path}` {status}")
    return lines


def build_items(max_items):
    govcon_rows = parse_triage_board(DEFAULT_TRIAGE_BOARD)
    govcon_items = [make_govcon_item(row) for row in govcon_rows]

    mybid_rows = read_csv_rows(DEFAULT_MYBIDMATCH_RESOLVED)
    mybid_items = [make_mybidmatch_item(row) for row in mybid_rows]

    items = govcon_items + mybid_items
    return sorted(items, key=priority_sort)[:max_items]


def build_report(items):
    buckets = {
        "Pursue / Price Next": [item for item in items if item["bucket"] == "Pursue / Price Next"],
        "Sources Sought / RFI Actions": [item for item in items if item["bucket"] == "Sources Sought / RFI Actions"],
        "Validate MyBidMatch Matches": [item for item in items if item["bucket"] == "Validate MyBidMatch Matches"],
        "Manual Review / Retry": [item for item in items if item["bucket"] == "Manual Review / Retry"],
        "Park / Pass": [item for item in items if item["bucket"] == "Park / Pass"],
    }
    lines = [
        "# Finalist Action Board",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Executive Summary",
        "",
        f"- **Action items shown:** {len(items)}",
        f"- **Pursue / price next:** {len(buckets['Pursue / Price Next'])}",
        f"- **Sources sought / RFI actions:** {len(buckets['Sources Sought / RFI Actions'])}",
        f"- **Validate MyBidMatch matches:** {len(buckets['Validate MyBidMatch Matches'])}",
        f"- **Manual review / retry:** {len(buckets['Manual Review / Retry'])}",
        f"- **Park / pass:** {len(buckets['Park / Pass'])}",
        "- This board uses local artifacts only. It does not call SAM.gov, USAspending, or document downloaders.",
        "",
        "## Action Board",
        "",
        table(items),
    ]

    for heading in [
        "Pursue / Price Next",
        "Sources Sought / RFI Actions",
        "Validate MyBidMatch Matches",
        "Manual Review / Retry",
        "Park / Pass",
    ]:
        lines.extend(["", f"## {heading}", "", table(buckets[heading])])

    lines.extend([
        "",
        "## Source Files Reviewed",
        "",
        *source_lines([
            ("Triage review pack", DEFAULT_REVIEW_PACK),
            ("Triage board", DEFAULT_TRIAGE_BOARD),
            ("GovCon CSV", DEFAULT_GOVCON_CSV),
            ("Market intel folder", "reports/market_intel"),
            ("Pricing folder", "reports/pricing"),
            ("Opportunity reviews folder", "reports/opportunity_reviews"),
            ("Sources sought folder", "reports/sources_sought"),
            ("Manual review folder", "reports/manual_review"),
            ("MyBidMatch resolved CSV", DEFAULT_MYBIDMATCH_RESOLVED),
            ("MyBidMatch SAM resolution report", "reports/mybidmatch/mybidmatch_sam_resolution.md"),
        ]),
        "",
    ])
    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="Build a daily finalist action board from local GovCon Scout artifacts.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--max-items", type=int, default=25)
    return parser.parse_args()


def main():
    args = parse_args()
    items = build_items(max(1, args.max_items))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(build_report(items), encoding="utf-8")
    print(f"Finalist action board written to: {output}")
    print(f"Action items: {len(items)}")


if __name__ == "__main__":
    main()
