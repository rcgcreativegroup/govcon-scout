import hashlib
import html
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import requests


try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


SAM_SEARCH_URL = "https://api.sam.gov/prod/opportunities/v2/search"
SAM_NOTICE_DESC_URL = "https://api.sam.gov/prod/opportunities/v1/noticedesc"

REQUEST_TIMEOUT = 30

# Be polite to SAM.gov.
REQUEST_DELAY_SECONDS = float(os.getenv("SAM_REQUEST_DELAY_SECONDS", "1.0"))
MAX_RETRIES = int(os.getenv("SAM_MAX_RETRIES", "2"))
BACKOFF_SECONDS = [10, 30]

# Circuit breaker: stop live API calls after repeated 429s.
MAX_CONSECUTIVE_RATE_LIMITS = int(os.getenv("SAM_MAX_CONSECUTIVE_RATE_LIMITS", "2"))
RATE_LIMIT_COOLDOWN_SECONDS = int(os.getenv("SAM_RATE_LIMIT_COOLDOWN_SECONDS", "3600"))

# Enrichment creates many extra API calls. Keep it controlled.
MAX_DESCRIPTION_CHARS = 12000
ENRICH_DESCRIPTION_LIMIT = int(os.getenv("SAM_ENRICH_DESCRIPTION_LIMIT", "75"))

# Local cache prevents repeat runs from hammering SAM.gov.
BASE_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE_DIR / "cache"
SEARCH_CACHE_DIR = CACHE_DIR / "sam_search"
DESC_CACHE_DIR = CACHE_DIR / "sam_descriptions"
RATE_LIMIT_STATE_FILE = CACHE_DIR / "sam_rate_limit_state.json"

SEARCH_CACHE_TTL_HOURS = int(os.getenv("SAM_SEARCH_CACHE_TTL_HOURS", "12"))
DESC_CACHE_TTL_HOURS = int(os.getenv("SAM_DESC_CACHE_TTL_HOURS", "72"))


class SamRateLimitCooldown(Exception):
    pass


def get_sam_api_key():
    api_key = os.getenv("SAM_API_KEY") or os.getenv("SAM_GOV_API_KEY")

    if not api_key:
        raise RuntimeError(
            "Missing SAM.gov API key. Add SAM_API_KEY=your_key_here to your .env file."
        )

    return api_key


def ensure_cache_dirs():
    SEARCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DESC_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def format_sam_date(date_value):
    return date_value.strftime("%m/%d/%Y")


def make_cache_key(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cache_is_fresh(path, ttl_hours):
    if not path.exists():
        return False

    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds <= ttl_hours * 3600


def read_json_cache(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return None


def write_json_cache(path, data):
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False)
    except OSError:
        pass


def read_text_cache(path):
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def write_text_cache(path, text):
    try:
        path.write_text(text or "", encoding="utf-8")
    except OSError:
        pass


def read_rate_limit_state():
    data = read_json_cache(RATE_LIMIT_STATE_FILE)

    if not isinstance(data, dict):
        return {
            "cooldown_until": 0,
            "consecutive_429s": 0,
        }

    return {
        "cooldown_until": float(data.get("cooldown_until", 0) or 0),
        "consecutive_429s": int(data.get("consecutive_429s", 0) or 0),
    }


def write_rate_limit_state(state):
    write_json_cache(RATE_LIMIT_STATE_FILE, state)


def reset_rate_limit_state():
    write_rate_limit_state({
        "cooldown_until": 0,
        "consecutive_429s": 0,
    })


def mark_rate_limited():
    state = read_rate_limit_state()
    consecutive = state.get("consecutive_429s", 0) + 1

    cooldown_until = state.get("cooldown_until", 0)

    if consecutive >= MAX_CONSECUTIVE_RATE_LIMITS:
        cooldown_until = time.time() + RATE_LIMIT_COOLDOWN_SECONDS

    write_rate_limit_state({
        "cooldown_until": cooldown_until,
        "consecutive_429s": consecutive,
    })

    return consecutive, cooldown_until


def api_is_in_cooldown():
    state = read_rate_limit_state()
    cooldown_until = state.get("cooldown_until", 0)

    if cooldown_until and time.time() < cooldown_until:
        remaining = int(cooldown_until - time.time())
        return True, remaining

    return False, 0


def clean_html_text(value):
    if not value:
        return ""

    text = str(value)

    text = html.unescape(text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)

    return text.strip()


def extract_description_from_possible_json_text(value):
    if not value:
        return ""

    raw_text = str(value).strip()

    if not raw_text:
        return ""

    if raw_text.startswith("{") and raw_text.endswith("}"):
        try:
            data = json.loads(raw_text)
            if isinstance(data, dict):
                description = (
                    data.get("description")
                    or data.get("noticeDesc")
                    or data.get("body")
                    or data.get("content")
                    or data.get("text")
                    or ""
                )

                if description:
                    return clean_html_text(description)

                return clean_html_text(json.dumps(data, ensure_ascii=False))

        except json.JSONDecodeError:
            pass

    return clean_html_text(raw_text)


def is_notice_desc_url(value):
    if not value:
        return False

    text = str(value).strip().lower()

    return (
        text.startswith("http")
        and "api.sam.gov" in text
        and "noticedesc" in text
    )


def compact_text(value, max_chars=MAX_DESCRIPTION_CHARS):
    if not value:
        return ""

    text = str(value).strip()

    if len(text) <= max_chars:
        return text

    return text[:max_chars].rstrip() + "\n\n...[truncated]"


def safe_get(data, *keys, default=""):
    if not isinstance(data, dict):
        return default

    for key in keys:
        value = data.get(key)
        if value not in [None, ""]:
            return value

    return default


def stringify_value(value):
    if value in [None, ""]:
        return ""

    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)

    return str(value)


def extract_place_of_performance(place_data):
    if not place_data:
        return ""

    if isinstance(place_data, str):
        return place_data

    if isinstance(place_data, dict):
        pieces = []

        city = safe_get(place_data, "city", "cityName")
        state = safe_get(place_data, "state", "stateCode")
        zip_code = safe_get(place_data, "zip", "zipCode")
        country = safe_get(place_data, "country", "countryCode")

        for item in [city, state, zip_code, country]:
            if item:
                pieces.append(str(item))

        if pieces:
            return ", ".join(pieces)

        return stringify_value(place_data)

    return stringify_value(place_data)


def extract_contacts(contact_data):
    if not contact_data:
        return ""

    contacts = []

    if isinstance(contact_data, dict):
        contact_data = [contact_data]

    if isinstance(contact_data, list):
        for contact in contact_data:
            if not isinstance(contact, dict):
                continue

            name = safe_get(contact, "fullName", "name")
            email = safe_get(contact, "email")
            phone = safe_get(contact, "phone", "phoneNumber")
            contact_type = safe_get(contact, "type", "contactType")

            parts = []

            if contact_type:
                parts.append(str(contact_type))

            if name:
                parts.append(str(name))

            if email:
                parts.append(str(email))

            if phone:
                parts.append(str(phone))

            if parts:
                contacts.append(" | ".join(parts))

    return " ; ".join(contacts)


def extract_resource_links(opportunity):
    resource_links = (
        opportunity.get("resourceLinks")
        or opportunity.get("resource_links")
        or opportunity.get("links")
        or []
    )

    if isinstance(resource_links, str):
        return [resource_links]

    if isinstance(resource_links, list):
        cleaned = []

        for item in resource_links:
            if isinstance(item, str):
                cleaned.append(item)
            elif isinstance(item, dict):
                url = safe_get(item, "url", "href", "link")
                if url:
                    cleaned.append(str(url))
                else:
                    cleaned.append(stringify_value(item))

        return cleaned

    return []


def add_api_key_to_url(url, api_key):
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    if "api_key" not in query:
        query["api_key"] = [api_key]

    new_query = urlencode(query, doseq=True)

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            parsed.fragment,
        )
    )


def build_notice_description_url(notice_id, api_key):
    return f"{SAM_NOTICE_DESC_URL}?noticeid={notice_id}&api_key={api_key}"


def get_retry_delay(response, attempt):
    retry_after = response.headers.get("Retry-After")

    if retry_after:
        try:
            return max(int(retry_after), 1)
        except ValueError:
            pass

    if attempt < len(BACKOFF_SECONDS):
        return BACKOFF_SECONDS[attempt]

    return BACKOFF_SECONDS[-1]


def request_with_retries(session, url, params=None, purpose="SAM.gov request"):
    cooldown_active, remaining = api_is_in_cooldown()

    if cooldown_active:
        raise SamRateLimitCooldown(
            f"SAM.gov API is in local cooldown for about {remaining} more second(s)."
        )

    last_response = None

    for attempt in range(MAX_RETRIES):
        if REQUEST_DELAY_SECONDS > 0:
            time.sleep(REQUEST_DELAY_SECONDS)

        response = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        last_response = response

        if response.status_code == 429:
            consecutive, cooldown_until = mark_rate_limited()

            if cooldown_until and time.time() < cooldown_until:
                remaining = int(cooldown_until - time.time())
                raise SamRateLimitCooldown(
                    f"SAM.gov returned repeated 429s. Entering local cooldown for about {remaining} second(s)."
                )

            wait_seconds = get_retry_delay(response, attempt)
            print(
                f"Rate limited during {purpose}. "
                f"Waiting {wait_seconds} second(s) before retry {attempt + 1}/{MAX_RETRIES}..."
            )
            time.sleep(wait_seconds)
            continue

        if response.status_code >= 500:
            wait_seconds = get_retry_delay(response, attempt)
            print(
                f"SAM.gov server error {response.status_code} during {purpose}. "
                f"Waiting {wait_seconds} second(s) before retry {attempt + 1}/{MAX_RETRIES}..."
            )
            time.sleep(wait_seconds)
            continue

        response.raise_for_status()
        reset_rate_limit_state()
        return response

    if last_response is not None:
        last_response.raise_for_status()

    raise RuntimeError(f"{purpose} failed without a response.")


def parse_notice_description_response(response):
    content_type = response.headers.get("content-type", "").lower()
    raw_text = response.text or ""

    if "application/json" in content_type:
        try:
            data = response.json()
        except ValueError:
            return extract_description_from_possible_json_text(raw_text)

        if isinstance(data, dict):
            description = (
                data.get("description")
                or data.get("noticeDesc")
                or data.get("body")
                or data.get("content")
                or data.get("text")
                or ""
            )

            if description:
                return clean_html_text(description)

            return clean_html_text(json.dumps(data, ensure_ascii=False))

        return clean_html_text(str(data))

    return extract_description_from_possible_json_text(raw_text)


def fetch_notice_description(session, api_key, opportunity):
    description_value = opportunity.get("description") or ""

    sam_notice_id = (
        opportunity.get("sam_notice_id")
        or opportunity.get("noticeId")
        or opportunity.get("id")
        or ""
    )

    description_url = ""

    if isinstance(description_value, str) and description_value.startswith("http"):
        description_url = description_value
    elif sam_notice_id:
        description_url = build_notice_description_url(sam_notice_id, api_key)

    if not description_url:
        if is_notice_desc_url(description_value):
            return ""
        return extract_description_from_possible_json_text(description_value)

    cache_key = make_cache_key({"description_url": description_url, "notice_id": sam_notice_id})
    cache_path = DESC_CACHE_DIR / f"{cache_key}.txt"

    cached_text = read_text_cache(cache_path)
    if cached_text is not None and cache_is_fresh(cache_path, DESC_CACHE_TTL_HOURS):
        return cached_text

    try:
        url_with_key = add_api_key_to_url(description_url, api_key)
        response = request_with_retries(
            session=session,
            url=url_with_key,
            params=None,
            purpose=f"notice description {sam_notice_id or description_url}",
        )

        full_description = parse_notice_description_response(response)

        if full_description and not is_notice_desc_url(full_description):
            full_description = compact_text(full_description)
            write_text_cache(cache_path, full_description)
            return full_description

    except SamRateLimitCooldown as error:
        print(f"Skipping description enrichment: {error}")
        if cached_text:
            return cached_text
        return ""

    except requests.RequestException:
        if cached_text:
            return cached_text

        if is_notice_desc_url(description_value):
            return ""

        return extract_description_from_possible_json_text(description_value)

    if is_notice_desc_url(description_value):
        return ""

    fallback = extract_description_from_possible_json_text(description_value)
    if fallback:
        write_text_cache(cache_path, fallback)

    return fallback


def normalize_opportunity(raw_opportunity, profile_name=None, search_term=None, search_type=None):
    sam_notice_id = safe_get(raw_opportunity, "noticeId", "notice_id", "id")
    solicitation_number = safe_get(
        raw_opportunity,
        "solicitationNumber",
        "solicitation_number",
        "solNo",
    )

    title = safe_get(raw_opportunity, "title", "solicitationTitle")

    notice_type = safe_get(raw_opportunity, "type", "noticeType", "baseType")
    set_aside = safe_get(
        raw_opportunity,
        "typeOfSetAsideDescription",
        "setAsideDescription",
        "setAside",
        "set_aside",
    )

    naics_code = safe_get(raw_opportunity, "naicsCode", "naics_code", "naics")
    psc_code = safe_get(
        raw_opportunity,
        "classificationCode",
        "psc",
        "pscCode",
        "productServiceCode",
    )

    department_agency = safe_get(
        raw_opportunity,
        "fullParentPathName",
        "department_ind_agency",
        "agency",
    )

    response_deadline = safe_get(
        raw_opportunity,
        "responseDeadLine",
        "response_deadline",
        "responseDate",
    )

    posted_date = safe_get(raw_opportunity, "postedDate", "posted_date")
    archive_date = safe_get(raw_opportunity, "archiveDate", "archive_date")

    place_of_performance = extract_place_of_performance(
        raw_opportunity.get("placeOfPerformance")
        or raw_opportunity.get("place_of_performance")
        or raw_opportunity.get("placeOfPerformanceState")
        or raw_opportunity.get("placeOfPerformanceCity")
    )

    contacts = extract_contacts(
        raw_opportunity.get("pointOfContact")
        or raw_opportunity.get("point_of_contact")
        or raw_opportunity.get("contacts")
    )

    resource_links = extract_resource_links(raw_opportunity)

    ui_link = safe_get(raw_opportunity, "uiLink", "ui_link")

    if not ui_link and sam_notice_id:
        ui_link = f"https://sam.gov/workspace/contract/opp/{sam_notice_id}/view"

    original_description = safe_get(raw_opportunity, "description", "desc", "noticeDesc")

    normalized = {
        "sam_notice_id": str(sam_notice_id or ""),
        "notice_id": str(solicitation_number or sam_notice_id or ""),
        "solicitation_number": str(solicitation_number or ""),
        "title": str(title or "Untitled Opportunity"),
        "description": str(original_description or ""),
        "full_description": "",
        "short_description": "",
        "description_enriched": "No",
        "department_ind_agency": str(department_agency or ""),
        "sub_tier": str(safe_get(raw_opportunity, "subTier", "sub_tier")),
        "office": str(safe_get(raw_opportunity, "office", "officeName")),
        "naics_code": str(naics_code or ""),
        "psc_code": str(psc_code or ""),
        "notice_type": str(notice_type or ""),
        "type": str(notice_type or ""),
        "set_aside": str(set_aside or ""),
        "typeOfSetAsideDescription": str(set_aside or ""),
        "response_deadline": str(response_deadline or ""),
        "posted_date": str(posted_date or ""),
        "archive_date": str(archive_date or ""),
        "place_of_performance": str(place_of_performance or ""),
        "contacts": contacts,
        "ui_link": str(ui_link or ""),
        "resource_links": " | ".join(resource_links),
        "resource_link_count": len(resource_links),
        "has_resource_links": "Yes" if resource_links else "No",
        "matched_search_profile": profile_name or "",
        "matched_search_term": search_term or "",
        "matched_search_type": search_type or "",
        "raw_notice_type": str(notice_type or ""),
        "raw_set_aside": str(set_aside or ""),
        "raw_place_of_performance": stringify_value(raw_opportunity.get("placeOfPerformance")),
        "raw_contacts": stringify_value(raw_opportunity.get("pointOfContact")),
        "raw_links": stringify_value(raw_opportunity.get("links")),
        "raw_resource_links": stringify_value(raw_opportunity.get("resourceLinks")),
        "raw_award_object": stringify_value(raw_opportunity.get("award")),
        "raw_api_record": stringify_value(raw_opportunity),
    }

    return normalized


def enrich_opportunity_description(session, api_key, opportunity):
    full_description = fetch_notice_description(session, api_key, opportunity)

    if full_description and not is_notice_desc_url(full_description):
        opportunity["full_description"] = full_description
        opportunity["description"] = full_description
        opportunity["short_description"] = compact_text(full_description, max_chars=1000)
        opportunity["description_enriched"] = "Yes"
    else:
        cleaned = extract_description_from_possible_json_text(opportunity.get("description", ""))

        if is_notice_desc_url(cleaned):
            cleaned = ""

        opportunity["full_description"] = cleaned
        opportunity["description"] = cleaned
        opportunity["short_description"] = compact_text(cleaned, max_chars=1000)
        opportunity["description_enriched"] = "No"

    return opportunity


def extract_opportunity_items(response_json):
    if not isinstance(response_json, dict):
        return []

    for key in ["opportunitiesData", "data", "results", "items"]:
        value = response_json.get(key)
        if isinstance(value, list):
            return value

    return []


def search_sam_opportunities(session, api_key, params):
    request_params = dict(params)
    request_params["api_key"] = api_key

    cache_params = dict(params)
    cache_key = make_cache_key(cache_params)
    cache_path = SEARCH_CACHE_DIR / f"{cache_key}.json"

    cached_data = read_json_cache(cache_path)
    cache_fresh = cache_is_fresh(cache_path, SEARCH_CACHE_TTL_HOURS)

    if cached_data is not None and cache_fresh:
        return extract_opportunity_items(cached_data)

    try:
        response = request_with_retries(
            session=session,
            url=SAM_SEARCH_URL,
            params=request_params,
            purpose=f"search {cache_params}",
        )

        response_json = response.json()
        write_json_cache(cache_path, response_json)
        return extract_opportunity_items(response_json)

    except SamRateLimitCooldown as error:
        if cached_data is not None:
            print(f"Using cached SAM.gov search results for {cache_params}")
            return extract_opportunity_items(cached_data)

        print(f"Skipping live search because of SAM.gov cooldown: {cache_params}")
        return []

    except requests.RequestException:
        if cached_data is not None:
            print(f"Using stale cached SAM.gov search results for {cache_params}")
            return extract_opportunity_items(cached_data)

        raise


def build_base_search_params(posted_days_back, limit_per_search):
    today = datetime.now()
    posted_from = today - timedelta(days=posted_days_back)

    return {
        "postedFrom": format_sam_date(posted_from),
        "postedTo": format_sam_date(today),
        "limit": limit_per_search,
        "offset": 0,
    }


def add_unique_opportunity(opportunities_by_key, opportunity):
    key_parts = [
        opportunity.get("sam_notice_id", ""),
        opportunity.get("notice_id", ""),
        opportunity.get("title", ""),
        opportunity.get("response_deadline", ""),
    ]

    unique_key = "|".join(str(part) for part in key_parts if part)

    if not unique_key:
        unique_key = json.dumps(opportunity, sort_keys=True)[:500]

    if unique_key not in opportunities_by_key:
        opportunities_by_key[unique_key] = opportunity
        return

    existing = opportunities_by_key[unique_key]

    existing_profiles = set(
        item.strip()
        for item in str(existing.get("matched_search_profile", "")).split(",")
        if item.strip()
    )

    if opportunity.get("matched_search_profile"):
        existing_profiles.add(opportunity["matched_search_profile"])

    existing_terms = set(
        item.strip()
        for item in str(existing.get("matched_search_term", "")).split(",")
        if item.strip()
    )

    if opportunity.get("matched_search_term"):
        existing_terms.add(opportunity["matched_search_term"])

    existing["matched_search_profile"] = ", ".join(sorted(existing_profiles))
    existing["matched_search_term"] = ", ".join(sorted(existing_terms))


def search_all_profiles(search_profiles, posted_days_back=30, limit_per_search=50):
    ensure_cache_dirs()

    cooldown_active, remaining = api_is_in_cooldown()

    if cooldown_active:
        print(
            f"SAM.gov API is currently in local cooldown for about {remaining} second(s). "
            f"GovCon Scout will use cached results where available and skip live API calls."
        )

    api_key = get_sam_api_key()
    base_params = build_base_search_params(posted_days_back, limit_per_search)

    opportunities_by_key = {}
    completed_searches = 0
    failed_searches = 0
    skipped_searches = 0

    with requests.Session() as session:
        for profile_name, profile_data in search_profiles.items():
            keywords = profile_data.get("keywords", [])
            naics_codes = profile_data.get("naics", [])

            for keyword in keywords:
                if not keyword:
                    continue

                params = dict(base_params)
                params["q"] = keyword

                try:
                    raw_results = search_sam_opportunities(session, api_key, params)

                    if raw_results:
                        completed_searches += 1
                    else:
                        skipped_searches += 1

                    for raw_opp in raw_results:
                        normalized = normalize_opportunity(
                            raw_opportunity=raw_opp,
                            profile_name=profile_name,
                            search_term=keyword,
                            search_type="keyword",
                        )
                        add_unique_opportunity(opportunities_by_key, normalized)

                except requests.RequestException as error:
                    failed_searches += 1
                    print(f"Search failed for keyword '{keyword}': {error}")

            for naics_code in naics_codes:
                if not naics_code:
                    continue

                params = dict(base_params)
                params["ncode"] = str(naics_code)

                try:
                    raw_results = search_sam_opportunities(session, api_key, params)

                    if raw_results:
                        completed_searches += 1
                    else:
                        skipped_searches += 1

                    for raw_opp in raw_results:
                        normalized = normalize_opportunity(
                            raw_opportunity=raw_opp,
                            profile_name=profile_name,
                            search_term=str(naics_code),
                            search_type="naics",
                        )
                        add_unique_opportunity(opportunities_by_key, normalized)

                except requests.RequestException as error:
                    failed_searches += 1
                    print(f"Search failed for NAICS '{naics_code}': {error}")

        opportunities = list(opportunities_by_key.values())

        print(f"Completed/cached SAM.gov searches with results: {completed_searches}.")

        if skipped_searches:
            print(f"Skipped/empty searches: {skipped_searches}")

        if failed_searches:
            print(f"Failed SAM.gov searches: {failed_searches}")

        print(f"Found {len(opportunities)} unique opportunities.")

        cooldown_active, remaining = api_is_in_cooldown()

        if cooldown_active:
            print(
                f"Skipping notice description enrichment because SAM.gov is cooling down "
                f"for about {remaining} second(s)."
            )
            return opportunities

        if ENRICH_DESCRIPTION_LIMIT <= 0:
            print("Skipping notice description enrichment because SAM_ENRICH_DESCRIPTION_LIMIT is 0.")
            return opportunities

        print(
            f"Enriching notice descriptions from SAM.gov "
            f"(limit {ENRICH_DESCRIPTION_LIMIT} per run)..."
        )

        enriched_count = 0
        enrichment_pool = opportunities[:ENRICH_DESCRIPTION_LIMIT]

        for index, opportunity in enumerate(enrichment_pool, start=1):
            cooldown_active, remaining = api_is_in_cooldown()

            if cooldown_active:
                print(
                    f"Stopping enrichment because SAM.gov entered cooldown "
                    f"for about {remaining} second(s)."
                )
                break

            before = opportunity.get("description", "")
            enriched = enrich_opportunity_description(session, api_key, opportunity)
            after = enriched.get("description", "")

            if after and after != before and not is_notice_desc_url(after):
                enriched_count += 1

            if index % 25 == 0:
                print(f"Enriched {index}/{len(enrichment_pool)} selected opportunities...")

        print(f"Enriched descriptions: {enriched_count}/{len(enrichment_pool)}")

    return opportunities