import argparse
import csv
import re
from datetime import datetime
from pathlib import Path


DEFAULT_GOVCON_CSV = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_MYBIDMATCH_RESOLVED = "data/mybidmatch/mybidmatch_resolved.csv"
DEFAULT_OUTPUT = "data/opportunity_state.csv"

STATE_FIELDS = [
    "source",
    "notice_id",
    "title",
    "agency",
    "route",
    "triage_status",
    "processed_status",
    "sources_sought_status",
    "manual_review_status",
    "usaspending_status",
    "pricing_status",
    "bid_price_sanity_status",
    "mybidmatch_resolution_status",
    "recommended_next_action",
    "priority",
    "last_updated",
    "analysis_packet_path",
    "decision_report_path",
    "compliance_matrix_path",
    "pricing_schedule_path",
    "pricing_table_path",
    "usaspending_report_path",
    "bid_price_sanity_path",
    "sources_sought_plan_path",
    "manual_review_path",
]

EARLY_STAGE_TERMS = [
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


def read_csv_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=STATE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def notice_id_for(row):
    return safe_text(
        row.get("notice_id")
        or row.get("solicitation_number")
        or row.get("sam_notice_id")
    )


def artifact_paths(notice_id):
    return {
        "analysis_packet_path": Path("reports/analysis_packets") / f"{notice_id}.md",
        "decision_report_path": Path("reports/opportunity_reviews") / f"{notice_id}_decision_report.md",
        "compliance_matrix_path": Path("reports/opportunity_reviews") / f"{notice_id}_compliance_matrix.md",
        "bid_no_bid_path": Path("reports/opportunity_reviews") / f"{notice_id}_bid_no_bid.md",
        "pricing_schedule_path": Path("reports/pricing") / f"{notice_id}_pricing_schedule.md",
        "pricing_table_path": Path("reports/pricing") / f"{notice_id}_pricing_table.csv",
        "usaspending_report_path": Path("reports/market_intel") / f"{notice_id}_usaspending_intel.md",
        "bid_price_sanity_path": Path("reports/pricing") / f"{notice_id}_bid_price_sanity.md",
        "sources_sought_plan_path": Path("reports/sources_sought") / f"{notice_id}_sources_sought_plan.md",
        "manual_review_path": Path("reports/manual_review") / f"{notice_id}_manual_review.md",
    }


def existing_path(path):
    return str(path) if Path(path).exists() else ""


def artifact_row_paths(notice_id):
    paths = artifact_paths(notice_id)
    return {
        key: existing_path(path)
        for key, path in paths.items()
        if key in STATE_FIELDS
    }


def has_artifact(notice_id, key):
    return artifact_paths(notice_id)[key].exists()


def combined_text(row):
    fields = [
        "title",
        "description",
        "short_description",
        "notice_type",
        "type",
        "solicitation_type",
        "recommendation",
        "conditional_recommendation",
    ]
    return " ".join(safe_text(row.get(field)) for field in fields).lower()


def infer_route(row, notice_id):
    text = combined_text(row)
    if any(term in text for term in EARLY_STAGE_TERMS):
        return "sources_sought"
    if has_artifact(notice_id, "sources_sought_plan_path"):
        return "sources_sought"
    if has_artifact(notice_id, "manual_review_path") or has_artifact(notice_id, "decision_report_path"):
        return "solicitation"
    return "not available"


def infer_processed_status(notice_id):
    has_review = all(
        has_artifact(notice_id, key)
        for key in ["bid_no_bid_path", "decision_report_path", "compliance_matrix_path"]
    )
    if has_review:
        return "processed"
    if has_artifact(notice_id, "decision_report_path") or has_artifact(notice_id, "compliance_matrix_path"):
        return "partially processed"
    return "not available"


def infer_sources_sought_status(notice_id):
    return "plan generated" if has_artifact(notice_id, "sources_sought_plan_path") else "not available"


def infer_manual_review_status(notice_id):
    return "manual review report exists" if has_artifact(notice_id, "manual_review_path") else "not available"


def infer_usaspending_status(notice_id):
    return "report exists" if has_artifact(notice_id, "usaspending_report_path") else "not run"


def infer_pricing_status(notice_id):
    schedule = has_artifact(notice_id, "pricing_schedule_path")
    table = has_artifact(notice_id, "pricing_table_path")
    if schedule and table:
        return "pricing extracted"
    if schedule or table:
        return "partial pricing artifact"
    return "not available"


def first_match(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return ""


def infer_bid_price_sanity_status(notice_id):
    path = artifact_paths(notice_id)["bid_price_sanity_path"]
    if not path.exists():
        return "not run"
    text = path.read_text(encoding="utf-8", errors="replace")
    action = first_match(
        text,
        [
            r"\*\*Recommended next action:\*\*\s*(.+)",
            r"## Recommended Next Action\s+\*\*(.+?)\*\*",
        ],
    )
    return action or "report exists"


def triage_status_for(notice_id):
    if infer_processed_status(notice_id) == "processed":
        return "Processed Successfully"
    if has_artifact(notice_id, "sources_sought_plan_path"):
        return "Sources Sought Plan Generated"
    if has_artifact(notice_id, "manual_review_path"):
        return "Manual Review"
    return "not available"


def recommended_action_for(row, notice_id):
    sanity = infer_bid_price_sanity_status(notice_id)
    route = infer_route(row, notice_id)
    processed = infer_processed_status(notice_id)

    if sanity == "Proceed Only After Vendor/Subcontractor Quote":
        title = safe_text(row.get("title")).lower()
        if "pest" in title:
            return "Get pest-control subcontractor quote before pricing."
        return "Get vendor/subcontractor quote before pricing."
    if sanity == "Pass / Not Priceable Yet":
        return "Park/pass until pricing package or local vendor quote exists."
    if sanity == "Proceed to Pricing Worksheet":
        return "Build pricing worksheet."
    if sanity == "Market Intel Needs More Validation":
        return "Validate market intel before pricing."
    if sanity == "Teaming Recommended Before Pricing":
        return "Validate teaming path before pricing."
    if route == "sources_sought" and has_artifact(notice_id, "sources_sought_plan_path"):
        return "Draft agency questions or sources-sought response."
    if processed == "processed" and infer_usaspending_status(notice_id) == "not run":
        return "Consider finalist USAspending intel if this remains a pursuit candidate."
    if has_artifact(notice_id, "manual_review_path"):
        return "Review manual-review report and decide retry, manual lookup, or pass."
    action = safe_text(row.get("prime_control_recommended_action"))
    if action:
        return action
    return "Review locally before taking further action."


def priority_for(row, notice_id):
    sanity = infer_bid_price_sanity_status(notice_id)
    triage = triage_status_for(notice_id)
    prime = score_int(row.get("prime_reality_score"))
    fit = score_int(row.get("fit_score"))

    if sanity in {"Proceed Only After Vendor/Subcontractor Quote", "Proceed to Pricing Worksheet"}:
        return "high"
    if triage == "Processed Successfully" and infer_usaspending_status(notice_id) == "report exists":
        return "high"
    if triage == "Sources Sought Plan Generated" and (prime >= 50 or fit >= 60):
        return "medium"
    if triage == "Manual Review":
        return "low"
    if prime >= 70:
        return "medium"
    return "not available"


def govcon_state_row(row, last_updated):
    notice_id = notice_id_for(row)
    base = {
        "source": "GovCon Scout",
        "notice_id": notice_id,
        "title": safe_text(row.get("title")),
        "agency": safe_text(row.get("department_ind_agency") or row.get("agency")),
        "route": infer_route(row, notice_id),
        "triage_status": triage_status_for(notice_id),
        "processed_status": infer_processed_status(notice_id),
        "sources_sought_status": infer_sources_sought_status(notice_id),
        "manual_review_status": infer_manual_review_status(notice_id),
        "usaspending_status": infer_usaspending_status(notice_id),
        "pricing_status": infer_pricing_status(notice_id),
        "bid_price_sanity_status": infer_bid_price_sanity_status(notice_id),
        "mybidmatch_resolution_status": "",
        "recommended_next_action": recommended_action_for(row, notice_id),
        "priority": priority_for(row, notice_id),
        "last_updated": last_updated,
    }
    base.update(artifact_row_paths(notice_id))
    return {field: base.get(field, "") for field in STATE_FIELDS}


def mybidmatch_priority(status):
    if status == "Confirmed GovCon Scout Match":
        return "medium"
    if status == "Possible GovCon Scout Match":
        return "medium"
    if status == "Needs Manual Lookup":
        return "low"
    return "not available"


def mybidmatch_route(status):
    if status in {"Confirmed GovCon Scout Match", "Possible GovCon Scout Match"}:
        return "match_validation"
    if status == "State/Local/Non-SAM Lead":
        return "state_local"
    if status == "Needs Manual Lookup":
        return "manual_lookup"
    if status == "Duplicate / Already Covered":
        return "duplicate"
    return "not available"


def mybidmatch_action(status):
    return {
        "Confirmed GovCon Scout Match": "Confirm title/agency, then use existing GovCon Scout record.",
        "Possible GovCon Scout Match": "Validate match manually before processing.",
        "State/Local/Non-SAM Lead": "Park for later state/local workflow.",
        "Needs Manual Lookup": "Open MyBidMatch source and locate source/notice manually.",
        "Duplicate / Already Covered": "Ignore unless new details are present.",
    }.get(status, "Review MyBidMatch source locally.")


def mybidmatch_state_row(row, last_updated):
    status = safe_text(row.get("resolution_status")) or "not available"
    notice_id = safe_text(row.get("matched_notice_id"))
    base = {
        "source": "MyBidMatch",
        "notice_id": notice_id,
        "title": safe_text(row.get("matched_govcon_title") or row.get("mybidmatch_title")),
        "agency": safe_text(row.get("matched_govcon_agency") or row.get("mybidmatch_agency")),
        "route": mybidmatch_route(status),
        "triage_status": "MyBidMatch lead",
        "processed_status": "not available",
        "sources_sought_status": "not available",
        "manual_review_status": "not available",
        "usaspending_status": infer_usaspending_status(notice_id) if notice_id else "not available",
        "pricing_status": infer_pricing_status(notice_id) if notice_id else "not available",
        "bid_price_sanity_status": infer_bid_price_sanity_status(notice_id) if notice_id else "not available",
        "mybidmatch_resolution_status": status,
        "recommended_next_action": safe_text(row.get("recommended_next_action")) or mybidmatch_action(status),
        "priority": mybidmatch_priority(status),
        "last_updated": last_updated,
    }
    if notice_id:
        base.update(artifact_row_paths(notice_id))
    return {field: base.get(field, "") for field in STATE_FIELDS}


def artifact_notice_ids():
    patterns = [
        ("reports/opportunity_reviews", r"(.+)_(?:bid_no_bid|decision_report|compliance_matrix)\.md"),
        ("reports/sources_sought", r"(.+)_sources_sought_plan\.md"),
        ("reports/manual_review", r"(.+)_manual_review\.md"),
        ("reports/pricing", r"(.+)_(?:pricing_schedule|pricing_table|bid_price_sanity)\.(?:md|csv)"),
        ("reports/market_intel", r"(.+)_usaspending_(?:intel|awards)\.(?:md|csv)"),
        ("reports/analysis_packets", r"(.+)\.md"),
    ]
    found = set()
    for folder, pattern in patterns:
        folder_path = Path(folder)
        if not folder_path.exists():
            continue
        for path in folder_path.iterdir():
            if not path.is_file():
                continue
            match = re.match(pattern, path.name)
            if match:
                found.add(match.group(1))
    return found


def artifact_only_row(notice_id, last_updated):
    row = {"notice_id": notice_id, "title": notice_id}
    return govcon_state_row(row, last_updated)


def build_state(govcon_csv, mybidmatch_csv):
    last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    seen_govcon = set()

    for row in read_csv_rows(govcon_csv):
        notice_id = notice_id_for(row)
        if not notice_id:
            continue
        rows.append(govcon_state_row(row, last_updated))
        seen_govcon.add(notice_id)

    for notice_id in sorted(artifact_notice_ids() - seen_govcon):
        rows.append(artifact_only_row(notice_id, last_updated))

    for row in read_csv_rows(mybidmatch_csv):
        rows.append(mybidmatch_state_row(row, last_updated))

    return rows


def parse_args():
    parser = argparse.ArgumentParser(
        description="Build local GovCon Scout opportunity state from existing CSVs and artifacts."
    )
    parser.add_argument("--govcon-csv", default=DEFAULT_GOVCON_CSV)
    parser.add_argument("--mybidmatch-resolved", default=DEFAULT_MYBIDMATCH_RESOLVED)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main():
    args = parse_args()
    rows = build_state(args.govcon_csv, args.mybidmatch_resolved)
    write_csv(args.output, rows)
    print(f"Opportunity state written to: {args.output}")
    print(f"Rows: {len(rows)}")


if __name__ == "__main__":
    main()
