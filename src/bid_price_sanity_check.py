import argparse
import csv
import re
from datetime import date
from pathlib import Path

DEFAULT_CSV = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_OUTPUT_DIR = "reports/pricing"
DEFAULT_INTEL_DIR = "reports/market_intel"
DEFAULT_PRICING_DIR = "reports/pricing"
DEFAULT_EXTRACT_DIR = "reports/document_extracts"
DEFAULT_REVIEWS_DIR = "reports/opportunity_reviews"


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def money(value):
    try:
        n = float(str(value).replace("$", "").replace(",", ""))
        return f"${n:,.0f}"
    except (TypeError, ValueError):
        return str(value) if value else ""


def load_notice_row(notice_id, csv_path):
    path = Path(csv_path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if safe_text(row.get("notice_id")) == notice_id:
                return dict(row)
            if safe_text(row.get("solicitation_number")) == notice_id:
                return dict(row)
    return {}


def read_file(path):
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def parse_award_range(intel_text):
    result = {}
    patterns = {
        "min": r"\*\*Minimum:\*\*\s+\$?([\d,]+)",
        "median": r"\*\*Median:\*\*\s+\$?([\d,]+)",
        "avg": r"\*\*Average:\*\*\s+\$?([\d,]+)",
        "max": r"\*\*Maximum:\*\*\s+\$?([\d,]+)",
        "total": r"\*\*Total returned award value:\*\*\s+\$?([\d,]+)",
        "count": r"\*\*Awards found:\*\*\s+(\d+)",
        "date_range": r"\*\*Date range in returned awards:\*\*\s+(.+)",
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, intel_text)
        if m:
            val = m.group(1).strip().replace(",", "")
            if key == "date_range":
                result[key] = val
            else:
                try:
                    result[key] = int(val)
                except ValueError:
                    result[key] = val
    return result


def parse_top_recipients(intel_text):
    recipients = []
    pattern = r"- \*\*(.+?):\*\*\s+(\d+)\s+award\(s\),\s+\$([\d,]+)"
    for m in re.finditer(pattern, intel_text):
        recipients.append({
            "name": m.group(1),
            "count": int(m.group(2)),
            "total": int(m.group(3).replace(",", "")),
        })
    return recipients[:5]


def parse_clins_from_pricing_md(pricing_text):
    clins = []
    header_found = False
    for line in pricing_text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        if "Period" in line and "CLIN" in line and "Description" in line:
            header_found = True
            continue
        if header_found and re.match(r"^\|[-| ]+\|$", line):
            continue
        if header_found and line.startswith("|"):
            parts = [p.strip() for p in line.split("|")]
            parts = [p for p in parts if p != ""]
            if len(parts) < 3:
                continue
            period = parts[0]
            clin = parts[1]
            desc = parts[2]
            qty = parts[3] if len(parts) > 3 else ""
            unit = parts[4] if len(parts) > 4 else ""
            if not clin or "Total" in desc or not re.match(r"^\d{4}$", clin):
                continue
            clins.append({
                "period": period,
                "clin": clin,
                "description": desc,
                "qty": qty,
                "unit": unit,
            })
    return clins


PERIOD_LABELS = [
    ("7/1/2026", "Base Period (FY27, Jul 2026 – Jun 2027)"),
    ("7/1/2027", "Option Period 1 (FY28, Jul 2027 – Jun 2028)"),
    ("7/1/2028", "Option Period 2 (FY29, Jul 2028 – Jun 2029)"),
    ("7/1/2029", "Option Period 3 (FY30, Jul 2029 – Jun 2030)"),
    ("7/1/2030", "Option Period 4 (FY31, Jul 2030 – Jun 2031)"),
    ("7/1/2031", "Option to Extend (6 months, Jul – Dec 2031)"),
]


def group_clins_by_period(clins):
    groups = {}
    order = []
    for clin in clins:
        label = None
        for start_date, period_label in PERIOD_LABELS:
            if start_date in clin.get("period", ""):
                label = period_label
                break
        if label is None:
            label = clin.get("period", "Unknown Period")
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append(clin)
    return groups, order


LANE_ALIASES = {
    "janitorial_facility_support": "janitorial",
    "janitorial_facilities": "janitorial",
    "trucking_transportation": "trucking",
    "towing_hauling": "towing",
    "roofing_hvac_trades": "roofing/HVAC",
    "facilities_services": "facilities",
}

LANE_EXPECTED_MAX = {
    "janitorial": 2_000_000,
    "janitorial_facility_support": 2_000_000,
    "pest_control": 5_000_000,
    "landscaping": 3_000_000,
    "roofing_hvac_trades": 5_000_000,
    "facilities_services": 5_000_000,
    "flooring": 2_000_000,
    "basic_security": 5_000_000,
    "trucking_transportation": 3_000_000,
    "towing_hauling": 1_000_000,
    "commodities": 2_000_000,
}


def award_range_is_noisy(award_range, lane):
    min_val = award_range.get("min")
    max_val = award_range.get("max")
    if not min_val or not max_val:
        return False
    expected_max = LANE_EXPECTED_MAX.get(lane, 20_000_000)
    # Flag if the minimum alone exceeds 3x expected max for this lane
    if min_val > expected_max * 3:
        return True
    # Flag if the spread is absurdly wide (max/min > 500x typically indicates mixed contract types)
    if min_val > 0 and max_val / min_val > 500:
        return True
    return False


ROUTINE_LANES = {
    "janitorial", "janitorial_facility_support", "pest_control",
    "trucking_transportation", "towing_hauling", "roofing_hvac_trades",
    "facilities_services", "landscaping", "flooring", "basic_security", "commodities",
}


def auto_classify(row):
    lane = safe_text(row.get("base_lane") or row.get("matched_lane"))
    naics = safe_text(row.get("naics_code", ""))
    psc = safe_text(row.get("psc_code") or row.get("psc", ""))

    if not lane or lane == "unknown":
        naics_map = {"561710": "pest_control", "561720": "janitorial", "484": "trucking_transportation"}
        for prefix, mapped in naics_map.items():
            if naics.startswith(prefix):
                lane = mapped
                break

    if lane in ("pest_control", "janitorial", "commodities"):
        feasibility = "easy_to_source"
    elif lane in ROUTINE_LANES:
        feasibility = "moderate_to_source"
    else:
        feasibility = "unknown"

    if lane in ROUTINE_LANES:
        return {
            "base_lane": lane,
            "specialization_level": "routine_commercial",
            "fulfillment_path": "prime_with_subcontractor",
            "subcontractor_feasibility": feasibility,
            "prime_control_risk": "low" if feasibility == "easy_to_source" else "medium",
            "prime_control_recommended_action": "",
        }

    return {
        "base_lane": lane,
        "specialization_level": "",
        "fulfillment_path": "",
        "subcontractor_feasibility": "",
        "prime_control_risk": "",
        "prime_control_recommended_action": "",
    }


def extract_prime_control(row, overrides=None):
    fields = [
        "base_lane",
        "specialization_level",
        "fulfillment_path",
        "subcontractor_feasibility",
        "prime_control_risk",
        "prime_control_recommended_action",
    ]
    result = {f: safe_text(row.get(f)) for f in fields}

    # auto-classify from matched_lane when CSV fields are unpopulated
    if not any(result[f] for f in ("fulfillment_path", "prime_control_risk", "subcontractor_feasibility")):
        classified = auto_classify(row)
        for f in fields:
            if not result[f] and classified.get(f):
                result[f] = classified[f]

    if overrides:
        for f in fields:
            if overrides.get(f):
                result[f] = overrides[f]

    return result


def subcontractor_quote_guidance(pc):
    path = pc.get("fulfillment_path", "")
    feasibility = pc.get("subcontractor_feasibility", "")
    risk = pc.get("prime_control_risk", "")
    lines = []

    if path in ("prime_with_subcontractor", "prime_with_qualified_subcontractor"):
        lines.append("- Subcontractor execution is required. JPTR/RCG holds the prime; a licensed pest control vendor performs the work.")
        if feasibility == "easy_to_source":
            lines.append("- Subcontractor market for pest control is broad and competitive. Local vendors are generally available without unusual licensing barriers.")
            lines.append("- **Get 2–3 firm quotes** covering all CLINs, all periods, and the Fort Campbell site before finalizing pricing.")
        elif feasibility == "moderate_to_source":
            lines.append("- Sourcing will take some effort. Allow 2–4 weeks to identify and qualify vendors.")
            lines.append("- Get at least 2 quotes. Confirm license requirements for Kentucky and military installation work.")
        elif feasibility == "specialized_required":
            lines.append("- Specialized subcontractor required. Allow extra time; verify certifications and insurance before pricing.")
        elif feasibility == "rare_or_highly_regulated":
            lines.append("- Subcontractor market is rare or heavily regulated. Do not price without a confirmed, qualified sub.")
        else:
            lines.append("- Subcontractor feasibility not classified. Manually assess vendor availability before pricing.")
    elif path == "direct_prime":
        lines.append("- Classified as direct prime. No subcontractor quoting required — price from direct labor and materials.")
    elif path == "teaming_only":
        lines.append("- Teaming only path. Identify a teaming partner before pricing.")
    elif path == "pass":
        lines.append("- This opportunity is classified as a pass. Pricing not recommended.")
    else:
        lines.append("- Fulfillment path not set. Review prime-control classification before pricing.")

    if risk == "low":
        lines.append("- Prime control risk is **low**. Standard sub management overhead (5–10% markup) is appropriate.")
        lines.append("- Replace-ability of sub is straightforward in this lane. Execution risk is manageable.")
    elif risk == "medium":
        lines.append("- Prime control risk is **medium**. Add 10–15% management and oversight margin.")
    elif risk == "high":
        lines.append("- Prime control risk is **high**. Do not price without confirmed, qualified sub and legal review.")

    return lines or ["- Prime-control classification not available. Review before pricing."]


def build_report(notice_id, row, intel_text, pricing_text, today_str, overrides=None):
    award_range = parse_award_range(intel_text)
    top_recipients = parse_top_recipients(intel_text)
    clins = parse_clins_from_pricing_md(pricing_text)
    clins_grouped, period_order = group_clins_by_period(clins)
    pc = extract_prime_control(row, overrides=overrides)

    title = safe_text(row.get("title")) or "Fort Campbell Integrated Pest Management (IPM) Services"
    agency = safe_text(row.get("department_ind_agency") or row.get("agency"))
    deadline = safe_text(row.get("response_deadline_local") or row.get("response_deadline"))
    fit_score = safe_text(row.get("fit_score")) or "65"
    naics = safe_text(row.get("naics_code")) or "561710"
    set_aside = safe_text(row.get("set_aside")) or "Total Small Business Set-Aside (FAR 19.5)"

    full_period_count = sum(1 for lbl in period_order if "Base" in lbl or "Option Period" in lbl)

    out = []

    out.append(f"# Bid Price Sanity Check — {notice_id}")
    out.append("")
    out.append(f"**Generated:** {today_str}")
    out.append(f"**Notice ID:** {notice_id}")
    out.append(f"**Title:** {title}")
    if agency:
        out.append(f"**Agency:** {agency}")
    if deadline:
        out.append(f"**Deadline:** {deadline}")
    out.append(f"**Fit Score:** {fit_score}")
    out.append(f"**NAICS:** {naics}")
    out.append(f"**Set-Aside:** {set_aside}")
    out.append("")

    out.append("## Purpose")
    out.append("")
    out.append(
        "This report synthesizes the extracted CLIN structure, USAspending historical award data, "
        "and prime-control classification to support a pricing realism check. "
        "**It does not produce a bid price.** It surfaces the inputs, risks, and validation steps "
        "needed before committing to a number."
    )
    out.append("")

    out.append("## Prime-Control Classification")
    out.append("")
    label_map = {
        "base_lane": "Base Lane",
        "specialization_level": "Specialization Level",
        "fulfillment_path": "Fulfillment Path",
        "subcontractor_feasibility": "Subcontractor Feasibility",
        "prime_control_risk": "Prime Control Risk",
        "prime_control_recommended_action": "Recommended Action",
    }
    for key, label in label_map.items():
        val = pc.get(key) or "(not set)"
        out.append(f"- **{label}:** {val}")
    out.append("")

    out.append("## Historical Award Range (USAspending)")
    out.append("")
    lane = pc.get("base_lane", "")
    noisy = award_range_is_noisy(award_range, lane) if award_range else False

    lane_display = LANE_ALIASES.get(lane, lane) if lane else "unknown"
    if award_range and noisy:
        out.append("> **DATA QUALITY WARNING:** The award range returned by USAspending appears to contain")
        out.append("> unrelated contracts. The minimum award value ({}) is far above what is expected".format(money(award_range.get("min", 0))))
        out.append("> for this lane (`{}`). The figures below should NOT be used for pricing.".format(lane_display))
        out.append("> Re-run USAspending with tighter filters or search FPDS manually for comparable awards.")
        out.append("")
        out.append("| Metric | Value | Status |")
        out.append("|---|---:|---|")
        for key, label in [("min", "Minimum"), ("median", "Median"), ("avg", "Average"), ("max", "Maximum")]:
            if key in award_range:
                out.append(f"| {label} | {money(award_range[key])} | NOT RELIABLE |")
        out.append("")
        count = award_range.get("count", "unknown")
        date_range_str = award_range.get("date_range", "unknown")
        out.append(f"- Awards returned: {count} (likely mixed contract types — validate before use)")
        out.append(f"- Date range in data: {date_range_str}")
    elif award_range:
        naics = safe_text(row.get("naics_code", ""))
        psc = safe_text(row.get("psc_code") or row.get("psc", ""))
        count = award_range.get("count", "unknown")
        date_range_str = award_range.get("date_range", "unknown")
        out.append(f"- **Awards analyzed:** {count} deduplicated contract awards (NAICS {naics} / PSC {psc})")
        out.append(f"- **Award date range in data:** {date_range_str}")
        out.append("")
        out.append("| Metric | Value |")
        out.append("|---|---:|")
        for key, label in [("min", "Minimum"), ("median", "Median"), ("avg", "Average"), ("max", "Maximum")]:
            if key in award_range:
                out.append(f"| {label} | {money(award_range[key])} |")
        out.append("")
        out.append(
            "> **Important:** These figures are from comparable awards across the federal market — "
            "not this specific location or agency. Award amounts vary by scope, facility count, "
            "period of performance, location, and option structure. Use as a sanity check range, not a bid target."
        )
    else:
        out.append("- USAspending intel not found or award range not parsed.")
        out.append(f"  Run: `python src/usaspending_intel.py --notice-id {notice_id} --limit 10`")
    out.append("")

    if top_recipients:
        out.append("### Likely Incumbents / Competitors")
        out.append("")
        out.append("| Recipient | Awards | Total Value |")
        out.append("|---|---:|---:|")
        for r in top_recipients:
            out.append(f"| {r['name']} | {r['count']} | {money(r['total'])} |")
        out.append("")
        out.append(
            "> Research these firms before finalizing pricing. They represent the most active "
            "recipients in similar federal pest control awards. Some may be incumbents; others "
            "are pricing benchmarks and likely competitors."
        )
        out.append("")

    out.append("## CLIN Structure")
    out.append("")
    if clins_grouped:
        out.append(f"**{len(clins)} CLINs extracted across {len(period_order)} performance periods.**")
        out.append(f"Contract structure: {full_period_count} full annual periods + option to extend.")
        out.append("")
        for label in period_order:
            out.append(f"### {label}")
            out.append("")
            out.append("| CLIN | Description | Qty | Unit |")
            out.append("|---|---|---:|---|")
            for c in clins_grouped[label]:
                out.append(f"| {c['clin']} | {c['description']} | {c['qty']} | {c['unit']} |")
            out.append("")
    else:
        out.append("- Pricing schedule not found or CLINs not parsed.")
        out.append(f"  Check: `reports/pricing/{notice_id}_pricing_schedule.md`")
        out.append(f"  Or: `reports/document_extracts/{notice_id}/ATT_4__Pricing_Schedule.txt`")
    out.append("")

    out.append("## Pricing Realism Notes")
    out.append("")
    out.append("**Contract type:** Firm Fixed Price (FFP). All costs must be embedded in unit prices.")
    out.append("")
    out.append("**Structural observations:**")
    out.append("")
    if full_period_count >= 5:
        out.append(
            f"- **{full_period_count}-year FFP with option to extend.** Unit prices must absorb inflation, "
            "supply cost escalation, and labor increases over the full performance period."
        )
    out.append(
        "- **5 service types per period.** Ant/Roach, Field/Turf, Termite, Wasp/Bee, Rodent/Small Animal — "
        "each CLIN must include all labor, materials, travel, callbacks, and contingency for that scope."
    )
    out.append(
        "- **School environment.** DoDEA Fort Campbell serves ~4,000 students across 6 schools. "
        "Scheduling, chemical safety, staff coordination, and possible after-hours/weekend execution "
        "add labor overhead beyond basic field-time estimates."
    )
    out.append(
        "- **Military installation access.** Fort Campbell base access requires badge/escort "
        "coordination, personnel vetting, and security compliance. Build mobilization time and "
        "overhead into per-visit cost assumptions."
    )
    if award_range.get("min") and award_range.get("max") and award_range.get("median"):
        low = money(award_range["min"])
        high = money(award_range["max"])
        med = money(award_range["median"])
        out.append(
            f"- **Market range benchmark:** Similar federal awards range from {low} to {high} (median {med}). "
            "These are full-contract award totals. Divide by scope and period count to calibrate per-year assumptions."
        )
    out.append("")

    out.append("## Underpricing Risks")
    out.append("")
    out.append("- **Callbacks not priced in.** FFP means re-treatment is at no additional cost to the Government. "
               "Each CLIN unit price must absorb callback frequency for that service type.")
    out.append("- **Access / scheduling overhead omitted.** Base entry delays, escort time, and school-day "
               "coordination add real time cost. Field-only quotes from subs may not capture this.")
    out.append("- **Material escalation over option years.** Pesticides, supplies, and equipment costs can "
               "rise over a 5-year contract. Fixed unit prices may compress margin in later option years.")
    out.append("- **Emergency / after-hours scope unclear.** Confirm whether the PWS requires out-of-hours "
               "response and whether it is included in base CLINs or separately priced.")
    out.append("- **Sub quote obtained too late.** If subcontractor pricing is gathered informally or "
               "at the last minute, unit prices may not reflect actual execution cost. Get quotes early.")
    out.append("")

    out.append("## Overpricing Risks")
    out.append("")
    out.append("- **Active small business competition.** Multiple established vendors have won similar "
               "federal pest control awards. Pricing well above the market median risks non-competitive standing.")
    out.append("- **Small business set-aside pool narrows field but not pricing.** Competitors are all "
               "small businesses, but pest control is a mature small-business market with capable, "
               "price-competitive vendors.")
    out.append("- **Unbalanced CLIN structure.** Stacking excessive contingency into one CLIN while "
               "underpricing another can trigger an evaluation notice or disqualification for unbalanced pricing.")
    out.append("")

    out.append("## Subcontractor Quote Requirements")
    out.append("")
    for line in subcontractor_quote_guidance(pc):
        out.append(line)
    out.append("")

    fulfillment = pc.get("fulfillment_path", "")
    if fulfillment in ("prime_with_subcontractor", "prime_with_qualified_subcontractor"):
        out.append("**Minimum quote package to send each vendor candidate:**")
        out.append("")
        out.append("- **Scope:** Integrated pest management (IPM) services — DoDEA Fort Campbell schools")
        out.append("- **Facilities:** 6 schools, ~4,000 students (confirm facility list from ATT_2 / footprint map)")
        out.append("- **CLINs:** Ant/Roach, Field/Turf, Termite, Wasp/Bee, Rodent/Small Animal (5 types)")
        out.append("- **Period:** Base year + 4 option years + 6-month extension (Jul 2026 – Dec 2031)")
        out.append("- **Site:** Fort Campbell, KY — military installation; base access and badge compliance required")
        out.append("- **Environment:** School operations; chemical handling safety, staff coordination, scheduling constraints")
        out.append("- **Format:** Unit price per CLIN per period. Must include all labor, materials, travel, and callbacks.")
        out.append("- **Deadline for quotes:** Allow at least 5 business days before your proposal submission.")
        out.append("")

    out.append("## Margin and Control Warnings")
    out.append("")
    risk = pc.get("prime_control_risk", "")
    if risk == "low":
        out.append("- Prime control risk is rated **low**. Standard sub management markup (5–10%) is appropriate.")
        out.append("- JPTR/RCG holds the prime contract and manages performance accountability. "
                   "Sub failure is a management issue, not a technical skill gap.")
        out.append("- Confirm sub has valid pest control licensing for Kentucky and can operate on a military installation.")
        out.append("- Push all applicable FAR/DFARS flow-downs, base access obligations, WAWF billing "
                   "requirements, and quality control obligations to the subcontractor via teaming/subcontract agreement.")
    elif risk == "medium":
        out.append("- Prime control risk is rated **medium**. Include 10–15% management and oversight margin.")
        out.append("- Identify a backup subcontractor or teaming partner before finalizing pricing.")
    elif risk == "high":
        out.append("- Prime control risk is rated **high**. Do not commit to pricing without a confirmed, "
                   "qualified subcontractor. Consult legal before proceeding.")
    else:
        out.append("- Prime control risk not classified. Complete prime-control review before pricing.")
    out.append("")

    out.append("## Recommended Pricing Next Actions")
    out.append("")
    if noisy or not award_range.get("min"):
        range_note = "USAspending range unreliable for this opportunity — search FPDS or GSA Advantage for comparable awards"
    else:
        low_str = money(award_range.get("min", 0))
        high_str = money(award_range.get("max", 0))
        med_str = money(award_range.get("median", 0))
        range_note = f"historical market range {low_str} – {high_str}, median {med_str}"

    lane_label = LANE_ALIASES.get(lane, lane).replace("_", " ") if lane else "subcontractor"
    out.append(f"1. **Get 2–3 {lane_label} subcontractor quotes** — all CLINs, all periods, site access and compliance included.")
    out.append(f"2. **Compare sub quotes against comparable award data** ({range_note}) to pressure-test realism.")
    out.append("3. **Build price from CLINs up** — do not work backward from a competitor total. Each CLIN needs its own cost buildup.")
    out.append("4. **Request site visit notes** — the May 18–19 site visits may have Q&A or clarifications "
               "distributed to all offerors. Request from CO before pricing.")
    out.append("5. **Confirm after-hours / emergency scope** — if out-of-hours callbacks are required, "
               "price them explicitly or document they are absorbed in base CLIN frequency assumptions.")
    out.append("6. **Verify sub licensing** — confirm the subcontractor holds a valid Kentucky pest control "
               "license and can operate under military installation access rules.")
    out.append("7. **Submit RFIs by May 22** — see decision report for question deadline and CO contact.")
    out.append("8. **Complete pricing schedule exactly as provided** — do not alter CLIN structure. "
               "Submit all base and option year unit prices per solicitation instructions.")
    out.append("")

    out.append("## Source Files Used")
    out.append("")
    out.append(f"- `reports/market_intel/{notice_id}_usaspending_intel.md`")
    out.append(f"- `reports/pricing/{notice_id}_pricing_schedule.md`")
    out.append(f"- `reports/document_extracts/{notice_id}/ATT_4__Pricing_Schedule.txt`")
    out.append(f"- `reports/opportunity_reviews/{notice_id}_decision_report.md`")
    out.append(f"- `reports/opportunity_reviews/{notice_id}_compliance_matrix.md`")
    out.append(f"- `{DEFAULT_CSV}`")
    out.append("")

    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(
        description="Bid price sanity check — combines pricing schedule, USAspending history, and prime-control classification."
    )
    parser.add_argument("--notice-id", required=True, help="Opportunity notice ID")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Pipeline CSV path")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Output directory for report")
    parser.add_argument("--intel-dir", default=DEFAULT_INTEL_DIR, help="USAspending intel directory")
    parser.add_argument("--pricing-dir", default=DEFAULT_PRICING_DIR, help="Pricing schedule directory")
    parser.add_argument("--reviews-dir", default=DEFAULT_REVIEWS_DIR, help="Opportunity reviews directory")
    parser.add_argument("--base-lane", default="", help="Override: base_lane classification")
    parser.add_argument("--specialization-level", default="", help="Override: specialization_level")
    parser.add_argument("--fulfillment-path", default="", help="Override: fulfillment_path")
    parser.add_argument("--subcontractor-feasibility", default="", help="Override: subcontractor_feasibility")
    parser.add_argument("--prime-control-risk", default="", help="Override: prime_control_risk (low/medium/high)")
    args = parser.parse_args()

    notice_id = args.notice_id.strip()
    today_str = date.today().isoformat()

    row = load_notice_row(notice_id, args.csv)
    if not row:
        print(f"[WARN] {notice_id} not found in {args.csv} — using empty row.")

    intel_path = Path(args.intel_dir) / f"{notice_id}_usaspending_intel.md"
    intel_text = read_file(intel_path)
    if not intel_text:
        print(f"[WARN] USAspending intel not found: {intel_path}")
        print(f"       Run: python src/usaspending_intel.py --notice-id {notice_id} --limit 10")

    pricing_path = Path(args.pricing_dir) / f"{notice_id}_pricing_schedule.md"
    pricing_text = read_file(pricing_path)
    if not pricing_text:
        extract_fallback = Path(DEFAULT_EXTRACT_DIR) / notice_id / "ATT_4__Pricing_Schedule.txt"
        pricing_text = read_file(extract_fallback)
        if pricing_text:
            print(f"[INFO] Pricing schedule MD not found; using raw extract: {extract_fallback}")
        else:
            print(f"[WARN] No pricing schedule found for {notice_id}.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{notice_id}_bid_price_sanity_check.md"

    overrides = {
        "base_lane": args.base_lane,
        "specialization_level": args.specialization_level,
        "fulfillment_path": args.fulfillment_path,
        "subcontractor_feasibility": args.subcontractor_feasibility,
        "prime_control_risk": args.prime_control_risk,
    }
    report = build_report(notice_id, row, intel_text, pricing_text, today_str, overrides=overrides)
    output_path.write_text(report, encoding="utf-8")

    line_count = len(report.splitlines())
    print(f"[OK] Bid price sanity check written: {output_path}")
    print(f"     Lines: {line_count}")


if __name__ == "__main__":
    main()
