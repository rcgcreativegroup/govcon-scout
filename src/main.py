import argparse
import copy
import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

from sam_client import search_all_profiles
from scorer import score_opportunities
from report_generator import generate_markdown_report, generate_awards_intel_report
from shortlist_generator import generate_shortlist_report, generate_analysis_packets
from pipeline import update_pipeline
from attachment_intel import apply_attachment_intel
from detail_enrichment import enrich_selected_sam_details
from local_attachments import apply_local_attachment_scan


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"
EXPORTS_DIR = BASE_DIR / "exports"
REPORTS_DIR = BASE_DIR / "reports"
DATA_DIR = BASE_DIR / "data"


LEAN_KEYWORD_LIMIT = 4

BROAD_KEYWORDS = {
    "marketing",
    "advertising",
    "communications",
    "support",
    "services",
    "training",
    "logistics",
    "transportation",
    "janitorial",
    "supplies",
    "automation",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="GovCon Scout — SAM.gov opportunity scanner and pursuit worklist generator."
    )

    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--offline",
        action="store_true",
        help="Regenerate reports from the latest CSV without running a broad SAM.gov scan.",
    )
    mode_group.add_argument(
        "--lean",
        action="store_true",
        help="Run a reduced SAM.gov search plan to conserve API calls. This is the default.",
    )
    mode_group.add_argument(
        "--full",
        action="store_true",
        help="Run the full SAM.gov search plan from config/search_profiles.json.",
    )

    parser.add_argument(
        "--enrich-details",
        action="store_true",
        help="Use selected SAM.gov detail API calls for top opportunities only.",
    )

    parser.add_argument(
        "--detail-limit",
        type=int,
        default=5,
        help="Maximum number of selected opportunities to detail-enrich.",
    )

    parser.add_argument(
        "--debug-detail-json",
        action="store_true",
        help="Save raw SAM.gov detail JSON responses for selected detail enrichment.",
    )

    parser.add_argument(
        "--debug-dir",
        default="debug",
        help="Folder for raw SAM.gov detail debug JSON files.",
    )

    parser.add_argument(
        "--scan-local-attachments",
        action="store_true",
        help="Scan local downloads/{notice_id}/ folders and classify manually downloaded attachments.",
    )

    parser.add_argument(
        "--downloads-dir",
        default="downloads",
        help="Folder containing manually downloaded opportunity attachments.",
    )

    parser.add_argument(
        "--input-csv",
        default="",
        help="CSV file to use for offline mode. Defaults to latest GovCon Scout CSV.",
    )

    parser.add_argument(
        "--posted-days-back",
        type=int,
        default=30,
        help="Number of days back to search SAM.gov.",
    )

    parser.add_argument(
        "--limit-per-search",
        type=int,
        default=50,
        help="SAM.gov result limit per individual search.",
    )

    parser.add_argument(
        "--analysis-packet-limit",
        type=int,
        default=25,
        help="Number of analysis packets to generate.",
    )

    parser.add_argument(
        "--pipeline-limit",
        type=int,
        default=150,
        help="Maximum number of opportunities to place in pipeline.csv.",
    )

    return parser.parse_args()


def determine_mode(args):
    if args.offline:
        return "offline"

    if args.full:
        return "full"

    return "lean"


def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def ensure_directories():
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def make_timestamp():
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def archive_output_file(file_path, latest_name=None):
    path = Path(file_path)

    if not path.exists():
        return ""

    run_stamp = make_timestamp()
    timestamped_path = path.with_name(f"{path.stem}_{run_stamp}{path.suffix}")
    shutil.copy2(path, timestamped_path)

    if latest_name:
        latest_path = path.with_name(latest_name)
        shutil.copy2(path, latest_path)

    return str(timestamped_path)


def write_failed_scan_report(reason):
    run_stamp = make_timestamp()
    failed_report_path = REPORTS_DIR / f"govcon_scout_failed_scan_{run_stamp}.md"

    failed_report_path.write_text(
        "\n".join([
            "# GovCon Scout Failed Scan",
            "",
            f"**Date/Time:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            reason,
            "",
            "No main pursuit report, shortlist, pipeline, CSV export, analysis packets, or awards report was generated from this empty run.",
            "",
            "This protects the last useful report from being overwritten.",
        ]),
        encoding="utf-8",
    )

    return str(failed_report_path)


def normalize_score_reasons(value):
    if value is None:
        return []

    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]

    text = str(value).strip()

    if not text:
        return []

    if " | " in text:
        return [item.strip() for item in text.split(" | ") if item.strip()]

    return [text]


def format_score_reasons_for_csv(value):
    return " | ".join(normalize_score_reasons(value))


def find_latest_csv():
    latest_copy = EXPORTS_DIR / "govcon_scout_opportunities_latest.csv"

    if latest_copy.exists():
        return latest_copy

    candidates = sorted(
        EXPORTS_DIR.glob("govcon_scout_opportunities*.csv"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )

    if candidates:
        return candidates[0]

    return None


def load_scored_opportunities_from_csv(csv_path):
    path = Path(csv_path)

    if not path.exists():
        raise FileNotFoundError(f"CSV file not found: {path}")

    opportunities = []

    with open(path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            row = dict(row)
            row["score_reasons"] = normalize_score_reasons(row.get("score_reasons"))
            opportunities.append(row)

    return opportunities


def keyword_priority(keyword):
    text = str(keyword).strip().lower()

    if not text:
        return 0

    score = 0

    if " " in text:
        score += 5

    if text not in BROAD_KEYWORDS:
        score += 3

    if len(text) >= 8:
        score += 1

    return score


def build_lean_search_profiles(search_profiles, keyword_limit=LEAN_KEYWORD_LIMIT):
    lean_profiles = {}

    for profile_name, profile_data in search_profiles.items():
        lane_copy = copy.deepcopy(profile_data)

        keywords = (
            lane_copy.get("lean_keywords")
            or lane_copy.get("priority_keywords")
            or lane_copy.get("keywords", [])
        )

        keywords = [
            str(keyword).strip()
            for keyword in keywords
            if str(keyword).strip()
        ]

        non_broad_keywords = [
            keyword for keyword in keywords
            if keyword.lower() not in BROAD_KEYWORDS
        ]

        if non_broad_keywords:
            selected_keywords = sorted(
                non_broad_keywords,
                key=keyword_priority,
                reverse=True,
            )[:keyword_limit]
        else:
            selected_keywords = sorted(
                keywords,
                key=keyword_priority,
                reverse=True,
            )[:keyword_limit]

        lane_copy["keywords"] = selected_keywords
        lean_profiles[profile_name] = lane_copy

    return lean_profiles


def describe_search_plan(search_profiles):
    keyword_count = 0
    naics_count = 0

    for profile_data in search_profiles.values():
        keyword_count += len(profile_data.get("keywords", []))
        naics_count += len(profile_data.get("naics", []))

    return keyword_count, naics_count, keyword_count + naics_count


def export_opportunities_to_csv(scored_opportunities):
    run_stamp = make_timestamp()
    csv_path = EXPORTS_DIR / f"govcon_scout_opportunities_{run_stamp}.csv"

    fieldnames = [
        "notice_actionability",
        "award_notice_flag",
        "market_intel_value",
        "awardee_name",
        "award_amount",
        "award_date",
        "award_number",
        "fit_score",
        "prime_reality_score",
        "recommendation",
        "conditional_recommendation",
        "matched_lane",
        "base_lane",
        "specialization_level",
        "fulfillment_path",
        "subcontractor_feasibility",
        "prime_control_risk",
        "prime_control_recommended_action",
        "evaluation_method",
        "submission_method",
        "forms_required_text",
        "amendment_compliance_alert",
        "amendment_compliance_task",
        "deadline_status",
        "days_until_deadline",
        "due_date_solicitation_local",
        "due_date_user_local",
        "on_site_staffing_flag",
        "mandatory_staffing_flag",
        "telework_ambiguity_flag",
        "remote_feasibility_score",
        "local_staffing_dependency",
        "performance_location_risk",
        "staffing_model",
        "execution_risk",
        "compliance_risk",
        "rfi_needed",
        "rfi_recommendation",
        "set_aside_hard_gate",
        "set_aside_hard_gate_reason",
        "force_teaming_target",
        "small_business_subcontracting_check",
        "subcontracting_note",
        "prime_case_report_required",
        "prime_case_report_note",
        "team_lock_alert",
        "team_lock_note",
        "idiq_ceiling_detected",
        "idiq_ceiling_text",
        "guaranteed_minimum_text",
        "step1_mandatory_flag",
        "step1_deadline_note",
        "step1_deadline_text",
        "scientific_domain_complexity_flag",
        "scientific_domain_terms",
        "scientific_domain_note",
        "subcontractor_role_classifier",

        # API / remote attachment intelligence
        "attachment_review_needed",
        "attachment_review_priority",
        "attachment_download_ready",
        "attachment_discovery_method",
        "resource_link_type_summary",
        "actual_downloadable_attachment_count",
        "actual_downloadable_attachment_links",
        "sam_detail_api_link_count",
        "sam_detail_api_links",
        "sam_notice_desc_link_count",
        "sam_notice_desc_links",
        "sam_workspace_link_count",
        "sam_workspace_links",
        "external_portal_link_count",
        "external_portal_links",
        "unknown_resource_link_count",
        "unknown_resource_links",
        "likely_documents_needed",
        "likely_pws_needed",
        "likely_sf1449_needed",
        "likely_sf30_needed",
        "likely_pricing_needed",
        "attachment_keywords_detected",
        "attachment_next_action",

        # Local attachment intake
        "local_attachment_folder",
        "local_attachments_found",
        "local_attachment_count",
        "local_attachment_file_list",
        "local_attachment_likely_types",
        "local_pdf_count",
        "local_doc_count",
        "local_docx_count",
        "local_xls_count",
        "local_xlsx_count",
        "local_csv_count",
        "local_zip_count",
        "local_txt_count",
        "local_pws_found",
        "local_sf1449_found",
        "local_sf30_found",
        "local_pricing_found",
        "local_wage_determination_found",
        "local_solicitation_found",
        "local_attachment_next_action",
        "ready_for_bid_no_bid_analysis",

        # Selected SAM detail enrichment
        "sam_detail_enriched",
        "sam_detail_enrichment_source",
        "sam_detail_enrichment_note",
        "sam_detail_debug_json_path",
        "sam_detail_raw_resource_links",
        "sam_detail_raw_links",
        "sam_detail_raw_attachments",
        "sam_detail_raw_documents",
        "sam_detail_raw_files",

        # Base opportunity fields
        "description_enriched",
        "resource_link_count",
        "has_resource_links",
        "resource_links",
        "psc_code",
        "title",
        "department_ind_agency",
        "notice_id",
        "solicitation_number",
        "sam_notice_id",
        "naics_code",
        "notice_type",
        "set_aside",
        "response_deadline",
        "posted_date",
        "archive_date",
        "place_of_performance",
        "contacts",
        "ui_link",
        "short_description",
        "score_reasons",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()

        for opp in scored_opportunities:
            row = {field: opp.get(field, "") for field in fieldnames}
            row["score_reasons"] = format_score_reasons_for_csv(opp.get("score_reasons"))
            writer.writerow(row)

    latest_path = EXPORTS_DIR / "govcon_scout_opportunities_latest.csv"
    shutil.copy2(csv_path, latest_path)

    return str(csv_path)


def produce_outputs(scored_opportunities, opportunities_found_count, args, mode):
    if not scored_opportunities:
        failed_report_path = write_failed_scan_report(
            "GovCon Scout found 0 scored opportunities. This usually means SAM.gov returned no data, "
            "the API key was rate-limited, the scan was interrupted, cached results were unavailable, "
            "or offline mode could not find usable CSV data."
        )

        print("")
        print("GovCon Scout scan returned 0 scored opportunities.")
        print(f"Failed scan report: {failed_report_path}")
        print("Main reports were not overwritten.")
        return

    if args.enrich_details:
        print("Running selected SAM detail enrichment...")
        scored_opportunities = enrich_selected_sam_details(
            scored_opportunities=scored_opportunities,
            limit=args.detail_limit,
            debug_detail_json=args.debug_detail_json,
            debug_dir=args.debug_dir,
        )

    if args.scan_local_attachments:
        print("Scanning local attachment folders...")
        scored_opportunities = apply_local_attachment_scan(
            scored_opportunities=scored_opportunities,
            downloads_dir=args.downloads_dir,
        )

    print("Applying attachment intelligence...")
    scored_opportunities = apply_attachment_intel(scored_opportunities)

    actionable_opportunities = [
        opp for opp in scored_opportunities
        if opp.get("notice_actionability") == "actionable"
    ]

    award_intel_opportunities = [
        opp for opp in scored_opportunities
        if opp.get("notice_actionability") == "awarded_market_intel"
    ]

    top_actionable = actionable_opportunities[:50]

    print("Exporting CSV...")
    csv_path = export_opportunities_to_csv(scored_opportunities)

    print("Generating Markdown pursuit report...")
    report_path = generate_markdown_report(
        scored_opportunities=scored_opportunities,
        output_dir=str(REPORTS_DIR),
        per_lane_limit=10,
    )

    timestamped_report_path = archive_output_file(
        report_path,
        latest_name="govcon_scout_daily_report_latest.md",
    )

    print("Generating shortlist report...")
    shortlist_path = generate_shortlist_report(
        scored_opportunities=scored_opportunities,
        output_dir=str(REPORTS_DIR),
        limit_per_group=15,
    )

    timestamped_shortlist_path = archive_output_file(
        shortlist_path,
        latest_name="govcon_scout_shortlist_latest.md",
    )

    print("Updating pipeline tracker...")
    pipeline_path = update_pipeline(
        scored_opportunities=scored_opportunities,
        output_path=str(DATA_DIR / "pipeline.csv"),
        limit=args.pipeline_limit,
    )

    print("Generating analysis packets...")
    analysis_packets_dir = generate_analysis_packets(
        scored_opportunities=scored_opportunities,
        output_dir=str(REPORTS_DIR / "analysis_packets"),
        limit=args.analysis_packet_limit,
    )

    print("Generating awards intelligence report...")
    awards_report_path = generate_awards_intel_report(
        scored_opportunities=scored_opportunities,
        output_dir=str(REPORTS_DIR),
        limit=50,
    )

    timestamped_awards_path = archive_output_file(
        awards_report_path,
        latest_name="govcon_scout_awards_intel_latest.md",
    )

    print("")
    print("GovCon Scout scan complete.")
    print(f"Mode: {mode}")
    print(f"Selected detail enrichment: {'On' if args.enrich_details else 'Off'}")
    print(f"Detail enrichment limit: {args.detail_limit if args.enrich_details else 0}")
    print(f"Debug detail JSON: {'On' if args.debug_detail_json else 'Off'}")
    print(f"Local attachment scan: {'On' if args.scan_local_attachments else 'Off'}")
    print(f"Downloads directory: {args.downloads_dir if args.scan_local_attachments else ''}")
    print(f"Total opportunities found/loaded: {opportunities_found_count}")
    print(f"Total opportunities scored: {len(scored_opportunities)}")
    print(f"Actionable pursuit opportunities: {len(actionable_opportunities)}")
    print(f"Award notices / market intel items: {len(award_intel_opportunities)}")
    print(f"CSV export: {csv_path}")
    print(f"Daily pursuit report: {report_path}")
    print(f"Timestamped pursuit report copy: {timestamped_report_path}")
    print(f"Shortlist report: {shortlist_path}")
    print(f"Timestamped shortlist copy: {timestamped_shortlist_path}")
    print(f"Pipeline tracker: {pipeline_path}")
    print(f"Analysis packets folder: {analysis_packets_dir}")
    print(f"Awards intelligence report: {awards_report_path}")
    print(f"Timestamped awards copy: {timestamped_awards_path}")
    print("")
    print("Top 5 actionable opportunities:")
    print("")

    for index, opp in enumerate(top_actionable[:5], start=1):
        deadline_status = opp.get("deadline_status", "unknown")
        days_until = opp.get("days_until_deadline", "")
        prime_reality = opp.get("prime_reality_score", 0)
        conditional = opp.get("conditional_recommendation", "")

        deadline_text = deadline_status

        if days_until != "":
            deadline_text += f", {days_until} day(s)"

        print(
            f"{index}. {opp.get('title', 'Untitled')} "
            f"— Fit: {opp.get('fit_score', 0)} "
            f"— Prime Reality: {prime_reality} "
            f"— {conditional or opp.get('recommendation', 'Review')} "
            f"— Attachment Review: {opp.get('attachment_review_priority', 'Low')} "
            f"— Download Ready: {opp.get('attachment_download_ready', 'No')} "
            f"— Local Files: {opp.get('local_attachments_found', 'No')} "
            f"— Ready for Bid/No-Bid: {opp.get('ready_for_bid_no_bid_analysis', 'No')} "
            f"— Deadline: {deadline_text}"
        )


def run_offline(args):
    csv_path = Path(args.input_csv) if args.input_csv else find_latest_csv()

    if not csv_path:
        failed_report_path = write_failed_scan_report(
            "Offline mode could not find a GovCon Scout CSV to load. "
            "Expected exports/govcon_scout_opportunities_latest.csv or a timestamped CSV in exports/."
        )

        print("")
        print("Offline mode could not find a CSV to load.")
        print(f"Failed scan report: {failed_report_path}")
        return

    print(f"Offline mode enabled. Loading scored opportunities from: {csv_path}")
    scored_opportunities = load_scored_opportunities_from_csv(csv_path)

    produce_outputs(
        scored_opportunities=scored_opportunities,
        opportunities_found_count=len(scored_opportunities),
        args=args,
        mode="offline",
    )


def run_api_scan(args, mode):
    company_profile_path = CONFIG_DIR / "company_profile.json"
    search_profiles_path = CONFIG_DIR / "search_profiles.json"

    print("Loading company profile...")
    company_profile = load_json(company_profile_path)

    print("Loading search profiles...")
    search_profiles = load_json(search_profiles_path)

    if mode == "lean":
        print("Lean mode enabled. Reducing keyword searches to conserve SAM.gov API calls.")
        search_profiles = build_lean_search_profiles(search_profiles)

    keyword_count, naics_count, total_search_count = describe_search_plan(search_profiles)

    print(
        f"Search plan: {keyword_count} keyword searches + "
        f"{naics_count} NAICS searches = {total_search_count} total searches."
    )

    print("Searching SAM.gov opportunities...")

    opportunities = search_all_profiles(
        search_profiles=search_profiles,
        posted_days_back=args.posted_days_back,
        limit_per_search=args.limit_per_search,
    )

    print("Scoring opportunities...")

    scored_opportunities = score_opportunities(
        opportunities=opportunities,
        company_profile=company_profile,
        search_profiles=search_profiles,
    )

    produce_outputs(
        scored_opportunities=scored_opportunities,
        opportunities_found_count=len(opportunities),
        args=args,
        mode=mode,
    )


def main():
    ensure_directories()
    args = parse_args()
    mode = determine_mode(args)

    if mode == "offline":
        run_offline(args)
        return

    run_api_scan(args=args, mode=mode)


if __name__ == "__main__":
    main()
