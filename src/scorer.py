import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


USER_LOCAL_TIMEZONE = "America/Chicago"


def normalize_text(value):
    if value is None:
        return ""
    return str(value).lower().strip()


def is_notice_desc_url(value):
    if not value:
        return False

    text = str(value).strip().lower()

    return (
        text.startswith("http")
        and "api.sam.gov" in text
        and "noticedesc" in text
    )


def keyword_matches_text(keyword, text):
    if not keyword or not text:
        return False

    keyword_clean = normalize_text(keyword)

    if len(keyword_clean) <= 3:
        pattern = r"(?<![a-z0-9])" + re.escape(keyword_clean) + r"(?![a-z0-9])"
        return re.search(pattern, text, flags=re.IGNORECASE) is not None

    if " " in keyword_clean:
        return keyword_clean in text

    pattern = r"\b" + re.escape(keyword_clean) + r"\b"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def get_primary_opportunity_text(opportunity):
    """
    High-value match text. This should drive scoring.
    """
    fields = [
        "title",
        "description",
        "full_description",
        "short_description",
        "notice_type",
        "set_aside",
        "typeOfSetAsideDescription",
        "place_of_performance",
        "response_deadline",
    ]

    combined = []

    for field in fields:
        value = opportunity.get(field)
        if value and not is_notice_desc_url(value):
            combined.append(str(value))

    return normalize_text(" ".join(combined))


def get_context_opportunity_text(opportunity):
    """
    Lower-value context text. This should not strongly drive scoring.
    """
    fields = [
        "department_ind_agency",
        "sub_tier",
        "office",
        "fullParentPathName",
        "contacts",
        "resource_links",
    ]

    combined = []

    for field in fields:
        value = opportunity.get(field)
        if value and not is_notice_desc_url(value):
            combined.append(str(value))

    return normalize_text(" ".join(combined))


def get_opportunity_text(opportunity):
    """
    Combined text for detection logic.
    """
    return f"{get_primary_opportunity_text(opportunity)} {get_context_opportunity_text(opportunity)}".strip()


def get_opportunity_naics(opportunity):
    return str(
        opportunity.get("naics_code")
        or opportunity.get("naicsCode")
        or opportunity.get("naics")
        or ""
    ).strip()


def get_notice_type_text(opportunity):
    return normalize_text(
        opportunity.get("type")
        or opportunity.get("notice_type")
        or opportunity.get("noticeType")
        or opportunity.get("typeOfNotice")
        or opportunity.get("baseType")
        or ""
    )


def get_award_value(opportunity, keys):
    for key in keys:
        value = opportunity.get(key)
        if value not in [None, ""]:
            return value
    return ""


def extract_award_details(opportunity):
    award_data = opportunity.get("award")

    if isinstance(award_data, dict):
        awardee_name = get_award_value(award_data, [
            "awardeeName",
            "contractorAwardedName",
            "contractorName",
            "awardee",
            "name",
        ])
        award_amount = get_award_value(award_data, [
            "awardAmount",
            "amount",
            "totalAwardAmount",
            "baseAndAllOptionsValue",
            "contractValue",
        ])
        award_date = get_award_value(award_data, [
            "awardDate",
            "date",
            "contractAwardDate",
        ])
        award_number = get_award_value(award_data, [
            "awardNumber",
            "contractAwardNumber",
            "contractNumber",
        ])
    else:
        awardee_name = get_award_value(opportunity, [
            "awardeeName",
            "contractorAwardedName",
            "contractor_awarded_name",
            "contractorName",
            "awardee",
        ])
        award_amount = get_award_value(opportunity, [
            "awardAmount",
            "award_amount",
            "baseAndAllOptionsValue",
            "base_and_all_options_value",
            "totalContractValue",
            "total_contract_value",
            "contractValue",
        ])
        award_date = get_award_value(opportunity, [
            "awardDate",
            "award_date",
            "contractAwardDate",
            "contract_award_date",
        ])
        award_number = get_award_value(opportunity, [
            "awardNumber",
            "award_number",
            "contractAwardNumber",
            "contract_award_number",
            "contractNumber",
        ])

    return {
        "awardee_name": str(awardee_name or ""),
        "award_amount": str(award_amount or ""),
        "award_date": str(award_date or ""),
        "award_number": str(award_number or ""),
    }


def classify_notice_actionability(opportunity):
    opportunity_text = get_opportunity_text(opportunity)
    notice_type = get_notice_type_text(opportunity)
    award_details = extract_award_details(opportunity)

    has_award_fields = any([
        award_details["awardee_name"],
        award_details["award_amount"],
        award_details["award_date"],
        award_details["award_number"],
    ])

    if "award notice" in notice_type or "award notice" in opportunity_text or has_award_fields:
        return {
            "notice_actionability": "awarded_market_intel",
            "award_notice_flag": "Yes",
            "market_intel_value": "High" if award_details["award_amount"] or award_details["awardee_name"] else "Medium",
            **award_details,
        }

    inactive_terms = ["inactive", "archived", "cancelled", "canceled"]

    if any(term in notice_type for term in inactive_terms):
        return {
            "notice_actionability": "inactive_or_archived",
            "award_notice_flag": "No",
            "market_intel_value": "Low",
            **award_details,
        }

    return {
        "notice_actionability": "actionable",
        "award_notice_flag": "No",
        "market_intel_value": "",
        **award_details,
    }


def parse_deadline_date(value):
    if not value:
        return None

    raw_value = str(value).strip()

    if "chst" in raw_value.lower():
        cleaned = re.sub(r"\s*chst\s*", "", raw_value, flags=re.IGNORECASE).strip()
        possible_chst_formats = [
            "%b %d, %Y %I:%M %p",
            "%B %d, %Y %I:%M %p",
            "%m/%d/%Y %I:%M %p",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
        ]

        for date_format in possible_chst_formats:
            try:
                parsed = datetime.strptime(cleaned, date_format)
                return parsed.replace(tzinfo=ZoneInfo("Pacific/Guam"))
            except ValueError:
                continue

    possible_formats = [
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M %p",
        "%b %d, %Y %I:%M %p",
        "%B %d, %Y %I:%M %p",
    ]

    cleaned_value = raw_value.replace("Z", "+0000")

    for date_format in possible_formats:
        try:
            if "%z" in date_format:
                return datetime.strptime(cleaned_value, date_format)

            parsed = datetime.strptime(raw_value, date_format)
            return parsed.replace(tzinfo=timezone.utc)

        except ValueError:
            continue

    return None


def format_datetime_for_timezone(dt, timezone_name):
    if not dt:
        return ""

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    converted = dt.astimezone(ZoneInfo(timezone_name))
    return converted.strftime("%Y-%m-%d %I:%M %p %Z")


def calculate_deadline_status(opportunity):
    response_deadline = (
        opportunity.get("response_deadline")
        or opportunity.get("responseDeadLine")
        or opportunity.get("responseDate")
        or ""
    )

    parsed_deadline = parse_deadline_date(response_deadline)

    if not response_deadline:
        return {
            "deadline_status": "unknown",
            "days_until_deadline": "",
            "deadline_score_adjustment": 0,
            "deadline_reason": "No response deadline found",
            "due_date_solicitation_local": "",
            "due_date_user_local": ""
        }

    if not parsed_deadline:
        return {
            "deadline_status": "unknown_format",
            "days_until_deadline": "",
            "deadline_score_adjustment": 0,
            "deadline_reason": f"Response deadline found but could not be parsed: {response_deadline}",
            "due_date_solicitation_local": str(response_deadline),
            "due_date_user_local": ""
        }

    now = datetime.now(timezone.utc)

    if parsed_deadline.tzinfo is None:
        parsed_deadline = parsed_deadline.replace(tzinfo=timezone.utc)

    delta = parsed_deadline - now
    days_until = delta.days

    due_date_user_local = format_datetime_for_timezone(parsed_deadline, USER_LOCAL_TIMEZONE)
    due_date_solicitation_local = parsed_deadline.strftime("%Y-%m-%d %I:%M %p %Z")

    if days_until < 0:
        return {
            "deadline_status": "overdue_or_archived",
            "days_until_deadline": days_until,
            "deadline_score_adjustment": -20,
            "deadline_reason": f"Deadline appears overdue by {abs(days_until)} day(s)",
            "due_date_solicitation_local": due_date_solicitation_local,
            "due_date_user_local": due_date_user_local
        }

    if days_until <= 2:
        return {
            "deadline_status": "too_soon",
            "days_until_deadline": days_until,
            "deadline_score_adjustment": -12,
            "deadline_reason": f"Deadline is very soon: {days_until} day(s) remaining",
            "due_date_solicitation_local": due_date_solicitation_local,
            "due_date_user_local": due_date_user_local
        }

    if days_until <= 7:
        return {
            "deadline_status": "urgent",
            "days_until_deadline": days_until,
            "deadline_score_adjustment": -3,
            "deadline_reason": f"Deadline is urgent: {days_until} day(s) remaining",
            "due_date_solicitation_local": due_date_solicitation_local,
            "due_date_user_local": due_date_user_local
        }

    if days_until <= 21:
        return {
            "deadline_status": "reasonable",
            "days_until_deadline": days_until,
            "deadline_score_adjustment": 6,
            "deadline_reason": f"Reasonable response window: {days_until} day(s) remaining",
            "due_date_solicitation_local": due_date_solicitation_local,
            "due_date_user_local": due_date_user_local
        }

    return {
        "deadline_status": "long_window",
        "days_until_deadline": days_until,
        "deadline_score_adjustment": 4,
        "deadline_reason": f"Longer response window: {days_until} day(s) remaining",
        "due_date_solicitation_local": due_date_solicitation_local,
        "due_date_user_local": due_date_user_local
    }


def detect_evaluation_method(opportunity_text):
    if "lowest price technically acceptable" in opportunity_text or "lpta" in opportunity_text:
        return "LPTA"

    if "non-price factors are significantly more important than price" in opportunity_text:
        return "Best Value Tradeoff"

    if "best value" in opportunity_text and ("trade-off" in opportunity_text or "tradeoff" in opportunity_text):
        return "Best Value Tradeoff"

    if "best value" in opportunity_text:
        return "Best Value"

    if "lowest price" in opportunity_text:
        return "Lowest Price"

    return "Unknown"


def detect_submission_method(opportunity_text):
    if "piee" in opportunity_text or "procurement integrated enterprise environment" in opportunity_text:
        return "PIEE"

    if "sam.gov" in opportunity_text:
        return "SAM.gov"

    if "email" in opportunity_text or "e-mail" in opportunity_text:
        return "Email"

    if "portal" in opportunity_text:
        return "Portal"

    return "Unknown"


def detect_required_forms(opportunity_text):
    forms = []

    form_patterns = {
        "SF1449": ["sf 1449", "sf1449", "standard form 1449"],
        "SF30 Amendment Acknowledgment": ["sf 30", "sf30", "standard form 30", "amendment acknowledgment"],
        "Technical Proposal": ["technical proposal", "technical approach"],
        "Price Proposal": ["price proposal", "pricing", "price shall be provided", "clin pricing"],
        "Quality Control Plan": ["quality control plan", "qcp"],
        "Past Performance": ["past performance"],
        "Prime Case Reports": ["case reports", "prime contractor experience", "comparable size, scope, and complexity"],
        "Representations and Certifications": ["representations and certifications", "reps and certs", "52.212-3"],
    }

    for form_name, patterns in form_patterns.items():
        if any(pattern in opportunity_text for pattern in patterns):
            forms.append(form_name)

    return sorted(set(forms))


def detect_amendment_alert(opportunity_text):
    has_sf30 = "sf 30" in opportunity_text or "sf30" in opportunity_text or "standard form 30" in opportunity_text
    has_amendment = "amendment" in opportunity_text
    has_rejection_warning = "may result in rejection" in opportunity_text or "will not be considered" in opportunity_text
    include_in_response = "include in response" in opportunity_text and "yes" in opportunity_text

    if include_in_response or has_sf30 or (has_amendment and has_rejection_warning):
        return {
            "amendment_compliance_alert": "Yes",
            "amendment_compliance_task": "Acknowledge and include amendment/SF30 with offer. Missing acknowledgment may be a no-submit blocker."
        }

    if has_amendment:
        return {
            "amendment_compliance_alert": "Possible",
            "amendment_compliance_task": "Review solicitation amendments and confirm acknowledgment requirements."
        }

    return {
        "amendment_compliance_alert": "No",
        "amendment_compliance_task": ""
    }


def detect_location_and_staffing(opportunity, opportunity_text):
    location_text = normalize_text(
        opportunity.get("place_of_performance")
        or opportunity.get("placeOfPerformance")
        or opportunity.get("placeOfPerformanceState")
        or opportunity.get("placeOfPerformanceCity")
        or ""
    )

    combined = f"{opportunity_text} {location_text}"

    onsite_phrases = [
        "one qualified on-site",
        "on-site contractor",
        "onsite contractor",
        "on-site",
        "onsite",
        "government facility",
        "standard operating hours",
        "0800 to 1700",
        "8:00 to 5:00",
        "monday through friday",
    ]

    mandatory_staffing_phrases = [
        "contractor shall provide one",
        "shall provide one qualified",
        "one qualified",
        "full-time equivalent",
        "fte",
        "key personnel",
        "personnel shall be located",
        "must be physically present",
    ]

    telework_phrases = [
        "telework",
        "remote work",
        "hybrid",
        "work remotely",
        "off-site",
        "offsite"
    ]

    on_site_flag = any(phrase in combined for phrase in onsite_phrases)
    mandatory_staffing_flag = any(phrase in combined for phrase in mandatory_staffing_phrases)
    telework_flag = any(phrase in combined for phrase in telework_phrases)

    out_of_region_markers = [
        "guam",
        "barrigada",
        "hawaii",
        "alaska",
        "puerto rico",
        "virgin islands",
        "northern mariana",
        "cnmi",
        "american samoa",
        "outside continental united states",
        "oconus"
    ]

    local_region_markers = [
        "mississippi",
        "tennessee",
        "memphis",
        "southaven",
        "desoto",
        "shelby county"
    ]

    out_of_region = any(marker in combined for marker in out_of_region_markers)
    local_region = any(marker in combined for marker in local_region_markers)

    if on_site_flag and out_of_region:
        performance_location_risk = "out_of_area_requires_partner"
        local_staffing_dependency = "Yes"
        remote_feasibility_score = 0 if not telework_flag else 5
        staffing_model = "local_subcontractor_or_employee_required"
        execution_risk = "High"
    elif on_site_flag and not local_region:
        performance_location_risk = "on_site_required"
        local_staffing_dependency = "Unclear"
        remote_feasibility_score = 0 if not telework_flag else 5
        staffing_model = "employee_or_subcontractor_required"
        execution_risk = "Medium"
    elif telework_flag and not on_site_flag:
        performance_location_risk = "remote_possible"
        local_staffing_dependency = "No"
        remote_feasibility_score = 10
        staffing_model = "remote_delivery_possible"
        execution_risk = "Low"
    else:
        performance_location_risk = "unknown"
        local_staffing_dependency = "Unclear"
        remote_feasibility_score = 3
        staffing_model = "unclear"
        execution_risk = "Medium"

    telework_ambiguity_flag = "Yes" if on_site_flag and telework_flag else "No"

    service_or_staffing_work = any(
        phrase in combined
        for phrase in [
            "services",
            "support services",
            "contractor shall provide",
            "staffing",
            "personnel",
            "assistant",
            "janitorial",
            "custodial",
            "pest control",
        ]
    )

    missing_place_for_service = (
        service_or_staffing_work
        and not location_text
        and ("place of performance" in combined)
    )

    rfi_needed = "No"
    rfi_recommendation = ""

    if telework_ambiguity_flag == "Yes":
        rfi_needed = "Yes"
        rfi_recommendation = "Clarify whether daily on-site performance is mandatory or whether hybrid/telework is acceptable when mission requirements permit."
    elif out_of_region and (on_site_flag or mandatory_staffing_flag):
        rfi_needed = "Yes"
        rfi_recommendation = "Clarify whether local staffing is mandatory and whether a local subcontractor or employee may perform the requirement."
    elif missing_place_for_service:
        rfi_needed = "Yes"
        rfi_recommendation = "Clarify the place of performance because the requirement appears service-based but location details are incomplete."

    return {
        "on_site_staffing_flag": "Yes" if on_site_flag else "No",
        "mandatory_staffing_flag": "Yes" if mandatory_staffing_flag else "No",
        "telework_ambiguity_flag": telework_ambiguity_flag,
        "remote_feasibility_score": remote_feasibility_score,
        "local_staffing_dependency": local_staffing_dependency,
        "performance_location_risk": performance_location_risk,
        "staffing_model": staffing_model,
        "execution_risk": execution_risk,
        "rfi_needed": rfi_needed,
        "rfi_recommendation": rfi_recommendation,
    }


def detect_prime_case_report_requirement(opportunity_text):
    patterns = [
        "two case reports",
        "case reports",
        "prime contractor experience",
        "comparable size, scope, and complexity",
        "same or similar size, scope, and complexity",
    ]

    if any(pattern in opportunity_text for pattern in patterns):
        return {
            "prime_case_report_required": "Yes",
            "prime_case_report_note": "Prime-level comparable case reports/past performance appear required. Reduce prime probability if matching past performance is weak."
        }

    return {
        "prime_case_report_required": "No",
        "prime_case_report_note": ""
    }


def detect_team_lock_alert(opportunity_text):
    patterns = [
        "may not change team members after step 1",
        "cannot change team members after step 1",
        "team members after step 1",
        "step 1",
    ]

    if any(pattern in opportunity_text for pattern in patterns) and "team" in opportunity_text:
        return {
            "team_lock_alert": "Yes",
            "team_lock_note": "Teaming may be locked after Step 1. Solve team composition before submission."
        }

    return {
        "team_lock_alert": "No",
        "team_lock_note": ""
    }


def detect_idiq_ceiling(opportunity_text):
    ceiling_patterns = [
        r"\$[\d,]+(?:\.\d+)?\s*(?:m|million|billion)?\s*(?:ceiling|maximum|idiq)",
        r"(?:ceiling|maximum|idiq)\s*(?:value|amount)?\s*(?:of)?\s*\$[\d,]+(?:\.\d+)?\s*(?:m|million|billion)?",
    ]

    guaranteed_patterns = [
        r"guaranteed minimum\s*(?:of)?\s*\$[\d,]+(?:\.\d+)?\s*(?:m|million|billion)?",
        r"minimum guarantee\s*(?:of)?\s*\$[\d,]+(?:\.\d+)?\s*(?:m|million|billion)?",
    ]

    ceiling_matches = []
    guaranteed_matches = []

    for pattern in ceiling_patterns:
        ceiling_matches.extend(re.findall(pattern, opportunity_text, flags=re.IGNORECASE))

    for pattern in guaranteed_patterns:
        guaranteed_matches.extend(re.findall(pattern, opportunity_text, flags=re.IGNORECASE))

    return {
        "idiq_ceiling_detected": "Yes" if ceiling_matches else "No",
        "idiq_ceiling_text": "; ".join(sorted(set(ceiling_matches)))[:500],
        "guaranteed_minimum_text": "; ".join(sorted(set(guaranteed_matches)))[:500],
        "idiq_note": "Store IDIQ ceiling separately from guaranteed revenue. Do not treat ceiling as likely revenue." if ceiling_matches else ""
    }


def detect_scientific_domain_complexity(opportunity_text):
    scientific_terms = [
        "seismology",
        "geodesy",
        "geophysics",
        "telemetry",
        "shakealert",
        "earthquake monitoring",
        "scientific algorithm",
        "scientific algorithms",
        "geospatial",
        "sensor network",
    ]

    matched = [term for term in scientific_terms if term in opportunity_text]

    if matched:
        return {
            "scientific_domain_complexity_flag": "Yes",
            "scientific_domain_terms": ", ".join(sorted(set(matched))),
            "scientific_domain_note": "Specialized scientific/technical domain detected. Increase domain-specialty risk unless company has direct experience."
        }

    return {
        "scientific_domain_complexity_flag": "No",
        "scientific_domain_terms": "",
        "scientific_domain_note": ""
    }


def detect_step1_deadline(opportunity_text):
    patterns = [
        r"step 1[^.]{0,120}(?:due|deadline|submission)[^.]{0,120}",
        r"(?:due|deadline|submission)[^.]{0,120}step 1[^.]{0,120}",
    ]

    matches = []
    for pattern in patterns:
        matches.extend(re.findall(pattern, opportunity_text, flags=re.IGNORECASE))

    return {
        "step1_mandatory_flag": "Yes" if matches else "No",
        "step1_deadline_note": "Step 1 deadline appears mandatory. Missing Step 1 may prevent later competition." if matches else "",
        "step1_deadline_text": "; ".join(matches)[:500],
    }


def detect_subcontractor_role_classifier(primary_text):
    possible_roles = []

    role_patterns = {
        "proposal support": [
            "proposal support",
            "technical volume",
            "compliance matrix",
            "proposal writing",
            "proposal development",
        ],
        "documentation": [
            "technical documentation",
            "technical writing",
            "manuals",
            "sop",
            "standard operating procedures",
            "documentation support",
        ],
        "AI workflow": [
            "artificial intelligence",
            "ai workflow",
            "ai marketing",
            "automation workflow",
            "machine learning",
            "ai-powered",
        ],
        "508 support": [
            "section 508",
            "508 compliance",
            "accessibility compliance",
        ],
        "full-stack support": [
            "full stack",
            "full-stack",
            "software development",
            "web application",
            "application development",
            "api integration",
            "system modernization",
            "cloud application",
        ],
        "training materials": [
            "training materials",
            "curriculum development",
            "courseware",
            "instructional design",
        ],
        "creative/content support": [
            "graphic design",
            "social media content",
            "visual information",
            "content creation",
            "publicity",
        ],
    }

    for role, patterns in role_patterns.items():
        if any(pattern in primary_text for pattern in patterns):
            possible_roles.append(role)

    return {
        "subcontractor_role_classifier": ", ".join(possible_roles)
    }


def detect_subcontracting_check(opportunity_text, set_aside_text, staffing_info):
    service_indicators = [
        "services",
        "service",
        "support",
        "assistant",
        "staffing",
        "contractor shall provide",
    ]

    is_set_aside = "small business" in set_aside_text or "set-aside" in opportunity_text
    is_service = any(indicator in opportunity_text for indicator in service_indicators)
    likely_subcontracting = staffing_info.get("local_staffing_dependency") in ["Yes"]

    if is_set_aside and is_service and likely_subcontracting:
        return {
            "small_business_subcontracting_check": "Yes",
            "subcontracting_note": "Review FAR 52.219-14 / limitations on subcontracting and confirm whether any proposed subcontractor must be similarly situated."
        }

    return {
        "small_business_subcontracting_check": "No",
        "subcontracting_note": ""
    }


def company_is_verified_sdvob(company_profile):
    ownership = " ".join(company_profile.get("ownership", []))
    ownership = normalize_text(ownership)

    sdvosb_terms = [
        "sdvosb",
        "service-disabled veteran-owned",
        "service disabled veteran owned",
        "service-disabled veteran owned",
    ]

    return any(term in ownership for term in sdvosb_terms)


def detect_set_aside_hard_gate(set_aside_text, opportunity_text, company_profile):
    sdvosb_required = (
        "sdvosb" in set_aside_text
        or "service-disabled veteran-owned" in set_aside_text
        or "service disabled veteran owned" in set_aside_text
        or "service-disabled veteran owned" in set_aside_text
        or "sdvosb" in opportunity_text
        or "service-disabled veteran-owned" in opportunity_text
        or "service disabled veteran owned" in opportunity_text
        or "service-disabled veteran owned" in opportunity_text
    )

    if sdvosb_required and not company_is_verified_sdvob(company_profile):
        return {
            "set_aside_hard_gate": "Yes",
            "set_aside_hard_gate_reason": "SDVOSB set-aside detected, but company profile does not show verified SDVOSB status.",
            "force_teaming_target": "Yes"
        }

    return {
        "set_aside_hard_gate": "No",
        "set_aside_hard_gate_reason": "",
        "force_teaming_target": "No"
    }


def detect_best_lane(opportunity, company_profile, search_profiles):
    primary_text = get_primary_opportunity_text(opportunity)
    context_text = get_context_opportunity_text(opportunity)
    opportunity_naics = get_opportunity_naics(opportunity)

    lane_scores = {}

    for lane_name, lane_data in search_profiles.items():
        lane_score = 0
        lane_reasons = []

        lane_naics_codes = [str(code).strip() for code in lane_data.get("naics", [])]

        if opportunity_naics and opportunity_naics in lane_naics_codes:
            lane_score += 25
            lane_reasons.append(f"Lane NAICS match: {opportunity_naics}")

        primary_matches = []
        context_matches = []

        for keyword in lane_data.get("keywords", []):
            if keyword_matches_text(keyword, primary_text):
                primary_matches.append(keyword)
            elif keyword_matches_text(keyword, context_text):
                context_matches.append(keyword)

        if primary_matches:
            lane_score += min(len(primary_matches) * 5, 25)
            lane_reasons.append(
                "Lane keywords: " + ", ".join(sorted(set(primary_matches))[:8])
            )

        if context_matches:
            lane_score += min(len(context_matches) * 1, 5)
            lane_reasons.append(
                "Context keywords: " + ", ".join(sorted(set(context_matches))[:5])
            )

        lane_scores[lane_name] = {
            "score": lane_score,
            "reasons": lane_reasons,
            "matched_keywords": primary_matches + context_matches,
        }

    if not lane_scores:
        return "unknown", [], 0

    best_lane = max(lane_scores.items(), key=lambda item: item[1]["score"])
    best_lane_name = best_lane[0]
    best_lane_data = best_lane[1]

    if best_lane_data["score"] <= 0:
        return "unknown", [], 0

    return best_lane_name, best_lane_data["reasons"], best_lane_data["score"]


def calculate_prime_reality_score(score, opportunity, staffing_info, evaluation_method, deadline_status):
    prime_score = score

    if opportunity.get("set_aside_hard_gate") == "Yes":
        return 0

    if opportunity.get("notice_actionability") != "actionable":
        return 0

    if staffing_info.get("performance_location_risk") == "out_of_area_requires_partner":
        prime_score -= 35
    elif staffing_info.get("performance_location_risk") == "on_site_required":
        prime_score -= 20

    if staffing_info.get("telework_ambiguity_flag") == "Yes":
        prime_score -= 10

    if staffing_info.get("local_staffing_dependency") == "Yes":
        prime_score -= 15

    if evaluation_method == "LPTA":
        prime_score -= 5

    if evaluation_method == "Best Value Tradeoff":
        prime_score -= 10

    if opportunity.get("prime_case_report_required") == "Yes":
        prime_score -= 20

    if opportunity.get("scientific_domain_complexity_flag") == "Yes":
        prime_score -= 25

    if opportunity.get("team_lock_alert") == "Yes":
        prime_score -= 10

    if deadline_status == "too_soon":
        prime_score -= 15

    if deadline_status == "overdue_or_archived":
        prime_score = 0

    return max(0, min(prime_score, 100))


def calculate_compliance_risk(opportunity):
    risk_points = 0

    if opportunity.get("amendment_compliance_alert") == "Yes":
        risk_points += 20

    if opportunity.get("forms_required_text"):
        risk_points += 10

    if opportunity.get("prime_case_report_required") == "Yes":
        risk_points += 20

    if opportunity.get("team_lock_alert") == "Yes":
        risk_points += 20

    if opportunity.get("set_aside_hard_gate") == "Yes":
        risk_points += 40

    if opportunity.get("submission_method") in ["PIEE", "Portal"]:
        risk_points += 10

    if risk_points >= 50:
        return "High"

    if risk_points >= 25:
        return "Medium"

    return "Low"


def build_conditional_recommendation(opportunity, score, prime_reality_score, staffing_info):
    if opportunity.get("notice_actionability") == "awarded_market_intel":
        return "Market Intelligence Only — Already Awarded"

    if opportunity.get("notice_actionability") == "inactive_or_archived":
        return "Pass — Inactive or Archived"

    if opportunity.get("deadline_status") == "overdue_or_archived":
        return "Pass — Deadline Missed"

    if opportunity.get("set_aside_hard_gate") == "Yes":
        return "Teaming/Subcontractor Target — Prime blocked by set-aside"

    if staffing_info.get("performance_location_risk") == "out_of_area_requires_partner":
        return "Conditional Pursue — Local subcontractor/employee required"

    if staffing_info.get("telework_ambiguity_flag") == "Yes":
        return "Conditional Pursue — RFI needed to clarify on-site vs telework"

    if prime_reality_score >= 70:
        return "Prime Candidate"

    if prime_reality_score >= 50:
        return "Review as Prime or Teaming Candidate"

    if score >= 60 and prime_reality_score < 50:
        return "Good Capability Fit / Weak Prime Reality"

    return "Watch or Pass"


def score_opportunity(opportunity, company_profile, search_profiles):
    score = 0
    reasons = []

    primary_text = get_primary_opportunity_text(opportunity)
    context_text = get_context_opportunity_text(opportunity)
    opportunity_text = f"{primary_text} {context_text}".strip()

    notice_info = classify_notice_actionability(opportunity)
    opportunity.update(notice_info)

    company_naics = set(str(code).strip() for code in company_profile.get("naics_codes", []))
    primary_naics = str(company_profile.get("primary_naics", "")).strip()
    opportunity_naics = get_opportunity_naics(opportunity)

    matched_lane, lane_reasons, lane_score = detect_best_lane(
        opportunity=opportunity,
        company_profile=company_profile,
        search_profiles=search_profiles,
    )

    opportunity["matched_lane"] = matched_lane

    if matched_lane != "unknown":
        score += min(lane_score, 20)
        reasons.append(f"Best matched lane: {matched_lane}")
        reasons.extend(lane_reasons)

    if opportunity_naics:
        if opportunity_naics == primary_naics:
            score += 20
            reasons.append(f"Strong primary NAICS match: {opportunity_naics}")
        elif opportunity_naics in company_naics:
            score += 15
            reasons.append(f"Company NAICS match: {opportunity_naics}")

    matched_keywords = set()
    context_only_keywords = set()

    for profile_name, profile_data in search_profiles.items():
        for keyword in profile_data.get("keywords", []):
            if keyword_matches_text(keyword, primary_text):
                matched_keywords.add(keyword)
            elif keyword_matches_text(keyword, context_text):
                context_only_keywords.add(keyword)

    if matched_keywords:
        keyword_points = min(len(matched_keywords) * 2, 12)
        score += keyword_points
        reasons.append("Matched keywords: " + ", ".join(sorted(matched_keywords)[:10]))

    if context_only_keywords:
        context_points = min(len(context_only_keywords), 3)
        score += context_points
        reasons.append("Context-only keywords: " + ", ".join(sorted(context_only_keywords)[:8]))

    matched_strengths = set()

    for strength in company_profile.get("core_strengths", []):
        if keyword_matches_text(strength, primary_text):
            matched_strengths.add(strength)

    if matched_strengths:
        strength_points = min(len(matched_strengths) * 2, 10)
        score += strength_points
        reasons.append("Matched core strengths: " + ", ".join(sorted(matched_strengths)[:10]))

    set_aside_text = normalize_text(
        opportunity.get("typeOfSetAsideDescription")
        or opportunity.get("type_of_set_aside_description")
        or opportunity.get("set_aside")
        or opportunity.get("setAside")
        or ""
    )

    hard_gate_info = detect_set_aside_hard_gate(set_aside_text, opportunity_text, company_profile)
    opportunity.update(hard_gate_info)

    if "small business" in set_aside_text:
        score += 8
        reasons.append("Small business set-aside or small business language detected")

    if "veteran" in set_aside_text:
        score += 6
        reasons.append("Veteran-owned business language detected")

    if "minority" in set_aside_text or "disadvantaged" in set_aside_text:
        score += 4
        reasons.append("Minority/disadvantaged business language detected")

    notice_type = get_notice_type_text(opportunity)

    if "sources sought" in notice_type:
        score += 8
        reasons.append("Sources sought notice — good chance to influence or introduce capability")
    elif "combined synopsis" in notice_type or "solicitation" in notice_type:
        score += 6
        reasons.append("Active solicitation-type notice")
    elif "special notice" in notice_type:
        score += 3
        reasons.append("Special notice — may be useful for market intelligence")

    deadline_info = calculate_deadline_status(opportunity)

    opportunity["deadline_status"] = deadline_info["deadline_status"]
    opportunity["days_until_deadline"] = deadline_info["days_until_deadline"]
    opportunity["due_date_solicitation_local"] = deadline_info["due_date_solicitation_local"]
    opportunity["due_date_user_local"] = deadline_info["due_date_user_local"]

    score += deadline_info["deadline_score_adjustment"]
    reasons.append(deadline_info["deadline_reason"])

    evaluation_method = detect_evaluation_method(opportunity_text)
    submission_method = detect_submission_method(opportunity_text)
    required_forms = detect_required_forms(opportunity_text)
    amendment_info = detect_amendment_alert(opportunity_text)
    staffing_info = detect_location_and_staffing(opportunity, opportunity_text)
    prime_case_info = detect_prime_case_report_requirement(opportunity_text)
    team_lock_info = detect_team_lock_alert(opportunity_text)
    idiq_info = detect_idiq_ceiling(opportunity_text)
    scientific_info = detect_scientific_domain_complexity(opportunity_text)
    step1_info = detect_step1_deadline(opportunity_text)
    subcontractor_role_info = detect_subcontractor_role_classifier(primary_text)
    subcontracting_info = detect_subcontracting_check(opportunity_text, set_aside_text, staffing_info)

    opportunity["evaluation_method"] = evaluation_method
    opportunity["submission_method"] = submission_method
    opportunity["forms_required"] = required_forms
    opportunity["forms_required_text"] = ", ".join(required_forms)

    opportunity.update(amendment_info)
    opportunity.update(staffing_info)
    opportunity.update(prime_case_info)
    opportunity.update(team_lock_info)
    opportunity.update(idiq_info)
    opportunity.update(scientific_info)
    opportunity.update(step1_info)
    opportunity.update(subcontractor_role_info)
    opportunity.update(subcontracting_info)

    if opportunity["notice_actionability"] == "awarded_market_intel":
        reasons.append("Award Notice detected — exclude from pursuit report and use for awards intelligence")

    if evaluation_method == "LPTA":
        reasons.append("LPTA detected — compliance, staffing, complete forms, CLIN pricing, and lowest realistic price matter more than brand differentiation")

    if evaluation_method == "Best Value Tradeoff":
        reasons.append("Best-value tradeoff detected — technical approach, experience, past performance, and risk reduction matter more than lowest price")

    if opportunity["set_aside_hard_gate"] == "Yes":
        reasons.append(opportunity["set_aside_hard_gate_reason"])

    if staffing_info["on_site_staffing_flag"] == "Yes":
        reasons.append("On-site staffing language detected")

    if staffing_info["telework_ambiguity_flag"] == "Yes":
        reasons.append("Telework ambiguity detected — RFI recommended")

    if staffing_info["performance_location_risk"] == "out_of_area_requires_partner":
        reasons.append("Place of performance appears outside current region — local staffing or subcontractor likely required")

    if amendment_info["amendment_compliance_alert"] in ["Yes", "Possible"]:
        reasons.append(amendment_info["amendment_compliance_task"])

    if subcontracting_info["small_business_subcontracting_check"] == "Yes":
        reasons.append(subcontracting_info["subcontracting_note"])

    if prime_case_info["prime_case_report_required"] == "Yes":
        reasons.append(prime_case_info["prime_case_report_note"])

    if team_lock_info["team_lock_alert"] == "Yes":
        reasons.append(team_lock_info["team_lock_note"])

    if idiq_info["idiq_note"]:
        reasons.append(idiq_info["idiq_note"])

    if scientific_info["scientific_domain_complexity_flag"] == "Yes":
        reasons.append(scientific_info["scientific_domain_note"])

    if step1_info["step1_mandatory_flag"] == "Yes":
        reasons.append(step1_info["step1_deadline_note"])

    score = max(0, min(score, 100))

    if opportunity["notice_actionability"] != "actionable":
        recommendation = "Market Intelligence Only"
    elif score >= 90:
        recommendation = "Priority Review"
    elif score >= 75:
        recommendation = "Pursue Candidate"
    elif score >= 60:
        recommendation = "Review"
    elif score >= 40:
        recommendation = "Watch / Possible Subcontractor"
    else:
        recommendation = "Pass"

    if opportunity["deadline_status"] == "overdue_or_archived":
        recommendation = "Pass / Deadline Missed"
    elif opportunity["deadline_status"] == "too_soon" and score < 75:
        recommendation = "Likely Too Late / Review Only"

    prime_reality_score = calculate_prime_reality_score(
        score=score,
        opportunity=opportunity,
        staffing_info=staffing_info,
        evaluation_method=evaluation_method,
        deadline_status=opportunity["deadline_status"],
    )

    opportunity["fit_score"] = score
    opportunity["prime_reality_score"] = prime_reality_score
    opportunity["compliance_risk"] = calculate_compliance_risk(opportunity)
    opportunity["recommendation"] = recommendation
    opportunity["conditional_recommendation"] = build_conditional_recommendation(
        opportunity=opportunity,
        score=score,
        prime_reality_score=prime_reality_score,
        staffing_info=staffing_info,
    )
    opportunity["score_reasons"] = reasons

    return opportunity


def score_opportunities(opportunities, company_profile, search_profiles):
    scored = []

    for opportunity in opportunities:
        scored_opp = score_opportunity(
            opportunity=opportunity,
            company_profile=company_profile,
            search_profiles=search_profiles,
        )
        scored.append(scored_opp)

    scored.sort(
        key=lambda item: (
            item.get("notice_actionability") == "actionable",
            item.get("fit_score", 0),
            item.get("prime_reality_score", 0),
        ),
        reverse=True,
    )

    return scored