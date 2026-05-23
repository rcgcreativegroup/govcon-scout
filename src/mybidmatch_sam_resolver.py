import argparse
import csv
import re
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


DEFAULT_MYBIDMATCH_CSV = "data/mybidmatch/mybidmatch_opportunities.csv"
DEFAULT_MYBIDMATCH_TRIAGE = "reports/mybidmatch/mybidmatch_triage.md"
DEFAULT_GOVCON_CSV = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_REVIEW_PACK = "reports/triage/govcon_triage_review_pack.md"
DEFAULT_OUTPUT_CSV = "data/mybidmatch/mybidmatch_resolved.csv"
DEFAULT_OUTPUT_REPORT = "reports/mybidmatch/mybidmatch_sam_resolution.md"

OUTPUT_FIELDS = [
    "mybidmatch_title",
    "mybidmatch_agency",
    "mybidmatch_source_file",
    "mybidmatch_source_url",
    "mybidmatch_category_or_lane",
    "resolution_status",
    "matched_notice_id",
    "matched_govcon_title",
    "matched_govcon_agency",
    "similarity_score",
    "reason",
    "recommended_next_action",
]

TRIAGE_SECTIONS = [
    "Priority Review",
    "Possible Fit",
    "Teaming/Subcontractor Lead",
    "Ignore / Poor Fit",
]

FEDERAL_AGENCY_HINTS = {
    "AGRICULTURE",
    "DEPT OF DEFENSE",
    "HOMELAND SECURITY",
    "INTERIOR",
    "JUSTICE",
    "NATIONAL AERONAUTICS AND SPACE ADMINISTRATION",
    "POSTAL SERVICE",
    "STATE",
    "VETERANS AFFAIRS",
    "HEALTH AND HUMAN SERVICES",
    "COMMERCE",
    "ENERGY",
    "TRANSPORTATION",
}

STOPWORDS = {
    "the", "and", "for", "with", "from", "that", "this", "services", "service",
    "solicitation", "notice", "request", "rfp", "rfq", "bid", "rebid", "annual",
}


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def read_csv_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def read_text(path):
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def split_markdown_row(line):
    line = line.strip()
    if not line.startswith("|") or not line.endswith("|"):
        return []
    return [cell.strip() for cell in line.strip("|").split("|")]


def extract_link(value):
    match = re.search(r"\(([^)]+)\)", safe_text(value))
    return match.group(1) if match else ""


def parse_triage_section(lines, section):
    records = []
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
        records.append({
            "title": title,
            "agency": safe_text(row.get("Agency")),
            "source_file": safe_text(row.get("Source File")),
            "source_url": extract_link(row.get("Source URL")),
            "lane": safe_text(row.get("Matched Lane")),
            "triage_section": section,
            "similar_govcon": safe_text(row.get("Similar GovCon Title")),
            "recommended_action": safe_text(row.get("Recommended Next Action")),
        })
    return records


def load_triage_records(path):
    text = read_text(path)
    if not text:
        return []
    lines = text.splitlines()
    records = []
    for section in TRIAGE_SECTIONS:
        records.extend(parse_triage_section(lines, section))
    return records


def fallback_csv_records(rows):
    return [{
        "title": safe_text(row.get("title")),
        "agency": safe_text(row.get("agency")),
        "source_file": safe_text(row.get("source_file")),
        "source_url": safe_text(row.get("article_url") or row.get("source_url")),
        "lane": safe_text(row.get("keywords") or row.get("fsg")),
        "triage_section": "Untriaged",
        "similar_govcon": "",
        "recommended_action": "",
    } for row in rows if safe_text(row.get("title"))]


def normalize_title(title):
    text = safe_text(title).lower()
    text = re.sub(r"\[[\d.]+\]$", "", text)
    text = re.sub(r"\([^)]*\)$", "", text)
    text = re.sub(r"^[a-z]-{1,2}", "", text)
    text = text.replace("...", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(word for word in text.split() if word not in STOPWORDS)


def tokens(text):
    return set(normalize_title(text).split())


def title_similarity(left, right):
    left_norm = normalize_title(left)
    right_norm = normalize_title(right)
    if not left_norm or not right_norm:
        return 0.0
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens = tokens(left_norm)
    right_tokens = tokens(right_norm)
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    substring = 0.0
    if min(len(left_norm), len(right_norm)) >= 12 and min(len(left_tokens), len(right_tokens)) >= 2:
        substring = 1.0 if left_norm in right_norm or right_norm in left_norm else 0.0
    return max(sequence, overlap, substring)


def agency_similarity(left, right):
    left_norm = normalize_title(left)
    right_norm = normalize_title(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm in right_norm or right_norm in left_norm:
        return 1.0
    left_tokens = tokens(left_norm)
    right_tokens = tokens(right_norm)
    return len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))


def keyword_overlap(left, right):
    left_tokens = tokens(left)
    right_tokens = tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens)))


def govcon_row_title(row):
    return safe_text(row.get("title"))


def govcon_row_notice(row):
    return safe_text(row.get("notice_id") or row.get("solicitation_number") or row.get("sam_notice_id"))


def govcon_row_agency(row):
    return safe_text(row.get("department_ind_agency") or row.get("agency"))


def parse_similar_flag(value):
    text = safe_text(value)
    if not text:
        return "", "", None
    score_match = re.search(r"\[([\d.]+)\]\s*$", text)
    score = float(score_match.group(1)) if score_match else None
    notice_match = re.search(r"\(([^()]+)\)\s*(?:\[[\d.]+\])?$", text)
    notice_id = notice_match.group(1).strip() if notice_match else ""
    title = text
    if notice_match:
        title = text[:notice_match.start()].strip()
    title = re.sub(r"\s*\[[\d.]+\]\s*$", "", title).strip()
    return title, notice_id, score


def review_pack_notice_ids(path):
    text = read_text(path)
    return set(re.findall(r"\b[A-Z0-9][A-Z0-9_-]{5,}\b", text))


def best_match(record, govcon_rows):
    similar_title, similar_notice, similar_score = parse_similar_flag(record.get("similar_govcon"))
    if similar_notice:
        for row in govcon_rows:
            if govcon_row_notice(row) == similar_notice:
                t_score = similar_score if similar_score is not None else title_similarity(record["title"], govcon_row_title(row))
                a_score = agency_similarity(record["agency"], govcon_row_agency(row))
                total = (t_score * 0.75) + (a_score * 0.2) + (keyword_overlap(record["title"], govcon_row_title(row)) * 0.05)
                return row, total, t_score, a_score, "similar-title flag"

    best = ({}, 0.0, 0.0, 0.0, "")
    for row in govcon_rows:
        t_score = title_similarity(record["title"], govcon_row_title(row))
        if t_score < 0.55:
            continue
        a_score = agency_similarity(record["agency"], govcon_row_agency(row))
        k_score = keyword_overlap(record["title"], govcon_row_title(row))
        total = (t_score * 0.7) + (a_score * 0.2) + (k_score * 0.1)
        if total > best[1]:
            best = (row, total, t_score, a_score, "computed similarity")
    return best


def is_state_local(record):
    agency = safe_text(record.get("agency"))
    if " - " in agency:
        return True
    lowered = agency.lower()
    terms = [
        "purchasing group", "city of", "county", "state of", "department of administration",
        "public notices", "pennbid", "ngem", "smartbuy", "ctsource", "emacs",
    ]
    if any(term in lowered for term in terms):
        return True
    return agency and agency.upper() not in FEDERAL_AGENCY_HINTS


def recommended_action(status):
    return {
        "Confirmed GovCon Scout Match": "Use existing GovCon Scout record",
        "Possible GovCon Scout Match": "Search SAM.gov by exact title",
        "State/Local/Non-SAM Lead": "Treat as state/local lead",
        "Needs Manual Lookup": "Manually open MyBidMatch source and locate notice ID",
        "Duplicate / Already Covered": "Use existing GovCon Scout record",
    }[status]


def resolve_record(record, govcon_rows, covered_notice_ids):
    match, score, title_score, agency_score, match_basis = best_match(record, govcon_rows)
    notice_id = govcon_row_notice(match)
    state_local = is_state_local(record)

    if notice_id and notice_id in covered_notice_ids and (
        title_score >= 0.95
        or (match_basis == "similar-title flag" and title_score >= 0.90 and agency_score >= 0.35)
    ):
        status = "Duplicate / Already Covered"
        reason = f"Matched existing triage/review-pack notice via {match_basis}; title={title_score:.2f}, agency={agency_score:.2f}."
    elif notice_id and title_score >= 0.95 and agency_score >= 0.35:
        status = "Confirmed GovCon Scout Match"
        reason = f"Strong title and agency similarity via {match_basis}; title={title_score:.2f}, agency={agency_score:.2f}."
    elif state_local:
        status = "State/Local/Non-SAM Lead"
        reason = "Agency/source looks like state, local, cooperative purchasing, or non-SAM origin."
        if title_score < 0.95 or agency_score < 0.35:
            match = {}
            notice_id = ""
            score = 0
    elif notice_id and title_score >= 0.82:
        status = "Possible GovCon Scout Match"
        reason = f"Strong title similarity but agency confirmation is weak or missing; title={title_score:.2f}, agency={agency_score:.2f}."
    else:
        status = "Needs Manual Lookup"
        reason = "No conservative local GovCon Scout match found."

    return {
        "mybidmatch_title": record["title"],
        "mybidmatch_agency": record["agency"],
        "mybidmatch_source_file": record["source_file"],
        "mybidmatch_source_url": record["source_url"],
        "mybidmatch_category_or_lane": record.get("lane") or record.get("triage_section", ""),
        "resolution_status": status,
        "matched_notice_id": notice_id,
        "matched_govcon_title": govcon_row_title(match),
        "matched_govcon_agency": govcon_row_agency(match),
        "similarity_score": f"{score:.3f}" if score else "",
        "reason": reason,
        "recommended_next_action": recommended_action(status),
    }


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def table(rows, limit=20):
    if not rows:
        return "None."
    lines = [
        "| MyBidMatch Title | Agency | Matched Notice | Match Title | Score | Next Action |",
        "|---|---|---|---|---:|---|",
    ]
    for row in rows[:limit]:
        lines.append(
            f"| {safe_text(row['mybidmatch_title']).replace('|', '/')} "
            f"| {safe_text(row['mybidmatch_agency']).replace('|', '/')} "
            f"| {row['matched_notice_id'] or '—'} "
            f"| {safe_text(row['matched_govcon_title']).replace('|', '/') or '—'} "
            f"| {row['similarity_score'] or '—'} "
            f"| {row['recommended_next_action']} |"
        )
    return "\n".join(lines)


def build_report(rows, input_counts):
    counts = Counter(row["resolution_status"] for row in rows)
    by_status = {status: [row for row in rows if row["resolution_status"] == status] for status in counts}
    statuses = [
        "Confirmed GovCon Scout Match",
        "Possible GovCon Scout Match",
        "State/Local/Non-SAM Lead",
        "Needs Manual Lookup",
        "Duplicate / Already Covered",
    ]

    lines = [
        "# MyBidMatch SAM Resolution",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Executive Summary",
        "",
        f"- **MyBidMatch CSV records available:** {input_counts['mybidmatch_csv']}",
        f"- **Triage records resolved:** {len(rows)}",
        f"- **GovCon Scout rows compared:** {input_counts['govcon_csv']}",
        "- Resolver is offline-only. It does not call SAM.gov, USAspending, or document downloaders.",
        "- Matching is intentionally conservative; possible matches and manual lookups should be confirmed by a human.",
        "",
        "## Counts by Resolution Status",
        "",
    ]

    for status in statuses:
        lines.append(f"- **{status}:** {counts.get(status, 0)}")

    section_map = [
        ("Confirmed GovCon Scout Matches", "Confirmed GovCon Scout Match"),
        ("Possible GovCon Scout Matches", "Possible GovCon Scout Match"),
        ("State/Local/Non-SAM Leads", "State/Local/Non-SAM Lead"),
        ("Needs Manual Lookup", "Needs Manual Lookup"),
        ("Duplicate / Already Covered", "Duplicate / Already Covered"),
    ]
    for heading, status in section_map:
        section_rows = sorted(by_status.get(status, []), key=lambda row: safe_text(row["similarity_score"]), reverse=True)
        lines.extend(["", f"## {heading}", "", table(section_rows)])

    lines.extend([
        "",
        "## Recommended Next Actions",
        "",
        "1. Use confirmed and duplicate matches through the existing GovCon Scout notice records.",
        "2. For possible matches, search SAM.gov by exact title and compare agency/source before processing.",
        "3. For state/local/non-SAM leads, keep them out of SAM.gov automation and manage them as separate pursuit leads.",
        "4. For manual lookups, open the MyBidMatch source URL and capture the origin notice ID or solicitation page.",
        "5. Ignore poor-fit unresolved rows until a stronger source signal appears.",
        "",
    ])
    return "\n".join(lines)


def run(args):
    mybid_rows = read_csv_rows(args.mybidmatch_csv)
    triage_records = load_triage_records(args.triage)
    records = triage_records or fallback_csv_records(mybid_rows)
    govcon_rows = read_csv_rows(args.govcon_csv)
    covered_ids = review_pack_notice_ids(args.review_pack)
    rows = [resolve_record(record, govcon_rows, covered_ids) for record in records]
    write_csv(args.output_csv, rows)
    Path(args.output_report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_report).write_text(
        build_report(rows, {"mybidmatch_csv": len(mybid_rows), "govcon_csv": len(govcon_rows)}),
        encoding="utf-8",
    )
    return rows


def parse_args():
    parser = argparse.ArgumentParser(description="Resolve MyBidMatch leads to existing local GovCon Scout/SAM records.")
    parser.add_argument("--mybidmatch-csv", default=DEFAULT_MYBIDMATCH_CSV)
    parser.add_argument("--triage", default=DEFAULT_MYBIDMATCH_TRIAGE)
    parser.add_argument("--govcon-csv", default=DEFAULT_GOVCON_CSV)
    parser.add_argument("--review-pack", default=DEFAULT_REVIEW_PACK)
    parser.add_argument("--output-csv", default=DEFAULT_OUTPUT_CSV)
    parser.add_argument("--output-report", default=DEFAULT_OUTPUT_REPORT)
    return parser.parse_args()


def main():
    args = parse_args()
    rows = run(args)
    counts = Counter(row["resolution_status"] for row in rows)
    print(f"MyBidMatch resolution CSV written to: {args.output_csv}")
    print(f"MyBidMatch resolution report written to: {args.output_report}")
    for status, count in counts.most_common():
        print(f"{status}: {count}")


if __name__ == "__main__":
    main()
