import argparse
import csv
import re
from datetime import datetime
from pathlib import Path


DEFAULT_TRIAGE_BOARD = "reports/triage/govcon_triage_board.md"
DEFAULT_MYBIDMATCH_TRIAGE = "reports/mybidmatch/mybidmatch_triage.md"
DEFAULT_MYBIDMATCH_RESOLVED = "data/mybidmatch/mybidmatch_resolved.csv"
DEFAULT_GOVCON_CSV = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_MYBIDMATCH_CSV = "data/mybidmatch/mybidmatch_opportunities.csv"
DEFAULT_OUTPUT = "reports/triage/govcon_triage_review_pack.md"


SECTION_NAMES = [
    "Pursue Now",
    "Processed Successfully",
    "Sources Sought Plans Generated",
    "Manual Review - No Link/No Download",
    "Manual Review - Likely External Portal/No Attachment",
    "Manual Review - Retry Candidate",
    "Pass/Not Ready",
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


def read_text(path):
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def artifact_path(notice_id, artifact):
    paths = {
        "bid_no_bid": Path("reports/opportunity_reviews") / f"{notice_id}_bid_no_bid.md",
        "decision": Path("reports/opportunity_reviews") / f"{notice_id}_decision_report.md",
        "compliance": Path("reports/opportunity_reviews") / f"{notice_id}_compliance_matrix.md",
        "pricing": Path("reports/pricing") / f"{notice_id}_pricing_schedule.md",
        "pricing_csv": Path("reports/pricing") / f"{notice_id}_pricing_table.csv",
        "sources_sought": Path("reports/sources_sought") / f"{notice_id}_sources_sought_plan.md",
        "manual_review": Path("reports/manual_review") / f"{notice_id}_manual_review.md",
        "analysis_packet": Path("reports/analysis_packets") / f"{notice_id}.md",
        "usaspending_intel": Path("reports/market_intel") / f"{notice_id}_usaspending_intel.md",
        "bid_price_sanity": Path("reports/pricing") / f"{notice_id}_bid_price_sanity.md",
        "bid_price_sanity_check": Path("reports/pricing") / f"{notice_id}_bid_price_sanity_check.md",
    }
    return paths[artifact]


def existing_artifacts(notice_id):
    artifacts = {}
    for key in [
        "bid_no_bid",
        "decision",
        "compliance",
        "pricing",
        "pricing_csv",
        "sources_sought",
        "manual_review",
        "analysis_packet",
        "usaspending_intel",
        "bid_price_sanity",
        "bid_price_sanity_check",
    ]:
        path = artifact_path(notice_id, key)
        if path.exists():
            artifacts[key] = str(path)
    return artifacts


def markdown_link(path, label="open"):
    if not path:
        return ""
    return f"[{label}]({path})"


def split_markdown_row(line):
    line = line.strip()
    if not line.startswith("|") or not line.endswith("|"):
        return []
    return [cell.strip() for cell in line.strip("|").split("|")]


def parse_notice_cell(value):
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", safe_text(value))
    if " - " not in text:
        return text, ""
    notice_id, title = text.split(" - ", 1)
    return notice_id.strip(), title.strip()


def extract_link(value):
    match = re.search(r"\(([^)]+)\)", safe_text(value))
    return match.group(1) if match else ""


def read_csv_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def normalize_lookup_text(value):
    text = safe_text(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def parse_board_table(lines, section):
    items = []
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

        if not in_section:
            continue

        if not line.startswith("|"):
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
        notice_id, title = parse_notice_cell(row.get("Notice", ""))
        if not notice_id:
            continue

        items.append({
            "notice_id": notice_id,
            "title": title,
            "status": row.get("Status", section),
            "fit": score_int(row.get("Fit")),
            "prime": score_int(row.get("Prime")),
            "deadline": row.get("Deadline", ""),
            "action": row.get("Action", ""),
            "board_output": extract_link(row.get("Output", "")),
            "board_section": section,
        })

    return items


def parse_triage_board(path):
    text = read_text(path)
    if not text:
        return {}, {}

    lines = text.splitlines()
    sections = {}
    items_by_notice = {}

    for section in SECTION_NAMES:
        section_items = parse_board_table(lines, section)
        sections[section] = section_items

        for item in section_items:
            notice_id = item["notice_id"]
            if notice_id not in items_by_notice:
                items_by_notice[notice_id] = item
            elif section == "Pursue Now":
                items_by_notice[notice_id]["pursue_now"] = True

            if section == "Pursue Now":
                items_by_notice[notice_id]["pursue_now"] = True

    return sections, items_by_notice


MYBIDMATCH_SECTIONS = ["Priority Review", "Possible Fit", "Teaming/Subcontractor Lead"]


def parse_mybidmatch_table(lines, section):
    items = []
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
        if cells[0] == "Title":
            headers = cells
            continue
        if not headers or len(cells) < len(headers):
            continue

        row = dict(zip(headers, cells))
        title = safe_text(row.get("Title"))
        if not title:
            continue
        items.append({
            "source": "MyBidMatch",
            "title": title,
            "agency": row.get("Agency", ""),
            "source_file": row.get("Source File", ""),
            "source_url": extract_link(row.get("Source URL", "")),
            "lane": row.get("Matched Lane", ""),
            "confidence": row.get("Confidence", ""),
            "reason": row.get("Reason", ""),
            "action": row.get("Recommended Next Action", ""),
            "similar_govcon": row.get("Similar GovCon Title", ""),
            "bucket": section,
        })
    return items


def parse_mybidmatch_triage(path):
    text = read_text(path)
    if not text:
        return {section: [] for section in MYBIDMATCH_SECTIONS}
    lines = text.splitlines()
    return {section: parse_mybidmatch_table(lines, section) for section in MYBIDMATCH_SECTIONS}


def resolution_key(title, source_url="", source_file=""):
    return (
        normalize_lookup_text(title),
        safe_text(source_url),
        safe_text(source_file),
    )


def build_resolution_index(rows):
    index = {}
    for row in rows:
        key = resolution_key(
            row.get("mybidmatch_title"),
            row.get("mybidmatch_source_url"),
            row.get("mybidmatch_source_file"),
        )
        if key[0]:
            index[key] = row
            index[(key[0], "", "")] = row
    return index


def attach_mybidmatch_resolution(item, resolution_index):
    item = dict(item)
    key = resolution_key(item.get("title"), item.get("source_url"), item.get("source_file"))
    resolution = resolution_index.get(key) or resolution_index.get((key[0], "", ""))
    item["resolution"] = resolution or {}
    return item


def mybidmatch_resolution_counts(rows):
    counts = {}
    for row in rows:
        status = safe_text(row.get("resolution_status")) or "Unknown"
        counts[status] = counts.get(status, 0) + 1
    return counts


def infer_title_from_file(path, notice_id):
    text = read_text(path)
    for pattern in [r"\*\*Title:\*\*\s*(.+)", r"- \*\*Title:\*\*\s*(.+)", r"# .+ — .+"]:
        match = re.search(pattern, text)
        if match:
            if pattern.startswith("#"):
                heading = match.group(0).strip("# ").strip()
                return heading.split(" — ", 1)[-1].strip()
            return match.group(1).strip()
    return notice_id


def discover_artifact_items(existing):
    items = {}

    folders = [
        ("reports/opportunity_reviews", r"(.+)_(?:bid_no_bid|decision_report|compliance_matrix)\.md"),
        ("reports/sources_sought", r"(.+)_sources_sought_plan\.md"),
        ("reports/manual_review", r"(.+)_manual_review\.md"),
        ("reports/analysis_packets", r"(.+)\.md"),
    ]

    for folder, pattern in folders:
        folder_path = Path(folder)
        if not folder_path.exists():
            continue

        for path in sorted(folder_path.glob("*.md")):
            match = re.match(pattern, path.name)
            if not match:
                continue

            notice_id = match.group(1)
            if notice_id in existing:
                continue

            status = "Manual review needed - insufficient structured data"
            if artifact_path(notice_id, "sources_sought").exists():
                status = "Sources Sought Plan Generated"
            elif all(artifact_path(notice_id, key).exists() for key in ["bid_no_bid", "decision", "compliance"]):
                status = "Processed Successfully"
            elif artifact_path(notice_id, "manual_review").exists():
                status = "Manual Review - No Link/No Download"

            items[notice_id] = {
                "notice_id": notice_id,
                "title": infer_title_from_file(path, notice_id),
                "status": status,
                "fit": 0,
                "prime": 0,
                "deadline": "",
                "action": "Manual review needed - insufficient structured data.",
                "board_output": str(path),
                "board_section": "Artifact Discovery",
            }

    return items


def first_match(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1).strip()
    return ""


def decision_summary(notice_id):
    text = read_text(artifact_path(notice_id, "decision"))
    if not text:
        return ""
    decision = first_match(text, [r"\*\*Recommended Decision:\*\*\s*(.+)"])
    rationale = first_match(text, [r"\*\*Rationale:\*\*\s*(.+)"])
    if decision and rationale:
        return f"{decision}. {rationale}"
    return decision or rationale


def sources_strategy(notice_id):
    text = read_text(artifact_path(notice_id, "sources_sought"))
    if not text:
        return ""
    strategy = first_match(text, [r"\*\*Recommended Strategy:\*\*\s*(.+)"])
    why = first_match(text, [r"\*\*Why:\*\*\s*(.+)"])
    if strategy and why:
        return f"{strategy}. {why}"
    return strategy or why


def manual_reason(notice_id):
    text = read_text(artifact_path(notice_id, "manual_review"))
    if not text:
        return ""
    reason = first_match(text, [r"- \*\*Reason:\*\*\s*(.+)"])
    details = first_match(text, [r"- \*\*Details:\*\*\s*(.+)"])
    if reason and details:
        return f"{reason} {details}"
    return reason or details


def attach_artifacts(item):
    item = dict(item)
    item["artifacts"] = existing_artifacts(item["notice_id"])
    item["decision_summary"] = decision_summary(item["notice_id"])
    item["sources_strategy"] = sources_strategy(item["notice_id"])
    item["manual_reason"] = manual_reason(item["notice_id"])
    item["intel"] = intel_status(item["notice_id"], item["artifacts"])
    return item


def has_core_processed_artifacts(item):
    artifacts = item.get("artifacts", {})
    return all(key in artifacts for key in ["bid_no_bid", "decision", "compliance"])


def has_full_pricing_artifacts(item):
    artifacts = item.get("artifacts", {})
    return "pricing" in artifacts or "pricing_csv" in artifacts


def is_sources_candidate(item):
    return (
        item["status"] == "Sources Sought Plan Generated"
        and item["prime"] >= 50
        and is_strategic_title(item.get("title", ""))
        and not is_obvious_poor_fit(item.get("title", ""))
    )


def is_retry_candidate(item):
    return "Retry Candidate" in item["status"]


def is_pass_not_ready(item):
    return item["status"] == "Pass/Not Ready"


def candidate_score(item):
    score = item.get("fit", 0) + item.get("prime", 0)
    if item.get("pursue_now"):
        score += 30
    if has_core_processed_artifacts(item):
        score += 30
    if has_full_pricing_artifacts(item):
        score += 15
    if is_sources_candidate(item):
        score += 15
    if item["status"].startswith("Manual Review"):
        score -= 20
    if is_pass_not_ready(item):
        score -= 50
    return score


STRATEGIC_TITLE_TERMS = [
    "advertis",
    "ai ",
    "automation",
    "communications",
    "curriculum",
    "custodial",
    "event",
    "janitor",
    "marketing",
    "media",
    "outreach",
    "pest",
    "publicity",
    "recruit",
    "rfi",
    "server",
    "software",
    "termite",
    "training",
    "technical documentation",
    "trainer",
]

POOR_FIT_TITLE_TERMS = [
    "ammunition",
    "apparel",
    "cups",
    "disposable",
    "food",
    "pharmaceutical",
    "uniform",
    "vehicle purchase",
    "weapon",
]

STRATEGIC_MYBIDMATCH_LANES = {
    "ai_technology_training",
    "facilities_services",
    "janitorial",
    "marketing_communications",
    "pest_control",
}


def is_strategic_title(title):
    title_lower = safe_text(title).lower()
    return any(term in title_lower for term in STRATEGIC_TITLE_TERMS)


def is_obvious_poor_fit(title):
    title_lower = safe_text(title).lower()
    return any(term in title_lower for term in POOR_FIT_TITLE_TERMS)


def artifact_links(item):
    artifacts = item.get("artifacts", {})
    labels = [
        ("decision", "decision"),
        ("compliance", "compliance"),
        ("bid_no_bid", "bid/no-bid"),
        ("pricing", "pricing"),
        ("pricing_csv", "pricing csv"),
        ("usaspending_intel", "usa-spending"),
        ("bid_price_sanity", "bid price sanity"),
        ("bid_price_sanity_check", "sanity check"),
        ("sources_sought", "sources sought"),
        ("manual_review", "manual review"),
        ("analysis_packet", "analysis packet"),
    ]
    links = [markdown_link(artifacts[key], label) for key, label in labels if key in artifacts]
    return ", ".join(links) if links else "Manual review needed - insufficient structured data."


def section_bullets(text, heading):
    bullets = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            section = line.replace("## ", "", 1).strip()
            if in_section and section != heading:
                break
            in_section = section == heading
            continue
        if in_section and line.startswith("- "):
            bullets.append(line)
    return bullets


def clean_bullet_text(line):
    text = re.sub(r"^-\s*", "", safe_text(line))
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    return " ".join(text.split())


def section_body_text(text, heading):
    lines = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## "):
            section = line.replace("## ", "", 1).strip()
            if in_section and section != heading:
                break
            in_section = section == heading
            continue
        if in_section:
            lines.append(line)
    return "\n".join(lines).strip()


def bid_price_sanity_source(notice_id):
    primary = artifact_path(notice_id, "bid_price_sanity")
    if primary.exists():
        return primary
    fallback = artifact_path(notice_id, "bid_price_sanity_check")
    if fallback.exists():
        return fallback
    return None


def bid_price_next_action(status):
    status_lower = safe_text(status).lower()
    if "vendor" in status_lower or "subcontractor quote" in status_lower:
        return "Get vendor quote"
    if "pass" in status_lower and "not priceable" in status_lower:
        return "Park unless pricing docs are obtained"
    if "not priceable" in status_lower:
        return "Obtain pricing docs before pricing"
    return safe_text(status) or "No pricing sanity action available"


def bid_price_sanity_summary(notice_id):
    path = bid_price_sanity_source(notice_id)
    if not path:
        return {
            "exists": False,
            "status": "Not run",
            "recommended_next_action": "No bid price sanity report available",
            "risk_summary": "Pending",
            "source_path": "",
        }

    text = read_text(path)
    status = first_match(text, [
        r"- \*\*Recommended next action:\*\*\s*(.+)",
        r"\*\*Recommended next action:\*\*\s*(.+)",
    ])
    if not status:
        action_section = section_body_text(text, "Recommended Next Action")
        status = first_match(action_section, [r"\*\*(.+?)\*\*"]) or "Report available"

    risk_flags = [
        clean_bullet_text(line)
        for line in section_bullets(text, "Pricing Risk Flags")
    ]
    risk_summary = " ".join(risk_flags[:3]) if risk_flags else "No pricing risk flags found."

    return {
        "exists": True,
        "status": status,
        "recommended_next_action": bid_price_next_action(status),
        "risk_summary": risk_summary,
        "source_path": str(path),
    }


def usaspending_award_count(notice_id, text):
    match = re.search(r"\*\*Awards found:\*\*\s*(\d+)", text)
    if match:
        return score_int(match.group(1))

    csv_path = Path("reports/market_intel") / f"{notice_id}_usaspending_awards.csv"
    if not csv_path.exists():
        return None
    with csv_path.open("r", encoding="utf-8", newline="") as file:
        return sum(1 for _row in csv.DictReader(file))


def usaspending_top_recipients(text, limit=3):
    recipients = []
    for bullet in section_bullets(text, "Top Recipients / Possible Incumbents"):
        match = re.match(r"- \*\*(.+?):\*\*", bullet)
        if not match:
            continue
        recipients.append(match.group(1).strip())
        if len(recipients) >= limit:
            break
    return recipients


def market_intel_posture(item, intel):
    if not intel["report_exists"] or not intel["award_count"]:
        return "Further Validation"
    if item.get("intel", {}).get("usaspending_data_quality_status") == "warning":
        return "Further Validation"
    if item.get("status") == "Sources Sought Plan Generated":
        return "Teaming"
    if not has_core_processed_artifacts(item) or not has_full_pricing_artifacts(item):
        return "Further Validation"
    if item.get("prime", 0) >= 60 and "hold" not in item_note(item).lower():
        return "Prime"
    return "Further Validation"


def usaspending_queue_intel(item):
    if not item.get("notice_id"):
        return {
            "report_exists": False,
            "award_count": None,
            "top_recipients": [],
            "posture": "Further Validation",
        }

    path = artifact_path(item["notice_id"], "usaspending_intel")
    text = read_text(path)
    intel = {
        "report_exists": bool(text),
        "award_count": usaspending_award_count(item["notice_id"], text),
        "top_recipients": usaspending_top_recipients(text),
    }
    intel["posture"] = market_intel_posture(item, intel)
    return intel


def recipient_summary(recipients):
    if not recipients:
        return "Pending"
    return ", ".join(recipients)


def mybidmatch_score(item):
    score = 0
    if item.get("confidence", "").lower() == "high":
        score += 30
    elif item.get("confidence", "").lower() == "medium":
        score += 15
    if item.get("lane") in STRATEGIC_MYBIDMATCH_LANES:
        score += 20
    if is_strategic_title(item.get("title", "")):
        score += 20
    if item.get("similar_govcon"):
        score -= 25
    if is_obvious_poor_fit(item.get("title", "")):
        score -= 50
    return score


def mybidmatch_related_outputs(item):
    links = []
    source_url = item.get("source_url", "")
    source_file = Path("data/mybidmatch/raw") / item.get("source_file", "")
    if source_url:
        links.append(markdown_link(source_url, "source URL"))
    if item.get("source_file") and source_file.exists():
        links.append(markdown_link(str(source_file), "source file"))
    if item.get("similar_govcon"):
        links.append(f"Similar GovCon title: {item['similar_govcon']}")
    return ", ".join(links) if links else "Manual review needed - insufficient structured data."


def mybidmatch_resolution_text(item, field, default=""):
    resolution = item.get("resolution") or {}
    return safe_text(resolution.get(field)) or default


def mybidmatch_table(items, include_resolution=False):
    if not items:
        return "None."

    if include_resolution:
        lines = [
            "| Title | Agency | Lane | Resolution | Matched Notice | Matched Title | Score | Recommended Next Action | Source / Related Output |",
            "|---|---|---|---|---|---|---:|---|---|",
        ]
    else:
        lines = [
            "| Title | Agency | Lane | Confidence | Recommended Next Action | Source / Related Output |",
            "|---|---|---|---|---|---|",
        ]

    for item in sorted(items, key=mybidmatch_score, reverse=True):
        title = safe_text(item.get("title")).replace("|", "/")
        agency = safe_text(item.get("agency")).replace("|", "/")
        lane = safe_text(item.get("lane")).replace("|", "/")
        related = mybidmatch_related_outputs(item).replace("|", "/")
        if include_resolution:
            status = mybidmatch_resolution_text(item, "resolution_status", "Not resolved").replace("|", "/")
            notice = mybidmatch_resolution_text(item, "matched_notice_id", "—").replace("|", "/")
            matched_title = mybidmatch_resolution_text(item, "matched_govcon_title", "—").replace("|", "/")
            score = mybidmatch_resolution_text(item, "similarity_score", "—")
            action = mybidmatch_resolution_text(
                item,
                "recommended_next_action",
                safe_text(item.get("action")) or "Manual lookup required",
            ).replace("|", "/")
            lines.append(
                f"| {title} | {agency} | {lane} | {status} | {notice} | "
                f"{matched_title} | {score} | {action} | {related} |"
            )
        else:
            confidence = safe_text(item.get("confidence")).replace("|", "/") or "unknown"
            action = safe_text(item.get("action")).replace("|", "/")
            lines.append(f"| {title} | {agency} | {lane} | {confidence} | {action} | {related} |")
    return "\n".join(lines)


QUALITY_WARNING_PHRASES = [
    "DATA QUALITY WARNING",
    "NOT RELIABLE",
    "unrelated contracts",
    "award range unreliable",
    "figures below should not be used",
]


def detect_usaspending_data_quality(notice_id):
    """Scan intel and sanity check reports for data quality warning phrases.

    Returns 'warning', 'clean', or 'no_data'.
    """
    intel_text = read_text(artifact_path(notice_id, "usaspending_intel"))
    sanity_text = read_text(artifact_path(notice_id, "bid_price_sanity"))
    check_text = read_text(artifact_path(notice_id, "bid_price_sanity_check"))
    check_text = sanity_text + check_text
    combined = (intel_text + check_text).upper()

    if not intel_text and not check_text:
        return "no_data"

    for phrase in QUALITY_WARNING_PHRASES:
        if phrase.upper() in combined:
            return "warning"

    return "clean"


def intel_status(notice_id, artifacts):
    pricing_exists = "pricing" in artifacts
    pricing_csv_exists = "pricing_csv" in artifacts
    usaspending_exists = "usaspending_intel" in artifacts
    sanity_check_exists = "bid_price_sanity" in artifacts or "bid_price_sanity_check" in artifacts
    dq_status = detect_usaspending_data_quality(notice_id)

    has_core = all(key in artifacts for key in ["bid_no_bid", "decision", "compliance"])
    sources_exists = "sources_sought" in artifacts
    manual_exists = "manual_review" in artifacts

    if has_core and (pricing_exists or pricing_csv_exists) and usaspending_exists and sanity_check_exists and dq_status == "clean":
        action = "Ready for Subcontractor Quotes / Pricing Review"
    elif has_core and usaspending_exists and dq_status == "warning":
        action = "Needs Better Market Data"
    elif has_core and (pricing_exists or pricing_csv_exists) and not usaspending_exists:
        action = "Run USAspending Intel"
    elif has_core and not pricing_exists and not pricing_csv_exists:
        action = "Extract / Review Pricing Schedule"
    elif sources_exists:
        action = "Response Strategy Review"
    elif manual_exists:
        action = "Manual Trace / Downloader Review"
    elif has_core:
        action = "Run USAspending Intel"
    else:
        action = "Needs Processing"

    return {
        "pricing_schedule_exists": pricing_exists,
        "pricing_table_exists": pricing_csv_exists,
        "usaspending_intel_exists": usaspending_exists,
        "bid_price_sanity_check_exists": sanity_check_exists,
        "usaspending_data_quality_status": dq_status,
        "recommended_next_action": action,
    }


def item_note(item):
    if item.get("decision_summary"):
        return item["decision_summary"]
    if item.get("sources_strategy"):
        return item["sources_strategy"]
    if item.get("manual_reason"):
        return item["manual_reason"]
    return item.get("action") or "Manual review needed - insufficient structured data."


def is_ready_for_pricing(item):
    return item.get("intel", {}).get("recommended_next_action") == "Ready for Subcontractor Quotes / Pricing Review"


def needs_better_data(item):
    return item.get("intel", {}).get("recommended_next_action") == "Needs Better Market Data"


def has_any_intel(item):
    intel = item.get("intel", {})
    return any([
        intel.get("pricing_schedule_exists"),
        intel.get("pricing_table_exists"),
        intel.get("usaspending_intel_exists"),
        intel.get("bid_price_sanity_check_exists"),
    ])


def checkmark(val):
    return "Yes" if val else "—"


def dq_label(status):
    if status == "clean":
        return "Clean"
    if status == "warning":
        return "WARNING"
    return "No data"


def finalist_intel_table(items):
    relevant = [item for item in items if has_core_processed_artifacts(item) or has_any_intel(item)]
    relevant = sorted(relevant, key=candidate_score, reverse=True)

    if not relevant:
        return "No processed or intel-enriched items found."

    lines = [
        "| Notice | Pricing | Pricing CSV | USAspending | Sanity Check | Data Quality | Next Action |",
        "|---|:---:|:---:|:---:|:---:|---|---|",
    ]
    for item in relevant:
        intel = item.get("intel", {})
        notice = f"{item['notice_id']} — {item['title']}".replace("|", "\\|")
        lines.append(
            f"| {notice} "
            f"| {checkmark(intel.get('pricing_schedule_exists'))} "
            f"| {checkmark(intel.get('pricing_table_exists'))} "
            f"| {checkmark(intel.get('usaspending_intel_exists'))} "
            f"| {checkmark(intel.get('bid_price_sanity_check_exists'))} "
            f"| {dq_label(intel.get('usaspending_data_quality_status', 'no_data'))} "
            f"| {intel.get('recommended_next_action', '—')} |"
        )
    return "\n".join(lines)


def item_table(items):
    lines = [
        "| Notice | Status | Fit | Prime | Deadline | Practical Note | Related Outputs |",
        "|---|---:|---:|---:|---|---|---|",
    ]

    for item in sorted(items, key=candidate_score, reverse=True):
        notice = f"{item['notice_id']} - {item['title']}".replace("|", "\\|")
        note = item_note(item).replace("|", "/")
        links = artifact_links(item).replace("|", "/")
        lines.append(
            f"| {notice} | {item['status']} | {item['fit']} | {item['prime']} | "
            f"{item['deadline']} | {note} | {links} |"
        )

    return "\n".join(lines) if items else "None."


def is_mybidmatch_queue_candidate(item):
    resolution = item.get("resolution") or {}
    status = resolution.get("resolution_status")
    if status == "Confirmed GovCon Scout Match":
        return True
    if status == "Possible GovCon Scout Match":
        return (
            item.get("bucket") == "Priority Review"
            and item.get("confidence", "").lower() == "high"
            and item.get("lane") in STRATEGIC_MYBIDMATCH_LANES
            and is_strategic_title(item.get("title", ""))
            and not is_obvious_poor_fit(item.get("title", ""))
        )
    if status in {
        "State/Local/Non-SAM Lead",
        "Needs Manual Lookup",
        "Duplicate / Already Covered",
    }:
        return False

    return (
        item.get("bucket") == "Priority Review"
        and item.get("confidence", "").lower() == "high"
        and item.get("lane") in STRATEGIC_MYBIDMATCH_LANES
        and not item.get("similar_govcon")
        and is_strategic_title(item.get("title", ""))
        and not is_obvious_poor_fit(item.get("title", ""))
    )


def build_usaspending_queue(items, mybidmatch_items, max_items):
    queue = []
    queued_notice_ids = set()

    for item in items:
        if is_pass_not_ready(item):
            continue

        if has_core_processed_artifacts(item) and has_full_pricing_artifacts(item):
            queue.append((
                item,
                "Processed package has decision, compliance, and pricing artifacts. "
                "Consider USAspending to validate historical award context.",
            ))
            queued_notice_ids.add(item.get("notice_id"))
            continue

        if has_core_processed_artifacts(item):
            queue.append((
                item,
                "Processed package has decision and compliance artifacts. "
                "Review award history before deeper pursuit work.",
            ))
            queued_notice_ids.add(item.get("notice_id"))
            continue

        if is_sources_candidate(item):
            queue.append((
                item,
                "Early-stage item appears strategically relevant. "
                "Consider incumbent and buying-pattern research before response shaping.",
            ))
            queued_notice_ids.add(item.get("notice_id"))

    for item in mybidmatch_items:
        if is_mybidmatch_queue_candidate(item):
            resolution = item.get("resolution") or {}
            status = resolution.get("resolution_status")
            matched_notice_id = safe_text(resolution.get("matched_notice_id"))
            if status == "Confirmed GovCon Scout Match" and matched_notice_id:
                if matched_notice_id in queued_notice_ids:
                    continue
                mapped = {
                    "notice_id": matched_notice_id,
                    "title": safe_text(resolution.get("matched_govcon_title")) or item.get("title", ""),
                    "status": "Confirmed MyBidMatch/GovCon Match",
                    "fit": 0,
                    "prime": 0,
                    "deadline": "",
                    "action": "Use matched GovCon Scout record; requires validation before deeper research.",
                    "artifacts": existing_artifacts(matched_notice_id),
                    "intel": intel_status(matched_notice_id, existing_artifacts(matched_notice_id)),
                    "resolution": resolution,
                }
                queue.append((
                    mapped,
                    "Confirmed MyBidMatch match to an existing GovCon Scout notice. "
                    "Use the matched notice ID and validate scope before USAspending.",
                ))
                queued_notice_ids.add(matched_notice_id)
                continue

            qualifier = " requires match validation" if status == "Possible GovCon Scout Match" else ""
            queue.append((
                item,
                "MyBidMatch priority lead aligns with a strategic lane. "
                f"Trace the source identifier first; deeper award research{qualifier}.",
            ))

    queue = sorted(
        queue,
        key=lambda pair: candidate_score(pair[0]) if pair[0].get("notice_id") else mybidmatch_score(pair[0]),
        reverse=True,
    )
    return queue[:max_items]


def queue_table(queue):
    if not queue:
        return "No conservative USAspending candidates found yet."

    lines = [
        "| Priority | Candidate | USAspending | Awards | Top Recipients | Bid Price Sanity | Recommended Next Action | Pricing Risk Summary | Bid Price Sanity Source | Related Outputs |",
        "|---:|---|---|---:|---|---|---|---|---|---|",
    ]

    for index, (item, reason) in enumerate(queue, start=1):
        if item.get("notice_id"):
            candidate = f"{item['notice_id']} - {item['title']}".replace("|", "\\|")
            related = artifact_links(item)
            pricing = bid_price_sanity_summary(item["notice_id"])
        else:
            candidate = f"MyBidMatch - {item['title']}".replace("|", "\\|")
            related = mybidmatch_related_outputs(item)
            pricing = {
                "status": "Not run",
                "recommended_next_action": "Validate source identifier first",
                "risk_summary": reason,
                "source_path": "",
            }
        intel = usaspending_queue_intel(item)
        market_intel = "Ready" if intel["report_exists"] else "Not run"
        award_count = intel["award_count"] if intel["award_count"] is not None else "Pending"
        recipients = recipient_summary(intel["top_recipients"]).replace("|", "/")
        pricing_status = pricing["status"].replace("|", "/")
        pricing_action = pricing["recommended_next_action"].replace("|", "/")
        pricing_risk = pricing["risk_summary"].replace("|", "/")
        pricing_source = (
            markdown_link(pricing["source_path"], pricing["source_path"])
            if pricing.get("source_path")
            else "Pending"
        )
        lines.append(
            f"| {index} | {candidate} | {market_intel} | {award_count} | {recipients} "
            f"| {pricing_status} | {pricing_action} | {pricing_risk} | {pricing_source} | {related} |"
        )

    return "\n".join(lines)


def count_by_status(items):
    counts = {}
    for item in items:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return counts


def newest_batch_report():
    folder = Path("reports/batch_runs")
    if not folder.exists():
        return ""
    reports = sorted(folder.glob("process_shortlist_*.md"))
    return str(reports[-1]) if reports else ""


def source_review_lines(source_paths):
    lines = []
    for label, path, rows in source_paths:
        path_obj = Path(path)
        status = "reviewed" if path_obj.exists() else "not available"
        detail = f"; {rows} rows" if rows is not None and path_obj.exists() else ""
        lines.append(f"- **{label}:** `{path}` {status}{detail}")
    return lines


def mybidmatch_resolution_summary(rows):
    counts = mybidmatch_resolution_counts(rows)
    statuses = [
        "Confirmed GovCon Scout Match",
        "Possible GovCon Scout Match",
        "State/Local/Non-SAM Lead",
        "Needs Manual Lookup",
        "Duplicate / Already Covered",
    ]
    lines = []
    for status in statuses:
        lines.append(f"- **{status}:** {counts.get(status, 0)}")
    if not rows:
        lines.append("- Resolution data not available yet.")
    return "\n".join(lines)


def mybidmatch_next_actions():
    return "\n".join([
        "- **Confirmed GovCon Scout matches:** use the existing GovCon Scout workflow and matched notice ID.",
        "- **Possible matches:** manually verify title and agency before processing.",
        "- **State/local leads:** hold for a later state/local workflow; not ready for SAM.gov automation.",
        "- **Manual lookup:** open the MyBidMatch source and locate the source/origin record.",
        "- **Duplicates:** ignore unless the MyBidMatch source contains new details not already captured.",
    ])


def build_review_pack(
    items,
    triage_board,
    mybidmatch_triage,
    mybidmatch_resolution_rows,
    mybidmatch_path,
    mybidmatch_resolution_path,
    govcon_csv,
    govcon_csv_rows,
    mybidmatch_csv,
    mybidmatch_csv_rows,
    max_usaspending,
    max_mybidmatch_priority,
    max_mybidmatch_possible,
):
    counts = count_by_status(items)
    pursuit = [item for item in items if item.get("pursue_now")]
    processed = [item for item in items if has_core_processed_artifacts(item)]
    sources = [item for item in items if item["status"] == "Sources Sought Plan Generated"]
    manual = [item for item in items if item["status"].startswith("Manual Review")]
    retry = [item for item in manual if is_retry_candidate(item)]
    pass_items = [item for item in items if is_pass_not_ready(item)]
    mybidmatch_priority = mybidmatch_triage.get("Priority Review", [])
    mybidmatch_possible = mybidmatch_triage.get("Possible Fit", [])
    resolution_index = build_resolution_index(mybidmatch_resolution_rows)
    mybidmatch_priority = [attach_mybidmatch_resolution(item, resolution_index) for item in mybidmatch_priority]
    mybidmatch_possible = [attach_mybidmatch_resolution(item, resolution_index) for item in mybidmatch_possible]
    usaspending_queue = build_usaspending_queue(items, mybidmatch_priority, max_usaspending)
    manual_retry_combined = sorted(
        {item["notice_id"]: item for item in (manual + retry)}.values(),
        key=candidate_score, reverse=True,
    )

    reviewed_sources = [
        ("GovCon triage board", triage_board, None),
        ("MyBidMatch triage", mybidmatch_path, None),
        ("MyBidMatch SAM resolution CSV", mybidmatch_resolution_path, len(mybidmatch_resolution_rows)),
        ("GovCon opportunities CSV", govcon_csv, len(govcon_csv_rows)),
        ("MyBidMatch opportunities CSV", mybidmatch_csv, len(mybidmatch_csv_rows)),
        ("Opportunity reviews folder", "reports/opportunity_reviews", None),
        ("Sources sought folder", "reports/sources_sought", None),
        ("Manual review folder", "reports/manual_review", None),
        ("Pricing folder", "reports/pricing", None),
        ("Batch runs folder", "reports/batch_runs", None),
        ("Analysis packets folder", "reports/analysis_packets", None),
    ]

    lines = [
        "# GovCon Scout Triage Review Pack",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Triage Board:** `{triage_board}`" if Path(triage_board).exists() else f"**Triage Board:** `{triage_board}` not found",
        f"**Latest Batch Report:** `{newest_batch_report() or 'None found'}`",
        "",
        "## Executive Summary",
        "",
        f"- **GovCon Scout items reviewed:** {len(items)}",
        f"- **GovCon pursuit candidates:** {len(pursuit)}",
        f"- **Processed solicitations (full review package):** {len(processed)}",
        f"- **Sources sought / RFI candidates:** {len(sources)}",
        f"- **Manual review / retry candidates:** {len(manual)}",
        f"- **Pass / not ready:** {len(pass_items)}",
        f"- **MyBidMatch Priority Review leads:** {len(mybidmatch_priority)}",
        f"- **MyBidMatch Possible Fit leads:** {len(mybidmatch_possible)}",
        f"- **MyBidMatch SAM-resolved rows:** {len(mybidmatch_resolution_rows)}",
        f"- **Recommended USAspending queue size:** {len(usaspending_queue)}",
        "",
        "Use this pack to decide where deeper award intelligence is worth the time. "
        "It does not estimate win probability, and MyBidMatch leads still require source validation when a notice ID is missing.",
        "",
        "## GovCon Scout Pursuit Candidates",
        "",
        "Candidates surfaced by the GovCon triage board for human pursuit review.",
        "",
        item_table(pursuit) if pursuit else "None.",
        "",
        "## Processed Solicitations Ready for Deeper Intel",
        "",
        "Processed solicitation candidates with the decision, bid/no-bid, and compliance outputs already available.",
        "",
        item_table(processed) if processed else "None.",
        "",
        "## Sources Sought / RFI Response Candidates",
        "",
        item_table(sources) if sources else "None.",
        "",
        "## MyBidMatch SAM Resolution Summary",
        "",
        mybidmatch_resolution_summary(mybidmatch_resolution_rows),
        "",
        "## MyBidMatch Priority Review Leads",
        "",
        "Priority daily-list leads worth source/origin review before deeper automation or market research.",
        "",
        mybidmatch_table(mybidmatch_priority[:max_mybidmatch_priority], include_resolution=True),
        "",
        "## MyBidMatch Possible Fit Leads",
        "",
        "Leads that may fit after detail-page validation, source tracing, and scope review.",
        "",
        mybidmatch_table(mybidmatch_possible[:max_mybidmatch_possible]),
        "",
        "## Manual Review / Retry Candidates",
        "",
        item_table(manual_retry_combined) if manual_retry_combined else "None.",
        "",
        "## Pass / Not Ready",
        "",
        item_table(pass_items) if pass_items else "None.",
        "",
        "## Recommended USAspending Queue",
        "",
        "This queue is intentionally small. MyBidMatch rows without a confirmed identifier remain validation-first candidates.",
        "",
        queue_table(usaspending_queue),
        "",
        "## Recommended Next Actions",
        "",
        "1. Review processed GovCon solicitation packages first; pricing and compliance outputs make award research more actionable.",
        "2. Consider USAspending for the recommended queue only after confirming the candidate scope and source record.",
        "3. For sources-sought/RFI items, review the response plan and validate likely agency buying patterns before outreach.",
        "4. Trace MyBidMatch Priority Review leads to the article or origin source before attempting GovCon processing.",
        "5. Retry manual-review items only when live/session or downloader evidence suggests the opportunity can be recovered.",
        "6. Keep pass/not-ready and weak MyBidMatch items out of deeper research until requirements become clearer.",
        "",
        "### MyBidMatch Next Actions",
        "",
        mybidmatch_next_actions(),
        "",
        "## Source Files Reviewed",
        "",
        *source_review_lines(reviewed_sources),
        "",
    ]

    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="Build a combined GovCon Scout and MyBidMatch triage review pack.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--triage-board", default=DEFAULT_TRIAGE_BOARD)
    parser.add_argument("--mybidmatch-triage", default=DEFAULT_MYBIDMATCH_TRIAGE)
    parser.add_argument("--mybidmatch-resolution", default=DEFAULT_MYBIDMATCH_RESOLVED)
    parser.add_argument("--govcon-csv", default=DEFAULT_GOVCON_CSV)
    parser.add_argument("--mybidmatch-csv", default=DEFAULT_MYBIDMATCH_CSV)
    parser.add_argument("--max-usaspending", type=int, default=10)
    parser.add_argument("--max-mybidmatch-priority", type=int, default=25)
    parser.add_argument("--max-mybidmatch-possible", type=int, default=25)
    return parser.parse_args()


def main():
    args = parse_args()
    _sections, board_items = parse_triage_board(args.triage_board)
    mybidmatch_triage = parse_mybidmatch_triage(args.mybidmatch_triage)
    mybidmatch_resolution_rows = read_csv_rows(args.mybidmatch_resolution)
    govcon_csv_rows = read_csv_rows(args.govcon_csv)
    mybidmatch_csv_rows = read_csv_rows(args.mybidmatch_csv)
    discovered = discover_artifact_items(board_items)

    merged = {**discovered, **board_items}
    items = [attach_artifacts(item) for item in merged.values()]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_review_pack(
            items,
            args.triage_board,
            mybidmatch_triage,
            mybidmatch_resolution_rows,
            args.mybidmatch_triage,
            args.mybidmatch_resolution,
            args.govcon_csv,
            govcon_csv_rows,
            args.mybidmatch_csv,
            mybidmatch_csv_rows,
            max(1, args.max_usaspending),
            max(1, args.max_mybidmatch_priority),
            max(1, args.max_mybidmatch_possible),
        ),
        encoding="utf-8",
    )

    print(f"Triage review pack written to: {output_path}")


if __name__ == "__main__":
    main()
