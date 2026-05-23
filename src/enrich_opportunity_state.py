import argparse
import ast
import csv
import html
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo


BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data/opportunity_state.csv"
EXPORTS_DIR = BASE_DIR / "exports"
BACKUP_DIR = BASE_DIR / "data/backups"

TARGET_COLUMNS = [
    "description",
    "synopsis",
    "set_aside",
    "set_aside_type",
    "set_aside_description",
    "place_of_performance",
    "place_of_performance_city",
    "place_of_performance_state",
    "place_of_performance_country",
    "buyer_name",
    "buyer_email",
    "buyer_phone",
    "source_url",
    "ui_link",
    "notice_type",
    "solicitation_type",
    "psc",
    "response_deadline_time",
    "response_deadline_timezone",
    "deadline_time",
    "deadline_tz",
]

PROTECTED_COLUMNS = {
    "macro_stage",
    "triage_status",
    "operator_status",
    "flagged",
    "recommended_next_action",
    "last_operator_action",
    "notes",
    "last_updated",
    "current_stage",
    "manual_review_status",
    "sources_sought_status",
    "processed_status",
    "bid_price_sanity_status",
}

NOTICE_ID_FIELDS = [
    "notice_id",
    "solicitation_number",
    "sam_notice_id",
    "noticeId",
    "solicitationNumber",
]

FIELD_SOURCES = {
    "description": ["description", "description_enriched", "notice_description", "synopsis", "short_description"],
    "synopsis": ["synopsis", "short_description", "description_enriched", "notice_description", "description"],
    "set_aside": ["set_aside", "setAsideDescription", "setAside", "set_aside_type", "type_of_set_aside"],
    "set_aside_type": ["set_aside_type", "type_of_set_aside", "set_aside", "setAside"],
    "set_aside_description": ["set_aside_description", "setAsideDescription", "set_aside"],
    "place_of_performance": ["place_of_performance", "performance_location", "location", "pop"],
    "place_of_performance_city": ["place_of_performance_city", "city"],
    "place_of_performance_state": ["place_of_performance_state", "state", "province", "region"],
    "place_of_performance_country": ["place_of_performance_country", "country"],
    "buyer_name": ["buyer_name", "contracting_officer_name", "contact_name", "contacts"],
    "buyer_email": ["buyer_email", "contracting_officer_email", "contact_email", "contacts"],
    "buyer_phone": ["buyer_phone", "contracting_officer_phone", "contact_phone", "contacts"],
    "source_url": ["source_url", "ui_link", "url", "link", "sam_url", "notice_url"],
    "ui_link": ["ui_link", "source_url", "url", "link", "sam_url", "notice_url"],
    "notice_type": ["notice_type", "type", "solicitation_type"],
    "solicitation_type": ["solicitation_type", "notice_type", "type"],
    "psc": ["psc", "psc_code"],
    "response_deadline_time": ["response_deadline"],
    "response_deadline_timezone": ["response_deadline"],
    "deadline_time": ["response_deadline"],
    "deadline_tz": ["response_deadline"],
}

REQUESTED_FIELD_GROUPS = {
    "synopsis / description / notice description": ["description", "description_enriched", "notice_description", "synopsis", "short_description"],
    "set-aside / set_aside_type / set_aside_description": ["set_aside", "set_aside_type", "set_aside_description", "setAside", "setAsideDescription"],
    "place of performance city": ["place_of_performance_city", "city"],
    "place of performance state": ["place_of_performance_state", "state", "province", "region"],
    "place of performance country": ["place_of_performance_country", "country"],
    "buyer / contracting officer name": ["buyer_name", "contracting_officer_name", "contact_name", "contacts"],
    "buyer email": ["buyer_email", "contracting_officer_email", "contact_email", "contacts"],
    "buyer phone": ["buyer_phone", "contracting_officer_phone", "contact_phone", "contacts"],
    "source URL / ui_link": ["source_url", "ui_link", "url", "link", "sam_url", "notice_url"],
    "notice type / solicitation type": ["notice_type", "solicitation_type", "type"],
    "PSC code": ["psc", "psc_code"],
    "response deadline time": ["response_deadline"],
    "response deadline timezone": ["response_deadline"],
}

TIMEZONE_BY_OFFSET = {
    "-04:00": "ET",
    "-05:00": "CT",
    "-06:00": "MT",
    "-07:00": "PT",
    "-08:00": "AKT",
    "-10:00": "HT",
}


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.skip = False

    def handle_starttag(self, tag, attrs):
        if tag.lower() in {"script", "style"}:
            self.skip = True
        if tag.lower() in {"p", "br", "div", "li", "tr"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag.lower() in {"script", "style"}:
            self.skip = False

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)

    def text(self):
        return " ".join("".join(self.parts).split())


def clean(value):
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"", "none", "null", "n/a", "na", "not available", "no"}:
        return ""
    return text


def csv_safe_text(value, max_chars=None):
    text = clean(value)
    if not text:
        return "", False
    if "<" in text and ">" in text:
        parser = TextExtractor()
        parser.feed(text)
        text = parser.text()
    text = html.unescape(text)
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[\t\r\n]+", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    truncated = False
    if max_chars and len(text) > max_chars:
        text = text[:max_chars].rstrip() + " [truncated]"
        truncated = True
    return text, truncated


def useful_synopsis_text(value, max_chars=2000):
    text, truncated = csv_safe_text(value, max_chars=max_chars)
    if not text:
        return "", False
    if text.strip().lower() in {"yes", "no", "true", "false", "none", "null", "not available"}:
        return "", False
    meaningful = re.sub(r"[^A-Za-z0-9]+", "", text)
    if len(meaningful) < 20:
        return "", False
    return text, truncated


def first_value(row, candidates):
    for field in candidates:
        value = clean(row.get(field))
        if value:
            return value
    return ""


def normalize_set_aside(value):
    text, _ = csv_safe_text(value)
    if not text:
        return ""
    lower = text.lower()
    if "unrestrict" in lower or "full and open" in lower:
        return "Unrestricted"
    if "service-disabled" in lower or "sdvosb" in lower:
        return "Service-Disabled Veteran-Owned Small Business"
    if "economically disadvantaged" in lower or "edwosb" in lower:
        return "Economically Disadvantaged Woman-Owned Small Business"
    if "woman" in lower or "women" in lower or "wosb" in lower:
        return "Woman-Owned Small Business"
    if "hubzone" in lower:
        return "HUBZone"
    if "8(a)" in lower or re.search(r"\b8a\b", lower):
        return "8(a)"
    if "total small business" in lower:
        return "Total Small Business"
    if "small business" in lower or lower == "sba":
        return "Small Business Set-Aside"
    return text


def empty_location(value):
    if value is None:
        return True
    if isinstance(value, dict):
        return not any(not empty_location(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return not any(not empty_location(item) for item in value)
    return clean(value) == ""


def scalar_location(value):
    if empty_location(value):
        return ""
    if isinstance(value, dict):
        for key in ["name", "city", "state", "province", "region", "streetAddress", "street_address", "address", "country", "code"]:
            text = scalar_location(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = scalar_location(item)
            if text:
                return text
        return ""
    return clean(value)


def parse_location(value):
    text = clean(value)
    if not text or ("{" not in text and "[" not in text):
        return None
    for candidate in [text, f"[{text}]"]:
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            return ast.literal_eval(candidate)
        except (SyntaxError, ValueError, TypeError):
            pass
    return None


def location_from_object(value):
    if empty_location(value):
        return {}
    if isinstance(value, (list, tuple)):
        city = state = country = address = zip_code = ""
        loose_parts = []
        for item in value:
            item_parts = location_from_object(item)
            item_city = item_parts.get("city", "")
            item_state = item_parts.get("state", "")
            item_country = item_parts.get("country", "")
            item_address = item_parts.get("address", "")
            item_zip = item_parts.get("zip", "")
            if item_city and not item_state and not item_country and not item_address:
                loose_parts.append(item_city)
            else:
                city = city or item_city
                state = state or item_state
                country = country or item_country
                address = address or item_address
            if item_zip and not re.fullmatch(r"\d{5}(?:-\d{4})?", item_zip):
                address = address or item_zip
            zip_code = zip_code or item_zip
        if loose_parts:
            useful = [part for part in loose_parts if not re.fullmatch(r"\d{5}(?:-\d{4})?", part)]
            useful = [part for part in useful if part.upper() not in {"UNITED STATES", "USA", "US"}]
            city = city or (useful[0] if useful else "")
            state = state or (useful[1] if len(useful) > 1 else "")
            country = country or (useful[2] if len(useful) > 2 else "")
        return {"city": city, "state": state, "country": country, "address": address, "zip": zip_code}
    if isinstance(value, dict):
        city = scalar_location(value.get("city"))
        state = scalar_location(value.get("state")) or scalar_location(value.get("province")) or scalar_location(value.get("region"))
        country = scalar_location(value.get("country"))
        address = scalar_location(value.get("streetAddress")) or scalar_location(value.get("street_address")) or scalar_location(value.get("address"))
        zip_code = scalar_location(value.get("zip")) or scalar_location(value.get("postalCode")) or scalar_location(value.get("postal_code"))
        name = scalar_location(value.get("name"))
        code = scalar_location(value.get("code"))
        if not city and not state and not country and not address:
            if name:
                city = name
            elif code and not re.fullmatch(r"\d+", code):
                state = code
        return {"city": city, "state": state, "country": country, "address": address, "zip": zip_code}
    text = scalar_location(value)
    if re.fullmatch(r"\d+", text):
        return {"code": text}
    return {"address": text}


def normalize_place(value):
    text = clean(value)
    if not text:
        return {}
    if re.fullmatch(r"\d+", text):
        return {"place_of_performance": f"Location code: {text} — name not available"}
    structured = parse_location(text)
    if structured is not None:
        parts = location_from_object(structured)
    else:
        names = re.findall(r"['\"]name['\"]\s*:\s*['\"]([^'\"]+)['\"]", text)
        if names:
            useful = [name for name in names if name.upper() not in {"UNITED STATES", "USA", "US"}]
            parts = {"city": useful[0] if useful else "", "state": useful[1] if len(useful) > 1 else "", "country": ""}
        elif "{" in text or "}" in text:
            cleaned = re.sub(r"[{}\\[\\]'\"]+", " ", text)
            cleaned = re.sub(r"\b(streetAddress|street_address|address|city|state|province|region|country|zip|postalCode|postal_code|code|name)\b\s*:\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
            parts = {"address": cleaned if cleaned and not re.fullmatch(r"\d+", cleaned) else ""}
        else:
            parts = {"address": text}
    city = parts.get("city", "")
    state = parts.get("state", "")
    country = parts.get("country", "")
    address = parts.get("address", "")
    if country.upper() in {"UNITED STATES", "USA", "US"} and (city or state):
        country = ""
    display = ", ".join(piece for piece in [city, state, country] if piece)
    if not display:
        display = address
    if not display and parts.get("code"):
        display = f"Location code: {parts['code']} — name not available"
    return {
        "place_of_performance": display,
        "place_of_performance_city": city,
        "place_of_performance_state": state,
        "place_of_performance_country": country,
    }


def parse_contacts(value):
    text, _ = csv_safe_text(value)
    if not text:
        return {}
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", text)
    phone_match = re.search(r"(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}", text)
    pieces = [piece.strip() for piece in text.split("|")]
    name = ""
    for piece in pieces:
        if not piece or piece.lower() in {"primary", "secondary", "n/a"}:
            continue
        if email_match and piece == email_match.group(0):
            continue
        if phone_match and piece == phone_match.group(0):
            continue
        name = piece
        break
    return {
        "buyer_name": name,
        "buyer_email": email_match.group(0) if email_match else "",
        "buyer_phone": phone_match.group(0) if phone_match else "",
    }


def parse_deadline(value):
    text = clean(value)
    if not text:
        return {}
    match = re.match(r"^(.+?)([+-]\d{2}:\d{2})$", text)
    offset = match.group(2) if match else ""
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return {}
    return {
        "response_deadline_time": dt.strftime("%H:%M"),
        "response_deadline_timezone": TIMEZONE_BY_OFFSET.get(offset, offset),
        "deadline_time": dt.strftime("%H:%M"),
        "deadline_tz": TIMEZONE_BY_OFFSET.get(offset, offset),
    }


def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        return list(reader), list(reader.fieldnames or [])


def row_id(row):
    for field in NOTICE_ID_FIELDS:
        value = clean(row.get(field))
        if value:
            return value
    return ""


def select_export_file(state_ids):
    candidates = []
    required = set().union(*[set(fields) for fields in FIELD_SOURCES.values()])
    for path in sorted(EXPORTS_DIR.glob("*.csv")):
        rows, headers = read_csv(path)
        export_ids = {row_id(row) for row in rows if row_id(row)}
        score = len(state_ids & export_ids) * 10
        score += len(required & set(headers))
        if path.name.endswith("_latest.csv"):
            score += 25
        candidates.append((score, path, rows, headers, len(state_ids & export_ids)))
    if not candidates:
        raise FileNotFoundError(f"No export CSV files found in {EXPORTS_DIR}")
    candidates.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    return candidates[0], candidates


def build_enrichment(export_row):
    out = {}
    truncated = set()

    for target in ["description", "synopsis"]:
        value = first_value(export_row, FIELD_SOURCES[target])
        text, was_truncated = useful_synopsis_text(value, max_chars=2000)
        if text:
            out[target] = text
            if was_truncated:
                truncated.add(target)

    set_aside = normalize_set_aside(first_value(export_row, FIELD_SOURCES["set_aside"]))
    if set_aside:
        out["set_aside"] = set_aside
        out["set_aside_type"] = set_aside
        out["set_aside_description"] = set_aside

    place_parts = normalize_place(first_value(export_row, FIELD_SOURCES["place_of_performance"]))
    for key in ["place_of_performance", "place_of_performance_city", "place_of_performance_state", "place_of_performance_country"]:
        if place_parts.get(key):
            out[key] = place_parts[key]

    contact_parts = parse_contacts(first_value(export_row, ["contacts"]))
    for key in ["buyer_name", "buyer_email", "buyer_phone"]:
        direct_fields = [field for field in FIELD_SOURCES[key] if field != "contacts"]
        value = first_value(export_row, direct_fields)
        if value:
            out[key], _ = csv_safe_text(value)
        elif contact_parts.get(key):
            out[key] = contact_parts[key]

    for target in ["source_url", "ui_link", "notice_type", "solicitation_type", "psc"]:
        value = first_value(export_row, FIELD_SOURCES[target])
        text, _ = csv_safe_text(value)
        if text:
            out[target] = text

    deadline_parts = parse_deadline(first_value(export_row, ["response_deadline"]))
    out.update({key: value for key, value in deadline_parts.items() if value})
    return out, truncated


def target_available_from_export(target, export_row):
    if target in ["place_of_performance_city", "place_of_performance_state", "place_of_performance_country"]:
        place_parts = normalize_place(first_value(export_row, FIELD_SOURCES["place_of_performance"]))
        return bool(place_parts.get(target))
    if target in ["response_deadline_time", "response_deadline_timezone", "deadline_time", "deadline_tz"]:
        return bool(parse_deadline(first_value(export_row, ["response_deadline"])).get(target))
    if target in ["buyer_name", "buyer_email", "buyer_phone"]:
        direct_fields = [field for field in FIELD_SOURCES[target] if field != "contacts"]
        if first_value(export_row, direct_fields):
            return True
        return bool(parse_contacts(first_value(export_row, ["contacts"])).get(target))
    if target in FIELD_SOURCES:
        return bool(first_value(export_row, FIELD_SOURCES[target]))
    return False


def enrich(write=False):
    state_rows, state_headers = read_csv(STATE_PATH)
    state_ids = {row_id(row) for row in state_rows if row_id(row)}
    (score, selected_path, export_rows, export_headers, matched_count), export_candidates = select_export_file(state_ids)
    export_by_id = {}
    for row in export_rows:
        rid = row_id(row)
        if rid and rid not in export_by_id:
            export_by_id[rid] = row

    output_headers = list(state_headers)
    for column in TARGET_COLUMNS:
        if column not in output_headers and column not in PROTECTED_COLUMNS:
            output_headers.append(column)

    field_counts = Counter()
    skipped_populated_counts = Counter()
    export_blank_counts = Counter()
    truncated_counts = Counter()
    rows_updated = 0
    matched_rows = 0
    examples = []

    for row in state_rows:
        rid = row_id(row)
        export_row = export_by_id.get(rid)
        if not export_row:
            continue
        matched_rows += 1
        enrichment, truncated = build_enrichment(export_row)
        changed_fields = []
        for field in TARGET_COLUMNS:
            if field in PROTECTED_COLUMNS:
                continue
            if clean(row.get(field)):
                skipped_populated_counts[field] += 1
            elif field not in enrichment and not target_available_from_export(field, export_row):
                export_blank_counts[field] += 1
        for field, value in enrichment.items():
            if field in PROTECTED_COLUMNS or not value:
                continue
            if clean(row.get(field)):
                continue
            row[field] = value
            field_counts[field] += 1
            changed_fields.append(field)
            if field in truncated:
                truncated_counts[field] += 1
        if changed_fields:
            rows_updated += 1
            if len(examples) < 10:
                examples.append((rid, changed_fields))

    missing_groups = [
        group for group, names in REQUESTED_FIELD_GROUPS.items()
        if not any(name in export_headers for name in names)
    ]

    backup_path = ""
    if write:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        backup_path = BACKUP_DIR / f"opportunity_state_before_enrichment_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        shutil.copy2(STATE_PATH, backup_path)
        with STATE_PATH.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=output_headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(state_rows)

    return {
        "mode": "write" if write else "dry-run",
        "export_files": [str(item[1].relative_to(BASE_DIR)) for item in export_candidates],
        "selected_export": str(selected_path.relative_to(BASE_DIR)),
        "selected_export_score": score,
        "export_headers": export_headers,
        "state_headers_before": state_headers,
        "state_rows": len(state_rows),
        "export_rows": len(export_rows),
        "rows_matched": matched_rows,
        "rows_updated": rows_updated,
        "field_counts": dict(field_counts),
        "skipped_populated_counts": dict(skipped_populated_counts),
        "export_blank_counts": dict(export_blank_counts),
        "truncated_counts": dict(truncated_counts),
        "examples": examples,
        "missing_groups": missing_groups,
        "backup_path": str(backup_path.relative_to(BASE_DIR)) if backup_path else "",
        "output_headers": output_headers,
    }


def print_summary(summary):
    print(f"Mode: {summary['mode']}")
    print("Export CSV files found:")
    for path in summary["export_files"]:
        print(f"- {path}")
    print(f"Selected export: {summary['selected_export']} (score {summary['selected_export_score']})")
    print(f"Opportunity state rows: {summary['state_rows']}")
    print(f"Export rows: {summary['export_rows']}")
    print(f"Rows matched: {summary['rows_matched']}")
    print(f"Rows updated: {summary['rows_updated']}")
    print("Fields that would be filled:" if summary["mode"] == "dry-run" else "Fields filled:")
    if summary["field_counts"]:
        for field, count in sorted(summary["field_counts"].items()):
            print(f"- {field}: {count}")
    else:
        print("- none")
    if summary["truncated_counts"]:
        print("Truncated values:")
        for field, count in sorted(summary["truncated_counts"].items()):
            print(f"- {field}: {count}")
    else:
        print("Truncated values: none")
    print("Fields skipped because already populated:")
    if summary["skipped_populated_counts"]:
        for field, count in sorted(summary["skipped_populated_counts"].items()):
            print(f"- {field}: {count}")
    else:
        print("- none")
    print("Fields unavailable or blank in the export for matched rows:")
    if summary["export_blank_counts"]:
        for field, count in sorted(summary["export_blank_counts"].items()):
            print(f"- {field}: {count}")
    else:
        print("- none")
    if summary["examples"]:
        print("Example updated rows:")
        for rid, fields in summary["examples"]:
            print(f"- {rid}: {', '.join(fields)}")
    if summary["missing_groups"]:
        print("Requested field groups not found in selected export:")
        for group in summary["missing_groups"]:
            print(f"- {group}")
    else:
        print("Requested field groups not found in selected export: none")
    if summary["backup_path"]:
        print(f"Backup created: {summary['backup_path']}")


def main():
    parser = argparse.ArgumentParser(description="Enrich local opportunity state from local SAM export CSV data.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Show proposed enrichment without writing files.")
    mode.add_argument("--write", action="store_true", help="Write enrichment to data/opportunity_state.csv.")
    args = parser.parse_args()
    summary = enrich(write=args.write)
    print_summary(summary)


if __name__ == "__main__":
    main()
