import argparse
import csv
import re
import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


DEFAULT_CSV = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_OUTPUT_DIR = "reports/pricing"


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def safe_float(value):
    text = safe_text(value).replace("$", "").replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def money(value):
    amount = safe_float(value)
    return f"${amount:,.0f}" if amount is not None else "Not available"


def read_text(path):
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def read_csv_rows(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as file:
        return [dict(row) for row in csv.DictReader(file)]


def load_notice_row(notice_id, csv_path):
    for row in read_csv_rows(csv_path):
        if notice_id in {
            safe_text(row.get("notice_id")),
            safe_text(row.get("solicitation_number")),
            safe_text(row.get("sam_notice_id")),
        }:
            return row
    return {"notice_id": notice_id}


def paths_for(notice_id, csv_path):
    return {
        "opportunities csv": Path(csv_path),
        "pricing schedule": Path("reports/pricing") / f"{notice_id}_pricing_schedule.md",
        "pricing table": Path("reports/pricing") / f"{notice_id}_pricing_table.csv",
        "USAspending intel": Path("reports/market_intel") / f"{notice_id}_usaspending_intel.md",
        "USAspending awards": Path("reports/market_intel") / f"{notice_id}_usaspending_awards.csv",
        "decision report": Path("reports/opportunity_reviews") / f"{notice_id}_decision_report.md",
        "compliance matrix": Path("reports/opportunity_reviews") / f"{notice_id}_compliance_matrix.md",
        "bid/no-bid review": Path("reports/opportunity_reviews") / f"{notice_id}_bid_no_bid.md",
        "analysis packet": Path("reports/analysis_packets") / f"{notice_id}.md",
    }


def is_clin_row(row):
    return bool(re.fullmatch(r"[A-Za-z0-9]{4,}", safe_text(row.get("clin"))))


def looks_formula(value):
    return safe_text(value).startswith("=")


def summarize_pricing(rows):
    clin_rows = [row for row in rows if is_clin_row(row)]
    periods = []
    descriptions = []
    quantities_units = []
    blank_unit_price_count = 0
    formula_total_count = 0

    for row in clin_rows:
        period = safe_text(row.get("period"))
        if period and period not in periods:
            periods.append(period)
        description = safe_text(row.get("description"))
        if description and description not in descriptions:
            descriptions.append(description)
        quantity_unit = " ".join(part for part in [safe_text(row.get("quantity")), safe_text(row.get("unit"))] if part)
        if quantity_unit and quantity_unit not in quantities_units:
            quantities_units.append(quantity_unit)
        if not safe_text(row.get("unit_price")):
            blank_unit_price_count += 1
        if looks_formula(row.get("total_price")) or looks_formula(row.get("extended_price")):
            formula_total_count += 1

    formula_rows = sum(
        1 for row in rows
        if any(looks_formula(row.get(field)) for field in ["total_price", "extended_price", "raw"])
    )
    return {
        "row_count": len(rows),
        "clin_count": len(clin_rows),
        "periods": periods,
        "descriptions": descriptions,
        "quantities_units": quantities_units,
        "blank_unit_price_count": blank_unit_price_count,
        "formula_clin_count": formula_total_count,
        "formula_row_count": formula_rows,
    }


def award_amount(row):
    for field in ["total_obligation", "award_amount", "base_and_all_options_value"]:
        amount = safe_float(row.get(field))
        if amount is not None:
            return amount
    return None


def summarize_awards(rows, intel_text):
    values = [amount for amount in (award_amount(row) for row in rows) if amount is not None]
    recipient_counts = Counter()
    recipient_values = defaultdict(float)
    naics = Counter()
    psc = Counter()

    for row in rows:
        recipient = safe_text(row.get("recipient_name")) or "Unknown recipient"
        recipient_counts[recipient] += 1
        recipient_values[recipient] += award_amount(row) or 0
        if safe_text(row.get("naics")):
            naics[safe_text(row.get("naics"))] += 1
        if safe_text(row.get("psc")):
            psc[safe_text(row.get("psc"))] += 1

    agency_weak = agency_queries_weak(intel_text)
    broad_context = agency_weak or broad_scope_warning(intel_text, values)
    return {
        "count": len(rows),
        "values": values,
        "recipient_counts": recipient_counts.most_common(5),
        "recipient_values": sorted(recipient_values.items(), key=lambda item: item[1], reverse=True)[:5],
        "naics": naics.most_common(5),
        "psc": psc.most_common(5),
        "agency_weak": agency_weak,
        "context": "Broad market context" if broad_context else "Candidate comparable context",
    }


def agency_queries_weak(intel_text):
    rows = [
        line for line in intel_text.splitlines()
        if line.startswith("| Query ") and "agency" in line.lower()
    ]
    return bool(rows) and all(re.search(r"\|\s*0\s*\|", line) for line in rows)


def broad_scope_warning(intel_text, values):
    if "Healthcare Housekeeping" in intel_text or "ASEPTIC" in intel_text:
        return True
    if values and statistics.median(values) > 10_000_000:
        return True
    return False


def contains_any(text, terms):
    text = text.lower()
    return any(term.lower() in text for term in terms)


def likely_subcontractable_lane(row):
    context = " ".join([
        safe_text(row.get("title")),
        safe_text(row.get("base_lane")),
        safe_text(row.get("matched_lane")),
        safe_text(row.get("score_reasons")),
    ])
    return contains_any(context, ["pest", "janitor", "custodial", "facilities", "termite"])


def risk_flags(row, pricing, awards, review_text):
    flags = []
    if pricing["blank_unit_price_count"]:
        flags.append(f"Blank unit prices detected on {pricing['blank_unit_price_count']} CLIN row(s).")
    if pricing["formula_clin_count"] and pricing["blank_unit_price_count"]:
        flags.append("Formula totals appear in the pricing table before unit prices are entered.")
    if awards["context"] == "Broad market context":
        flags.append("USAspending matches appear to be broad market context, not a close price benchmark.")
    if awards["agency_weak"]:
        flags.append("Agency-specific USAspending searches were weak or returned zero matches.")
    if contains_any(review_text, ["firm fixed price", "fixed price line", "pricing arrangement: firm fixed price"]):
        flags.append("Firm fixed price language raises estimate, inflation, and contingency discipline risk.")
    if contains_any(review_text, ["site visit", "site-visit"]):
        flags.append("Site visit details may affect scope certainty and priceability.")
    if contains_any(review_text, ["base access", "installation access", "campbell", "installation "]):
        flags.append("Local performance or installation access should be validated before pricing.")
    fulfillment_path = safe_text(row.get("fulfillment_path"))
    feasibility = safe_text(row.get("subcontractor_feasibility"))
    if (
        "subcontractor" in fulfillment_path
        or feasibility in {"easy_to_source", "moderate_to_source", "specialized_required"}
        or likely_subcontractable_lane(row)
    ):
        flags.append("Subcontractor/vendor dependency requires quote coverage and execution control.")
    if len(pricing["periods"]) > 1 or contains_any(review_text, ["option period", "option year", "option to extend"]):
        flags.append("Option periods create long-term escalation and scope exposure.")
    return flags


def validation_questions(flags, pricing, awards):
    questions = [
        "Are quantities, service frequencies, period lengths, and CLIN descriptions confirmed against the solicitation workbook?",
        "Which labor, materials, travel, mobilization, insurance, reporting, and contingency costs must be included in each unit price?",
    ]
    if any("Subcontractor" in flag for flag in flags):
        questions.append("Do vendor or subcontractor quotes cover every CLIN, access requirement, base period, and option period?")
    if any("Site visit" in flag for flag in flags):
        questions.append("Did the site visit or related Q&A change assumptions about locations, treatment areas, access, or schedule?")
    if awards["context"] == "Broad market context":
        questions.append("Which historical awards match the actual scope and scale closely enough to keep as comparables?")
    if not pricing["clin_count"]:
        questions.append("Is there a pricing attachment, CLIN schedule, or quote template that still needs extraction?")
    return questions


def next_action(row, pricing, awards, flags):
    if not pricing["clin_count"]:
        return "Pass / Not Priceable Yet"
    if safe_text(row.get("fulfillment_path")) == "teaming_only":
        return "Teaming Recommended Before Pricing"
    if any("Subcontractor/vendor dependency" in flag for flag in flags):
        return "Proceed Only After Vendor/Subcontractor Quote"
    if awards["count"] == 0 or (awards["context"] == "Broad market context" and awards["agency_weak"]):
        return "Market Intel Needs More Validation"
    if any("unit prices" in flag for flag in flags):
        return "Proceed to Pricing Worksheet"
    return "Proceed to Pricing Worksheet"


def compact_list(values, limit=8):
    if not values:
        return "Not available."
    shown = ", ".join(values[:limit])
    return shown + ("." if len(values) <= limit else f", and {len(values) - limit} more.")


def opportunity_snapshot(notice_id, row):
    return [
        f"- **Notice ID:** {notice_id}",
        f"- **Title:** {safe_text(row.get('title')) or 'Not found in CSV.'}",
        f"- **Agency:** {safe_text(row.get('department_ind_agency') or row.get('agency')) or 'Not found in CSV.'}",
        f"- **NAICS / PSC:** {safe_text(row.get('naics_code')) or 'Unknown'} / {safe_text(row.get('psc_code') or row.get('psc')) or 'Unknown'}",
        f"- **Set-aside:** {safe_text(row.get('set_aside')) or 'Not specified.'}",
        f"- **Fit / Prime reality:** {safe_text(row.get('fit_score')) or 'Unknown'} / {safe_text(row.get('prime_reality_score')) or 'Unknown'}",
        f"- **Fulfillment path:** {safe_text(row.get('fulfillment_path')) or 'Not classified.'}",
        f"- **Prime control risk:** {safe_text(row.get('prime_control_risk')) or 'Not classified.'}",
    ]


def amount_lines(awards):
    values = awards["values"]
    if not values:
        return ["- Historical award obligations were not available from the local awards CSV."]
    return [
        f"- **Minimum:** {money(min(values))}",
        f"- **Median:** {money(statistics.median(values))}",
        f"- **Average:** {money(statistics.mean(values))}",
        f"- **Maximum:** {money(max(values))}",
    ]


def stats_lines(items, value_is_money=False):
    if not items:
        return ["- Not available."]
    lines = []
    for label, value in items:
        display = money(value) if value_is_money else value
        lines.append(f"- **{label}:** {display}")
    return lines


def flag_lines(flags):
    if not flags:
        return ["- No automatic risk flags were detected from local artifacts. Manual validation is still required."]
    return [f"- {flag}" for flag in flags]


def underbid_lines(flags, pricing):
    lines = [
        "- Historical awards are context only; small scope differences can change labor, travel, materials, and inspection burden.",
    ]
    if pricing["blank_unit_price_count"]:
        lines.append("- Blank unit prices make it easy to omit low-frequency treatments, callback work, or period-specific costs.")
    if any("Option periods" in flag for flag in flags):
        lines.append("- Option periods can understate escalation risk when vendor quotes or wage/material assumptions stop at the base period.")
    if any("Site visit" in flag or "access" in flag.lower() for flag in flags):
        lines.append("- Site/access uncertainty can turn an apparently routine service into extra trips, wait time, or compliance overhead.")
    return lines


def overbid_lines(awards):
    lines = [
        "- Do not carry a broad historical ceiling into a narrower CLIN schedule without scope and period matching.",
    ]
    if awards["context"] == "Broad market context":
        lines.append("- Returned market awards include broad comparables; relying on their upper range may make a smaller quote noncompetitive.")
    else:
        lines.append("- Validate local subcontractor pricing against the actual quantities before adding management margin and contingency.")
    return lines


def prime_teaming_lines(row, action):
    lines = [
        f"- **Current fulfillment path:** {safe_text(row.get('fulfillment_path')) or 'Not classified.'}",
        f"- **Subcontractor feasibility:** {safe_text(row.get('subcontractor_feasibility')) or 'Not classified.'}",
        f"- **Prime control risk:** {safe_text(row.get('prime_control_risk')) or 'Not classified.'}",
    ]
    if action == "Teaming Recommended Before Pricing":
        lines.append("- Local artifacts suggest pricing should wait until a partner path is validated.")
    elif "subcontractor" in safe_text(row.get("fulfillment_path")):
        lines.append("- Prime pricing can be reviewed only after performer quotes, insurance/licensing checks, and replacement coverage are understood.")
    else:
        lines.append("- Prime versus teaming remains a validation decision; this check does not create a bid price.")
    return lines


def source_lines(paths):
    lines = []
    for label, path in paths.items():
        status = "reviewed" if path.exists() else "not available"
        lines.append(f"- **{label.title()}:** `{path}` {status}")
    return lines


def build_report(notice_id, row, paths):
    pricing_rows = read_csv_rows(paths["pricing table"])
    awards_rows = read_csv_rows(paths["USAspending awards"])
    pricing = summarize_pricing(pricing_rows)
    intel_text = read_text(paths["USAspending intel"])
    awards = summarize_awards(awards_rows, intel_text)
    review_text = "\n".join(
        read_text(paths[key])
        for key in [
            "pricing schedule",
            "decision report",
            "compliance matrix",
            "bid/no-bid review",
            "analysis packet",
        ]
    )
    flags = risk_flags(row, pricing, awards, review_text)
    action = next_action(row, pricing, awards, flags)

    lines = [
        f"# Bid Price Sanity Check - {notice_id}",
        "",
        f"**Generated:** {date.today().isoformat()}",
        "**Method:** Local artifact review only. This report does not call USAspending or generate a bid price.",
        "",
        "## Executive Summary",
        "",
        f"- **Recommended next action:** {action}",
        f"- **Pricing CLIN rows found:** {pricing['clin_count']}",
        f"- **Historical award rows found:** {awards['count']}",
        f"- **Market context read:** {awards['context']}",
        "- Price conclusions require solicitation validation and quote support; historical awards are not pricing instructions.",
        "",
        "## Opportunity Snapshot",
        "",
        *opportunity_snapshot(notice_id, row),
        "",
        "## Pricing Schedule Summary",
        "",
        f"- **Pricing CSV rows:** {pricing['row_count']}",
        f"- **CLIN rows:** {pricing['clin_count']}",
        f"- **Detected periods:** {len(pricing['periods'])}",
        f"- **Base / option signal:** {'Base plus option or extension periods detected.' if len(pricing['periods']) > 1 else 'No option-period sequence detected from the pricing CSV.'}",
        f"- **Service descriptions:** {compact_list(pricing['descriptions'])}",
        f"- **Quantities and units:** {compact_list(pricing['quantities_units'])}",
        f"- **Blank unit prices on CLIN rows:** {pricing['blank_unit_price_count']}",
        f"- **Formula totals on CLIN rows:** {pricing['formula_clin_count']}",
        f"- **Formula rows overall:** {pricing['formula_row_count']}",
        "",
        "## Historical Award Context",
        "",
        f"- **USAspending award rows:** {awards['count']}",
        f"- **Context signal:** {awards['context']}.",
        f"- **Agency-specific query signal:** {'Weak or zero local report matches.' if awards['agency_weak'] else 'No zero-only agency warning detected.'}",
        "",
        "Top recipients by award count:",
        "",
        *stats_lines(awards["recipient_counts"]),
        "",
        "Top recipients by returned obligation/value:",
        "",
        *stats_lines(awards["recipient_values"], value_is_money=True),
        "",
        f"- **Common NAICS:** {compact_list([f'{code} ({count})' for code, count in awards['naics']])}",
        f"- **Common PSC:** {compact_list([f'{code} ({count})' for code, count in awards['psc']])}",
        "",
        "## Market Range Signals",
        "",
        *amount_lines(awards),
        "",
        "## Pricing Risk Flags",
        "",
        *flag_lines(flags),
        "",
        "## Underbid Risk",
        "",
        *underbid_lines(flags, pricing),
        "",
        "## Overbid / Noncompetitive Risk",
        "",
        *overbid_lines(awards),
        "",
        "## Prime vs Teaming Implications",
        "",
        *prime_teaming_lines(row, action),
        "",
        "## Validation Questions Before Pricing",
        "",
    ]

    for question in validation_questions(flags, pricing, awards):
        lines.append(f"- {question}")

    lines.extend([
        "",
        "## Recommended Next Action",
        "",
        f"**{action}**",
        "",
        "Use the action above as a pricing gate, not as a final bid/no-bid decision. Build actual pricing only from validated requirements, vendor/subcontractor support where needed, and solicitation instructions.",
        "",
        "## Source Files Reviewed",
        "",
        *source_lines(paths),
        "",
    ])
    return "\n".join(lines), action


def write_report(notice_id, csv_path, output_dir):
    row = load_notice_row(notice_id, csv_path)
    paths = paths_for(notice_id, csv_path)
    report, action = build_report(notice_id, row, paths)
    output_path = Path(output_dir) / f"{notice_id}_bid_price_sanity.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report, encoding="utf-8")
    return output_path, action


def parse_args():
    parser = argparse.ArgumentParser(description="Build a local bid price sanity check from GovCon Scout artifacts.")
    parser.add_argument("--notice-id", required=True)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--csv", default=DEFAULT_CSV)
    return parser.parse_args()


def main():
    args = parse_args()
    output_path, action = write_report(args.notice_id, args.csv, args.output_dir)
    print(f"Bid price sanity check written to: {output_path}")
    print(f"Recommended next action: {action}")


if __name__ == "__main__":
    main()
