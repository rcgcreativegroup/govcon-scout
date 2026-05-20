import csv
from datetime import datetime
from pathlib import Path


PIPELINE_FIELDS = [
    "notice_id",
    "sam_notice_id",
    "title",
    "agency",
    "matched_lane",
    "fit_score",
    "prime_reality_score",
    "recommendation",
    "conditional_recommendation",
    "status",
    "next_action",
    "owner",
    "due_date_user_local",
    "deadline_status",
    "compliance_risk",
    "rfi_needed",
    "set_aside_hard_gate",
    "has_resource_links",
    "resource_link_count",
    "ui_link",
    "notes",
    "last_updated",
]


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def score_value(opp, field_name):
    try:
        return int(opp.get(field_name, 0) or 0)
    except (TypeError, ValueError):
        return 0


def read_existing_pipeline(path):
    if not path.exists():
        return {}

    existing = {}

    try:
        with open(path, "r", encoding="utf-8", newline="") as file:
            reader = csv.DictReader(file)

            for row in reader:
                notice_id = safe_text(row.get("notice_id"))
                if notice_id:
                    existing[notice_id] = row

    except OSError:
        return {}

    return existing


def determine_status(opp):
    if opp.get("notice_actionability") == "awarded_market_intel":
        return "Award Intel"

    if opp.get("notice_actionability") != "actionable":
        return "Pass"

    if opp.get("deadline_status") in ["overdue_or_archived"]:
        return "Pass"

    if opp.get("set_aside_hard_gate") == "Yes":
        return "Teaming Target"

    if opp.get("rfi_needed") == "Yes":
        return "RFI Needed"

    if score_value(opp, "prime_reality_score") >= 70:
        return "Review"

    if score_value(opp, "fit_score") >= 60 and score_value(opp, "prime_reality_score") < 50:
        return "Teaming Target"

    if score_value(opp, "fit_score") >= 50:
        return "Watch"

    return "Pass"


def determine_next_action(opp, status):
    if status == "Award Intel":
        return "Review awardee, value, agency pattern, and possible recompete/team target."

    if status == "Pass":
        return "No immediate action."

    if opp.get("set_aside_hard_gate") == "Yes":
        return "Identify eligible prime or similarly situated teaming partner before pursuing."

    if opp.get("rfi_needed") == "Yes":
        return "Draft RFI and confirm requirement before bid/no-bid."

    if opp.get("compliance_risk") == "High":
        return "Review solicitation instructions, forms, amendments, and submission requirements."

    if str(opp.get("has_resource_links", "")).lower() == "yes":
        return "Open attachments and review PWS, SF1449, pricing, amendments, and evaluation criteria."

    if score_value(opp, "prime_reality_score") >= 70:
        return "Analyze as prime candidate and prepare bid/no-bid decision."

    if status == "Teaming Target":
        return "Find prime or subcontracting angle."

    return "Review opportunity details and decide pursue/watch/pass."


def build_pipeline_row(opp, existing_row=None):
    existing_row = existing_row or {}

    notice_id = safe_text(opp.get("notice_id") or opp.get("sam_notice_id"))
    status = safe_text(existing_row.get("status")) or determine_status(opp)
    next_action = safe_text(existing_row.get("next_action")) or determine_next_action(opp, status)

    return {
        "notice_id": notice_id,
        "sam_notice_id": safe_text(opp.get("sam_notice_id")),
        "title": safe_text(opp.get("title")),
        "agency": safe_text(opp.get("department_ind_agency")),
        "matched_lane": safe_text(opp.get("matched_lane")),
        "fit_score": safe_text(opp.get("fit_score")),
        "prime_reality_score": safe_text(opp.get("prime_reality_score")),
        "recommendation": safe_text(opp.get("recommendation")),
        "conditional_recommendation": safe_text(opp.get("conditional_recommendation")),
        "status": status,
        "next_action": next_action,
        "owner": safe_text(existing_row.get("owner")),
        "due_date_user_local": safe_text(opp.get("due_date_user_local")),
        "deadline_status": safe_text(opp.get("deadline_status")),
        "compliance_risk": safe_text(opp.get("compliance_risk")),
        "rfi_needed": safe_text(opp.get("rfi_needed")),
        "set_aside_hard_gate": safe_text(opp.get("set_aside_hard_gate")),
        "has_resource_links": safe_text(opp.get("has_resource_links")),
        "resource_link_count": safe_text(opp.get("resource_link_count")),
        "ui_link": safe_text(opp.get("ui_link")),
        "notes": safe_text(existing_row.get("notes")),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def should_include_in_pipeline(opp):
    if opp.get("notice_actionability") == "awarded_market_intel":
        return True

    if opp.get("notice_actionability") != "actionable":
        return False

    if score_value(opp, "fit_score") >= 50:
        return True

    if opp.get("rfi_needed") == "Yes":
        return True

    if opp.get("set_aside_hard_gate") == "Yes":
        return True

    if str(opp.get("has_resource_links", "")).lower() == "yes":
        return True

    return False


def update_pipeline(scored_opportunities, output_path="data/pipeline.csv", limit=150):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = read_existing_pipeline(path)

    pipeline_candidates = [
        opp for opp in scored_opportunities
        if should_include_in_pipeline(opp)
    ]

    pipeline_candidates = sorted(
        pipeline_candidates,
        key=lambda opp: (
            score_value(opp, "prime_reality_score"),
            score_value(opp, "fit_score"),
        ),
        reverse=True,
    )[:limit]

    rows = []

    seen_notice_ids = set()

    for opp in pipeline_candidates:
        notice_id = safe_text(opp.get("notice_id") or opp.get("sam_notice_id"))
        if not notice_id or notice_id in seen_notice_ids:
            continue

        rows.append(
            build_pipeline_row(
                opp=opp,
                existing_row=existing.get(notice_id),
            )
        )
        seen_notice_ids.add(notice_id)

    # Preserve manually tracked records that no longer appear in today’s scan,
    # unless they were empty or malformed.
    for notice_id, old_row in existing.items():
        if notice_id not in seen_notice_ids:
            preserved = {field: safe_text(old_row.get(field)) for field in PIPELINE_FIELDS}
            rows.append(preserved)

    with open(path, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=PIPELINE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    return str(path)