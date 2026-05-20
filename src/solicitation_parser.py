import argparse
import csv
import re
from pathlib import Path


DEFAULT_EXTRACTS_DIR = "reports/document_extracts"
DEFAULT_REVIEWS_DIR = "reports/opportunity_reviews"
DEFAULT_CSV_PATH = "exports/govcon_scout_opportunities_latest.csv"


SECTION_PATTERNS = {
    "submission": [
        "submission to offers",
        "submission of offers",
        "instructions to offerors",
        "addendum to paragraph",
        "quote shall",
        "quotation shall",
        "offeror shall submit",
        "shall be submitted",
        "submit",
        "piee",
        "email",
    ],
    "evaluation": [
        "evaluation",
        "basis for award",
        "award will be made",
        "factor i",
        "factor ii",
        "technical",
        "price",
        "past performance",
        "lowest price",
        "technically acceptable",
        "best value",
        "source selection",
    ],
    "pricing": [
        "pricing schedule",
        "attachment #4",
        "attachment 4",
        "clin",
        "unit price",
        "extended price",
        "firm fixed price",
        "ffp",
        "schedule of supplies",
        "schedule of services",
        "supplies or services and prices",
    ],
    "documents_required": [
        "quote shall include",
        "quotation shall include",
        "offeror shall provide",
        "shall provide",
        "shall submit",
        "complete pricing schedule",
        "technical approach",
        "quality control",
        "past performance",
        "representations",
        "certifications",
    ],
    "deadlines": [
        "offer due date",
        "offers are due",
        "quotations are due",
        "questions",
        "no later than",
        "site visit",
        "may 14",
        "may 18",
        "may 19",
        "may 22",
        "june 2",
    ],
    "site_visit": [
        "site visit",
        "site visits",
        "base access",
        "date of birth",
        "dob",
        "failure to meet this deadline",
        "preclude",
        "access",
    ],
    "forms_clauses": [
        "far 52.212-1",
        "far 52.212-2",
        "far 52.212-3",
        "far 52.212-4",
        "far 52.212-5",
        "dfars",
        "representations",
        "certifications",
        "sam",
        "wawf",
    ],
    "pws_performance": [
        "performance work statement",
        "contractor shall",
        "integrated pest management",
        "ipm",
        "pest",
        "inspection",
        "treatment",
        "report",
        "schedule",
        "school",
        "student",
        "staff",
        "cor",
    ],
}


QUOTE_BLOCK_HINTS = [
    "submission requirement may result in rejection",
    "removed from further evaluation",
    "complete pricing schedule",
    "technical approach",
    "factor i",
    "factor ii",
    "the government shall evaluate",
    "offeror shall provide",
    "quote shall",
    "shall be submitted",
]


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def make_safe_name(value):
    text = safe_text(value) or "unknown"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text.strip("_")[:120] or "unknown"


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def compact_text(text):
    text = safe_text(text)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


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
        text = compact_text(path.read_text(encoding="utf-8", errors="ignore"))
        records.append({
            "path": str(path),
            "name": path.name,
            "text": text,
            "doc_type": classify_doc(path.name),
        })

    return records


def classify_doc(name):
    lower = name.lower()

    if "pws" in lower or "sow" in lower:
        return "PWS/SOW"

    if "pricing" in lower or "price" in lower or "clin" in lower:
        return "Pricing"

    if "_sol_" in lower or lower.startswith("sol") or "solicitation" in lower or "rfq" in lower or "rfp" in lower:
        return "Solicitation"

    if "map" in lower or "footprint" in lower:
        return "Map / Facility Reference"

    if "facilities" in lower or "facility" in lower:
        return "Facility Information"

    return "Other"


def sentence_split(text):
    text = compact_text(text)
    chunks = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [chunk.strip() for chunk in chunks if len(chunk.strip()) > 5]


def find_lines(records, patterns, limit=25, preferred_doc_types=None):
    hits = []
    seen = set()

    preferred_doc_types = preferred_doc_types or []

    ordered_records = sorted(
        records,
        key=lambda record: (
            0 if record.get("doc_type") in preferred_doc_types else 1,
            record.get("name", ""),
        ),
    )

    for record in ordered_records:
        lines = sentence_split(record["text"])

        for line in lines:
            lower = line.lower()

            if any(pattern in lower for pattern in patterns):
                cleaned = line[:900]

                if cleaned in seen:
                    continue

                hits.append({
                    "doc": record["name"],
                    "doc_type": record["doc_type"],
                    "text": cleaned,
                })
                seen.add(cleaned)

                if len(hits) >= limit:
                    return hits

    return hits


def find_quote_blocks(records, limit=25):
    hits = []
    seen = set()

    for record in records:
        if record["doc_type"] not in ["Solicitation", "PWS/SOW", "Pricing"]:
            continue

        lines = sentence_split(record["text"])

        for line in lines:
            lower = line.lower()

            if any(hint in lower for hint in QUOTE_BLOCK_HINTS):
                cleaned = line[:1200]

                if cleaned not in seen:
                    hits.append({
                        "doc": record["name"],
                        "doc_type": record["doc_type"],
                        "text": cleaned,
                    })
                    seen.add(cleaned)

            if len(hits) >= limit:
                return hits

    return hits


def extract_email_addresses(records):
    combined = "\n".join(record["text"] for record in records)
    emails = sorted(set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", combined)))
    return emails


def extract_dates(records):
    combined = "\n".join(record["text"] for record in records)
    patterns = [
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+\d{4}(?:[^.\n]{0,160})",
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sept|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+\d{4}(?:[^.\n]{0,160})",
        r"\b\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)(?:[^.\n]{0,160})",
    ]

    hits = []
    seen = set()

    for pattern in patterns:
        for match in re.finditer(pattern, combined):
            value = compact_text(match.group(0))

            if value not in seen:
                hits.append(value)
                seen.add(value)

            if len(hits) >= 35:
                return hits

    return hits


def infer_required_documents(records):
    combined = "\n".join(record["text"].lower() for record in records)
    required = []

    if "complete pricing schedule" in combined or "attachment #4" in combined or "attachment 4" in combined:
        required.append({
            "item": "Completed Pricing Schedule / Attachment #4",
            "status": "Required / Confirm",
            "source": "Pricing/evaluation language detected.",
        })

    if "technical approach" in combined or "factor i" in combined:
        required.append({
            "item": "Technical Approach / Technical Quote",
            "status": "Required / Confirm",
            "source": "Technical factor language detected.",
        })

    if "quality control" in combined:
        required.append({
            "item": "Quality Control Plan",
            "status": "Likely Required / Confirm",
            "source": "Quality control language detected.",
        })

    if "past performance" in combined or "experience" in combined:
        required.append({
            "item": "Past Performance / Experience",
            "status": "Likely Required / Confirm",
            "source": "Past performance or experience language detected.",
        })

    if "52.212-3" in combined or "representations" in combined or "certifications" in combined:
        required.append({
            "item": "Representations and Certifications / FAR 52.212-3",
            "status": "Required / Confirm",
            "source": "FAR 52.212-3 / reps and certs language detected.",
        })

    if "sam" in combined and "system for award management" in combined:
        required.append({
            "item": "Active SAM Registration",
            "status": "Required",
            "source": "System for Award Management language detected.",
        })

    if not required:
        required.append({
            "item": "Required quote documents not confidently detected",
            "status": "Manual Review",
            "source": "Parser did not find enough required-document language.",
        })

    return required


def infer_submission_method(records):
    combined = "\n".join(record["text"].lower() for record in records)

    methods = []

    if "piee" in combined or "solicitation module" in combined:
        methods.append("PIEE Solicitation Module")

    if "sam.gov" in combined:
        methods.append("SAM.gov")

    if "email" in combined or "submitted via email" in combined:
        methods.append("Email mentioned — confirm whether for questions only or quote submission")

    if not methods:
        methods.append("Unknown — manual review required")

    return methods


def infer_evaluation_method(records):
    combined = "\n".join(record["text"].lower() for record in records)

    if "lowest price technically acceptable" in combined or "lpta" in combined:
        return "LPTA / Lowest Price Technically Acceptable"

    if "best value" in combined:
        return "Best Value"

    if "factor i" in combined and "factor ii" in combined and "price" in combined:
        return "Factor-based evaluation with technical and price factors"

    if "52.212-2" in combined:
        return "FAR 52.212-2 evaluation factors present — manual review needed"

    return "Unknown"


def infer_blockers(records):
    combined = "\n".join(record["text"].lower() for record in records)
    blockers = []

    if "failure to meet this deadline will preclude" in combined:
        blockers.append({
            "level": "Possible Blocker",
            "item": "Missed site visit access deadline may preclude attendance.",
            "action": "Confirm whether site visit attendance was mandatory or whether non-attendees may still quote.",
        })

    if "submission requirement may result in rejection" in combined:
        blockers.append({
            "level": "Possible Blocker",
            "item": "Failure to follow submission requirements may result in rejection.",
            "action": "Build exact compliance checklist from Instructions to Offerors.",
        })

    if "removed from further evaluation" in combined or "removal of the quote from further evaluation" in combined:
        blockers.append({
            "level": "Possible Blocker",
            "item": "Noncompliant quote may be removed from further evaluation.",
            "action": "Confirm every required volume/document/page limit/pricing file.",
        })

    if not blockers:
        blockers.append({
            "level": "None Detected",
            "item": "No obvious no-bid blocker detected by parser.",
            "action": "Manual review still required.",
        })

    return blockers


def write_hit_section(lines, title, hits):
    lines.append(f"## {title}")
    lines.append("")

    if not hits:
        lines.append("- Not detected. Manual review required.")
        lines.append("")
        return

    for hit in hits:
        lines.append(f"- **{hit['doc_type']} / {hit['doc']}:** {hit['text']}")

    lines.append("")


def write_compliance_matrix(notice_id, opportunity, records, output_path):
    submission_hits = find_lines(records, SECTION_PATTERNS["submission"], preferred_doc_types=["Solicitation"])
    evaluation_hits = find_lines(records, SECTION_PATTERNS["evaluation"], preferred_doc_types=["Solicitation"])
    pricing_hits = find_lines(records, SECTION_PATTERNS["pricing"], preferred_doc_types=["Solicitation", "Pricing"])
    documents_hits = find_lines(records, SECTION_PATTERNS["documents_required"], preferred_doc_types=["Solicitation"])
    deadline_hits = find_lines(records, SECTION_PATTERNS["deadlines"], preferred_doc_types=["Solicitation"])
    site_visit_hits = find_lines(records, SECTION_PATTERNS["site_visit"], preferred_doc_types=["Solicitation"])
    forms_hits = find_lines(records, SECTION_PATTERNS["forms_clauses"], preferred_doc_types=["Solicitation"])
    pws_hits = find_lines(records, SECTION_PATTERNS["pws_performance"], preferred_doc_types=["PWS/SOW"])
    quote_blocks = find_quote_blocks(records)

    emails = extract_email_addresses(records)
    dates = extract_dates(records)
    required_docs = infer_required_documents(records)
    submission_methods = infer_submission_method(records)
    evaluation_method = infer_evaluation_method(records)
    blockers = infer_blockers(records)

    lines = []
    lines.append(f"# GovCon Scout Compliance Matrix — {notice_id}")
    lines.append("")
    lines.append("## Opportunity Summary")
    lines.append("")
    lines.append(f"- **Title:** {safe_text(opportunity.get('title') or notice_id)}")
    lines.append(f"- **Agency:** {safe_text(opportunity.get('department_ind_agency'))}")
    lines.append(f"- **Deadline:** {safe_text(opportunity.get('due_date_user_local') or opportunity.get('response_deadline'))}")
    lines.append(f"- **Fit Score:** {safe_text(opportunity.get('fit_score'))}")
    lines.append(f"- **Prime Reality Score:** {safe_text(opportunity.get('prime_reality_score'))}")
    lines.append(f"- **SAM.gov Link:** {safe_text(opportunity.get('ui_link'))}")
    lines.append("")
    lines.append("## Parser Summary")
    lines.append("")
    lines.append(f"- **Likely Submission Method:** {', '.join(submission_methods)}")
    lines.append(f"- **Likely Evaluation Method:** {evaluation_method}")
    lines.append(f"- **Extracted Email Addresses:** {', '.join(emails) if emails else 'None detected'}")
    lines.append("")
    lines.append("## Possible No-Bid / Rejection Blockers")
    lines.append("")

    for blocker in blockers:
        lines.append(f"- **{blocker['level']}:** {blocker['item']} **Action:** {blocker['action']}")

    lines.append("")
    lines.append("## Required / Likely Required Submission Items")
    lines.append("")
    lines.append("| Item | Status | Source / Reason |")
    lines.append("|---|---:|---|")

    for item in required_docs:
        lines.append(f"| {item['item']} | {item['status']} | {item['source']} |")

    lines.append("")
    lines.append("## Timeline / Deadline Clues")
    lines.append("")

    if dates:
        for item in dates:
            lines.append(f"- {item}")
    else:
        lines.append("- No date/time clues detected.")

    lines.append("")
    write_hit_section(lines, "Quote / Submission Requirement Clues", submission_hits)
    write_hit_section(lines, "High-Value Quote Requirement Blocks", quote_blocks)
    write_hit_section(lines, "Required Documents / Volume Clues", documents_hits)
    write_hit_section(lines, "Evaluation Factor Clues", evaluation_hits)
    write_hit_section(lines, "Pricing Requirement Clues", pricing_hits)
    write_hit_section(lines, "Site Visit / Access Clues", site_visit_hits)
    write_hit_section(lines, "FAR / DFARS / Forms / Compliance Clues", forms_hits)
    write_hit_section(lines, "PWS Performance Clues", pws_hits)

    lines.append("## Working Proposal Compliance Checklist")
    lines.append("")
    lines.append("| Status | Task | Owner | Notes |")
    lines.append("|---|---|---|---|")
    lines.append("| Not Started | Confirm submission portal/method and final due date/time | Travis | Check Instructions to Offerors and PIEE/SAM details |")
    lines.append("| Not Started | Confirm whether site visit was mandatory or optional | Travis | High-priority go/no-go issue |")
    lines.append("| Not Started | Confirm question deadline and whether questions are still accepted | Travis | Prepare RFIs if open |")
    lines.append("| Not Started | Complete Attachment #4 pricing schedule exactly | Pricing | Do not alter CLIN structure |")
    lines.append("| Not Started | Draft technical approach against PWS/IPM requirements | Proposal | Address school safety, scheduling, reporting, callbacks |")
    lines.append("| Not Started | Build staffing/local execution plan | Operations | Confirm local pest control capability / subcontractor |")
    lines.append("| Not Started | Prepare compliance/reps/certs | Admin | FAR 52.212-3, SAM registration, required forms |")
    lines.append("| Not Started | Final compliance review before submission | Travis | Confirm all required docs and naming instructions |")
    lines.append("")
    lines.append("## Recommended RFI / Clarification Questions")
    lines.append("")
    lines.append("1. Was attendance at the May 18–19 site visit mandatory, optional, or only recommended?")
    lines.append("2. If the site visit was missed, may a contractor still submit a quote?")
    lines.append("3. Will site visit notes, questions, or clarifications be released to all interested vendors?")
    lines.append("4. Is Attachment #4 the only pricing document required, or is supporting cost detail required?")
    lines.append("5. Are after-hours, weekend, emergency, or callback services included in the fixed price?")
    lines.append("6. Are there incumbent service history, pest activity reports, or treatment frequency data available?")
    lines.append("")
    lines.append("## Source Extract Files")
    lines.append("")

    for record in records:
        lines.append(f"- {record['path']}")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def parse_notice(notice_id, extracts_dir, reviews_dir, csv_path):
    notice_id = make_safe_name(notice_id)
    records = load_extracts(notice_id, extracts_dir)

    if not records:
        print(f"No extracted text files found for notice ID: {notice_id}")
        print(f"Expected folder: {Path(extracts_dir) / notice_id}")
        return ""

    opportunity = load_opportunity_from_csv(notice_id, csv_path)

    ensure_dir(reviews_dir)
    output_path = Path(reviews_dir) / f"{notice_id}_compliance_matrix.md"

    write_compliance_matrix(
        notice_id=notice_id,
        opportunity=opportunity,
        records=records,
        output_path=output_path,
    )

    print("")
    print(f"Compliance matrix written to: {output_path}")
    print("")

    return str(output_path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a GovCon Scout compliance matrix from extracted solicitation documents."
    )

    parser.add_argument(
        "--notice-id",
        required=True,
        help="Notice ID to parse, e.g. HE125426QE041.",
    )

    parser.add_argument(
        "--extracts-dir",
        default=DEFAULT_EXTRACTS_DIR,
        help="Folder containing reports/document_extracts/{notice_id}/.",
    )

    parser.add_argument(
        "--reviews-dir",
        default=DEFAULT_REVIEWS_DIR,
        help="Folder for compliance matrix output.",
    )

    parser.add_argument(
        "--csv",
        default=DEFAULT_CSV_PATH,
        help="GovCon Scout CSV for opportunity metadata.",
    )

    return parser.parse_args()


def main():
    args = parse_args()

    parse_notice(
        notice_id=args.notice_id,
        extracts_dir=args.extracts_dir,
        reviews_dir=args.reviews_dir,
        csv_path=args.csv,
    )


if __name__ == "__main__":
    main()