import argparse
import csv
import re
from pathlib import Path


DEFAULT_EXTRACTS_DIR = "reports/document_extracts"
DEFAULT_REVIEWS_DIR = "reports/opportunity_reviews"
DEFAULT_CSV_PATH = "exports/govcon_scout_opportunities_latest.csv"


DECISION_KEYWORDS = {
    "site_visit": [
        "site visit",
        "site visits",
        "failure to meet this deadline",
        "preclude",
        "base access",
    ],
    "questions_rfi": [
        "questions",
        "rfi",
        "request for information",
        "no later than",
        "subject line",
    ],
    "submission": [
        "quote shall",
        "quotation shall",
        "proposal shall",
        "submit",
        "submission",
        "shall be submitted",
        "piee",
        "sam.gov",
        "email",
    ],
    "evaluation": [
        "evaluation",
        "lowest price",
        "technically acceptable",
        "lpta",
        "best value",
        "past performance",
        "technically acceptable",
        "award will be made",
        "basis for award",
        "source selection",
    ],
    "pricing": [
        "firm fixed price",
        "ffp",
        "pricing arrangement",
        "unit price",
        "extended price",
        "clin",
        "schedule of supplies",
        "schedule of services",
        "pricing schedule",
    ],
    "staffing_execution": [
        "contractor shall",
        "personnel",
        "supervisor",
        "normal school operating hours",
        "evenings",
        "weekends",
        "federal holidays",
        "coordination",
        "food service",
        "students",
        "staff",
    ],
    "compliance": [
        "far",
        "dfars",
        "52.",
        "252.",
        "wage determination",
        "service contract",
        "insurance",
        "certification",
        "representations",
        "sam",
        "system for award management",
    ],
    "pest_scope": [
        "integrated pest management",
        "ipm",
        "pest",
        "rodent",
        "insect",
        "treatment",
        "inspection",
        "prevent recurrence",
        "sanitary environment",
    ],
}


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def compact_text(text):
    text = safe_text(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def make_safe_name(value):
    text = safe_text(value) or "unknown"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text.strip("_")[:120] or "unknown"


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


def load_extracts(notice_id, extracts_dir):
    folder = Path(extracts_dir) / notice_id

    if not folder.exists():
        return []

    records = []

    for path in sorted(folder.glob("*.txt")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        records.append({
            "path": str(path),
            "name": path.name,
            "text": compact_text(text),
        })

    return records


def classify_extract(name):
    lower = name.lower()

    if "pws" in lower or "sow" in lower:
        return "PWS/SOW"

    if "pricing" in lower or "price" in lower or "clin" in lower:
        return "Pricing"

    if "_sol_" in lower or lower.startswith("sol") or "solicitation" in lower or "rfq" in lower or "rfp" in lower:
        return "Solicitation"

    if "facilities" in lower or "facility" in lower:
        return "Facility Information"

    if "map" in lower or "footprint" in lower:
        return "Map / Facility Reference"

    return "Other"


def line_hits(text, keywords, limit=18):
    hits = []
    seen = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if len(line) < 8:
            continue

        lower = line.lower()

        if any(keyword in lower for keyword in keywords):
            cleaned = line[:700]

            if cleaned not in seen:
                hits.append(cleaned)
                seen.add(cleaned)

        if len(hits) >= limit:
            break

    return hits


def extract_section_hits(records):
    combined = "\n".join(record["text"] for record in records)
    sections = {}

    for section_name, keywords in DECISION_KEYWORDS.items():
        sections[section_name] = line_hits(combined, keywords)

    return sections


def find_dates_and_times(records):
    combined = "\n".join(record["text"] for record in records)

    patterns = [
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},\s+\d{4}\b(?:[^.\n]{0,120})",
        r"\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)\b(?:[^.\n]{0,120})",
        r"\b\d{1,2}/\d{1,2}/\d{2,4}\b(?:[^.\n]{0,120})",
        r"\b\d{4}-\d{2}-\d{2}\b(?:[^.\n]{0,120})",
    ]

    hits = []
    seen = set()

    for pattern in patterns:
        for match in re.finditer(pattern, combined):
            value = match.group(0).strip()

            if value not in seen:
                hits.append(value)
                seen.add(value)

            if len(hits) >= 30:
                return hits

    return hits


def detect_risks(opportunity, records, sections):
    risks = []

    combined = "\n".join(record["text"].lower() for record in records)

    site_hits = sections.get("site_visit", [])
    if site_hits:
        risks.append({
            "level": "High",
            "risk": "Site visit timing may affect competitiveness.",
            "why": "The extracted package mentions site visits and base access instructions. If the site visit window has passed, bidding may be risky unless attendance was optional or enough information is in the documents.",
        })

    if "base access" in combined:
        risks.append({
            "level": "Medium",
            "risk": "Base access / installation coordination required.",
            "why": "The opportunity appears to involve Fort Campbell access, requiring coordination, personnel information, and compliance with base access rules.",
        })

    if "normal school operating hours" in combined or "students" in combined:
        risks.append({
            "level": "Medium",
            "risk": "School environment execution risk.",
            "why": "Work appears to occur around students, staff, school operations, food service areas, and possible after-hours/weekend scheduling.",
        })

    if "firm fixed price" in combined or "pricing arrangement: firm fixed price" in combined:
        risks.append({
            "level": "Medium",
            "risk": "Firm Fixed Price risk.",
            "why": "Pricing must cover labor, materials, travel, callbacks, site conditions, and compliance costs. Underpricing can hurt execution.",
        })

    if opportunity.get("set_aside_hard_gate") == "Yes":
        risks.append({
            "level": "Blocker",
            "risk": "Set-aside hard gate blocks prime pursuit.",
            "why": opportunity.get("set_aside_hard_gate_reason", "Company may not meet the required set-aside status."),
        })

    if not risks:
        risks.append({
            "level": "Low",
            "risk": "No major blocker detected from keyword scan.",
            "why": "Manual review is still required before pursuit.",
        })

    return risks


def detect_checklist_items(records, sections):
    checklist = []

    combined = "\n".join(record["text"].lower() for record in records)

    checklist.append({
        "category": "Submission",
        "item": "Confirm exact submission method, portal, email addresses, subject line, due date, and time zone.",
        "status": "Needs Review",
    })

    checklist.append({
        "category": "Pricing",
        "item": "Complete pricing schedule exactly as provided. Confirm CLINs, base/option years, unit prices, and total price.",
        "status": "Needs Review",
    })

    checklist.append({
        "category": "Technical",
        "item": "Prepare technical approach aligned to PWS/IPM scope and school safety requirements.",
        "status": "Needs Review",
    })

    if "site visit" in combined:
        checklist.append({
            "category": "Site Visit",
            "item": "Determine whether site visit was mandatory, optional, or strongly recommended. Confirm whether missing it creates a no-bid risk.",
            "status": "High Priority",
        })

    if "questions" in combined:
        checklist.append({
            "category": "RFI",
            "item": "Confirm question deadline and submit RFIs before cutoff if still open.",
            "status": "High Priority",
        })

    if "representations" in combined or "52.212-3" in combined:
        checklist.append({
            "category": "Compliance",
            "item": "Confirm FAR 52.212-3 representations/certifications and SAM registration status.",
            "status": "Needs Review",
        })

    if "52.212-1" in combined:
        checklist.append({
            "category": "Compliance",
            "item": "Review FAR 52.212-1 instructions to offerors and addenda.",
            "status": "Needs Review",
        })

    if "52.212-2" in combined:
        checklist.append({
            "category": "Evaluation",
            "item": "Review FAR 52.212-2 evaluation factors and quote page limits.",
            "status": "Needs Review",
        })

    if "52.212-5" in combined:
        checklist.append({
            "category": "Compliance",
            "item": "Review FAR 52.212-5 clauses and flow-down requirements.",
            "status": "Needs Review",
        })

    return checklist


def recommend_decision(opportunity, records, risks):
    fit_score = to_int(opportunity.get("fit_score"))
    prime_score = to_int(opportunity.get("prime_reality_score"))

    has_blocker = any(risk["level"] == "Blocker" for risk in risks)
    has_high = any(risk["level"] == "High" for risk in risks)

    doc_types = {classify_extract(record["name"]) for record in records}
    has_pws = "PWS/SOW" in doc_types
    has_pricing = "Pricing" in doc_types
    has_solicitation = "Solicitation" in doc_types

    if has_blocker:
        return "Pass as Prime / Consider Teaming Only", "A hard gate or eligibility issue appears to block prime pursuit."

    if not has_pws or not has_pricing:
        return "Hold / More Document Review Needed", "The document package is incomplete for confident bid/no-bid."

    if has_high and prime_score < 70:
        return "Conditional Pursue", "The opportunity fits, but site visit/timing/execution risk should be resolved before committing."

    if prime_score >= 70 and fit_score >= 65:
        return "Pursue as Prime", "Fit and prime reality are strong enough to justify proposal preparation."

    if prime_score >= 55 and fit_score >= 60:
        return "Conditional Pursue as Prime or Teaming Candidate", "The opportunity is realistic, but should be pursued only if execution and pricing confidence are confirmed."

    return "Teaming / Subcontractor Target", "Fit exists, but prime probability is not strong enough yet."


def to_int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def add_bullets(lines, items):
    if not items:
        lines.append("- Not detected in extracted text.")
        return

    for item in items:
        lines.append(f"- {item}")


def write_decision_report(notice_id, opportunity, records, output_path):
    sections = extract_section_hits(records)
    risks = detect_risks(opportunity, records, sections)
    checklist = detect_checklist_items(records, sections)
    dates = find_dates_and_times(records)
    decision, rationale = recommend_decision(opportunity, records, risks)

    doc_types = {classify_extract(record["name"]) for record in records}

    lines = []
    lines.append(f"# GovCon Scout Decision Report — {notice_id}")
    lines.append("")
    lines.append("## Executive Decision")
    lines.append("")
    lines.append(f"**Recommended Decision:** {decision}")
    lines.append("")
    lines.append(f"**Rationale:** {rationale}")
    lines.append("")
    lines.append(f"**Title:** {safe_text(opportunity.get('title') or notice_id)}")
    lines.append(f"**Agency:** {safe_text(opportunity.get('department_ind_agency'))}")
    lines.append(f"**Deadline:** {safe_text(opportunity.get('due_date_user_local') or opportunity.get('response_deadline'))}")
    lines.append(f"**Fit Score:** {safe_text(opportunity.get('fit_score'))}")
    lines.append(f"**Prime Reality Score:** {safe_text(opportunity.get('prime_reality_score'))}")
    lines.append(f"**Current GovCon Scout Recommendation:** {safe_text(opportunity.get('conditional_recommendation') or opportunity.get('recommendation'))}")
    lines.append("")
    lines.append("## Document Readiness")
    lines.append("")
    lines.append(f"- **Solicitation Found:** {'Yes' if 'Solicitation' in doc_types else 'No'}")
    lines.append(f"- **PWS/SOW Found:** {'Yes' if 'PWS/SOW' in doc_types else 'No'}")
    lines.append(f"- **Pricing Found:** {'Yes' if 'Pricing' in doc_types else 'No'}")
    lines.append(f"- **Facility/Map References Found:** {'Yes' if 'Facility Information' in doc_types or 'Map / Facility Reference' in doc_types else 'No'}")
    lines.append("")
    lines.append("## Key Risks")
    lines.append("")

    for risk in risks:
        lines.append(f"- **{risk['level']}: {risk['risk']}** {risk['why']}")

    lines.append("")
    lines.append("## Timeline / Date Clues")
    lines.append("")
    add_bullets(lines, dates)

    lines.append("")
    lines.append("## Submission Checklist")
    lines.append("")

    for item in checklist:
        lines.append(f"- **{item['status']} — {item['category']}:** {item['item']}")

    lines.append("")
    lines.append("## Scope / PWS Summary Clues")
    lines.append("")
    add_bullets(lines, sections.get("pest_scope", []) + sections.get("scope", []))

    lines.append("")
    lines.append("## Submission Instructions Clues")
    lines.append("")
    add_bullets(lines, sections.get("submission", []))

    lines.append("")
    lines.append("## Pricing Strategy Notes")
    lines.append("")
    lines.append("- Treat this as a Firm Fixed Price pricing exercise unless the solicitation says otherwise.")
    lines.append("- Build pricing from the PWS, facility list, expected frequency, labor hours, materials, travel, supervision, insurance, and contingency.")
    lines.append("- Use the provided pricing schedule exactly. Do not alter CLIN structure unless instructions allow it.")
    lines.append("")
    lines.append("### Pricing Clues")
    lines.append("")
    add_bullets(lines, sections.get("pricing", []))

    lines.append("")
    lines.append("## Staffing / Execution Notes")
    lines.append("")
    lines.append("- Confirm whether service must be performed during school hours, after hours, weekends, or coordinated around school closures.")
    lines.append("- Confirm base access requirements, badging, escorting, security rules, and reporting requirements.")
    lines.append("- Confirm whether a local pest control subcontractor is needed for performance at Fort Campbell.")
    lines.append("")
    lines.append("### Staffing / Execution Clues")
    lines.append("")
    add_bullets(lines, sections.get("staffing_execution", []))

    lines.append("")
    lines.append("## Evaluation / Award Notes")
    lines.append("")
    add_bullets(lines, sections.get("evaluation", []))

    lines.append("")
    lines.append("## Recommended RFI Questions")
    lines.append("")
    lines.append("1. Was attendance at the May 18–19 site visit mandatory, optional, or only recommended?")
    lines.append("2. If the site visit was missed, will the Government provide site visit notes, attendee questions, or clarifications to all offerors?")
    lines.append("3. Are there incumbent service levels, pest activity history, or prior treatment frequencies available for pricing validation?")
    lines.append("4. Are after-hours, weekend, or emergency services expected to be included in the fixed price?")
    lines.append("5. Are base access delays, escort requirements, or badging timelines expected to affect start of performance?")
    lines.append("")
    lines.append("## Proposal Outline")
    lines.append("")
    lines.append("1. Cover Page / Quote Identification")
    lines.append("2. Completed Pricing Schedule")
    lines.append("3. Technical Approach")
    lines.append("   - IPM methodology")
    lines.append("   - School safety and coordination")
    lines.append("   - Treatment schedule and reporting")
    lines.append("   - Emergency/callback process")
    lines.append("4. Staffing and Management Plan")
    lines.append("5. Quality Control Plan")
    lines.append("6. Past Performance / Experience")
    lines.append("7. Compliance Representations / Required Forms")
    lines.append("")
    lines.append("## Prime vs Subcontractor Strategy")
    lines.append("")
    lines.append("- **Prime path:** Reasonable only if JPTR/RCG can secure qualified pest control performance capability, local execution coverage, pricing confidence, and compliance with school/base requirements.")
    lines.append("- **Subcontractor path:** Strong fallback if a qualified local pest control vendor can prime or perform while JPTR/RCG supports proposal, documentation, compliance, pricing structure, and workflow automation.")
    lines.append("")
    lines.append("## Source Extract Files")
    lines.append("")

    for record in records:
        lines.append(f"- {record['path']}")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def analyze_notice(notice_id, extracts_dir, reviews_dir, csv_path):
    notice_id = make_safe_name(notice_id)
    records = load_extracts(notice_id, extracts_dir)

    if not records:
        print(f"No extracted text files found for notice ID: {notice_id}")
        print(f"Expected folder: {Path(extracts_dir) / notice_id}")
        return ""

    opportunity = load_opportunity_from_csv(notice_id, csv_path)

    ensure_dir(reviews_dir)
    output_path = Path(reviews_dir) / f"{notice_id}_decision_report.md"

    write_decision_report(
        notice_id=notice_id,
        opportunity=opportunity,
        records=records,
        output_path=output_path,
    )

    print("")
    print(f"Decision report written to: {output_path}")
    print("")

    return str(output_path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a structured GovCon Scout bid/no-bid decision report from extracted documents."
    )

    parser.add_argument(
        "--notice-id",
        required=True,
        help="Notice ID to analyze, e.g. HE125426QE041.",
    )

    parser.add_argument(
        "--extracts-dir",
        default=DEFAULT_EXTRACTS_DIR,
        help="Folder containing reports/document_extracts/{notice_id}/.",
    )

    parser.add_argument(
        "--reviews-dir",
        default=DEFAULT_REVIEWS_DIR,
        help="Folder for decision report output.",
    )

    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV_PATH,
        help="GovCon Scout CSV for opportunity metadata.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    analyze_notice(
        notice_id=args.notice_id,
        extracts_dir=args.extracts_dir,
        reviews_dir=args.reviews_dir,
        csv_path=args.csv,
    )


if __name__ == "__main__":
    main()