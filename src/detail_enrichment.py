import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests

from sam_client import (
    get_sam_api_key,
    request_with_retries,
    extract_opportunity_items,
    extract_description_from_possible_json_text,
    compact_text,
    is_notice_desc_url,
)


SAM_SEARCH_URL = "https://api.sam.gov/prod/opportunities/v2/search"


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def score_value(opp, field_name):
    try:
        return int(float(opp.get(field_name, 0) or 0))
    except (TypeError, ValueError):
        return 0


def split_pipe_links(value):
    text = safe_text(value)

    if not text:
        return []

    return [item.strip() for item in text.split(" | ") if item.strip()]


def make_safe_filename(value):
    text = safe_text(value) or "unknown"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text.strip("_")[:120] or "unknown"


def extract_notice_id_from_sam_detail_link(link):
    parsed = urlparse(safe_text(link))
    query = parse_qs(parsed.query)

    if "noticeid" in query and query["noticeid"]:
        return query["noticeid"][0]

    if "noticeId" in query and query["noticeId"]:
        return query["noticeId"][0]

    return ""


def get_sam_detail_links(opp):
    links = []

    for link in split_pipe_links(opp.get("sam_detail_api_links")):
        if link:
            links.append(link)

    for link in split_pipe_links(opp.get("resource_links")):
        lower_link = link.lower()
        if "api.sam.gov" in lower_link and "/opportunities/v2/search" in lower_link:
            links.append(link)

    seen = set()
    unique_links = []

    for link in links:
        if link not in seen:
            unique_links.append(link)
            seen.add(link)

    return unique_links


def opportunity_needs_detail_enrichment(opp):
    if opp.get("notice_actionability") != "actionable":
        return False

    if opp.get("attachment_download_ready") == "Yes":
        return False

    if opp.get("sam_detail_enriched") == "Yes":
        return False

    if opp.get("attachment_discovery_method") == "sam_detail_enrichment_required":
        return True

    return bool(get_sam_detail_links(opp))


def select_opportunities_for_detail_enrichment(scored_opportunities, limit=5):
    candidates = [
        opp for opp in scored_opportunities
        if opportunity_needs_detail_enrichment(opp)
    ]

    candidates = sorted(
        candidates,
        key=lambda opp: (
            score_value(opp, "prime_reality_score"),
            score_value(opp, "fit_score"),
        ),
        reverse=True,
    )

    return candidates[:limit]


def stringify_value(value):
    if value in [None, ""]:
        return ""

    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)

    return str(value)


def safe_get(data, *keys, default=""):
    if not isinstance(data, dict):
        return default

    for key in keys:
        value = data.get(key)
        if value not in [None, ""]:
            return value

    return default


def extract_resource_links_from_detail(raw_detail):
    possible_values = [
        raw_detail.get("resourceLinks"),
        raw_detail.get("resource_links"),
        raw_detail.get("links"),
        raw_detail.get("attachments"),
        raw_detail.get("attachmentLinks"),
        raw_detail.get("documents"),
        raw_detail.get("files"),
        raw_detail.get("resources"),
    ]

    cleaned = []

    for value in possible_values:
        if not value:
            continue

        if isinstance(value, str):
            cleaned.append(value)

        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    cleaned.append(item)
                elif isinstance(item, dict):
                    url = safe_get(
                        item,
                        "url",
                        "href",
                        "link",
                        "downloadUrl",
                        "downloadURL",
                        "fileUrl",
                        "fileURL",
                        "attachmentUrl",
                        "attachmentURL",
                    )

                    name = safe_get(
                        item,
                        "name",
                        "filename",
                        "fileName",
                        "title",
                        "description",
                    )

                    if url:
                        cleaned.append(str(url))
                    elif name:
                        cleaned.append(str(name))
                    else:
                        cleaned.append(stringify_value(item))

        elif isinstance(value, dict):
            url = safe_get(
                value,
                "url",
                "href",
                "link",
                "downloadUrl",
                "downloadURL",
                "fileUrl",
                "fileURL",
                "attachmentUrl",
                "attachmentURL",
            )

            if url:
                cleaned.append(str(url))
            else:
                cleaned.append(stringify_value(value))

    seen = set()
    unique = []

    for link in cleaned:
        link = safe_text(link)
        if link and link not in seen:
            unique.append(link)
            seen.add(link)

    return unique


def extract_description_from_detail(raw_detail):
    description = (
        raw_detail.get("description")
        or raw_detail.get("noticeDesc")
        or raw_detail.get("desc")
        or raw_detail.get("body")
        or raw_detail.get("content")
        or ""
    )

    if not description:
        return ""

    cleaned = extract_description_from_possible_json_text(description)

    if is_notice_desc_url(cleaned):
        return ""

    return compact_text(cleaned, max_chars=12000)


def save_debug_detail_json(debug_dir, notice_label, detail_link, response_json, raw_detail):
    debug_path = Path(debug_dir)
    debug_path.mkdir(parents=True, exist_ok=True)

    safe_notice = make_safe_filename(notice_label)
    output_path = debug_path / f"sam_detail_{safe_notice}.json"

    payload = {
        "notice_label": notice_label,
        "detail_link": detail_link,
        "raw_response_top_level_keys": sorted(list(response_json.keys())) if isinstance(response_json, dict) else [],
        "raw_detail_keys": sorted(list(raw_detail.keys())) if isinstance(raw_detail, dict) else [],
        "raw_response": response_json,
        "raw_detail": raw_detail,
    }

    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return str(output_path)


def fetch_sam_detail(session, api_key, detail_link):
    notice_id = extract_notice_id_from_sam_detail_link(detail_link)

    if not notice_id:
        return None, None, "No noticeid found in SAM detail link."

    params = {
        "noticeid": notice_id,
        "limit": 1,
        "api_key": api_key,
    }

    response = request_with_retries(
        session=session,
        url=SAM_SEARCH_URL,
        params=params,
        purpose=f"selected SAM detail enrichment for {notice_id}",
    )

    response_json = response.json()
    items = extract_opportunity_items(response_json)

    if not items:
        return response_json, None, f"No detail record returned for notice {notice_id}."

    return response_json, items[0], ""


def merge_detail_into_opportunity(opp, raw_detail, detail_link):
    original_links = split_pipe_links(opp.get("resource_links"))
    detail_links = extract_resource_links_from_detail(raw_detail)

    merged_links = []
    seen = set()

    for link in original_links + detail_links:
        link = safe_text(link)
        if link and link not in seen:
            merged_links.append(link)
            seen.add(link)

    if merged_links:
        opp["resource_links"] = " | ".join(merged_links)
        opp["resource_link_count"] = len(merged_links)
        opp["has_resource_links"] = "Yes"

    description = extract_description_from_detail(raw_detail)

    if description:
        opp["full_description"] = description
        opp["description"] = description
        opp["short_description"] = compact_text(description, max_chars=1000)
        opp["description_enriched"] = "Yes"

    opp["sam_detail_enriched"] = "Yes"
    opp["sam_detail_enrichment_source"] = detail_link
    opp["sam_detail_raw_resource_links"] = stringify_value(raw_detail.get("resourceLinks"))
    opp["sam_detail_raw_links"] = stringify_value(raw_detail.get("links"))
    opp["sam_detail_raw_attachments"] = stringify_value(raw_detail.get("attachments"))
    opp["sam_detail_raw_documents"] = stringify_value(raw_detail.get("documents"))
    opp["sam_detail_raw_files"] = stringify_value(raw_detail.get("files"))
    opp["sam_detail_raw_record"] = stringify_value(raw_detail)

    if not raw_detail.get("resourceLinks") and not raw_detail.get("attachments"):
        opp["sam_detail_enrichment_note"] = (
            "SAM detail enrichment completed, but the API response did not expose resourceLinks or attachments."
        )

    return opp


def enrich_selected_sam_details(scored_opportunities, limit=5, debug_detail_json=False, debug_dir="debug"):
    selected = select_opportunities_for_detail_enrichment(
        scored_opportunities=scored_opportunities,
        limit=limit,
    )

    if not selected:
        print("No opportunities selected for SAM detail enrichment.")
        return scored_opportunities

    api_key = get_sam_api_key()

    print(f"Selected SAM detail enrichment target count: {len(selected)}")

    with requests.Session() as session:
        for index, opp in enumerate(selected, start=1):
            notice_label = safe_text(opp.get("notice_id") or opp.get("sam_notice_id"))
            detail_links = get_sam_detail_links(opp)

            if not detail_links:
                opp["sam_detail_enriched"] = "No"
                opp["sam_detail_enrichment_note"] = "No SAM detail API link found."
                continue

            detail_link = detail_links[0]

            print(f"Enriching detail {index}/{len(selected)}: {notice_label}")

            try:
                response_json, raw_detail, error = fetch_sam_detail(
                    session=session,
                    api_key=api_key,
                    detail_link=detail_link,
                )

                if error:
                    opp["sam_detail_enriched"] = "No"
                    opp["sam_detail_enrichment_note"] = error
                    print(f"Detail enrichment warning for {notice_label}: {error}")

                    if debug_detail_json and response_json is not None:
                        debug_path = save_debug_detail_json(
                            debug_dir=debug_dir,
                            notice_label=notice_label,
                            detail_link=detail_link,
                            response_json=response_json,
                            raw_detail={},
                        )
                        opp["sam_detail_debug_json_path"] = debug_path
                        print(f"Saved SAM detail debug JSON: {debug_path}")

                    continue

                if debug_detail_json:
                    debug_path = save_debug_detail_json(
                        debug_dir=debug_dir,
                        notice_label=notice_label,
                        detail_link=detail_link,
                        response_json=response_json,
                        raw_detail=raw_detail,
                    )
                    opp["sam_detail_debug_json_path"] = debug_path
                    print(f"Saved SAM detail debug JSON: {debug_path}")

                merge_detail_into_opportunity(
                    opp=opp,
                    raw_detail=raw_detail,
                    detail_link=detail_link,
                )

            except Exception as error:
                opp["sam_detail_enriched"] = "No"
                opp["sam_detail_enrichment_note"] = str(error)
                print(f"Detail enrichment failed for {notice_label}: {error}")

    return scored_opportunities