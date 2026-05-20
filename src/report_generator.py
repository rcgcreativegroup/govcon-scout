from collections import defaultdict
from datetime import datetime
from pathlib import Path


LANE_LABELS = {
    "marketing_communications": "Marketing / Communications",
    "ai_automation": "AI / Automation",
    "training_documentation": "Training / Documentation",
    "events_video": "Events / Video Production",
    "trucking_transportation": "Trucking / Transportation",
    "janitorial_facility_support": "Janitorial / Facility Support",
    "pest_control": "Pest Control",
    "facility_supplies_distribution": "Facility Supplies / Consumables",
    "unknown": "Unknown / Needs Review",
}


DEADLINE_LABELS = {
    "unknown": "Unknown Deadline",
    "unknown_format": "Deadline Format Needs Review",
    "overdue_or_archived": "Overdue / Archived",
    "too_soon": "Too Soon",
    "urgent": "Urgent",
    "reasonable": "Reasonable Window",
    "long_window": "Longer Window",
}


def format_lane_label(lane_name):
    return LANE_LABELS.get(lane_name, lane_name.replace("_", " ").title())


def format_deadline_label(deadline_status):
    return DEADLINE_LABELS.get(deadline_status, deadline_status.replace("_", " ").title())


def is_notice_desc_url(value):
    if not value:
        return False

    text = str(value).strip().lower()

    return (
        text.startswith("http")
        and "api.sam.gov" in text
        and "noticedesc" in text
    )


def clean_summary_text(value):
    if not value:
        return ""

    text = str(value).strip()

    if is_notice_desc_url(text):
        return ""

    if text.startswith('{"description"'):
        text = text.replace('{"description":"', "").rstrip('"}')

    text = text.replace("\\n", "\n")
    text = text.replace('\\"', '"')
    text = text.replace("\\t", "\t")

    return text.strip()


def group_opportunities_by_lane(scored_opportunities):
    grouped = defaultdict(list)

    for opportunity in scored_opportunities:
        lane = opportunity.get("matched_lane", "unknown")
        grouped[lane].append(opportunity)

    return grouped


def write_opportunity_block(lines, index, opp):
    title = opp.get("title", "Untitled Opportunity")
    agency = opp.get("department_ind_agency", "Unknown Agency")
    notice_id = opp.get("notice_id", "N/A")
    response_deadline = opp.get("response_deadline", "N/A")
    naics = opp.get("naics_code", "N/A")
    psc = opp.get("psc_code", "N/A")
    score = opp.get("fit_score", 0)
    prime_reality_score = opp.get("prime_reality_score", 0)
    recommendation = opp.get("recommendation", "Review")
    conditional_recommendation = opp.get("conditional_recommendation", "")
    matched_lane = opp.get("matched_lane", "unknown")
    deadline_status = opp.get("deadline_status", "unknown")
    days_until_deadline = opp.get("days_until_deadline", "")

    lines.append(f"### {index}. {title}")
    lines.append("")
    lines.append(f"**Fit Score:** {score}/100")
    lines.append(f"**Prime Reality Score:** {prime_reality_score}/100")
    lines.append(f"**Recommendation:** {recommendation}")

    if conditional_recommendation:
        lines.append(f"**Conditional Recommendation:** {conditional_recommendation}")

    lines.append(f"**Notice Actionability:** {opp.get('notice_actionability', 'Unknown')}")
    lines.append(f"**Matched Lane:** {format_lane_label(matched_lane)}")
    lines.append(f"**Deadline Status:** {format_deadline_label(deadline_status)}")

    if days_until_deadline != "":
        lines.append(f"**Days Until Deadline:** {days_until_deadline}")

    if opp.get("due_date_solicitation_local"):
        lines.append(f"**Due Date — Solicitation Local Time:** {opp.get('due_date_solicitation_local')}")

    if opp.get("due_date_user_local"):
        lines.append(f"**Due Date — User Local Time:** {opp.get('due_date_user_local')}")

    lines.append(f"**Agency:** {agency}")
    lines.append(f"**Notice ID:** {notice_id}")
    lines.append(f"**NAICS:** {naics}")
    lines.append(f"**PSC:** {psc}")
    lines.append(f"**Response Deadline:** {response_deadline}")
    lines.append(f"**Description Enriched:** {opp.get('description_enriched', 'No')}")
    lines.append(f"**Resource Links / Attachments Found:** {opp.get('resource_link_count', 0)}")
    lines.append("")

    lines.append("**Execution / Compliance Flags:**")
    lines.append(f"- Evaluation Method: {opp.get('evaluation_method', 'Unknown')}")
    lines.append(f"- Submission Method: {opp.get('submission_method', 'Unknown')}")
    lines.append(f"- Compliance Risk: {opp.get('compliance_risk', 'Unknown')}")
    lines.append(f"- Set-Aside Hard Gate: {opp.get('set_aside_hard_gate', 'Unknown')}")
    lines.append(f"- On-Site Staffing Flag: {opp.get('on_site_staffing_flag', 'Unknown')}")
    lines.append(f"- Mandatory Staffing Flag: {opp.get('mandatory_staffing_flag', 'Unknown')}")
    lines.append(f"- Telework Ambiguity Flag: {opp.get('telework_ambiguity_flag', 'Unknown')}")
    lines.append(f"- Remote Feasibility Score: {opp.get('remote_feasibility_score', '')}/10")
    lines.append(f"- Local Staffing Dependency: {opp.get('local_staffing_dependency', 'Unknown')}")
    lines.append(f"- Performance Location Risk: {opp.get('performance_location_risk', 'Unknown')}")
    lines.append(f"- Staffing Model: {opp.get('staffing_model', 'Unknown')}")
    lines.append(f"- Execution Risk: {opp.get('execution_risk', 'Unknown')}")
    lines.append(f"- RFI Needed: {opp.get('rfi_needed', 'Unknown')}")
    lines.append(f"- Amendment Compliance Alert: {opp.get('amendment_compliance_alert', 'Unknown')}")
    lines.append(f"- Small Business Subcontracting Check: {opp.get('small_business_subcontracting_check', 'Unknown')}")
    lines.append(f"- Prime Case Report Required: {opp.get('prime_case_report_required', 'Unknown')}")
    lines.append(f"- Team Lock Alert: {opp.get('team_lock_alert', 'Unknown')}")
    lines.append(f"- Step 1 Mandatory Flag: {opp.get('step1_mandatory_flag', 'Unknown')}")
    lines.append(f"- Scientific Domain Complexity: {opp.get('scientific_domain_complexity_flag', 'Unknown')}")
    lines.append("")

    if opp.get("subcontractor_role_classifier"):
        lines.append("**Possible Subcontractor Workshare:**")
        lines.append("")
        lines.append(opp.get("subcontractor_role_classifier"))
        lines.append("")

    if opp.get("rfi_recommendation"):
        lines.append("**Recommended RFI:**")
        lines.append("")
        lines.append(opp.get("rfi_recommendation"))
        lines.append("")

    if opp.get("amendment_compliance_task"):
        lines.append("**Amendment Compliance Task:**")
        lines.append("")
        lines.append(opp.get("amendment_compliance_task"))
        lines.append("")

    if opp.get("subcontracting_note"):
        lines.append("**Subcontracting Review Note:**")
        lines.append("")
        lines.append(opp.get("subcontracting_note"))
        lines.append("")

    if opp.get("prime_case_report_note"):
        lines.append("**Prime Case Report Note:**")
        lines.append("")
        lines.append(opp.get("prime_case_report_note"))
        lines.append("")

    if opp.get("team_lock_note"):
        lines.append("**Team Lock Note:**")
        lines.append("")
        lines.append(opp.get("team_lock_note"))
        lines.append("")

    if opp.get("idiq_note"):
        lines.append("**IDIQ Revenue Note:**")
        lines.append("")
        lines.append(opp.get("idiq_note"))
        lines.append("")

    if opp.get("scientific_domain_note"):
        lines.append("**Scientific Domain Risk Note:**")
        lines.append("")
        lines.append(opp.get("scientific_domain_note"))
        lines.append("")

    if opp.get("step1_deadline_note"):
        lines.append("**Step 1 Deadline Note:**")
        lines.append("")
        lines.append(opp.get("step1_deadline_note"))
        lines.append("")

    if opp.get("forms_required_text"):
        lines.append("**Detected Required Forms / Attachments:**")
        lines.append("")
        lines.append(opp.get("forms_required_text"))
        lines.append("")

    reasons = opp.get("score_reasons", [])
    if reasons:
        lines.append("**Why this matched:**")
        for reason in reasons[:14]:
            lines.append(f"- {reason}")
        lines.append("")

    summary = (
        opp.get("short_description")
        or opp.get("full_description")
        or opp.get("description")
        or ""
    )

    summary = clean_summary_text(summary)

    if summary:
        short_description = summary[:1000].strip()
        lines.append("**Summary:**")
        lines.append("")
        lines.append(short_description)
        if len(summary) > 1000:
            lines.append("...")
        lines.append("")

    if opp.get("resource_links"):
        lines.append("**Resource Links:**")
        lines.append("")
        for link in str(opp.get("resource_links")).split(" | ")[:8]:
            if link.strip():
                lines.append(f"- {link.strip()}")
        lines.append("")

    ui_link = opp.get("ui_link")
    if ui_link:
        lines.append(f"**SAM.gov Link:** {ui_link}")
        lines.append("")

    lines.append("---")
    lines.append("")


def write_award_intel_block(lines, index, opp):
    lines.append(f"### {index}. {opp.get('title', 'Untitled Award')}")
    lines.append("")
    lines.append(f"**Agency:** {opp.get('department_ind_agency', 'Unknown Agency')}")
    lines.append(f"**Notice ID:** {opp.get('notice_id', 'N/A')}")
    lines.append(f"**Matched Lane:** {format_lane_label(opp.get('matched_lane', 'unknown'))}")
    lines.append(f"**NAICS:** {opp.get('naics_code', 'N/A')}")
    lines.append(f"**PSC:** {opp.get('psc_code', 'N/A')}")
    lines.append(f"**Awardee:** {opp.get('awardee_name', '') or 'Not detected'}")
    lines.append(f"**Award Amount:** {opp.get('award_amount', '') or 'Not detected'}")
    lines.append(f"**Award Date:** {opp.get('award_date', '') or 'Not detected'}")
    lines.append(f"**Award Number:** {opp.get('award_number', '') or 'Not detected'}")
    lines.append(f"**Market Intel Value:** {opp.get('market_intel_value', 'Unknown')}")
    lines.append("")

    if opp.get("score_reasons"):
        lines.append("**Why this matters:**")
        for reason in opp.get("score_reasons", [])[:8]:
            lines.append(f"- {reason}")
        lines.append("")

    if opp.get("ui_link"):
        lines.append(f"**SAM.gov Link:** {opp.get('ui_link')}")
        lines.append("")

    lines.append("---")
    lines.append("")


def generate_markdown_report(scored_opportunities, output_dir="reports", per_lane_limit=10):
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    report_path = Path(output_dir) / f"govcon_scout_daily_report_{today}.md"

    actionable_opportunities = [
        opp for opp in scored_opportunities
        if opp.get("notice_actionability") == "actionable"
    ]

    non_actionable_count = len(scored_opportunities) - len(actionable_opportunities)

    top_actionable_pool = actionable_opportunities[:50]

    lines = []
    lines.append("# GovCon Scout Daily Pursuit Report")
    lines.append("")
    lines.append(f"**Date:** {today}")
    lines.append("")
    lines.append(f"**Total Opportunities Scored:** {len(scored_opportunities)}")
    lines.append(f"**Actionable Opportunities Found:** {len(actionable_opportunities)}")
    lines.append(f"**Actionable Opportunities in Report Pool:** {len(top_actionable_pool)}")
    lines.append(f"**Excluded Non-Actionable / Awarded Notices:** {non_actionable_count}")
    lines.append("")

    if not top_actionable_pool:
        lines.append("No actionable matching opportunities found today.")
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return str(report_path)

    grouped = group_opportunities_by_lane(top_actionable_pool)

    preferred_order = [
        "marketing_communications",
        "ai_automation",
        "training_documentation",
        "events_video",
        "trucking_transportation",
        "janitorial_facility_support",
        "pest_control",
        "facility_supplies_distribution",
        "unknown",
    ]

    lines.append("## Executive Snapshot")
    lines.append("")

    for lane in preferred_order:
        lane_items = grouped.get(lane, [])
        if not lane_items:
            continue

        best_score = max(item.get("fit_score", 0) for item in lane_items)
        best_prime_score = max(item.get("prime_reality_score", 0) for item in lane_items)
        urgent_count = sum(1 for item in lane_items if item.get("deadline_status") in ["too_soon", "urgent"])
        rfi_count = sum(1 for item in lane_items if item.get("rfi_needed") == "Yes")
        hard_gate_count = sum(1 for item in lane_items if item.get("set_aside_hard_gate") == "Yes")
        local_staffing_count = sum(1 for item in lane_items if item.get("local_staffing_dependency") == "Yes")
        enriched_count = sum(1 for item in lane_items if item.get("description_enriched") == "Yes")

        snapshot_line = (
            f"- **{format_lane_label(lane)}:** "
            f"{len(lane_items)} reviewed, best fit {best_score}/100, "
            f"best prime reality {best_prime_score}/100, "
            f"{enriched_count} enriched description(s)"
        )

        if urgent_count:
            snapshot_line += f", {urgent_count} urgent/too-soon deadline(s)"
        if rfi_count:
            snapshot_line += f", {rfi_count} RFI-needed item(s)"
        if hard_gate_count:
            snapshot_line += f", {hard_gate_count} set-aside hard gate(s)"
        if local_staffing_count:
            snapshot_line += f", {local_staffing_count} local-staffing dependency item(s)"

        lines.append(snapshot_line)

    lines.append("")
    lines.append("---")
    lines.append("")

    for lane in preferred_order:
        lane_items = grouped.get(lane, [])

        if not lane_items:
            continue

        lane_items = sorted(
            lane_items,
            key=lambda item: (
                item.get("fit_score", 0),
                item.get("prime_reality_score", 0),
            ),
            reverse=True,
        )

        lines.append(f"## Top {format_lane_label(lane)} Opportunities")
        lines.append("")

        for index, opp in enumerate(lane_items[:per_lane_limit], start=1):
            write_opportunity_block(lines, index, opp)

    report_path.write_text("\n".join(lines), encoding="utf-8")

    return str(report_path)


def generate_awards_intel_report(scored_opportunities, output_dir="reports", limit=50):
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    report_path = Path(output_dir) / f"govcon_scout_awards_intel_{today}.md"

    award_items = [
        opp for opp in scored_opportunities
        if opp.get("notice_actionability") == "awarded_market_intel"
    ]

    award_items = sorted(
        award_items,
        key=lambda item: (
            item.get("market_intel_value") == "High",
            item.get("award_amount", ""),
            item.get("fit_score", 0),
        ),
        reverse=True,
    )

    lines = []
    lines.append("# GovCon Scout Awards Intelligence Report")
    lines.append("")
    lines.append(f"**Date:** {today}")
    lines.append("")
    lines.append(f"**Award Notices Found:** {len(award_items)}")
    lines.append("")
    lines.append("Use this report for incumbent research, recompete tracking, agency buying patterns, pricing intelligence, and teaming target discovery.")
    lines.append("")

    if not award_items:
        lines.append("No award notices detected in this scan.")
        report_path.write_text("\n".join(lines), encoding="utf-8")
        return str(report_path)

    for index, opp in enumerate(award_items[:limit], start=1):
        write_award_intel_block(lines, index, opp)

    report_path.write_text("\n".join(lines), encoding="utf-8")

    return str(report_path)