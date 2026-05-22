import argparse
import csv
import re
from collections import Counter
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path


DEFAULT_INPUT = "data/mybidmatch/mybidmatch_opportunities.csv"
DEFAULT_OUTPUT = "reports/mybidmatch/mybidmatch_triage.md"
DEFAULT_GOVCON_CSV = "exports/govcon_scout_opportunities_latest.csv"


LANE_TERMS = [
    ("marketing_communications", [
        "marketing", "advertising", "public event", "event management", "outreach",
        "recruiting", "media", "communications", "public affairs", "public relations",
        "social media", "creative", "graphic design", "campaign", "video production",
    ]),
    ("ai_technology_training", [
        "artificial intelligence", " ai ", "automation", "software", "training",
        "curriculum", "instructional", "technical documentation", "courseware",
        "documentation", "application development", "technology", "data analytics",
    ]),
    ("janitorial", [
        "janitorial", "custodial", "cleaning", "housekeeping", "sanitation",
        "floor care", "window cleaning", "custodian",
    ]),
    ("pest_control", [
        "pest control", "pest management", "integrated pest", " ipm ",
        "termite", "fumigation", "rodent", "extermination", "mosquito",
    ]),
    ("facilities_services", [
        "facilities", "facility", "building maintenance", "grounds maintenance",
        "operations and maintenance", "property maintenance", "landscaping",
    ]),
    ("trucking_transportation", [
        "trucking", "truck", "hauling", "freight", "transportation", "delivery",
        "box truck", "courier", "moving services", "logistics",
    ]),
    ("security_services", [
        "security guard", "guard services", "security services", "armed guard",
        "unarmed guard", "physical security", "access control",
    ]),
]


NAICS_LANES = {
    "541613": "marketing_communications",
    "541810": "marketing_communications",
    "541820": "marketing_communications",
    "561920": "marketing_communications",
    "541512": "ai_technology_training",
    "611430": "ai_technology_training",
    "541430": "ai_technology_training",
    "561720": "janitorial",
    "561710": "pest_control",
    "561210": "facilities_services",
    "561612": "security_services",
}


HIGH_RISK_TERMS = [
    "hazmat", "hazardous material", "tank hauling", "mortuary", "body removal",
    "nuclear", "radiological", "clearance", "top secret", "ts/sci", "clinical",
    "medical equipment", "laboratory instrument",
]


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def inline(value):
    return safe_text(value).replace("|", "/").replace("\n", " ")


def normalize(value):
    text = safe_text(value).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def padded_text(row):
    values = [
        row.get("title"),
        row.get("description"),
        row.get("keywords"),
        row.get("agency"),
        row.get("fsg"),
        row.get("naics"),
    ]
    return f" {normalize(' '.join(safe_text(value) for value in values))} "


def term_matches(term, text):
    cleaned = normalize(term)
    if not cleaned:
        return False
    return re.search(rf"(?<![a-z0-9]){re.escape(cleaned)}(?![a-z0-9])", text) is not None


def lane_matches(row):
    text = padded_text(row)
    matches = []
    for lane, terms in LANE_TERMS:
        hit_terms = [term.strip() for term in terms if term_matches(term, text)]
        if hit_terms:
            matches.append((lane, hit_terms))

    naics = safe_text(row.get("naics"))
    if naics in NAICS_LANES:
        lane = NAICS_LANES[naics]
        for current_lane, hit_terms in matches:
            if current_lane == lane:
                hit_terms.append(f"NAICS {naics}")
                break
        else:
            matches.insert(0, (lane, [f"NAICS {naics}"]))
    return matches


def detect_lane(row):
    matches = lane_matches(row)
    if not matches:
        return "unknown", []
    matches.sort(key=lambda item: (len(item[1]), item[0] in {"marketing_communications", "ai_technology_training"}), reverse=True)
    return matches[0]


def is_high_risk(row):
    text = padded_text(row)
    return [term for term in HIGH_RISK_TERMS if term in text]


def classify(row):
    lane, hits = detect_lane(row)
    risk_hits = is_high_risk(row)
    title = safe_text(row.get("title")) or "Untitled"

    if lane == "unknown":
        return {
            "bucket": "Ignore / Poor Fit",
            "lane": lane,
            "reason": "No strategic keyword or NAICS lane match detected from the current MyBidMatch row.",
            "action": "Ignore unless the article detail reveals a JPTR/RCG lane.",
        }

    hit_text = ", ".join(hits[:4])
    reason = f"Matched {lane} using {hit_text}."

    if risk_hits:
        return {
            "bucket": "Teaming/Subcontractor Lead",
            "lane": lane,
            "reason": f"{reason} Specialized/control-risk term(s): {', '.join(risk_hits[:3])}.",
            "action": "Review source detail and identify a qualified performer before deciding whether to pursue.",
        }

    if lane in {"marketing_communications", "ai_technology_training"}:
        return {
            "bucket": "Strong RCG/JPTR Fit",
            "lane": lane,
            "reason": reason,
            "action": "Open the MyBidMatch article/source and capture notice ID or origin link for qualification.",
        }

    if lane in {"janitorial", "pest_control", "trucking_transportation"}:
        return {
            "bucket": "Strong RCG/JPTR Fit" if len(hits) >= 1 else "Possible Fit",
            "lane": lane,
            "reason": f"{reason} Routine lane can be led through subcontractor-managed fulfillment after validation.",
            "action": "Trace the source, validate subcontractor/supplier path, then decide whether it belongs in GovCon Scout.",
        }

    if lane in {"facilities_services", "security_services"}:
        return {
            "bucket": "Teaming/Subcontractor Lead",
            "lane": lane,
            "reason": f"{reason} Likely needs scope, licensing, or performer validation before prime pursuit.",
            "action": "Open source detail and validate performer, insurance/licensing, compliance, and control risk.",
        }

    return {
        "bucket": "Possible Fit",
        "lane": lane,
        "reason": reason,
        "action": "Review article detail before adding it to the pursuit queue.",
    }


def read_csv(path):
    csv_path = Path(path)
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def title_tokens(title):
    stop = {
        "the", "and", "for", "with", "from", "services", "service", "support",
        "solicitation", "rfq", "rfp", "request", "notice", "contract",
    }
    return {word for word in normalize(title).split() if len(word) > 2 and word not in stop}


def similarity(left, right):
    left_norm = normalize(left)
    right_norm = normalize(right)
    if not left_norm or not right_norm:
        return 0
    left_tokens = title_tokens(left_norm)
    right_tokens = title_tokens(right_norm)
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    return max(overlap, sequence * 0.85)


def load_govcon_titles(path):
    titles = []
    for row in read_csv(path):
        title = safe_text(row.get("title"))
        if title:
            titles.append({
                "title": title,
                "notice_id": safe_text(row.get("notice_id") or row.get("solicitation_number")),
                "agency": safe_text(row.get("department_ind_agency") or row.get("agency")),
            })
    return titles


def find_similar_title(title, govcon_titles, threshold=0.66):
    best = None
    for item in govcon_titles:
        score = similarity(title, item["title"])
        if best is None or score > best["score"]:
            best = {**item, "score": score}
    if best and best["score"] >= threshold:
        return best
    return None


def source_link(row):
    return safe_text(row.get("source_url") or row.get("article_url"))


def row_label(row):
    title = inline(row.get("title")) or "Untitled"
    return title


def classify_rows(rows, govcon_titles):
    output = []
    for row in rows:
        item = {**row, **classify(row)}
        item["similar_govcon"] = find_similar_title(row.get("title"), govcon_titles)
        output.append(item)
    return output


def table(items):
    if not items:
        return "None."
    lines = [
        "| Title | Agency | Source File | Source URL | Matched Lane | Reason | Recommended Next Action | Similar GovCon Title |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in items:
        similar = item.get("similar_govcon")
        if similar:
            notice = f" ({similar['notice_id']})" if similar.get("notice_id") else ""
            duplicate = f"{similar['title']}{notice} [{similar['score']:.2f}]"
        else:
            duplicate = ""
        link = source_link(item)
        link_text = f"[open]({link})" if link else ""
        lines.append(
            f"| {row_label(item)} | {inline(item.get('agency'))} | {inline(item.get('source_file'))} | "
            f"{link_text} | {inline(item.get('lane'))} | {inline(item.get('reason'))} | "
            f"{inline(item.get('action'))} | {inline(duplicate)} |"
        )
    return "\n".join(lines)


def build_report(items, input_path, govcon_path):
    buckets = [
        "Strong RCG/JPTR Fit",
        "Possible Fit",
        "Teaming/Subcontractor Lead",
        "Ignore / Poor Fit",
    ]
    grouped = {bucket: [] for bucket in buckets}
    for item in items:
        grouped[item["bucket"]].append(item)

    lane_counts = Counter(item["lane"] for item in items if item["lane"] != "unknown")
    similar_count = sum(1 for item in items if item.get("similar_govcon"))
    missing_notice_ids = sum(1 for item in items if not safe_text(item.get("notice_id")))

    lines = [
        "# MyBidMatch Triage",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Input CSV:** `{input_path}`",
        f"**GovCon title comparison:** `{govcon_path}`",
        "",
        "## Executive Summary",
        "",
        f"- **Records triaged:** {len(items)}",
        f"- **Records missing notice ID:** {missing_notice_ids}",
        f"- **Possible GovCon title duplicates/similar items:** {similar_count}",
        "- Missing notice IDs are treated as a source-tracing problem. This report does not route MyBidMatch rows into `process_opportunity.py`.",
    ]
    for bucket in buckets:
        lines.append(f"- **{bucket}:** {len(grouped[bucket])}")

    lines.extend(["", "## Lane Mix", ""])
    if lane_counts:
        for lane, count in lane_counts.most_common():
            lines.append(f"- **{lane}:** {count}")
    else:
        lines.append("- No lane matches found.")

    lines.extend([
        "",
        "## Triage Notes",
        "",
        "- Strong and possible fits still require source/origin review because daily-list rows often omit notice IDs, due dates, and full scope.",
        "- Similar-title flags are approximate. Use them to avoid duplicate chasing, not as proof of a SAM.gov match.",
        "- Security, facility, and specialized/control-risk items are kept as teaming/subcontractor leads until performer and compliance requirements are clear.",
    ])

    for bucket in buckets:
        rows = grouped[bucket]
        if bucket == "Ignore / Poor Fit":
            rows = rows[:100]
        lines.extend(["", f"## {bucket}", "", table(rows)])
        if bucket == "Ignore / Poor Fit" and len(grouped[bucket]) > len(rows):
            lines.append("")
            lines.append(f"_Showing first {len(rows)} of {len(grouped[bucket])} ignore/poor-fit rows._")

    lines.extend([
        "",
        "## Recommended Next Actions",
        "",
        "1. Open Strong Fit article links first and recover source URL, notice ID, due date, and attachment path where available.",
        "2. Check similar-title flags against GovCon Scout before manually chasing a MyBidMatch lead.",
        "3. Keep teaming/subcontractor leads in review until prime-control and compliance requirements are clear.",
        "4. Import or process only leads with a confirmed SAM/origin path; do not process daily-list rows solely from missing-notice-ID records.",
        "",
    ])
    return "\n".join(lines)


def write_report(items, input_path, govcon_path, output_path):
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(items, input_path, govcon_path), encoding="utf-8")
    return path


def parse_args():
    parser = argparse.ArgumentParser(description="Triage saved MyBidMatch daily-list opportunity rows.")
    parser.add_argument("--input", default=DEFAULT_INPUT)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--govcon-csv", default=DEFAULT_GOVCON_CSV)
    return parser.parse_args()


def main():
    args = parse_args()
    rows = read_csv(args.input)
    govcon_titles = load_govcon_titles(args.govcon_csv)
    items = classify_rows(rows, govcon_titles)
    report = write_report(items, args.input, args.govcon_csv, args.output)
    print(f"MyBidMatch triage written to: {report}")
    print(f"Records triaged: {len(items)}")


if __name__ == "__main__":
    main()
