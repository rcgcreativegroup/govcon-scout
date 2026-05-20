from datetime import datetime
from pathlib import Path


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def score_value(opp, field_name):
    try:
        return int(float(opp.get(field_name, 0) or 0))
    except (TypeError, ValueError):
        return 0


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


def is_actionable(opp):
    return opp.get("notice_actionability") == "actionable"


def is_notice_desc_url(value):
    if not value:
        return False

    text = str(value).strip().lower()

    return (
        text.startswith("http")
        and "api.sam.gov" in text
        and "noticedesc" in text
    )


def clean_summary(value):
    text = safe_text(value)

    if is_notice_desc_url(text):
        return ""

    return text


def has_resource_links(opp):
    return (
        str(opp.get("has_resource_links", "")).lower() == "yes"
        or score_value(opp, "resource_link_count") > 0
    )


def sort_opportunities(opportunities):
    return sorted(
        opportunities,
        key=lambda item: (
            score_value(item, "prime_reality_score"),
            score_value(item, "fit_score"),
        ),
        reverse=True,
    )


def format_opportunity_summary(opp):
    title = safe_text(opp.get("title", "Untitled Opportunity"))
    notice_id = safe_text(opp.get("notice_id", "N/A"))
    agency = safe_text(opp.get("department_ind_agency", "Unknown Agency"))
    lane = safe_text(opp.get("matched_lane", "unknown"))
    fit_score = safe_text(opp.get("fit_score", ""))
    prime_reality_score = safe_text(opp.get("prime_reality_score", ""))
    recommendation = safe_text(opp.get("conditional_recommendation") or opp.get("recommendation", "Review"))
    deadline = safe_text(opp.get("due_date_user_local") or opp.get("response_deadline", ""))
    deadline_status = safe_text(opp.get("deadline_status", "unknown"))
    compliance_risk = safe_text(opp.get("compliance_risk", "Unknown"))
    rfi_needed = safe_text(opp.get("rfi_needed", "No"))
    set_aside_gate = safe_text(opp.get("set_aside_hard_gate", "No"))
    resource_count = safe_text(opp.get("resource_link_count", 0))
    link = safe_text(opp.get("ui_link", ""))

    lines = []
    lines.append(f"### {title}")
    lines.append("")
    lines.append(f"- **Notice ID:** {notice_id}")
    lines.append(f"- **Agency:** {agency}")
    lines.append(f"- **Matched Lane:** {lane}")
    lines.append(f"- **Fit Score:** {fit_score}/100")
    lines.append(f"- **Prime Reality Score:** {prime_reality_score}/100")
    lines.append(f"- **Recommendation:** {recommendation}")
    lines.append(f"- **Deadline:** {deadline}")
    lines.append(f"- **Deadline Status:** {deadline_status}")
    lines.append(f"- **Compliance Risk:** {compliance_risk}")
    lines.append(f"- **RFI Needed:** {rfi_needed}")
    lines.append(f"- **Set-Aside Hard Gate:** {set_aside_gate}")
    lines.append(f"- **Resource Links Found:** {resource_count}")

    if opp.get("resource_link_type_summary"):
        lines.append(f"- **Resource Link Types:** {opp.get('resource_link_type_summary')}")

    if opp.get("attachment_review_needed"):
        lines.append(f"- **Attachment Review Needed:** {opp.get('attachment_review_needed')}")
        lines.append(f"- **Attachment Review Priority:** {opp.get('attachment_review_priority', 'Low')}")
        lines.append(f"- **Attachment Download Ready:** {opp.get('attachment_download_ready', 'No')}")
        lines.append(f"- **Attachment Discovery Method:** {opp.get('attachment_discovery_method', '')}")

    if opp.get("actual_downloadable_attachment_count") not in [None, ""]:
        lines.append(f"- **Actual Downloadable Attachments:** {opp.get('actual_downloadable_attachment_count')}")

    if opp.get("sam_detail_api_link_count") not in [None, ""]:
        lines.append(f"- **SAM Detail API Links:** {opp.get('sam_detail_api_link_count')}")

    if opp.get("local_attachments_found"):
        lines.append(f"- **Local Attachments Found:** {opp.get('local_attachments_found')}")
        lines.append(f"- **Local Attachment Count:** {opp.get('local_attachment_count', '')}")
        lines.append(f"- **Ready for Bid/No-Bid Analysis:** {opp.get('ready_for_bid_no_bid_analysis', '')}")

    if opp.get("likely_documents_needed"):
        lines.append(f"- **Likely Documents Needed:** {opp.get('likely_documents_needed')}")

    if opp.get("subcontractor_role_classifier"):
        lines.append(f"- **Possible Workshare:** {opp.get('subcontractor_role_classifier')}")

    if opp.get("rfi_recommendation"):
        lines.append(f"- **RFI Recommendation:** {opp.get('rfi_recommendation')}")

    if opp.get("attachment_next_action"):
        lines.append(f"- **Attachment Next Action:** {opp.get('attachment_next_action')}")

    if opp.get("local_attachment_next_action"):
        lines.append(f"- **Local Attachment Next Action:** {opp.get('local_attachment_next_action')}")

    if link:
        lines.append(f"- **SAM.gov Link:** {link}")

    short_description = clean_summary(
        opp.get("short_description")
        or opp.get("full_description")
        or opp.get("description")
    )

    if short_description:
        lines.append("")
        lines.append("**Summary:**")
        lines.append("")
        lines.append(short_description[:750].strip())
        if len(short_description) > 750:
            lines.append("...")

    lines.append("")
    lines.append("---")
    lines.append("")

    return lines


def write_group(lines, title, opportunities, limit=15):
    lines.append(f"## {title}")
    lines.append("")

    if not opportunities:
        lines.append("No opportunities in this category.")
        lines.append("")
        lines.append("---")
        lines.append("")
        return

    for opp in sort_opportunities(opportunities)[:limit]:
        lines.extend(format_opportunity_summary(opp))


def build_shortlist_groups(scored_opportunities):
    actionable = [opp for opp in scored_opportunities if is_actionable(opp)]

    prime_candidates = [
        opp for opp in actionable
        if score_value(opp, "prime_reality_score") >= 70
        and opp.get("set_aside_hard_gate") != "Yes"
        and opp.get("deadline_status") not in ["overdue_or_archived", "too_soon"]
    ]

    strong_review = [
        opp for opp in actionable
        if 55 <= score_value(opp, "prime_reality_score") < 70
        and opp.get("set_aside_hard_gate") != "Yes"
        and opp.get("deadline_status") not in ["overdue_or_archived", "too_soon"]
    ]

    conditional_pursue = [
        opp for opp in actionable
        if "Conditional Pursue" in safe_text(opp.get("conditional_recommendation"))
    ]

    teaming_targets = [
        opp for opp in actionable
        if opp.get("set_aside_hard_gate") == "Yes"
        or opp.get("force_teaming_target") == "Yes"
        or safe_text(opp.get("conditional_recommendation")).startswith("Teaming/Subcontractor Target")
        or (
            score_value(opp, "fit_score") >= 60
            and score_value(opp, "prime_reality_score") < 50
        )
    ]

    rfi_needed = [
        opp for opp in actionable
        if opp.get("rfi_needed") == "Yes"
    ]

    high_compliance_risk = [
        opp for opp in actionable
        if opp.get("compliance_risk") == "High"
    ]

    download_ready = [
        opp for opp in actionable
        if opp.get("attachment_download_ready") == "Yes"
    ]

    detail_enrichment_needed = [
        opp for opp in actionable
        if opp.get("attachment_discovery_method") == "sam_detail_enrichment_required"
    ]

    local_files_ready = [
        opp for opp in actionable
        if opp.get("local_attachments_found") == "Yes"
    ]

    bid_no_bid_ready = [
        opp for opp in actionable
        if opp.get("ready_for_bid_no_bid_analysis") == "Yes"
    ]

    attachment_review_needed = [
        opp for opp in actionable
        if opp.get("attachment_review_needed") == "Yes" or has_resource_links(opp)
    ]

    market_intel_follow_up = [
        opp for opp in scored_opportunities
        if opp.get("notice_actionability") == "awarded_market_intel"
    ]

    return {
        "Prime Candidates": prime_candidates,
        "Strong Review — Possible Prime": strong_review,
        "Conditional Pursue": conditional_pursue,
        "Teaming / Subcontractor Targets": teaming_targets,
        "RFI Needed": rfi_needed,
        "High Compliance Risk": high_compliance_risk,
        "Download-Ready Attachments": download_ready,
        "SAM Detail Enrichment Needed": detail_enrichment_needed,
        "Local Files Found": local_files_ready,
        "Ready for Bid/No-Bid Analysis": bid_no_bid_ready,
        "Attachment Review Needed": attachment_review_needed,
        "Market Intel Follow-Up": market_intel_follow_up,
    }


def generate_shortlist_report(scored_opportunities, output_dir="reports", limit_per_group=15):
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    report_path = Path(output_dir) / f"govcon_scout_shortlist_{today}.md"

    groups = build_shortlist_groups(scored_opportunities)

    actionable_count = sum(1 for opp in scored_opportunities if is_actionable(opp))
    awards_count = sum(
        1 for opp in scored_opportunities
        if opp.get("notice_actionability") == "awarded_market_intel"
    )
    download_ready_count = sum(
        1 for opp in scored_opportunities
        if opp.get("attachment_download_ready") == "Yes"
    )
    detail_needed_count = sum(
        1 for opp in scored_opportunities
        if opp.get("attachment_discovery_method") == "sam_detail_enrichment_required"
    )
    local_files_count = sum(
        1 for opp in scored_opportunities
        if opp.get("local_attachments_found") == "Yes"
    )
    bid_no_bid_ready_count = sum(
        1 for opp in scored_opportunities
        if opp.get("ready_for_bid_no_bid_analysis") == "Yes"
    )

    lines = []
    lines.append("# GovCon Scout Shortlist")
    lines.append("")
    lines.append(f"**Date:** {today}")
    lines.append("")
    lines.append(f"**Total Scored:** {len(scored_opportunities)}")
    lines.append(f"**Actionable Opportunities:** {actionable_count}")
    lines.append(f"**Award / Market Intel Items:** {awards_count}")
    lines.append(f"**Download-Ready Attachment Items:** {download_ready_count}")
    lines.append(f"**SAM Detail Enrichment Needed:** {detail_needed_count}")
    lines.append(f"**Local Files Found:** {local_files_count}")
    lines.append(f"**Ready for Bid/No-Bid Analysis:** {bid_no_bid_ready_count}")
    lines.append("")
    lines.append("This is the daily worklist. Use it to decide what to open, what to analyze, what to pursue, and what to pass.")
    lines.append("")
    lines.append("---")
    lines.append("")

    for title, opportunities in groups.items():
        write_group(lines, title, opportunities, limit=limit_per_group)

    report_path.write_text("\n".join(lines), encoding="utf-8")

    return str(report_path)


def make_safe_filename(value):
    text = safe_text(value) or "unknown"
    allowed = []

    for char in text:
        if char.isalnum() or char in ["-", "_"]:
            allowed.append(char)
        elif char in [" ", ".", "/"]:
            allowed.append("_")

    cleaned = "".join(allowed).strip("_")
    return cleaned[:120] or "unknown"


def generate_analysis_packets(scored_opportunities, output_dir="reports/analysis_packets", limit=25):
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    actionable = [
        opp for opp in scored_opportunities
        if opp.get("notice_actionability") == "actionable"
    ]

    actionable = sort_opportunities(actionable)[:limit]

    for opp in actionable:
        notice_id = make_safe_filename(opp.get("notice_id") or opp.get("sam_notice_id"))
        packet_path = output_path / f"{notice_id}.md"

        lines = []
        lines.append("# GovCon Scout Analysis Packet")
        lines.append("")
        lines.append(f"**Title:** {safe_text(opp.get('title'))}")
        lines.append(f"**Notice ID:** {safe_text(opp.get('notice_id'))}")
        lines.append(f"**SAM Notice ID:** {safe_text(opp.get('sam_notice_id'))}")
        lines.append(f"**Agency:** {safe_text(opp.get('department_ind_agency'))}")
        lines.append(f"**Office:** {safe_text(opp.get('office'))}")
        lines.append(f"**NAICS:** {safe_text(opp.get('naics_code'))}")
        lines.append(f"**PSC:** {safe_text(opp.get('psc_code'))}")
        lines.append(f"**Set-Aside:** {safe_text(opp.get('set_aside'))}")
        lines.append(f"**Notice Type:** {safe_text(opp.get('notice_type'))}")
        lines.append(f"**Response Deadline:** {safe_text(opp.get('response_deadline'))}")
        lines.append(f"**Due Date — User Local:** {safe_text(opp.get('due_date_user_local'))}")
        lines.append(f"**Place of Performance:** {safe_text(opp.get('place_of_performance'))}")
        lines.append(f"**Contacts:** {safe_text(opp.get('contacts'))}")
        lines.append("")
        lines.append("## Scores")
        lines.append("")
        lines.append(f"- **Fit Score:** {safe_text(opp.get('fit_score'))}/100")
        lines.append(f"- **Prime Reality Score:** {safe_text(opp.get('prime_reality_score'))}/100")
        lines.append(f"- **Recommendation:** {safe_text(opp.get('recommendation'))}")
        lines.append(f"- **Conditional Recommendation:** {safe_text(opp.get('conditional_recommendation'))}")
        lines.append(f"- **Compliance Risk:** {safe_text(opp.get('compliance_risk'))}")
        lines.append("")
        lines.append("## Attachment / Resource Link Intelligence")
        lines.append("")
        lines.append(f"- **Attachment Review Needed:** {safe_text(opp.get('attachment_review_needed'))}")
        lines.append(f"- **Attachment Review Priority:** {safe_text(opp.get('attachment_review_priority'))}")
        lines.append(f"- **Attachment Download Ready:** {safe_text(opp.get('attachment_download_ready'))}")
        lines.append(f"- **Attachment Discovery Method:** {safe_text(opp.get('attachment_discovery_method'))}")
        lines.append(f"- **Resource Link Type Summary:** {safe_text(opp.get('resource_link_type_summary'))}")
        lines.append(f"- **Actual Downloadable Attachments:** {safe_text(opp.get('actual_downloadable_attachment_count'))}")
        lines.append(f"- **SAM Detail API Links:** {safe_text(opp.get('sam_detail_api_link_count'))}")
        lines.append(f"- **SAM Notice Description API Links:** {safe_text(opp.get('sam_notice_desc_link_count'))}")
        lines.append(f"- **SAM Workspace Links:** {safe_text(opp.get('sam_workspace_link_count'))}")
        lines.append(f"- **External Portal Links:** {safe_text(opp.get('external_portal_link_count'))}")
        lines.append(f"- **Likely Documents Needed:** {safe_text(opp.get('likely_documents_needed'))}")
        lines.append(f"- **Likely PWS/SOW Needed:** {safe_text(opp.get('likely_pws_needed'))}")
        lines.append(f"- **Likely SF1449 Needed:** {safe_text(opp.get('likely_sf1449_needed'))}")
        lines.append(f"- **Likely SF30 Needed:** {safe_text(opp.get('likely_sf30_needed'))}")
        lines.append(f"- **Likely Pricing Needed:** {safe_text(opp.get('likely_pricing_needed'))}")
        lines.append(f"- **Attachment Next Action:** {safe_text(opp.get('attachment_next_action'))}")

        if opp.get("resource_link_classification_text"):
            lines.append("")
            lines.append("### Resource Link Classification")
            lines.append("")
            lines.append(safe_text(opp.get("resource_link_classification_text")))

        lines.append("")
        lines.append("## Local Attachment Intake")
        lines.append("")
        lines.append(f"- **Local Attachment Folder:** {safe_text(opp.get('local_attachment_folder'))}")
        lines.append(f"- **Local Attachments Found:** {safe_text(opp.get('local_attachments_found'))}")
        lines.append(f"- **Local Attachment Count:** {safe_text(opp.get('local_attachment_count'))}")
        lines.append(f"- **Local Attachment Likely Types:** {safe_text(opp.get('local_attachment_likely_types'))}")
        lines.append(f"- **Local PWS/SOW Found:** {safe_text(opp.get('local_pws_found'))}")
        lines.append(f"- **Local SF1449 Found:** {safe_text(opp.get('local_sf1449_found'))}")
        lines.append(f"- **Local SF30/Amendment Found:** {safe_text(opp.get('local_sf30_found'))}")
        lines.append(f"- **Local Pricing Found:** {safe_text(opp.get('local_pricing_found'))}")
        lines.append(f"- **Local Wage Determination Found:** {safe_text(opp.get('local_wage_determination_found'))}")
        lines.append(f"- **Local Solicitation Found:** {safe_text(opp.get('local_solicitation_found'))}")
        lines.append(f"- **Ready for Bid/No-Bid Analysis:** {safe_text(opp.get('ready_for_bid_no_bid_analysis'))}")
        lines.append(f"- **Local Attachment Next Action:** {safe_text(opp.get('local_attachment_next_action'))}")

        if opp.get("local_attachment_file_list"):
            lines.append("")
            lines.append("### Local Attachment Files")
            lines.append("")
            for item in str(opp.get("local_attachment_file_list")).split("\n"):
                if item.strip():
                    lines.append(f"- {item.strip()}")

        lines.append("")
        lines.append("## Execution / Compliance Flags")
        lines.append("")

        flag_fields = [
            "evaluation_method",
            "submission_method",
            "forms_required_text",
            "amendment_compliance_alert",
            "rfi_needed",
            "set_aside_hard_gate",
            "on_site_staffing_flag",
            "mandatory_staffing_flag",
            "telework_ambiguity_flag",
            "local_staffing_dependency",
            "performance_location_risk",
            "staffing_model",
            "small_business_subcontracting_check",
            "prime_case_report_required",
            "team_lock_alert",
            "step1_mandatory_flag",
            "scientific_domain_complexity_flag",
            "subcontractor_role_classifier",
        ]

        for field in flag_fields:
            lines.append(f"- **{field}:** {safe_text(opp.get(field))}")

        if opp.get("rfi_recommendation"):
            lines.append("")
            lines.append("## Recommended RFI")
            lines.append("")
            lines.append(safe_text(opp.get("rfi_recommendation")))

        score_reasons = normalize_score_reasons(opp.get("score_reasons"))

        if score_reasons:
            lines.append("")
            lines.append("## Why This Matched")
            lines.append("")
            for reason in score_reasons:
                lines.append(f"- {reason}")

        summary = clean_summary(
            opp.get("full_description")
            or opp.get("short_description")
            or opp.get("description")
        )

        if summary:
            lines.append("")
            lines.append("## Description / Summary")
            lines.append("")
            lines.append(summary[:4000])
            if len(summary) > 4000:
                lines.append("")
                lines.append("...[truncated]")

        if opp.get("actual_downloadable_attachment_links"):
            lines.append("")
            lines.append("## Actual Downloadable Attachment Links")
            lines.append("")
            for link in str(opp.get("actual_downloadable_attachment_links")).split(" | "):
                if link.strip():
                    lines.append(f"- {link.strip()}")

        if opp.get("sam_detail_api_links"):
            lines.append("")
            lines.append("## SAM Detail API Links")
            lines.append("")
            for link in str(opp.get("sam_detail_api_links")).split(" | "):
                if link.strip():
                    lines.append(f"- {link.strip()}")

        if opp.get("resource_links"):
            lines.append("")
            lines.append("## Original Resource Links")
            lines.append("")
            for link in str(opp.get("resource_links")).split(" | "):
                if link.strip():
                    lines.append(f"- {link.strip()}")

        if opp.get("ui_link"):
            lines.append("")
            lines.append("## SAM.gov Link")
            lines.append("")
            lines.append(safe_text(opp.get("ui_link")))

        lines.append("")
        lines.append("## Paste This Into Opportunity Analysis Chat")
        lines.append("")
        lines.append("Analyze this opportunity for bid/no-bid, prime vs subcontractor strategy, compliance risk, RFI questions, and proposal outline.")

        packet_path.write_text("\n".join(lines), encoding="utf-8")

    return str(output_path)