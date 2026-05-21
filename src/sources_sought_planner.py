import argparse
import csv
import re
from datetime import datetime
from pathlib import Path


DEFAULT_CSV_PATH = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_OUTPUT_DIR = "reports/sources_sought"


SOURCES_SOUGHT_NOTICE_TYPES = [
    "sources sought",
    "rfi",
    "request for information",
    "special notice",
    "intent to sole source",
    "presolicitation",
]


TECH_SUPPORT_KEYWORDS = [
    "training",
    "curriculum",
    "instruction",
    "course",
    "learning",
    "e-learning",
    "elearning",
    "lms",
    "digital",
    "website",
    "web",
    "application",
    "software",
    "data",
    "analytics",
    "automation",
    "ai",
    "508",
    "accessibility",
    "documentation",
    "workflow",
    "technical support",
    "proposal support",
]


MARKETING_OUTREACH_KEYWORDS = [
    "outreach",
    "community",
    "marketing",
    "communications",
    "campaign",
    "public affairs",
    "media",
    "digital advertising",
    "social media",
    "creative",
    "content",
    "audience",
    "engagement",
]


TEAMING_KEYWORDS = [
    "sdvosb",
    "vosb",
    "8(a)",
    "hubzone",
    "wosb",
    "small business",
    "set-aside",
    "mentor",
    "subcontract",
    "teaming",
    "joint venture",
]


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def make_safe_name(value):
    text = safe_text(value) or "unknown"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text.strip("_")[:120] or "unknown"


def score_int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


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


def combined_text(row):
    fields = [
        "title",
        "description",
        "notice_type",
        "type",
        "solicitation_type",
        "agency",
        "department_ind_agency",
        "office",
        "naics_code",
        "psc",
        "set_aside",
        "matched_keywords",
        "matched_core_strengths",
        "why_matched",
        "subcontractor_role_classifier",
        "conditional_recommendation",
        "recommendation",
    ]

    return " ".join(safe_text(row.get(field)) for field in fields).lower()


def detect_notice_category(row):
    text = combined_text(row)
    notice_type = safe_text(row.get("notice_type") or row.get("type") or row.get("solicitation_type")).lower()
    title = safe_text(row.get("title")).lower()

    joined = f"{notice_type} {title} {text}"

    if "sources sought" in joined:
        return "Sources Sought"

    if "request for information" in joined or re.search(r"\brfi\b", joined):
        return "RFI"

    if "special notice" in joined:
        return "Special Notice"

    if "presolicitation" in joined or "pre-solicitation" in joined:
        return "Pre-Solicitation"

    return "Early-Stage / Market Research Candidate"


def keyword_hits(text, keywords):
    return [keyword for keyword in keywords if keyword in text]


def infer_response_lane(row):
    text = combined_text(row)

    tech_hits = keyword_hits(text, TECH_SUPPORT_KEYWORDS)
    marketing_hits = keyword_hits(text, MARKETING_OUTREACH_KEYWORDS)
    teaming_hits = keyword_hits(text, TEAMING_KEYWORDS)

    lanes = []

    if tech_hits:
        lanes.append({
            "lane": "Technical / AI / Workflow / Documentation Support",
            "hits": tech_hits,
            "positioning": "Position as a specialized support partner for technical documentation, workflow automation, AI-assisted process design, web/application support, accessibility, and training-content systems.",
        })

    if marketing_hits:
        lanes.append({
            "lane": "Marketing / Outreach / Communications Support",
            "hits": marketing_hits,
            "positioning": "Position around audience strategy, campaign execution, veteran/community messaging, digital outreach, analytics, and content operations.",
        })

    if teaming_hits:
        lanes.append({
            "lane": "Teaming / Subcontractor Target",
            "hits": teaming_hits,
            "positioning": "Use the notice to identify qualified primes or set-aside holders and pitch a narrow subcontract support role.",
        })

    if not lanes:
        lanes.append({
            "lane": "General Capability / Market Intelligence",
            "hits": [],
            "positioning": "Use this notice as market intelligence. Respond only if capability alignment, agency access, or partner discovery value is clear.",
        })

    return lanes


def infer_prime_or_sub(row, lanes):
    prime_reality = score_int(row.get("prime_reality_score"))
    fit_score = score_int(row.get("fit_score"))
    set_aside_hard_gate = safe_text(row.get("set_aside_hard_gate")).lower() == "yes"

    if set_aside_hard_gate:
        return "Subcontractor / Teaming Target", "Prime pursuit appears blocked by set-aside eligibility. Use the notice to find eligible primes and pitch a specific workshare."

    if prime_reality >= 70 and fit_score >= 65:
        return "Potential Prime Response", "Prime reality and fit are strong enough to justify a direct response if the notice requirements align."

    if prime_reality >= 50 and fit_score >= 50:
        return "Dual Track: Prime-Lite Response + Teaming Outreach", "Submit a concise capability response if low-effort, while also using the notice to identify teaming partners."

    return "Market Intelligence / Teaming Only", "Use this mostly to learn the agency requirement and identify partner targets rather than trying to lead."


def build_response_outline(row, lanes):
    outline = []

    outline.append("1. Company Snapshot")
    outline.append("   - Legal business name, UEI/CAGE if available, small business status, NAICS alignment, and primary point of contact.")
    outline.append("2. Requirement Understanding")
    outline.append("   - Briefly restate the agency’s apparent need and mission context.")
    outline.append("3. Relevant Capabilities")
    outline.append("   - Map only the capabilities that fit this notice. Avoid generic capability-statement language.")
    outline.append("4. Experience / Transferable Past Performance")
    outline.append("   - Include direct past performance if available; otherwise use adjacent commercial, campaign, technical, training, workflow, or operations experience.")
    outline.append("5. Recommended Approach")
    outline.append("   - Explain how the team would support the requirement, reduce risk, and help the agency define the future solicitation.")
    outline.append("6. Differentiators")
    outline.append("   - Keep differentiators practical: speed, clarity, compliance, workflow discipline, audience understanding, technical documentation, automation, or local execution.")
    outline.append("7. Questions / Clarifications")
    outline.append("   - Ask useful shaping questions that can influence the final solicitation.")
    outline.append("8. Teaming Availability")
    outline.append("   - State willingness to prime, subcontract, or support a qualified prime depending on final set-aside and scope.")

    return outline


def build_shaping_questions(row, lanes):
    text = combined_text(row)
    questions = []

    questions.append("What specific outcomes or mission improvements is the Government trying to achieve through this requirement?")
    questions.append("What pain points or gaps in the current approach led to this market research notice?")
    questions.append("What contract type, period of performance, and anticipated acquisition timeline is the Government considering?")
    questions.append("Will the future solicitation require prime contractor past performance of similar size, scope, and complexity?")
    questions.append("Is the Government considering a small business set-aside or a specific socioeconomic set-aside?")

    if "training" in text or "curriculum" in text or "course" in text:
        questions.append("Will the requirement include curriculum development, instructor delivery, LMS support, evaluation metrics, or all of the above?")
        questions.append("Are existing course materials available, or will the contractor create materials from scratch?")

    if "software" in text or "web" in text or "application" in text or "data" in text:
        questions.append("Will the requirement include software development, sustainment, documentation, data integration, cybersecurity, or user support?")
        questions.append("Are there Government-furnished systems, data sources, or technical standards the contractor must use?")

    if "outreach" in text or "marketing" in text or "communications" in text:
        questions.append("Will the Government provide creative assets and messaging, or will the contractor develop strategy and creative?")
        questions.append("What performance metrics will define outreach success, such as reach, impressions, engagement, conversions, attendance, or awareness lift?")

    if "508" in text or "accessibility" in text:
        questions.append("Will Section 508/WCAG compliance documentation and testing be required as a deliverable?")

    questions.append("Would the Government consider allowing specialized subcontractor support for documentation, AI workflow, accessibility, training materials, analytics, or proposal support?")

    return questions


def build_teaming_targets(row, lanes):
    text = combined_text(row)
    targets = []

    if "sdvosb" in text:
        targets.append("Verified SDVOSB prime with relevant federal past performance.")

    if "8(a)" in text or "8a" in text:
        targets.append("8(a) prime or mentor-protégé partner with agency access.")

    if "training" in text or "curriculum" in text:
        targets.append("Federal training/curriculum development prime.")

    if "software" in text or "web" in text or "data" in text:
        targets.append("Federal software, systems integration, or technical services prime.")

    if "outreach" in text or "marketing" in text:
        targets.append("Federal outreach, communications, public affairs, or advertising prime.")

    if not targets:
        targets.append("Small business prime already selling into this agency, NAICS, or PSC.")

    return targets


def build_workshare_options(row, lanes):
    text = combined_text(row)
    options = []

    if "training" in text or "curriculum" in text:
        options.extend([
            "Training materials support",
            "Curriculum documentation",
            "Instructor guide / participant guide development",
            "Evaluation survey and reporting workflow",
        ])

    if "software" in text or "web" in text or "application" in text:
        options.extend([
            "Full-stack support",
            "Documentation and user guides",
            "AI-assisted workflow automation",
            "Requirements analysis and process mapping",
            "Accessibility / Section 508 review support",
        ])

    if "data" in text or "analytics" in text:
        options.extend([
            "Analytics dashboard support",
            "Reporting automation",
            "Data cleanup and documentation",
        ])

    if "outreach" in text or "marketing" in text or "communications" in text:
        options.extend([
            "Digital campaign planning",
            "Audience research and messaging support",
            "Performance reporting",
            "Creative trafficking / campaign operations",
        ])

    if not options:
        options.extend([
            "Proposal support",
            "Compliance matrix support",
            "Technical writing",
            "Workflow documentation",
            "Agency research and market intelligence",
        ])

    return options


def write_sources_sought_plan(notice_id, row, output_dir):
    ensure_dir(output_dir)

    category = detect_notice_category(row)
    lanes = infer_response_lane(row)
    strategy, strategy_reason = infer_prime_or_sub(row, lanes)
    outline = build_response_outline(row, lanes)
    questions = build_shaping_questions(row, lanes)
    teaming_targets = build_teaming_targets(row, lanes)
    workshare_options = build_workshare_options(row, lanes)

    output_path = Path(output_dir) / f"{notice_id}_sources_sought_plan.md"

    lines = []
    lines.append(f"# Sources Sought / RFI Response Planner — {notice_id}")
    lines.append("")
    lines.append(f"**Created:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")
    lines.append("## Opportunity Summary")
    lines.append("")
    lines.append(f"- **Title:** {safe_text(row.get('title') or notice_id)}")
    lines.append(f"- **Notice Category:** {category}")
    lines.append(f"- **Agency:** {safe_text(row.get('department_ind_agency') or row.get('agency'))}")
    lines.append(f"- **Office:** {safe_text(row.get('office'))}")
    lines.append(f"- **NAICS:** {safe_text(row.get('naics_code') or row.get('naics'))}")
    lines.append(f"- **PSC:** {safe_text(row.get('psc'))}")
    lines.append(f"- **Set-Aside:** {safe_text(row.get('set_aside'))}")
    lines.append(f"- **Deadline:** {safe_text(row.get('due_date_user_local') or row.get('response_deadline'))}")
    lines.append(f"- **Fit Score:** {safe_text(row.get('fit_score'))}")
    lines.append(f"- **Prime Reality Score:** {safe_text(row.get('prime_reality_score'))}")
    lines.append(f"- **Current Scout Recommendation:** {safe_text(row.get('conditional_recommendation') or row.get('recommendation'))}")
    lines.append(f"- **SAM.gov Link:** {safe_text(row.get('ui_link'))}")
    lines.append("")
    lines.append("## Strategic Call")
    lines.append("")
    lines.append(f"**Recommended Strategy:** {strategy}")
    lines.append("")
    lines.append(f"**Why:** {strategy_reason}")
    lines.append("")
    lines.append("## Recommended Response Lanes")
    lines.append("")

    for lane in lanes:
        hits = ", ".join(lane["hits"]) if lane["hits"] else "No strong keyword hits detected"
        lines.append(f"### {lane['lane']}")
        lines.append("")
        lines.append(f"- **Detected Clues:** {hits}")
        lines.append(f"- **Positioning:** {lane['positioning']}")
        lines.append("")

    lines.append("## Possible Workshare / Subcontract Roles")
    lines.append("")
    for option in workshare_options:
        lines.append(f"- {option}")

    lines.append("")
    lines.append("## Teaming Partner Targets")
    lines.append("")
    for target in teaming_targets:
        lines.append(f"- {target}")

    lines.append("")
    lines.append("## Shaping Questions to Ask the Agency")
    lines.append("")
    for index, question in enumerate(questions, start=1):
        lines.append(f"{index}. {question}")

    lines.append("")
    lines.append("## Recommended Sources Sought Response Outline")
    lines.append("")
    for item in outline:
        lines.append(item)

    lines.append("")
    lines.append("## Response Strategy Notes")
    lines.append("")
    lines.append("- Keep the response short, credible, and tightly mapped to the agency need.")
    lines.append("- Do not overclaim prime capability if the stronger path is subcontract support.")
    lines.append("- Use this as an agency-shaping and relationship-building opportunity, not only a bid/no-bid event.")
    lines.append("- If eligibility blocks prime pursuit, use the notice to identify qualified primes before the formal solicitation drops.")
    lines.append("- Capture agency language and keywords for future capability statements, teaming emails, and proposal templates.")
    lines.append("")
    lines.append("## Suggested Follow-Up Actions")
    lines.append("")
    lines.append("1. Review the SAM.gov notice manually for response format, page limit, and due date.")
    lines.append("2. Identify the contracting officer and technical point of contact.")
    lines.append("3. Decide whether to respond as prime, subcontractor, or market-intel only.")
    lines.append("4. Draft a focused response using the outline above.")
    lines.append("5. Add the agency and likely primes to a follow-up list.")
    lines.append("6. Search USAspending for similar historical awards once the market-intel module is built.")
    lines.append("")
    lines.append("## Draft Positioning Statement")
    lines.append("")
    lines.append("JPTR/RCG can support this requirement through a focused combination of technical documentation, workflow design, AI-assisted process support, digital systems experience, training/content support, and proposal/compliance operations. Where prime performance requirements or socioeconomic eligibility make direct prime pursuit less realistic, JPTR/RCG is positioned to support a qualified prime in a narrow, high-leverage subcontract role.")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")

    print("")
    print(f"Sources sought/RFI plan written to: {output_path}")
    print("")

    return str(output_path)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate a sources sought / RFI response strategy plan from GovCon Scout CSV data."
    )

    parser.add_argument("--notice-id", required=True)
    parser.add_argument("--csv", default=DEFAULT_CSV_PATH)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)

    return parser.parse_args()


def main():
    args = parse_args()
    notice_id = make_safe_name(args.notice_id)
    row = load_opportunity_from_csv(notice_id, args.csv)

    if not row:
        print("")
        print(f"Notice ID not found in CSV: {notice_id}")
        print(f"CSV: {args.csv}")
        print("")
        raise SystemExit(1)

    write_sources_sought_plan(
        notice_id=notice_id,
        row=row,
        output_dir=args.output_dir,
    )


if __name__ == "__main__":
    main()