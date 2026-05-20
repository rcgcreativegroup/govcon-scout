from urllib.parse import parse_qs, urlparse


DOWNLOAD_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".zip",
    ".txt",
    ".rtf",
    ".ppt",
    ".pptx",
}


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def lower_text(value):
    return safe_text(value).lower()


def score_value(opp, field_name):
    try:
        return int(float(opp.get(field_name, 0) or 0))
    except (TypeError, ValueError):
        return 0


def split_resource_links(value):
    text = safe_text(value)

    if not text:
        return []

    return [item.strip() for item in text.split(" | ") if item.strip()]


def classify_resource_link(link):
    raw_link = safe_text(link)
    lower_link = raw_link.lower()

    if not raw_link:
        return {
            "url": raw_link,
            "link_type": "empty",
            "is_downloadable": "No",
            "detected_filename": "",
            "detected_extension": "",
            "note": "Empty link",
        }

    parsed = urlparse(raw_link)
    path = parsed.path or ""
    path_lower = path.lower()
    query = parse_qs(parsed.query)
    filename = path.split("/")[-1] if path else ""
    detected_extension = ""

    for extension in DOWNLOAD_EXTENSIONS:
        if path_lower.endswith(extension):
            detected_extension = extension
            break

    if detected_extension:
        return {
            "url": raw_link,
            "link_type": "direct_download",
            "is_downloadable": "Yes",
            "detected_filename": filename,
            "detected_extension": detected_extension,
            "note": "Direct downloadable file link detected",
        }

    if "api.sam.gov" in lower_link and "noticedesc" in lower_link:
        return {
            "url": raw_link,
            "link_type": "sam_notice_description_api",
            "is_downloadable": "No",
            "detected_filename": "",
            "detected_extension": "",
            "note": "SAM.gov notice description API link; useful for description enrichment, not an attachment",
        }

    if "api.sam.gov" in lower_link and "/opportunities/v2/search" in lower_link:
        notice_id = ""
        if "noticeid" in query and query["noticeid"]:
            notice_id = query["noticeid"][0]

        return {
            "url": raw_link,
            "link_type": "sam_detail_api",
            "is_downloadable": "No",
            "detected_filename": "",
            "detected_extension": "",
            "note": f"SAM.gov detail/search API link for notice {notice_id}; use selected detail enrichment before attempting attachment download",
        }

    if "sam.gov/workspace/contract/opp/" in lower_link:
        return {
            "url": raw_link,
            "link_type": "sam_workspace_page",
            "is_downloadable": "No",
            "detected_filename": "",
            "detected_extension": "",
            "note": "SAM.gov workspace opportunity page; manual review or browser workflow required",
        }

    if "sam.gov" in lower_link:
        return {
            "url": raw_link,
            "link_type": "sam_other",
            "is_downloadable": "No",
            "detected_filename": "",
            "detected_extension": "",
            "note": "SAM.gov link, but not classified as a direct attachment",
        }

    if parsed.scheme in ["http", "https"]:
        return {
            "url": raw_link,
            "link_type": "external_portal_or_link",
            "is_downloadable": "No",
            "detected_filename": "",
            "detected_extension": "",
            "note": "External portal/link detected; may require manual review",
        }

    return {
        "url": raw_link,
        "link_type": "unknown",
        "is_downloadable": "No",
        "detected_filename": "",
        "detected_extension": "",
        "note": "Unknown resource link type",
    }


def classify_resource_links(opp):
    links = split_resource_links(opp.get("resource_links"))
    classifications = [classify_resource_link(link) for link in links]

    counts = {
        "direct_download": 0,
        "sam_detail_api": 0,
        "sam_notice_description_api": 0,
        "sam_workspace_page": 0,
        "sam_other": 0,
        "external_portal_or_link": 0,
        "unknown": 0,
        "empty": 0,
    }

    downloadable_links = []
    detail_api_links = []
    notice_desc_links = []
    workspace_links = []
    external_links = []
    unknown_links = []

    for item in classifications:
        link_type = item.get("link_type", "unknown")
        counts[link_type] = counts.get(link_type, 0) + 1

        if item.get("is_downloadable") == "Yes":
            downloadable_links.append(item.get("url", ""))

        if link_type == "sam_detail_api":
            detail_api_links.append(item.get("url", ""))

        if link_type == "sam_notice_description_api":
            notice_desc_links.append(item.get("url", ""))

        if link_type == "sam_workspace_page":
            workspace_links.append(item.get("url", ""))

        if link_type == "external_portal_or_link":
            external_links.append(item.get("url", ""))

        if link_type in ["unknown", "sam_other"]:
            unknown_links.append(item.get("url", ""))

    classification_lines = []

    for item in classifications:
        classification_lines.append(
            f"{item.get('link_type')}: {item.get('url')} — {item.get('note')}"
        )

    return {
        "resource_link_classifications": classifications,
        "resource_link_classification_text": "\n".join(classification_lines),
        "actual_downloadable_attachment_count": len(downloadable_links),
        "actual_downloadable_attachment_links": " | ".join(downloadable_links),
        "sam_detail_api_link_count": len(detail_api_links),
        "sam_detail_api_links": " | ".join(detail_api_links),
        "sam_notice_desc_link_count": len(notice_desc_links),
        "sam_notice_desc_links": " | ".join(notice_desc_links),
        "sam_workspace_link_count": len(workspace_links),
        "sam_workspace_links": " | ".join(workspace_links),
        "external_portal_link_count": len(external_links),
        "external_portal_links": " | ".join(external_links),
        "unknown_resource_link_count": len(unknown_links),
        "unknown_resource_links": " | ".join(unknown_links),
        "resource_link_type_summary": (
            f"direct_download={counts.get('direct_download', 0)}, "
            f"sam_detail_api={counts.get('sam_detail_api', 0)}, "
            f"sam_notice_description_api={counts.get('sam_notice_description_api', 0)}, "
            f"sam_workspace_page={counts.get('sam_workspace_page', 0)}, "
            f"external={counts.get('external_portal_or_link', 0)}, "
            f"unknown={counts.get('unknown', 0) + counts.get('sam_other', 0)}"
        ),
    }


def detect_likely_documents(opp):
    title = lower_text(opp.get("title"))
    description = lower_text(
        opp.get("short_description")
        or opp.get("full_description")
        or opp.get("description")
    )
    forms = lower_text(opp.get("forms_required_text"))
    resource_links_text = lower_text(opp.get("resource_links"))
    notice_type = lower_text(opp.get("notice_type"))

    combined = " ".join([
        title,
        description,
        forms,
        resource_links_text,
        notice_type,
    ])

    likely_docs = set()
    detected_keywords = set()

    doc_patterns = {
        "Solicitation / RFQ / RFP": [
            "solicitation",
            "rfq",
            "request for quote",
            "request for quotation",
            "rfp",
            "request for proposal",
            "combined synopsis",
        ],
        "Performance Work Statement / Statement of Work": [
            "pws",
            "performance work statement",
            "statement of work",
            "sow",
            "scope of work",
        ],
        "SF1449": [
            "sf1449",
            "sf 1449",
            "standard form 1449",
        ],
        "SF30 Amendment": [
            "sf30",
            "sf 30",
            "standard form 30",
            "amendment",
        ],
        "Pricing Schedule / CLINs": [
            "pricing",
            "price schedule",
            "clin",
            "clins",
            "bid schedule",
            "schedule of supplies",
        ],
        "Quality Control Plan": [
            "quality control plan",
            "qcp",
        ],
        "Wage Determination": [
            "wage determination",
            "service contract act",
            "sca",
        ],
        "Past Performance / Experience": [
            "past performance",
            "experience",
            "case report",
            "case reports",
        ],
        "Questions and Answers": [
            "questions and answers",
            "q&a",
            "qa",
            "answers to questions",
        ],
    }

    for doc_name, patterns in doc_patterns.items():
        for pattern in patterns:
            if pattern in combined:
                likely_docs.add(doc_name)
                detected_keywords.add(pattern)

    has_links = (
        safe_text(opp.get("has_resource_links")).lower() == "yes"
        or score_value(opp, "resource_link_count") > 0
    )

    if has_links and not likely_docs:
        likely_docs.add("Solicitation / Attachment Package")

    if opp.get("amendment_compliance_alert") in ["Yes", "Possible"]:
        likely_docs.add("SF30 Amendment")

    if opp.get("forms_required_text"):
        for form_name in safe_text(opp.get("forms_required_text")).split(","):
            cleaned = form_name.strip()
            if cleaned:
                likely_docs.add(cleaned)

    return sorted(likely_docs), sorted(detected_keywords)


def determine_attachment_review_priority(opp, likely_docs):
    resource_count = score_value(opp, "resource_link_count")
    downloadable_count = score_value(opp, "actual_downloadable_attachment_count")
    compliance_risk = safe_text(opp.get("compliance_risk"))
    fit_score = score_value(opp, "fit_score")
    prime_score = score_value(opp, "prime_reality_score")

    if opp.get("sam_detail_enriched") == "Yes" and downloadable_count == 0:
        if prime_score >= 55:
            return "Medium"
        return "Low"

    high_value_docs = {
        "Performance Work Statement / Statement of Work",
        "SF1449",
        "SF30 Amendment",
        "Pricing Schedule / CLINs",
        "Wage Determination",
    }

    has_high_value_docs = any(doc in high_value_docs for doc in likely_docs)

    if downloadable_count > 0 and resource_count >= 5:
        return "High"

    if downloadable_count > 0 and has_high_value_docs:
        return "High"

    if downloadable_count > 0 and prime_score >= 55:
        return "High"

    if compliance_risk == "High":
        return "High"

    if resource_count > 0 and prime_score >= 55:
        return "Medium"

    if fit_score >= 50 and resource_count > 0:
        return "Medium"

    if resource_count > 0:
        return "Medium"

    return "Low"


def determine_attachment_discovery_method(opp):
    downloadable_count = score_value(opp, "actual_downloadable_attachment_count")
    detail_count = score_value(opp, "sam_detail_api_link_count")
    workspace_count = score_value(opp, "sam_workspace_link_count")
    external_count = score_value(opp, "external_portal_link_count")
    resource_count = score_value(opp, "resource_link_count")

    if downloadable_count > 0:
        return "direct_download_links_available"

    if opp.get("sam_detail_enriched") == "Yes" and downloadable_count == 0:
        return "sam_detail_completed_no_attachments"

    if detail_count > 0:
        return "sam_detail_enrichment_required"

    if external_count > 0:
        return "external_portal_review_required"

    if workspace_count > 0:
        return "sam_workspace_manual_review_required"

    if resource_count > 0:
        return "resource_links_unclassified"

    return "no_resource_links_detected"


def build_attachment_next_action(opp, priority, likely_docs):
    has_links = (
        safe_text(opp.get("has_resource_links")).lower() == "yes"
        or score_value(opp, "resource_link_count") > 0
    )

    discovery_method = determine_attachment_discovery_method(opp)

    if not has_links:
        return "No resource links detected. Review SAM.gov manually only if this opportunity is otherwise interesting."

    if discovery_method == "direct_download_links_available":
        if priority == "High":
            return (
                "Download and review attachments before bid/no-bid. Confirm PWS/SOW, SF1449, amendments, "
                "pricing schedule, submission instructions, site visit requirements, wage determination, and required forms."
            )

        return (
            "Download attachments if the opportunity remains interesting. Confirm scope, deadline, pricing format, "
            "and submission instructions."
        )

    if discovery_method == "sam_detail_completed_no_attachments":
        return (
            "SAM detail enrichment was completed, but the API response did not expose downloadable attachments. "
            "Open the SAM.gov workspace manually or use a browser automation workflow to retrieve files."
        )

    if discovery_method == "sam_detail_enrichment_required":
        return (
            "Actual attachment links are not available yet. Run selected SAM detail enrichment for this opportunity, "
            "or open the SAM.gov workspace page manually to locate attachments."
        )

    if discovery_method == "external_portal_review_required":
        return (
            "Resource link points to an external portal or non-SAM page. Open manually and confirm whether downloads, "
            "registration, or portal submission steps are required."
        )

    if discovery_method == "sam_workspace_manual_review_required":
        return (
            "SAM.gov workspace page detected. Open manually to inspect attachments until browser/detail enrichment is added."
        )

    return (
        "Resource links exist but are not classified as downloadable. Manual review or selected detail enrichment is required."
    )


def apply_attachment_intel_to_opportunity(opp):
    resource_info = classify_resource_links(opp)
    opp.update(resource_info)

    likely_docs, detected_keywords = detect_likely_documents(opp)
    priority = determine_attachment_review_priority(opp, likely_docs)

    resource_count = score_value(opp, "resource_link_count")
    has_links = (
        safe_text(opp.get("has_resource_links")).lower() == "yes"
        or resource_count > 0
    )

    downloadable_count = score_value(opp, "actual_downloadable_attachment_count")
    discovery_method = determine_attachment_discovery_method(opp)

    attachment_review_needed = "Yes" if has_links else "No"
    attachment_download_ready = "Yes" if downloadable_count > 0 else "No"

    opp["attachment_review_needed"] = attachment_review_needed
    opp["attachment_review_priority"] = priority if has_links else "Low"
    opp["attachment_download_ready"] = attachment_download_ready
    opp["attachment_discovery_method"] = discovery_method
    opp["likely_documents_needed"] = ", ".join(likely_docs)
    opp["attachment_keywords_detected"] = ", ".join(detected_keywords)
    opp["attachment_next_action"] = build_attachment_next_action(opp, priority, likely_docs)

    opp["likely_pws_needed"] = (
        "Yes"
        if "Performance Work Statement / Statement of Work" in likely_docs
        else "Unknown"
        if has_links
        else "No"
    )

    opp["likely_sf1449_needed"] = (
        "Yes"
        if "SF1449" in likely_docs
        else "Unknown"
        if has_links
        else "No"
    )

    opp["likely_sf30_needed"] = (
        "Yes"
        if "SF30 Amendment" in likely_docs
        else "Unknown"
        if has_links
        else "No"
    )

    opp["likely_pricing_needed"] = (
        "Yes"
        if "Pricing Schedule / CLINs" in likely_docs
        else "Unknown"
        if has_links
        else "No"
    )

    return opp


def apply_attachment_intel(scored_opportunities):
    return [
        apply_attachment_intel_to_opportunity(opp)
        for opp in scored_opportunities
    ]