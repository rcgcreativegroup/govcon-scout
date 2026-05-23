import argparse
import csv
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data/opportunity_state.csv"
BACKUP_DIR = BASE_DIR / "data/backups"

ANALYSIS_DIR = BASE_DIR / "reports/analysis_packets"
REVIEWS_DIR = BASE_DIR / "reports/opportunity_reviews"
SOURCES_SOUGHT_DIR = BASE_DIR / "reports/sources_sought"
MANUAL_REVIEW_DIR = BASE_DIR / "reports/manual_review"
PRICING_DIR = BASE_DIR / "reports/pricing"

BACKFILL_COLUMNS = [
    "ai_summary",
    "requirements",
    "disqualifiers",
    "description",
    "manual_review_reason",
    "document_status",
    "next_data_step",
    "artifact_backfill_status",
    "artifact_backfill_source",
    "artifact_backfill_updated_at",
]

PROTECTED_COLUMNS = {
    "macro_stage",
    "triage_status",
    "operator_status",
    "flagged",
    "last_operator_action",
    "notes",
    "last_updated",
    "current_stage",
    "manual_review_status",
    "sources_sought_status",
    "processed_status",
    "bid_price_sanity_status",
    "set_aside",
    "set_aside_type",
    "set_aside_description",
    "buyer_name",
    "buyer_email",
    "buyer_phone",
    "source_url",
    "ui_link",
    "place_of_performance",
    "place_of_performance_city",
    "place_of_performance_state",
    "place_of_performance_country",
}

EMPTY_VALUES = {"", "not available", "none", "null", "n/a", "na"}

LIMITS = {
    "ai_summary": 800,
    "description": 1200,
    "requirements": 1500,
    "disqualifiers": 1200,
    "manual_review_reason": 800,
    "next_data_step": 300,
}

SECTION_HEADINGS = [
    "Summary",
    "Executive Summary",
    "Opportunity Summary",
    "Recommended Next Action",
    "Recommendation",
    "Requirements",
    "Compliance",
    "Risks",
    "Disqualifiers",
    "Pass / No-Bid",
    "Manual Review Reason",
    "Findings",
    "Response Strategy",
    "Questions",
    "Pricing Flags",
    "Executive Decision",
    "Key Risks",
    "Submission Checklist",
    "Scope / PWS Summary Clues",
    "Strategic Call",
    "Recommended Sources Sought Response Outline",
    "Suggested Follow-Up Actions",
    "Suggested Classification",
]


def is_empty(value):
    return str(value or "").strip().lower() in EMPTY_VALUES


def clean_text(value):
    text = str(value or "")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"[*_#>`]+", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    text = re.sub(r"\s*\|\s*", " ", text)
    text = re.sub(r"[\t\r\n]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" -:;")
    return text


def limit_text(value, field):
    text = clean_text(value)
    max_len = LIMITS.get(field)
    if max_len and len(text) > max_len:
        return text[:max_len].rstrip() + " [truncated]"
    return text


def meaningful_file(path):
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    meaningful = clean_text(text)
    if len(meaningful) < 50:
        return ""
    return text


def split_sections(text):
    sections = {}
    current = "_intro"
    parts = []
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}#{1,4}\s+(.+?)\s*$", line)
        if match:
            sections[current] = "\n".join(parts).strip()
            current = clean_text(match.group(1)).lower()
            parts = []
        else:
            parts.append(line)
    sections[current] = "\n".join(parts).strip()
    return sections


def section_text(sections, *needles):
    for heading, body in sections.items():
        normalized = heading.lower()
        if any(needle.lower() in normalized for needle in needles):
            if clean_text(body):
                return body
    return ""


def extract_bold_value(text, label):
    pattern = rf"\*\*{re.escape(label)}:\*\*\s*(.+)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return clean_text(match.group(1)) if match else ""


def extract_bullets(text, max_items=10):
    items = []
    for line in text.splitlines():
        stripped = line.strip()
        if not re.match(r"^[-*]|\d+\.", stripped):
            continue
        cleaned = clean_text(re.sub(r"^[-*]\s*|\d+\.\s*", "", stripped))
        if cleaned and len(cleaned) > 3 and not cleaned.lower().startswith(("file exists", "created:", "title:")):
            items.append(cleaned)
        if len(items) >= max_items:
            break
    return items


def first_paragraph(text, max_chars=500):
    for paragraph in re.split(r"\n\s*\n", text):
        cleaned = clean_text(paragraph)
        if len(cleaned) < 50:
            continue
        if cleaned.lower().startswith(("created:", "title:", "agency:", "deadline:")):
            continue
        return cleaned[:max_chars].rstrip() + (" [truncated]" if len(cleaned) > max_chars else "")
    return ""


def pipe_join(items, field):
    seen = []
    for item in items:
        cleaned = clean_text(item)
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    return limit_text(" | ".join(seen), field)


def checked_paths(notice_id):
    return {
        "bid_no_bid": REVIEWS_DIR / f"{notice_id}_bid_no_bid.md",
        "decision_report": REVIEWS_DIR / f"{notice_id}_decision_report.md",
        "compliance_matrix": REVIEWS_DIR / f"{notice_id}_compliance_matrix.md",
        "sources_sought_plan": SOURCES_SOUGHT_DIR / f"{notice_id}_sources_sought_plan.md",
        "manual_review": MANUAL_REVIEW_DIR / f"{notice_id}_manual_review.md",
        "analysis_packet": ANALYSIS_DIR / f"{notice_id}.md",
        "bid_price_sanity": PRICING_DIR / f"{notice_id}_bid_price_sanity.md",
        "pricing_schedule": PRICING_DIR / f"{notice_id}_pricing_schedule.md",
    }


def apply_if_empty(values, field, value):
    if field in PROTECTED_COLUMNS:
        return False
    if not value or not is_empty(values.get(field)):
        return False
    values[field] = limit_text(value, field)
    return True


def set_generated(values, field, value):
    if field in PROTECTED_COLUMNS:
        return False
    if values.get(field) == value:
        return False
    values[field] = value
    return True


def parse_bid_no_bid(text):
    sections = split_sections(text)
    values = {}
    summary = section_text(sections, "Early Bid/No-Bid Takeaway") or first_paragraph(text)
    if summary:
        values["ai_summary"] = summary
    reqs = []
    for name in ["Scope / PWS Clues", "Submission Instructions Clues", "Compliance / Clauses Clues", "Staffing / Execution Clues", "Evaluation / Award Clues"]:
        reqs.extend(extract_bullets(section_text(sections, name), max_items=5))
    if reqs:
        values["requirements"] = pipe_join(reqs[:14], "requirements")
    risks = extract_bullets(section_text(sections, "Deadline / RFI / Site Visit Clues"), max_items=5)
    if risks:
        values["disqualifiers"] = pipe_join(risks, "disqualifiers")
    values["document_status"] = "AI review outputs exist"
    return values


def parse_decision_report(text):
    sections = split_sections(text)
    values = {}
    decision = extract_bold_value(text, "Recommended Decision")
    rationale = extract_bold_value(text, "Rationale")
    if decision or rationale:
        values["ai_summary"] = f"Decision: {decision}. {rationale}".strip()
    else:
        values["ai_summary"] = section_text(sections, "Executive Decision") or first_paragraph(text)
    reqs = extract_bullets(section_text(sections, "Submission Checklist"), max_items=12)
    if reqs:
        values["requirements"] = pipe_join(reqs, "requirements")
    risks = extract_bullets(section_text(sections, "Key Risks"), max_items=8)
    if risks:
        values["disqualifiers"] = pipe_join(risks, "disqualifiers")
    if decision:
        values["next_data_step"] = f"Review decision report and proceed according to: {decision}."
    return values


def parse_compliance_matrix(text):
    sections = split_sections(text)
    reqs = []
    for body in sections.values():
        reqs.extend(extract_bullets(body, max_items=20))
        if reqs:
            break
    if not reqs:
        for line in text.splitlines():
            if "|" in line and not re.match(r"^\s*\|?\s*-", line):
                cleaned = clean_text(line)
                if cleaned and "requirement" not in cleaned.lower():
                    reqs.append(cleaned)
            if len(reqs) >= 12:
                break
    return {"requirements": pipe_join(reqs[:12], "requirements")} if reqs else {}


def parse_sources_sought(text):
    sections = split_sections(text)
    values = {
        "document_status": "Sources-sought response plan exists",
        "next_data_step": "Draft agency questions or sources-sought response.",
    }
    strategy = section_text(sections, "Strategic Call", "Response Strategy Notes", "Opportunity Summary")
    if strategy:
        values["ai_summary"] = strategy
    questions = extract_bullets(section_text(sections, "Shaping Questions", "Recommended Sources Sought Response Outline"), max_items=12)
    if questions:
        values["requirements"] = pipe_join(questions, "requirements")
    return values


def parse_manual_review(text):
    sections = split_sections(text)
    values = {
        "document_status": "Manual review report exists.",
        "next_data_step": "Review manual-review report and decide retry, manual lookup, or pass.",
    }
    reason = section_text(sections, "Manual Review Reason")
    if not reason:
        reason = extract_bold_value(text, "Reason") or first_paragraph(text)
    if reason:
        values["manual_review_reason"] = reason
    summary = section_text(sections, "Opportunity Summary")
    if summary and len(clean_text(summary)) > 80:
        values["ai_summary"] = summary
    return values


def parse_analysis_packet(text, row):
    values = {}
    local_found = extract_bold_value(text, "Local Attachments Found")
    local_count = extract_bold_value(text, "Local Attachment Count")
    ready = extract_bold_value(text, "Ready for Bid/No-Bid Analysis")
    download_ready = extract_bold_value(text, "Attachment Download Ready")
    status_parts = []
    if local_found:
        status_parts.append(f"Local attachments found: {local_found}")
    if local_count:
        status_parts.append(f"Local attachment count: {local_count}")
    if ready:
        status_parts.append(f"Ready for bid/no-bid analysis: {ready}")
    if download_ready:
        status_parts.append(f"Attachment download ready: {download_ready}")
    if status_parts:
        values["document_status"] = "; ".join(status_parts)
    local_yes = local_found.lower() == "yes" or (local_count.isdigit() and int(local_count) > 0)
    if local_yes:
        values["next_data_step"] = "Run AI Review."
    elif row.get("source_url") or row.get("ui_link"):
        values["next_data_step"] = "Prepare SAM Docs or upload documents manually."
    else:
        values["next_data_step"] = "Manual lookup required."
    return values


def parse_pricing_sanity(text):
    sections = split_sections(text)
    values = {}
    next_action = section_text(sections, "Recommended Next Action") or extract_bold_value(text, "Recommended Next Action")
    if next_action:
        values["next_data_step"] = next_action
    lower = text.lower()
    if "not priceable" in lower or "pass / not priceable" in lower:
        values["disqualifiers"] = "Pricing package is not currently priceable; validate documents, scope, or vendor quotes before pricing."
    return values


PARSERS = [
    ("bid_no_bid", parse_bid_no_bid),
    ("decision_report", parse_decision_report),
    ("compliance_matrix", parse_compliance_matrix),
    ("sources_sought_plan", parse_sources_sought),
    ("manual_review", parse_manual_review),
    ("bid_price_sanity", parse_pricing_sanity),
    ("analysis_packet", parse_analysis_packet),
]


def read_state():
    with STATE_PATH.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), list(reader)


def backfill_row(row):
    notice_id = row.get("notice_id", "").strip()
    paths = checked_paths(notice_id)
    found_sources = []
    updated_fields = []
    proposed = dict(row)
    analysis_only = False

    for source, parser in PARSERS:
        text = meaningful_file(paths[source])
        if not text:
            continue
        found_sources.append(source)
        values = parser(text, row) if source == "analysis_packet" else parser(text)
        if source == "analysis_packet" and found_sources == ["analysis_packet"]:
            analysis_only = True
        for field, value in values.items():
            if field == "recommended_next_action":
                if is_empty(proposed.get(field)) and apply_if_empty(proposed, field, value):
                    updated_fields.append(field)
                continue
            if apply_if_empty(proposed, field, value):
                updated_fields.append(field)

    if found_sources:
        if set_generated(proposed, "artifact_backfill_status", "artifacts found"):
            updated_fields.append("artifact_backfill_status")
        if set_generated(proposed, "artifact_backfill_source", ", ".join(found_sources)):
            updated_fields.append("artifact_backfill_source")
        if set_generated(proposed, "artifact_backfill_updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")):
            updated_fields.append("artifact_backfill_updated_at")

    return proposed, found_sources, updated_fields, paths, analysis_only


def run(notice_id=None, write=False):
    headers, rows = read_state()
    for column in BACKFILL_COLUMNS:
        if column not in headers:
            headers.append(column)
    out_rows = []
    field_counts = Counter()
    source_counts = Counter()
    rows_with_artifacts = 0
    rows_updated = 0
    examples = []
    analysis_only = []
    detailed = None

    for row in rows:
        if notice_id and row.get("notice_id") != notice_id:
            out_rows.append(row)
            continue
        before = dict(row)
        proposed, sources, updated_fields, paths, is_analysis_only = backfill_row(row)
        if sources:
            rows_with_artifacts += 1
            source_counts.update(sources)
        if updated_fields:
            rows_updated += 1
            field_counts.update(updated_fields)
            if len(examples) < 10:
                examples.append((row.get("notice_id"), updated_fields, sources))
        if is_analysis_only:
            notice = row.get("notice_id")
            if notice not in analysis_only:
                analysis_only.append(notice)
        if notice_id and row.get("notice_id") == notice_id:
            detailed = (before, proposed, sources, updated_fields, paths)
        out_rows.append(proposed)

    backup_path = ""
    if write:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUP_DIR / f"opportunity_state_before_card_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        shutil.copy2(STATE_PATH, backup)
        backup_path = str(backup.relative_to(BASE_DIR))
        with STATE_PATH.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(out_rows)

    return {
        "mode": "write" if write else "dry-run",
        "rows": len(rows),
        "rows_with_artifacts": rows_with_artifacts,
        "rows_updated": rows_updated,
        "field_counts": field_counts,
        "source_counts": source_counts,
        "backup_path": backup_path,
        "examples": examples,
        "analysis_only": analysis_only,
        "detailed": detailed,
        "notice_id": notice_id,
    }


def print_summary(summary):
    print(f"Mode: {summary['mode']}")
    print(f"Rows in opportunity_state.csv: {summary['rows']}")
    print(f"Rows with artifacts found: {summary['rows_with_artifacts']}")
    print(f"Rows updated: {summary['rows_updated']}")
    print("Fields filled:")
    if summary["field_counts"]:
        for field, count in sorted(summary["field_counts"].items()):
            print(f"- {field}: {count}")
    else:
        print("- none")
    print("Artifact sources used:")
    if summary["source_counts"]:
        for source, count in sorted(summary["source_counts"].items()):
            print(f"- {source}: {count}")
    else:
        print("- none")
    if summary["backup_path"]:
        print(f"Backup created: {summary['backup_path']}")
    if summary["analysis_only"]:
        print("Notices with analysis packet only but no true AI outputs:")
        for notice_id in summary["analysis_only"][:40]:
            print(f"- {notice_id}")
        if len(summary["analysis_only"]) > 40:
            print(f"- ... {len(summary['analysis_only']) - 40} more")
    else:
        print("Notices with analysis packet only but no true AI outputs: none")
    if summary["examples"]:
        print("Examples:")
        for notice_id, fields, sources in summary["examples"]:
            print(f"- {notice_id}: {', '.join(fields)} from {', '.join(sources)}")
    if summary["detailed"]:
        before, after, sources, fields, paths = summary["detailed"]
        print(f"\nDetailed preview for {summary['notice_id']}:")
        print(f"Artifacts found: {', '.join(sources) if sources else 'none'}")
        print(f"Fields changed: {', '.join(fields) if fields else 'none'}")
        for field in BACKFILL_COLUMNS:
            if before.get(field, "") != after.get(field, ""):
                print(f"- {field}:")
                print(f"  before: {before.get(field, '') or '(empty)'}")
                print(f"  after: {after.get(field, '') or '(empty)'}")
        if not sources:
            print("Checked paths:")
            for label, path in paths.items():
                print(f"- {label}: {path.relative_to(BASE_DIR)}")
    print("Limitations: deterministic local parsing only; no LLM, no external fetches, no document download.")


def main():
    parser = argparse.ArgumentParser(description="Backfill dashboard card fields from local GovCon Scout artifacts.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")
    mode.add_argument("--write", action="store_true", help="Write changes to data/opportunity_state.csv.")
    parser.add_argument("--notice-id", help="Limit backfill preview/write to one notice ID.")
    args = parser.parse_args()
    summary = run(notice_id=args.notice_id, write=args.write)
    print_summary(summary)


if __name__ == "__main__":
    main()
