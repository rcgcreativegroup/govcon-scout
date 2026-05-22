import argparse
import csv
import json
import re
import statistics
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path


API_ENDPOINT = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
DEFAULT_CSV = "exports/govcon_scout_opportunities_latest.csv"
DEFAULT_OUTPUT_DIR = "reports/market_intel"
DEFAULT_REVIEW_PACK = "reports/triage/govcon_triage_review_pack.md"
DEFAULT_DEBUG_DIR = "debug/usaspending"
MAX_LIMIT = 100

CONTRACT_AWARD_TYPE_CODES = [
    "A",
    "B",
    "C",
    "D",
]

FIELDS = [
    "Award ID",
    "generated_unique_award_id",
    "PIID",
    "Recipient Name",
    "Start Date",
    "End Date",
    "Award Amount",
    "Total Obligation",
    "Base and All Options Value",
    "Awarding Agency",
    "Awarding Sub Agency",
    "Award Type",
    "Funding Agency",
    "Funding Sub Agency",
    "Description",
    "NAICS",
    "PSC",
    "Place of Performance",
]

AWARD_CSV_FIELDS = [
    "award_id",
    "generated_unique_award_id",
    "piid",
    "award_type",
    "recipient_name",
    "awarding_agency",
    "awarding_subagency",
    "funding_agency",
    "description",
    "naics",
    "psc",
    "award_amount",
    "total_obligation",
    "period_of_performance_start_date",
    "period_of_performance_current_end_date",
    "base_and_all_options_value",
    "place_of_performance",
    "source_query",
]


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def safe_float(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None


def money(value):
    number = safe_float(value)
    if number is None:
        return ""
    return f"${number:,.0f}"


def score_int(value):
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def load_notice_row(notice_id, csv_path):
    path = Path(csv_path)
    if not path.exists():
        return {}

    with path.open("r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            if safe_text(row.get("notice_id")) == notice_id:
                return dict(row)
            if safe_text(row.get("solicitation_number")) == notice_id:
                return dict(row)
            if safe_text(row.get("sam_notice_id")) == notice_id:
                return dict(row)

    return {}


def compact_agency_name(value):
    text = safe_text(value)
    if not text:
        return ""
    parts = [part.strip() for part in text.split(".") if part.strip()]
    return parts[0] if parts else text


def keyword_terms(row):
    title = safe_text(row.get("title"))
    explicit = " ".join([
        safe_text(row.get("matched_keywords")),
        safe_text(row.get("matched_core_strengths")),
    ])
    text = f"{title} {explicit}".lower()
    text = re.sub(r"[^a-z0-9\s-]", " ", text)
    stop = {
        "the",
        "and",
        "for",
        "with",
        "services",
        "service",
        "solicitation",
        "rfq",
        "rfp",
        "combined",
        "synopsis",
        "amendment",
    }
    terms = []
    for word in text.split():
        if len(word) < 4 or word in stop:
            continue
        if word not in terms:
            terms.append(word)
    return terms[:6]


def domain_terms(row):
    terms = keyword_terms(row)
    joined = " ".join(terms)
    phrases = []
    if "pest" in terms:
        phrases.extend(["pest", "rodent", "insect", "exterminat"])
    if "integrated" in terms and "management" in terms and "pest" in terms:
        phrases.append("integrated pest")
    if not phrases:
        phrases = [term for term in terms if term not in {"management", "support", "operation", "maintenance"}]
    return phrases or [joined]


def date_range(lookback_years):
    end = date.today()
    start = end - timedelta(days=365 * max(1, lookback_years))
    return start.isoformat(), end.isoformat()


def base_filters(lookback_years):
    start, end = date_range(lookback_years)
    return {
        "award_type_codes": CONTRACT_AWARD_TYPE_CODES,
        "time_period": [{"start_date": start, "end_date": end}],
    }


def agency_filter(agency):
    return [{
        "type": "awarding",
        "tier": "toptier",
        "name": agency,
    }]


def add_keywords(filters, terms):
    if terms:
        filters["keywords"] = terms


def build_queries(row, lookback_years, overrides=None):
    overrides = overrides or {}
    naics = safe_text(overrides.get("naics") or row.get("naics_code"))
    psc = safe_text(overrides.get("psc") or row.get("psc_code") or row.get("psc"))
    agency = compact_agency_name(overrides.get("agency") or row.get("department_ind_agency") or row.get("agency"))
    terms = overrides.get("keywords") or keyword_terms(row)

    queries = []

    if naics and agency:
        filters = base_filters(lookback_years)
        filters["naics_codes"] = [naics]
        filters["agencies"] = agency_filter(agency)
        queries.append(("Query A - NAICS + agency", filters))

    if psc and agency:
        filters = base_filters(lookback_years)
        filters["psc_codes"] = [psc]
        filters["agencies"] = agency_filter(agency)
        queries.append(("Query B - PSC + agency", filters))

    if naics and terms:
        filters = base_filters(lookback_years)
        filters["naics_codes"] = [naics]
        add_keywords(filters, terms)
        queries.append(("Query C - NAICS + title/lane terms", filters))

    if psc and terms:
        filters = base_filters(lookback_years)
        filters["psc_codes"] = [psc]
        add_keywords(filters, terms)
        queries.append(("Query D - PSC + title/lane terms", filters))

    if terms and not naics and not psc:
        filters = base_filters(lookback_years)
        add_keywords(filters, terms)
        queries.append(("Query E - title/lane terms only", filters))

    if not queries and agency:
        filters = base_filters(lookback_years)
        filters["agencies"] = agency_filter(agency)
        queries.append(("Query F - agency fallback", filters))

    return queries


def make_payload(filters, limit):
    return {
        "filters": filters,
        "fields": FIELDS,
        "page": 1,
        "limit": limit,
        "sort": "Award Amount",
        "order": "desc",
        "subawards": False,
    }


def post_json(url, payload, timeout=30):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "govcon-scout/1.0",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8")), ""
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return {}, f"HTTP {error.code}: {body[:1000]}"
    except urllib.error.URLError as error:
        return {}, f"URL error: {error}"
    except TimeoutError as error:
        return {}, f"Timeout: {error}"
    except json.JSONDecodeError as error:
        return {}, f"JSON decode error: {error}"


def save_debug_response(notice_id, query_name, payload, response, error, debug_dir):
    folder = Path(debug_dir)
    folder.mkdir(parents=True, exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", query_name).strip("_")
    path = folder / f"{notice_id}_{safe_name}.json"
    path.write_text(
        json.dumps({
            "query": query_name,
            "payload": payload,
            "response": response,
            "error": error,
        }, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return str(path)


def result_awards(response):
    results = response.get("results", [])
    if isinstance(results, list):
        return results
    return []


def award_key(award):
    for key in [
        "generated_internal_id",
        "generated_unique_award_id",
        "Award ID",
        "award_id",
        "piid",
        "PIID",
    ]:
        value = safe_text(award.get(key))
        if value:
            return value
    piid = safe_text(get_field(award, "PIID", "piid", "Award ID"))
    recipient = safe_text(get_field(award, "Recipient Name", "recipient_name"))
    amount = safe_text(get_field(award, "Award Amount", "award_amount", "total_obligation"))
    if piid or recipient or amount:
        return f"{piid}|{recipient}|{amount}"
    return json.dumps(award, sort_keys=True)[:200]


def get_field(award, *names):
    for name in names:
        value = award.get(name)
        if value not in (None, ""):
            return value
    return ""


def code_value(value):
    if isinstance(value, dict):
        return safe_text(value.get("code") or value.get("id") or value.get("description"))
    return safe_text(value)


def normalize_award(award, query_name):
    award_amount = safe_float(get_field(award, "Award Amount", "award_amount"))
    total_obligation = safe_float(get_field(award, "Total Obligation", "total_obligation"))
    return {
        "key": award_key(award),
        "query": query_name,
        "award_id": safe_text(get_field(award, "Award ID", "award_id")),
        "generated_unique_award_id": safe_text(get_field(award, "generated_unique_award_id", "generated_internal_id")),
        "piid": safe_text(get_field(award, "PIID", "piid", "Award ID")),
        "recipient": safe_text(get_field(award, "Recipient Name", "recipient_name")),
        "amount": award_amount if award_amount is not None else total_obligation,
        "total_obligation": total_obligation,
        "start_date": safe_text(get_field(
            award,
            "Start Date",
            "start_date",
            "period_of_performance_start_date",
            "date_signed",
        )),
        "end_date": safe_text(get_field(
            award,
            "End Date",
            "end_date",
            "period_of_performance_current_end_date",
        )),
        "awarding_agency": safe_text(get_field(award, "Awarding Agency", "awarding_agency")),
        "awarding_subagency": safe_text(get_field(award, "Awarding Sub Agency", "awarding_sub_agency", "awarding_subagency")),
        "funding_agency": safe_text(get_field(award, "Funding Agency", "funding_agency")),
        "description": safe_text(get_field(award, "Description", "description")),
        "naics": code_value(get_field(award, "NAICS", "naics_code")),
        "psc": code_value(get_field(award, "PSC", "psc_code")),
        "award_type": safe_text(get_field(award, "Award Type", "type_description", "award_type")),
        "base_and_all_options_value": safe_float(get_field(
            award,
            "Base and All Options Value",
            "base_and_all_options_value",
        )),
        "place_of_performance": safe_text(get_field(
            award,
            "Place of Performance",
            "place_of_performance",
        )),
    }


def keyword_award_relevant(award, row):
    haystack = " ".join([
        award.get("description", ""),
        award.get("recipient", ""),
        award.get("awarding_agency", ""),
        award.get("awarding_subagency", ""),
    ]).lower()
    return any(term and term.lower() in haystack for term in domain_terms(row))


def award_matches_structured_inputs(award, row):
    naics = safe_text(row.get("naics_code"))
    psc = safe_text(row.get("psc_code") or row.get("psc"))
    return bool((naics and award.get("naics") == naics) or (psc and award.get("psc") == psc))


def should_keep_award(normalized, row):
    if "title/lane terms only" not in normalized["query"]:
        return True
    return award_matches_structured_inputs(normalized, row) or keyword_award_relevant(normalized, row)


def run_queries(notice_id, row, limit, lookback_years, debug_json, overrides=None):
    awards = {}
    query_summaries = []
    debug_paths = []

    for query_name, filters in build_queries(row, lookback_years, overrides):
        payload = make_payload(filters, limit)
        response, error = post_json(API_ENDPOINT, payload)
        raw_awards = result_awards(response)

        if debug_json:
            debug_paths.append(save_debug_response(
                notice_id=notice_id,
                query_name=query_name,
                payload=payload,
                response=response,
                error=error,
                debug_dir=DEFAULT_DEBUG_DIR,
            ))

        query_summaries.append({
            "name": query_name,
            "filters": filters,
            "result_count": len(raw_awards),
            "error": error,
        })

        if error:
            continue

        for award in raw_awards:
            normalized = normalize_award(award, query_name)
            if not should_keep_award(normalized, row):
                continue
            if normalized["key"] not in awards:
                awards[normalized["key"]] = normalized

    return list(awards.values()), query_summaries, debug_paths


def award_csv_row(award):
    return {
        "award_id": award.get("award_id", ""),
        "generated_unique_award_id": award.get("generated_unique_award_id", ""),
        "piid": award.get("piid", ""),
        "award_type": award.get("award_type", ""),
        "recipient_name": award.get("recipient", ""),
        "awarding_agency": award.get("awarding_agency", ""),
        "awarding_subagency": award.get("awarding_subagency", ""),
        "funding_agency": award.get("funding_agency", ""),
        "description": award.get("description", ""),
        "naics": award.get("naics", ""),
        "psc": award.get("psc", ""),
        "award_amount": award.get("amount", ""),
        "total_obligation": award.get("total_obligation", ""),
        "period_of_performance_start_date": award.get("start_date", ""),
        "period_of_performance_current_end_date": award.get("end_date", ""),
        "base_and_all_options_value": award.get("base_and_all_options_value", ""),
        "place_of_performance": award.get("place_of_performance", ""),
        "source_query": award.get("query", ""),
    }


def write_awards_csv(notice_id, awards, output_dir):
    output_path = Path(output_dir) / f"{notice_id}_usaspending_awards.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=AWARD_CSV_FIELDS)
        writer.writeheader()
        writer.writerows(award_csv_row(award) for award in awards)
    return output_path


def amount_values(awards):
    return [award["amount"] for award in awards if award.get("amount") is not None]


def date_values(awards):
    values = []
    for award in awards:
        value = award.get("start_date")
        if value:
            values.append(value[:10])
    return sorted(values)


def aggregate_recipients(awards):
    stats = {}
    for award in awards:
        recipient = award.get("recipient") or "Unknown recipient"
        amount = award.get("amount") or 0
        item = stats.setdefault(recipient, {"count": 0, "value": 0})
        item["count"] += 1
        item["value"] += amount
    return sorted(stats.items(), key=lambda pair: (pair[1]["value"], pair[1]["count"]), reverse=True)


def aggregate_agencies(awards):
    stats = {}
    for award in awards:
        agency = award.get("awarding_subagency") or award.get("awarding_agency") or "Unknown agency"
        item = stats.setdefault(agency, 0)
        stats[agency] = item + 1
    return sorted(stats.items(), key=lambda pair: pair[1], reverse=True)


def aggregate_codes(awards, field):
    stats = {}
    for award in awards:
        code = award.get(field) or "Unknown"
        stats[code] = stats.get(code, 0) + 1
    return sorted(stats.items(), key=lambda pair: pair[1], reverse=True)


def artifact_text(path):
    path = Path(path)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def pricing_context(notice_id):
    path = Path("reports/pricing") / f"{notice_id}_pricing_schedule.md"
    if not path.exists():
        return "", ""
    text = artifact_text(path)
    line_count = ""
    match = re.search(r"Extracted Pricing Lines:\*\*\s*(\d+)", text)
    if match:
        line_count = match.group(1)
    return str(path), line_count


def related_paths(notice_id):
    candidates = [
        ("decision report", Path("reports/opportunity_reviews") / f"{notice_id}_decision_report.md"),
        ("compliance matrix", Path("reports/opportunity_reviews") / f"{notice_id}_compliance_matrix.md"),
        ("bid/no-bid review", Path("reports/opportunity_reviews") / f"{notice_id}_bid_no_bid.md"),
        ("pricing schedule", Path("reports/pricing") / f"{notice_id}_pricing_schedule.md"),
        ("sources sought plan", Path("reports/sources_sought") / f"{notice_id}_sources_sought_plan.md"),
        ("analysis packet", Path("reports/analysis_packets") / f"{notice_id}.md"),
    ]
    return [(label, str(path)) for label, path in candidates if path.exists()]


def award_range_section(awards):
    values = amount_values(awards)
    if not values:
        return [
            "- No award amounts were available from returned results.",
            "- Manual review needed - insufficient structured data.",
        ]
    return [
        f"- **Minimum:** {money(min(values))}",
        f"- **Median:** {money(statistics.median(values))}",
        f"- **Average:** {money(statistics.mean(values))}",
        f"- **Maximum:** {money(max(values))}",
    ]


def format_query_filters(filters):
    parts = []
    for key in ["naics_codes", "psc_codes", "keywords", "agencies"]:
        if key not in filters:
            continue
        parts.append(f"{key}={filters[key]}")
    return "; ".join(parts) if parts else "contract award type and date filters only"


def table_escape(value):
    return safe_text(value).replace("|", "/").replace("\n", " ")


def similar_award_table(awards, max_rows=15):
    if not awards:
        return "No similar award rows were returned."

    rows = sorted(
        awards,
        key=lambda award: award.get("amount") if award.get("amount") is not None else -1,
        reverse=True,
    )[:max_rows]

    lines = [
        "| Recipient | Amount | Award Date | Agency / Subagency | Description | NAICS | PSC | Award ID / PIID |",
        "|---|---:|---|---|---|---|---|---|",
    ]
    for award in rows:
        agency = award.get("awarding_subagency") or award.get("awarding_agency")
        lines.append(
            f"| {table_escape(award.get('recipient'))} | {money(award.get('amount'))} | "
            f"{table_escape(award.get('start_date'))} | {table_escape(agency)} | "
            f"{table_escape(award.get('description'))[:180]} | {table_escape(award.get('naics'))} | "
            f"{table_escape(award.get('psc'))} | {table_escape(award.get('award_id'))} |"
        )
    return "\n".join(lines)


def pricing_implications(notice_id, awards):
    pricing_path, line_count = pricing_context(notice_id)
    values = amount_values(awards)
    lines = []

    if pricing_path:
        detail = f" with {line_count} extracted pricing line(s)" if line_count else ""
        lines.append(f"- Pricing schedule exists at `{pricing_path}`{detail}.")
    else:
        lines.append("- No pricing schedule artifact was found; compare historical award values only at a rough market-sizing level.")

    if values:
        lines.append(
            f"- Historical range suggests comparable awards span {money(min(values))} to {money(max(values))}, "
            f"with a median of {money(statistics.median(values))}. This requires validation against scope, period, location, and contract type."
        )
    else:
        lines.append("- Historical award range could not be calculated from returned data.")

    lines.append("- Do not infer a bid price from USAspending alone; use it to pressure-test labor, materials, incumbent context, and ceiling/option-year scale.")
    return lines


def prime_teaming_notes(row, awards):
    prime = score_int(row.get("prime_reality_score"))
    set_aside = safe_text(row.get("set_aside"))
    recipients = aggregate_recipients(awards)[:3]

    lines = [
        f"- Prime reality score from GovCon Scout: {prime}.",
        f"- Set-aside context: {set_aside or 'Not specified in current CSV row.'}",
    ]
    if recipients:
        names = ", ".join(name for name, _stats in recipients)
        lines.append(f"- Repeat/large recipients to validate as possible incumbents or teaming intelligence: {names}.")
    else:
        lines.append("- No repeat recipients were available from returned data.")
    lines.append("- Treat prime vs teaming as a decision requiring validation, not as a probability estimate.")
    return lines


def build_report(notice_id, row, awards, query_summaries, debug_paths, limit, lookback_years):
    values = amount_values(awards)
    dates = date_values(awards)
    recipients = aggregate_recipients(awards)
    agencies = aggregate_agencies(awards)
    naics_counts = aggregate_codes(awards, "naics")
    psc_counts = aggregate_codes(awards, "psc")
    errors = [item for item in query_summaries if item["error"]]

    total_value = sum(values) if values else 0

    lines = [
        f"# USAspending Intel - {notice_id}",
        "",
        f"**Generated:** {date.today().isoformat()}",
        "**API Grounding:** USAspending API V2; `/api/v2/search/spending_by_award/` Advanced Award Search endpoint; no API key used.",
        "",
        "## Executive Summary",
        "",
        f"- **Awards found:** {len(awards)} deduplicated award(s) from finalist-only queries.",
        f"- **Total returned award value:** {money(total_value) if values else 'Not available'}",
        f"- **Date range in returned awards:** {dates[0]} to {dates[-1]}" if dates else "- **Date range in returned awards:** Not available",
        "- This is market intelligence for validation, not a win-probability estimate.",
        "",
        "## Opportunity Metadata",
        "",
        f"- **Notice ID:** {notice_id}",
        f"- **Title:** {safe_text(row.get('title')) or 'Not found in CSV'}",
        f"- **Agency:** {safe_text(row.get('department_ind_agency') or row.get('agency'))}",
        f"- **Office:** {safe_text(row.get('office'))}",
        f"- **NAICS:** {safe_text(row.get('naics_code'))}",
        f"- **PSC:** {safe_text(row.get('psc_code') or row.get('psc'))}",
        f"- **Place of Performance:** {safe_text(row.get('place_of_performance'))}",
        f"- **Set-Aside:** {safe_text(row.get('set_aside'))}",
        f"- **Matched Keywords:** {safe_text(row.get('matched_keywords'))}",
        f"- **Fit Score:** {safe_text(row.get('fit_score'))}",
        f"- **Prime Reality Score:** {safe_text(row.get('prime_reality_score'))}",
        f"- **Recommendation:** {safe_text(row.get('recommendation'))}",
        f"- **Conditional Recommendation:** {safe_text(row.get('conditional_recommendation'))}",
        "",
        "### Related GovCon Scout Outputs",
        "",
    ]

    paths = related_paths(notice_id)
    if paths:
        for label, path in paths:
            lines.append(f"- **{label.title()}:** `{path}`")
    else:
        lines.append("- Manual review needed - insufficient structured data.")

    lines.extend([
        "",
        "## Query Strategy Used",
        "",
        f"- **Endpoint:** `{API_ENDPOINT}`",
        f"- **Limit per query:** {limit}",
        f"- **Lookback:** approximately {lookback_years} fiscal/calendar year(s) using API date filters.",
        "- **Award type filter:** contract award type codes were used; IDVs are intentionally left for a later pass because USAspending requires award type filters from one group per request.",
        "",
        "| Query | Filters | Results | Error |",
        "|---|---|---:|---|",
    ])

    for item in query_summaries:
        error = table_escape(item["error"])
        lines.append(f"| {item['name']} | {table_escape(format_query_filters(item['filters']))} | {item['result_count']} | {error} |")

    lines.extend([
        "",
        "## Historical Award Summary",
        "",
        f"- **Deduplicated awards found:** {len(awards)}",
        f"- **Total value:** {money(total_value) if values else 'Not available'}",
    ])

    if dates:
        lines.append(f"- **Award date range:** {dates[0]} to {dates[-1]}")
    else:
        lines.append("- **Award date range:** Not available")

    lines.append("- **Top recipients by returned value/count:**")
    if recipients:
        for name, stats in recipients[:5]:
            lines.append(f"  - {name}: {stats['count']} award(s), {money(stats['value'])}")
    else:
        lines.append("  - Manual review needed - insufficient structured data.")

    lines.append("- **Most common awarding agencies/subagencies:**")
    if agencies:
        for name, count in agencies[:5]:
            lines.append(f"  - {name}: {count} award(s)")
    else:
        lines.append("  - Manual review needed - insufficient structured data.")

    lines.append("- **Most common returned NAICS:**")
    for code, count in naics_counts[:5]:
        lines.append(f"  - {code}: {count} award(s)")
    lines.append("- **Most common returned PSC:**")
    for code, count in psc_counts[:5]:
        lines.append(f"  - {code}: {count} award(s)")

    lines.extend([
        "",
        "## Award Value Range",
        "",
        *award_range_section(awards),
        "",
        "## Top Recipients / Possible Incumbents",
        "",
    ])

    if recipients:
        for name, stats in recipients[:10]:
            lines.append(f"- **{name}:** {stats['count']} award(s), {money(stats['value'])}")
    else:
        lines.append("- Manual review needed - insufficient structured data.")

    lines.extend([
        "",
        "## Similar Award Examples",
        "",
        similar_award_table(awards),
        "",
        "## Pricing / Bid Realism Notes",
        "",
        *pricing_implications(notice_id, awards),
        "",
        "## Prime vs Teaming Implications",
        "",
        *prime_teaming_notes(row, awards),
        "",
        "## Source API Notes",
        "",
        "- USAspending records are useful for market sizing and incumbent research, but they may not mirror the exact solicitation scope, period of performance, location, options, or set-aside strategy.",
        "- Award amounts can represent obligations, ceilings, base plus options, or modifications depending on award type and record structure.",
        "- The lookback filter can surface parent awards with older start dates when related spending activity falls inside the requested period; validate action dates before treating a record as current-market evidence.",
        "- Contract award type filters were used conservatively. IDV award history may still matter and should be checked separately for finalists where task-order context is important.",
        "- The official USAspending documentation identifies V2 as current and V1 as deprecated, and documents `/api/v2/search/spending_by_award/` as the Spending by Award Advanced Search endpoint.",
    ])

    if not awards:
        lines.append("- No relevant award rows were returned from the attempted filters. Consider alternate title terms, agency naming variants, PSC/NAICS validation, or a manual award search.")

    if errors:
        lines.append("- **API errors occurred:**")
        for item in errors:
            lines.append(f"  - {item['name']}: {item['error']}")

    if debug_paths:
        lines.append("- **Debug JSON saved:**")
        for path in debug_paths:
            lines.append(f"  - `{path}`")

    lines.extend([
        "",
        "## Recommended Next Action",
        "",
        "1. Validate whether the returned awards are truly comparable in scope, location, and period of performance.",
        "2. Identify repeat recipients that may be incumbents, partners, or benchmarks.",
        "3. Compare historical award range against the pricing schedule and solicitation scope; do not infer a bid price directly.",
        "4. Review decision/compliance outputs before deciding prime, teaming, sources-sought response, or pass.",
        "5. If results are sparse or noisy, refine with agency-specific terms, PSC/NAICS alternatives, or manual FPDS/SAM award checks.",
        "",
    ])

    return "\n".join(lines)


def write_report(notice_id, row, awards, query_summaries, debug_paths, output_dir, limit, lookback_years):
    output_path = Path(output_dir) / f"{notice_id}_usaspending_intel.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        build_report(
            notice_id=notice_id,
            row=row,
            awards=awards,
            query_summaries=query_summaries,
            debug_paths=debug_paths,
            limit=limit,
            lookback_years=lookback_years,
        ),
        encoding="utf-8",
    )
    return output_path


def extract_queue(review_pack_path=DEFAULT_REVIEW_PACK):
    text = Path(review_pack_path).read_text(encoding="utf-8", errors="replace") if Path(review_pack_path).exists() else ""
    if not text:
        return []

    in_section = False
    notice_ids = []
    for line in text.splitlines():
        if line.startswith("## "):
            section = line.replace("## ", "", 1).strip()
            if in_section and section != "Recommended USAspending Queue":
                break
            in_section = section == "Recommended USAspending Queue"
            continue

        if not in_section or not line.startswith("| "):
            continue
        if "Priority" in line or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        notice_cell = cells[1]
        notice_id = notice_cell.split(" - ", 1)[0].strip()
        if notice_id == "MyBidMatch":
            continue
        if notice_id and notice_id not in notice_ids:
            notice_ids.append(notice_id)
    return notice_ids


def print_queue():
    notice_ids = extract_queue()
    if not notice_ids:
        print("No USAspending queue notice IDs found.")
        return
    for notice_id in notice_ids:
        print(f"python src/usaspending_intel.py --notice-id {notice_id}")


def parse_args():
    parser = argparse.ArgumentParser(description="Build finalist-only USAspending award intelligence.")
    parser.add_argument("--notice-id")
    parser.add_argument("--csv", default=DEFAULT_CSV)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--years", "--lookback-years", dest="years", type=int, default=5)
    parser.add_argument("--debug", "--debug-json", dest="debug", action="store_true")
    parser.add_argument("--keyword", action="append")
    parser.add_argument("--agency")
    parser.add_argument("--naics")
    parser.add_argument("--psc")
    parser.add_argument("--queue", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()

    if args.queue:
        print_queue()
        return

    if not args.notice_id:
        raise SystemExit("--notice-id is required unless --queue is used")

    limit = min(max(1, args.limit), MAX_LIMIT)
    row = load_notice_row(args.notice_id, args.csv)
    if not row:
        row = {"notice_id": args.notice_id}

    overrides = {
        "keywords": args.keyword,
        "agency": args.agency,
        "naics": args.naics,
        "psc": args.psc,
    }
    awards, query_summaries, debug_paths = run_queries(
        notice_id=args.notice_id,
        row=row,
        limit=limit,
        lookback_years=max(1, args.years),
        debug_json=args.debug,
        overrides=overrides,
    )
    output_path = write_report(
        notice_id=args.notice_id,
        row=row,
        awards=awards,
        query_summaries=query_summaries,
        debug_paths=debug_paths,
        output_dir=args.output_dir,
        limit=limit,
        lookback_years=max(1, args.years),
    )
    awards_csv_path = write_awards_csv(args.notice_id, awards, args.output_dir)

    print(f"USAspending intel written to: {output_path}")
    print(f"USAspending awards CSV written to: {awards_csv_path}")
    print(f"Awards found: {len(awards)}")


if __name__ == "__main__":
    main()
