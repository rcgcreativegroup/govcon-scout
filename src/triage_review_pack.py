import argparse
import re
from datetime import datetime
from pathlib import Path


DEFAULT_TRIAGE_BOARD = "reports/triage/govcon_triage_board.md"
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
    return item["status"] == "Sources Sought Plan Generated" and item["prime"] >= 50


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


def artifact_links(item):
    artifacts = item.get("artifacts", {})
    labels = [
        ("decision", "decision"),
        ("compliance", "compliance"),
        ("bid_no_bid", "bid/no-bid"),
        ("pricing", "pricing"),
        ("pricing_csv", "pricing csv"),
        ("usaspending_intel", "usa-spending"),
        ("bid_price_sanity_check", "sanity check"),
        ("sources_sought", "sources sought"),
        ("manual_review", "manual review"),
        ("analysis_packet", "analysis packet"),
    ]
    links = [markdown_link(artifacts[key], label) for key, label in labels if key in artifacts]
    return ", ".join(links) if links else "Manual review needed - insufficient structured data."


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
    check_text = read_text(artifact_path(notice_id, "bid_price_sanity_check"))
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
    sanity_check_exists = "bid_price_sanity_check" in artifacts
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


def build_usaspending_queue(items):
    queue = []

    for item in items:
        if is_pass_not_ready(item):
            continue

        if has_core_processed_artifacts(item) and has_full_pricing_artifacts(item):
            queue.append((item, "Processed package has decision/compliance/pricing artifacts; use USAspending to validate pricing and incumbent context."))
            continue

        if is_sources_candidate(item):
            queue.append((item, "Early-stage item appears strategically relevant; use USAspending to identify likely incumbents, primes, and agency buying patterns."))
            continue

        if is_retry_candidate(item) and item.get("prime", 0) >= 60:
            queue.append((item, "Retry-worthy manual item with enough score to consider after one more live review."))

    queue = sorted(queue, key=lambda pair: candidate_score(pair[0]), reverse=True)
    return queue[:8]


def queue_table(queue):
    if not queue:
        return "No conservative USAspending candidates found yet."

    lines = [
        "| Priority | Notice | Reason | Related Outputs |",
        "|---:|---|---|---|",
    ]

    for index, (item, reason) in enumerate(queue, start=1):
        notice = f"{item['notice_id']} - {item['title']}".replace("|", "\\|")
        lines.append(f"| {index} | {notice} | {reason} | {artifact_links(item)} |")

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


def build_review_pack(items, triage_board):
    counts = count_by_status(items)
    processed = [item for item in items if has_core_processed_artifacts(item)]
    sources = [item for item in items if item["status"] == "Sources Sought Plan Generated"]
    manual = [item for item in items if item["status"].startswith("Manual Review")]
    retry = [item for item in manual if is_retry_candidate(item)]
    pass_items = [item for item in items if is_pass_not_ready(item)]
    usaspending_queue = build_usaspending_queue(items)
    ready_pricing_items = [item for item in items if is_ready_for_pricing(item)]
    needs_data_items = [item for item in items if needs_better_data(item)]
    manual_retry_combined = sorted(
        {item["notice_id"]: item for item in (manual + retry)}.values(),
        key=candidate_score, reverse=True,
    )

    n_clean = sum(
        1 for item in items
        if item.get("intel", {}).get("usaspending_data_quality_status") == "clean"
    )
    n_warning = sum(
        1 for item in items
        if item.get("intel", {}).get("usaspending_data_quality_status") == "warning"
    )
    n_no_data = sum(
        1 for item in items
        if item.get("intel", {}).get("usaspending_intel_exists")
        and item.get("intel", {}).get("usaspending_data_quality_status") == "no_data"
    )

    lines = [
        "# GovCon Scout Triage Review Pack",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Triage Board:** `{triage_board}`" if Path(triage_board).exists() else f"**Triage Board:** `{triage_board}` not found",
        f"**Latest Batch Report:** `{newest_batch_report() or 'None found'}`",
        "",
        "## Executive Summary",
        "",
        f"- **Total reviewed items:** {len(items)}",
        f"- **Processed solicitations (full review package):** {len(processed)}",
        f"- **Sources sought / RFI candidates:** {len(sources)}",
        f"- **Manual review / retry candidates:** {len(manual)}",
        f"- **Ready for subcontractor quotes / pricing review:** {len(ready_pricing_items)}",
        f"- **Needs better market data (USAspending noise detected):** {len(needs_data_items)}",
        f"- **USAspending: clean data:** {n_clean} | **warning:** {n_warning} | **no data:** {len(items) - n_clean - n_warning}",
        f"- **Recommended USAspending queue size:** {len(usaspending_queue)}",
        "",
        "Use this pack to decide where deeper award intelligence is worth the time. "
        "It does not estimate win probability.",
        "",
        "## Recommended USAspending Queue",
        "",
        queue_table(usaspending_queue),
        "",
        "## Finalist Intelligence Status",
        "",
        "Per-item status across all processed and intel-enriched opportunities.",
        "",
        finalist_intel_table(items),
        "",
        "## Ready for Pricing Review",
        "",
        "These items have a full review package (decision/compliance/bid-no-bid), a pricing schedule, "
        "USAspending intel, and a bid price sanity check with no data quality warning.",
        "",
        item_table(ready_pricing_items) if ready_pricing_items else "None yet. Run USAspending + sanity check on processed solicitations with pricing.",
        "",
        "## Needs Better Market Data",
        "",
        "These items have USAspending intel, but the data quality check flagged the award range as "
        "unreliable (unrelated contracts, implausible values, or noisy results). "
        "Do not use the flagged award range for pricing. Search FPDS or GSA Advantage for comparable awards manually.",
        "",
        item_table(needs_data_items) if needs_data_items else "None flagged.",
        "",
        "## Sources Sought / RFI Candidates",
        "",
        item_table(sources) if sources else "None.",
        "",
        "## Manual Review / Retry Candidates",
        "",
        item_table(manual_retry_combined) if manual_retry_combined else "None.",
        "",
        "## Recommended Next Actions",
        "",
        "1. **Start with Ready for Pricing Review items.** Get 2–3 subcontractor quotes, compare against "
        "USAspending range, build CLIN-level pricing.",
        "2. **For Needs Better Market Data items:** Search FPDS or GSA Advantage for comparable scope/location/agency awards. "
        "Do not price from the flagged USAspending range.",
        "3. **Run USAspending on processed items missing intel.** Priority: items with pricing schedule already extracted.",
        "4. **For sources-sought/RFI candidates:** Use USAspending to identify likely incumbents and agency buying patterns "
        "before drafting capability statements or outreach.",
        "5. **Retry manual-review candidates** only when debug evidence suggests login/session or selector issues — "
        "not when the notice appears text-only or attachment-less.",
        "6. **Keep pass/not-ready items out of deeper research** until attachments, clearer requirements, "
        "or stronger pursuit rationale appear.",
        "",
    ]

    return "\n".join(lines)


def parse_args():
    parser = argparse.ArgumentParser(description="Build a GovCon Scout triage review pack.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--triage-board", default=DEFAULT_TRIAGE_BOARD)
    return parser.parse_args()


def main():
    args = parse_args()
    _sections, board_items = parse_triage_board(args.triage_board)
    discovered = discover_artifact_items(board_items)

    merged = {**discovered, **board_items}
    items = [attach_artifacts(item) for item in merged.values()]

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_review_pack(items, args.triage_board), encoding="utf-8")

    print(f"Triage review pack written to: {output_path}")


if __name__ == "__main__":
    main()
