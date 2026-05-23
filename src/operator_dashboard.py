import argparse
import warnings

warnings.filterwarnings("ignore", message="'cgi' is deprecated.*", category=DeprecationWarning)

import ast
import cgi
import csv
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from detail_enrichment import extract_description_from_detail, fetch_sam_detail
from sam_client import fetch_notice_description, get_sam_api_key


# CONFIG / PATH CONSTANTS
BASE_DIR = Path(__file__).resolve().parent.parent
HOST = "0.0.0.0"
DEFAULT_PORT = 8765

WEB_INDEX_PATH = BASE_DIR / "web/operator_dashboard/index.html"
OPPORTUNITY_STATE_PATH = BASE_DIR / "data/opportunity_state.csv"
OPPORTUNITY_NOTES_PATH = BASE_DIR / "data/opportunity_notes.csv"
GOVCON_EXPORT_PATH = BASE_DIR / "exports/govcon_scout_opportunities_latest.csv"
STAGE_ENUMS_PATH = BASE_DIR / "config/stage_enums.json"
NAICS_LANES_PATH = BASE_DIR / "config/naics_lanes.json"
BUSINESS_RULES_PATH = BASE_DIR / "config/business_rules.json"
DASHBOARD_STATE_PATH = BASE_DIR / "reports/triage/operator_dashboard_state.json"

DATA_DIR = BASE_DIR / "data"
BACKUP_DIR = BASE_DIR / "data/backups"
MANUAL_UPLOADS_DIR = BASE_DIR / "manual_uploads"
AI_DRAFTS_DIR = BASE_DIR / "ai_drafts"
REPORTS_DIR = BASE_DIR / "reports"
DOWNLOADS_DIR = BASE_DIR / "downloads"
CONFIG_DIR = BASE_DIR / "config"
WEB_DIR = BASE_DIR / "web/operator_dashboard"

ALLOWED_FILE_ROOTS = [
    REPORTS_DIR,
    DOWNLOADS_DIR,
    MANUAL_UPLOADS_DIR,
    AI_DRAFTS_DIR,
    DATA_DIR,
]

NOTE_FIELDS = [
    "notice_id",
    "timestamp",
    "note_type",
    "note_text",
    "stage",
    "source",
]

DASHBOARD_COLUMNS = [
    "notice_id",
    "title",
    "agency",
    "source",
    "naics",
    "lane",
    "set_aside",
    "due_date",
    "deadline_time",
    "deadline_tz",
    "macro_stage",
    "fit_score",
    "fit_band",
    "disqualifier_flag",
    "ai_summary",
    "description",
    "requirements",
    "disqualifiers",
    "buyer_name",
    "buyer_phone",
    "buyer_email",
    "last_call_date",
    "last_call_notes",
    "draft_path",
    "source_url",
    "ui_link",
    "place_of_performance",
    "triage_status",
    "recommended_next_action",
    "last_operator_action",
    "operator_status",
    "last_updated",
]

SOURCE_URL_FIELDS = [
    "source_url",
    "ui_link",
    "url",
    "link",
    "sam_url",
    "notice_url",
]

DESCRIPTION_FIELDS = [
    "description",
    "full_description",
    "synopsis",
    "notice_description",
    "body",
    "summary",
    "description_enriched",
    "short_description",
]

LOCATION_FIELDS = [
    "place_of_performance",
    "place_of_performance_city",
    "place_of_performance_state",
    "place_of_performance_zip",
    "pop",
    "streetAddress",
    "street_address",
    "address",
    "city",
    "state",
    "province",
    "region",
    "country",
    "location",
    "performance_location",
    "postalCode",
    "postal_code",
    "name",
    "code",
]

LOCATION_CITY_FIELDS = [
    "place_of_performance_city",
    "city",
]

LOCATION_STATE_FIELDS = [
    "place_of_performance_state",
    "state",
    "province",
    "region",
]

LOCATION_ZIP_FIELDS = [
    "place_of_performance_zip",
    "zip",
    "zipcode",
    "postalCode",
    "postal_code",
]

SET_ASIDE_FIELDS = [
    "set_aside",
    "setaside",
    "set_aside_type",
    "set_aside_code",
    "type_of_set_aside",
    "solicitation_set_aside",
    "setAside",
    "setAsideCode",
    "setAsideDescription",
    "set_aside_description",
    "set_aside_hard_gate",
]

RELATED_PATH_COLUMNS = [
    ("analysis_packet_path", "analysis packet"),
    ("bid_no_bid_path", "bid/no-bid review"),
    ("decision_report_path", "decision report"),
    ("compliance_matrix_path", "compliance matrix"),
    ("pricing_schedule_path", "pricing schedule"),
    ("pricing_table_path", "pricing table"),
    ("bid_price_sanity_path", "bid price sanity report"),
    ("usaspending_report_path", "USAspending report"),
    ("sources_sought_plan_path", "sources sought plan"),
    ("manual_review_path", "manual review report"),
]

DOCUMENT_HINTS = [
    ("solicitation", ["solicitation", "rfq", "rfp", "sf1449", "sol_"]),
    ("pws_sow", ["pws", "sow", "performance work statement", "statement of work"]),
    ("pricing_schedule", ["pricing", "price", "clin", "schedule"]),
    ("sf1449", ["sf1449", "sf 1449"]),
    ("amendments", ["amendment", "sf30", "sf 30"]),
    ("wage_determination", ["wage", "wdol", "determination"]),
    ("attachments_maps", ["attachment", "att_", "map", "facilities"]),
    ("sources_sought_rfi", ["sources sought", "rfi", "request for information"]),
]

SUPPORTED_ANALYSIS_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".csv",
    ".xlsx",
    ".docx",
}


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def json_response(handler, payload, status=200):
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def ensure_directories():
    for path in [
        DATA_DIR,
        BACKUP_DIR,
        MANUAL_UPLOADS_DIR,
        AI_DRAFTS_DIR,
        REPORTS_DIR,
        DOWNLOADS_DIR,
        CONFIG_DIR,
        WEB_DIR,
        DASHBOARD_STATE_PATH.parent,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def ensure_config_files():
    defaults = {
        STAGE_ENUMS_PATH: {
            "stages": [
                "Triage",
                "Intake",
                "Manual Review",
                "AI Review",
                "Development",
                "Ready to Submit",
                "Execution",
                "Archive",
                "Done",
            ],
            "note_types": [
                "general_note",
                "buyer_feedback",
                "vendor_quote_note",
                "call_note",
                "requirement_question",
                "risk_note",
                "submission_note",
                "follow_up_note",
                "pricing_note",
            ],
        },
        NAICS_LANES_PATH: {
            "541613": "Marketing",
            "541810": "Advertising",
            "541820": "Public Relations",
            "561920": "Event Management",
            "611430": "Training",
            "561720": "Janitorial / Facilities",
            "561710": "Pest Control",
            "561612": "Security Guard Services",
            "484121": "Commercial Transportation",
            "541512": "IT / Computer Systems",
            "541511": "AI / Custom Programming",
            "512110": "Video Production",
            "611519": "CDL / Driver Training",
            "541430": "Technical Documentation",
            "541922": "Photography / Videography",
        },
        BUSINESS_RULES_PATH: {
            "hard_disqualifiers": [
                "top secret",
                "ts/sci",
                "secret clearance",
                "security clearance required",
                "team already in place",
                "existing team required",
                "5 years federal past performance",
                "government-specific past performance required",
                "same-size same-scope past performance required",
                "same or similar government past performance required",
                "armed guard license",
                "must be currently licensed",
            ],
            "soft_disqualifiers": [
                "preferred clearance",
                "preferred past performance",
                "incumbent contractor",
                "prior federal experience preferred",
            ],
        },
    }
    for path, payload in defaults.items():
        if not path.exists():
            write_json(path, payload)


def ensure_notes_file():
    if OPPORTUNITY_NOTES_PATH.exists():
        return
    with OPPORTUNITY_NOTES_PATH.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=NOTE_FIELDS)
        writer.writeheader()


def backup_state_file():
    if not OPPORTUNITY_STATE_PATH.exists():
        return ""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_{stamp}.csv"
    shutil.copy2(OPPORTUNITY_STATE_PATH, backup_path)
    return str(backup_path.relative_to(BASE_DIR))


def backup_state_file_for_synopsis():
    if not OPPORTUNITY_STATE_PATH.exists():
        return ""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_before_synopsis_{stamp}.csv"
    shutil.copy2(OPPORTUNITY_STATE_PATH, backup_path)
    return str(backup_path.relative_to(BASE_DIR))


def run_local_state_builder_if_needed():
    if OPPORTUNITY_STATE_PATH.exists():
        return
    subprocess.run([sys.executable, "src/opportunity_state.py"], cwd=BASE_DIR, check=False)


def read_csv(path):
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def write_csv_preserve(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def load_exports_by_notice():
    _fields, rows = read_csv(GOVCON_EXPORT_PATH)
    index = {}
    for row in rows:
        for key in ["notice_id", "solicitation_number", "sam_notice_id"]:
            notice_id = safe_text(row.get(key))
            if notice_id and notice_id not in index:
                index[notice_id] = row
    return index


def parse_due_date(value):
    text = safe_text(value)
    if not text:
        return "", "", ""
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    due_date = match.group(1) if match else ""
    time_match = re.search(r"(\d{1,2}:\d{2})", text)
    deadline_time = time_match.group(1) if time_match else ""
    tz_match = re.search(r"\b([A-Z]{2,4})\b", text)
    deadline_tz = tz_match.group(1) if tz_match else ""
    return due_date, deadline_time, deadline_tz


def first_value(*rows_and_fields):
    for row, fields in rows_and_fields:
        for field in fields:
            value = safe_text(row.get(field))
            if value:
                return value
    return ""


def meaningful_text(value):
    text = safe_text(value)
    if not text:
        return ""
    if text.lower() in {"no", "yes", "none", "n/a", "na", "not available", "null"}:
        return ""
    return text


def valid_synopsis_text(value):
    text = safe_text(value)
    if not text:
        return ""
    if text.lower() in {"yes", "no", "true", "false", "n/a", "na", "null", "none", "not available"}:
        return ""
    meaningful = re.sub(r"[^A-Za-z0-9]+", "", text)
    if len(meaningful) < 30:
        return ""
    return text


def first_description(*rows_and_fields):
    for row, fields in rows_and_fields:
        for field in fields:
            value = meaningful_text(row.get(field))
            if value:
                return value
    return ""


def is_numeric_code(value):
    text = safe_text(value)
    return bool(re.fullmatch(r"\d+", text))


def is_empty_location_value(value):
    if value is None:
        return True
    if isinstance(value, (dict, list, tuple)):
        return not any(not is_empty_location_value(item) for item in (
            value.values() if isinstance(value, dict) else value
        ))
    return safe_text(value).lower() in {"", "none", "null", "n/a", "na", "{}"}


def clean_country_piece(value, has_locality=False):
    text = safe_text(value)
    if has_locality and text.upper() in {"UNITED STATES", "USA", "US"}:
        return ""
    return text


def location_scalar(value):
    if is_empty_location_value(value):
        return ""
    if isinstance(value, dict):
        for key in [
            "name",
            "city",
            "state",
            "province",
            "region",
            "streetAddress",
            "street_address",
            "address",
            "country",
            "code",
        ]:
            text = location_scalar(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = location_scalar(item)
            if text:
                return text
        return ""
    text = safe_text(value)
    return "" if text in {"{}", "[]"} else text


def parse_structured_location(value):
    text = safe_text(value)
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


def format_structured_location(value):
    if is_empty_location_value(value):
        return ""

    if isinstance(value, (list, tuple)):
        pieces = []
        for item in value:
            text = format_structured_location(item)
            if text and text != "not available":
                pieces.append(text)
        pieces = [piece for piece in pieces if piece.upper() not in {"UNITED STATES", "USA", "US"}]
        return ", ".join(dict.fromkeys(pieces[:3]))

    if not isinstance(value, dict):
        text = location_scalar(value)
        if not text:
            return ""
        if is_numeric_code(text):
            return f"Location code: {text} — name not available"
        return text

    city = location_scalar(value.get("city"))
    state = (
        location_scalar(value.get("state"))
        or location_scalar(value.get("province"))
        or location_scalar(value.get("region"))
    )
    country = location_scalar(value.get("country"))
    address = location_scalar(value.get("streetAddress")) or location_scalar(value.get("street_address")) or location_scalar(value.get("address"))
    name = location_scalar(value.get("name"))
    code = location_scalar(value.get("code"))

    pieces = [piece for piece in [city, state] if piece]
    country = clean_country_piece(country, has_locality=bool(pieces))
    if country:
        pieces.append(country)
    if pieces:
        return ", ".join(pieces)
    if address:
        return address
    if name:
        return name
    if code and is_numeric_code(code):
        return f"Location code: {code} — name not available"
    if code:
        return code
    return ""


def clean_location_piece(value):
    text = safe_text(value)
    if not text:
        return ""
    structured = parse_structured_location(text)
    if structured is not None:
        return format_structured_location(structured)
    name_match = re.search(r"'name':\s*'([^']+)'", text)
    if name_match:
        return name_match.group(1)
    name_match = re.search(r'"name"\s*:\s*"([^"]+)"', text)
    if name_match:
        return name_match.group(1)
    if is_numeric_code(text):
        return ""
    return text


def normalize_place(value):
    text = safe_text(value)
    if not text:
        return ""
    if is_numeric_code(text):
        return f"Location code: {text} — name not available"

    structured = parse_structured_location(text)
    if structured is not None:
        formatted = format_structured_location(structured)
        return formatted or "not available"

    if "{" in text or "}" in text:
        cleaned = re.sub(r"[{}\\[\\]'\"]+", " ", text)
        cleaned = re.sub(
            r"\b(streetAddress|street_address|address|city|state|province|region|country|zip|postalCode|postal_code|code|name)\b\s*:\s*",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
        if cleaned and not is_numeric_code(cleaned):
            return cleaned
        return "not available"
    return text


def format_place_of_performance(row, export_row):
    city = clean_location_piece(first_value((row, LOCATION_CITY_FIELDS), (export_row, LOCATION_CITY_FIELDS)))
    state = clean_location_piece(first_value((row, LOCATION_STATE_FIELDS), (export_row, LOCATION_STATE_FIELDS)))
    zip_code = safe_text(first_value((row, LOCATION_ZIP_FIELDS), (export_row, LOCATION_ZIP_FIELDS)))

    if city and state:
        return f"{city}, {state}"
    if city or state:
        return city or state
    if zip_code and not is_numeric_code(zip_code):
        return zip_code

    raw_location = first_value((export_row, LOCATION_FIELDS), (row, LOCATION_FIELDS))
    if raw_location:
        return normalize_place(raw_location)
    return ""


def fit_band(value):
    try:
        score = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if score >= 70:
        return "HIGH"
    if score >= 50:
        return "MED"
    return "LOW"


def normalize_set_aside(value):
    text = meaningful_text(value)
    if not text:
        return ""
    lowered = text.lower()
    compact = re.sub(r"[^a-z0-9]+", "", lowered)

    if lowered in {"no", "none"}:
        return "Unrestricted"
    if "unrestrict" in lowered or "full and open" in lowered:
        return "Unrestricted"
    if "service-disabled" in lowered or "service disabled" in lowered or "sdvosb" in lowered:
        return "Service-Disabled Veteran-Owned Small Business"
    if "economically disadvantaged" in lowered or "edwosb" in lowered:
        return "Economically Disadvantaged Woman-Owned Small Business"
    if "woman" in lowered or "women" in lowered or "wosb" in lowered:
        return "Woman-Owned Small Business"
    if "hubzone" in compact:
        return "HUBZone"
    if "8(a)" in lowered or compact == "8a" or "8aprogram" in compact:
        return "8(a)"
    if "total small business" in lowered:
        return "Total Small Business"
    if "small business" in lowered or lowered == "sba":
        return "Small Business Set-Aside"
    return text


def artifact_exists(row, column):
    value = safe_text(row.get(column))
    return bool(value and (BASE_DIR / value).exists())


def infer_macro_stage(row):
    existing = safe_text(row.get("macro_stage"))
    stages = allowed_stages()
    if existing in stages:
        return existing

    action = safe_text(row.get("recommended_next_action")).lower()
    triage = safe_text(row.get("triage_status")).lower()
    sanity = safe_text(row.get("bid_price_sanity_status")).lower()
    operator_status = safe_text(row.get("operator_status")).lower()

    if artifact_exists(row, "manual_review_path") or "manual review" in triage:
        return "Manual Review"
    if "quote" in sanity or "development" in action or artifact_exists(row, "sources_sought_plan_path"):
        return "Development"
    if artifact_exists(row, "decision_report_path") or artifact_exists(row, "compliance_matrix_path"):
        return "AI Review"
    if "pass" in action or "park" in action or operator_status in {"pass", "parked"}:
        return "Archive"
    return "Triage"


def inspect_text_for_disqualifiers(row, export_row):
    rules = read_json(BUSINESS_RULES_PATH, {"hard_disqualifiers": [], "soft_disqualifiers": []})
    chunks = [
        row.get("description"),
        row.get("title"),
        row.get("recommended_next_action"),
        export_row.get("description"),
        export_row.get("short_description"),
        export_row.get("description_enriched"),
    ]
    for column in ["analysis_packet_path", "decision_report_path"]:
        rel = safe_text(row.get(column))
        path = BASE_DIR / rel if rel else None
        if path and path.exists():
            chunks.append(path.read_text(encoding="utf-8", errors="replace")[:20000])
    text = " ".join(safe_text(chunk) for chunk in chunks).lower()
    hard = [term for term in rules.get("hard_disqualifiers", []) if term.lower() in text]
    soft = [term for term in rules.get("soft_disqualifiers", []) if term.lower() in text]
    if hard:
        return "HARD", " | ".join(hard)
    if soft:
        return "SOFT", " | ".join(soft)
    return "NONE", ""


def ensure_state_schema():
    run_local_state_builder_if_needed()
    fieldnames, rows = read_csv(OPPORTUNITY_STATE_PATH)
    if not fieldnames:
        fieldnames = DASHBOARD_COLUMNS[:]
        rows = []

    for column in DASHBOARD_COLUMNS:
        if column not in fieldnames:
            fieldnames.append(column)

    lanes = read_json(NAICS_LANES_PATH, {})
    exports = load_exports_by_notice()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for row in rows:
        notice_id = safe_text(row.get("notice_id"))
        export_row = exports.get(notice_id, {})

        row["source"] = safe_text(row.get("source")) or "SAM.gov"
        row["title"] = safe_text(row.get("title")) or safe_text(export_row.get("title"))
        row["agency"] = safe_text(row.get("agency")) or safe_text(export_row.get("department_ind_agency"))
        row["naics"] = safe_text(row.get("naics")) or safe_text(export_row.get("naics_code"))
        row["lane"] = safe_text(row.get("lane")) or lanes.get(row.get("naics"), "")
        row["set_aside"] = normalize_set_aside(first_value((row, SET_ASIDE_FIELDS), (export_row, SET_ASIDE_FIELDS)))
        row["fit_score"] = safe_text(row.get("fit_score")) or safe_text(export_row.get("fit_score"))
        row["fit_band"] = safe_text(row.get("fit_band")) or fit_band(row.get("fit_score"))
        source_url = first_value((row, SOURCE_URL_FIELDS), (export_row, SOURCE_URL_FIELDS))
        row["source_url"] = safe_text(row.get("source_url")) or source_url
        row["ui_link"] = safe_text(row.get("ui_link")) or row["source_url"]
        row["place_of_performance"] = format_place_of_performance(row, export_row)
        row["description"] = first_description((row, DESCRIPTION_FIELDS), (export_row, DESCRIPTION_FIELDS))

        if not row.get("due_date"):
            due_date, deadline_time, deadline_tz = parse_due_date(
                export_row.get("due_date_user_local")
                or export_row.get("response_deadline")
                or row.get("due_date")
            )
            row["due_date"] = due_date
            row["deadline_time"] = safe_text(row.get("deadline_time")) or deadline_time
            row["deadline_tz"] = safe_text(row.get("deadline_tz")) or deadline_tz

        flag, matches = inspect_text_for_disqualifiers(row, export_row)
        row["disqualifier_flag"] = safe_text(row.get("disqualifier_flag")) or flag
        row["disqualifiers"] = safe_text(row.get("disqualifiers")) or matches
        row["macro_stage"] = infer_macro_stage(row)
        row["last_updated"] = safe_text(row.get("last_updated")) or now

    write_csv_preserve(OPPORTUNITY_STATE_PATH, fieldnames, rows)


def allowed_stages():
    return read_json(STAGE_ENUMS_PATH, {}).get("stages", [])


def allowed_note_types():
    return read_json(STAGE_ENUMS_PATH, {}).get("note_types", [])


def read_state():
    fieldnames, rows = read_csv(OPPORTUNITY_STATE_PATH)
    return fieldnames, rows


def update_state_row(notice_id, updates):
    fieldnames, rows = read_state()
    found = False
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row in rows:
        if safe_text(row.get("notice_id")) == notice_id:
            for key, value in updates.items():
                if key not in fieldnames:
                    fieldnames.append(key)
                row[key] = safe_text(value)
            row["last_updated"] = now
            found = True
            break
    if not found:
        return False
    write_csv_preserve(OPPORTUNITY_STATE_PATH, fieldnames, rows)
    return True


def update_synopsis_for_notice(notice_id, synopsis):
    fieldnames, rows = read_state()
    for column in ["description", "synopsis"]:
        if column not in fieldnames:
            fieldnames.append(column)
    found = False
    for row in rows:
        if safe_text(row.get("notice_id")) == notice_id:
            if not meaningful_text(row.get("description")):
                row["description"] = synopsis
            if not meaningful_text(row.get("synopsis")):
                row["synopsis"] = synopsis
            found = True
            break
    if not found:
        return False
    backup_state_file_for_synopsis()
    write_csv_preserve(OPPORTUNITY_STATE_PATH, fieldnames, rows)
    return True


def append_note(notice_id, note_type, note_text, stage, source="operator"):
    if note_type not in allowed_note_types():
        raise ValueError("Invalid note type")
    ensure_notes_file()
    with OPPORTUNITY_NOTES_PATH.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=NOTE_FIELDS)
        writer.writerow({
            "notice_id": notice_id,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "note_type": note_type,
            "note_text": note_text,
            "stage": stage,
            "source": source,
        })


def notes_by_notice():
    _fields, rows = read_csv(OPPORTUNITY_NOTES_PATH)
    index = {}
    for row in rows:
        index.setdefault(safe_text(row.get("notice_id")), []).append(row)
    for items in index.values():
        items.sort(key=lambda row: row.get("timestamp", ""), reverse=True)
    return index


def relative_existing(path):
    if not path.exists():
        return ""
    return str(path.relative_to(BASE_DIR))


def folder_has_files(folder):
    return folder.exists() and any(path.is_file() for path in folder.rglob("*"))


def path_has_files(path):
    if path.is_file():
        return True
    if path.is_dir():
        return any(item.is_file() for item in path.rglob("*"))
    return False


def row_has_text(row, *fields):
    return any(meaningful_text(row.get(field)) for field in fields)


def local_document_evidence(notice_id):
    folders = [
        DOWNLOADS_DIR / notice_id,
        MANUAL_UPLOADS_DIR / notice_id,
    ]
    return any(path_has_files(folder) for folder in folders)


def local_document_folder_exists(notice_id):
    return (DOWNLOADS_DIR / notice_id).exists() or (MANUAL_UPLOADS_DIR / notice_id).exists()


def report_file_exists(notice_id, *paths):
    for path in paths:
        if path and path.exists():
            return True
    return False


def infer_data_readiness(row):
    notice_id = safe_text(row.get("notice_id"))
    has_docs = local_document_evidence(notice_id)
    has_doc_folder = local_document_folder_exists(notice_id)
    has_description = row_has_text(row, "description", "synopsis", "full_description", "notice_description", "body", "summary")
    has_ai_fields = row_has_text(row, "ai_summary", "requirements", "disqualifiers")

    bid_no_bid = REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_bid_no_bid.md"
    decision = REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_decision_report.md"
    compliance = REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_compliance_matrix.md"
    sources_plan = REPORTS_DIR / "sources_sought" / f"{notice_id}_sources_sought_plan.md"
    manual_review = REPORTS_DIR / "manual_review" / f"{notice_id}_manual_review.md"
    analysis_packet = REPORTS_DIR / "analysis_packets" / f"{notice_id}.md"

    has_ai_outputs = report_file_exists(notice_id, bid_no_bid, decision, compliance)
    if has_ai_fields or has_ai_outputs:
        return {
            "value": "AI Reviewed",
            "style": "green",
            "tag": "reviewed",
            "helper": "AI review outputs exist. Requirements and disqualifiers should be available.",
        }

    if sources_plan.exists():
        return {
            "value": "Response Plan Ready",
            "style": "green",
            "tag": "plan ready",
            "helper": "Sources-sought/RFI response plan exists. Review and prepare next outreach or response step.",
        }

    if manual_review.exists():
        helper = "Manual review is needed before automated processing."
        if not has_docs:
            helper = "Manual review report exists, but no local documents are available yet."
        return {
            "value": "Manual Review Needed",
            "style": "amber",
            "tag": "manual",
            "helper": helper,
        }

    if has_docs:
        return {
            "value": "AI Review Ready",
            "style": "cyan",
            "tag": "AI ready",
            "helper": "Documents are available. Run AI Review to extract requirements and disqualifiers.",
        }

    if analysis_packet.exists() and not has_ai_fields:
        return {
            "value": "AI Review Ready",
            "style": "cyan",
            "tag": "AI ready",
            "helper": "Analysis packet exists — run backfill to populate card fields.",
        }

    if has_doc_folder:
        return {
            "value": "Documents Ready",
            "style": "cyan",
            "tag": "docs ready",
            "helper": "Documents appear to be available. Run AI Review if requirements are not shown.",
        }

    if (safe_text(row.get("source_url")) or safe_text(row.get("ui_link"))) and not has_description:
        return {
            "value": "Documents Needed",
            "style": "amber",
            "tag": "docs needed",
            "helper": "No synopsis or local documents available yet. Use the source link above, Prepare SAM Docs, or upload documents manually before AI review.",
        }

    return {
        "value": "Metadata Only",
        "style": "muted",
        "tag": "metadata",
        "helper": "Only scan metadata is available. Manual lookup may be required.",
    }


def related_links(row):
    notice_id = safe_text(row.get("notice_id"))
    links = []
    for column, label in RELATED_PATH_COLUMNS:
        path = safe_text(row.get(column))
        if path and (BASE_DIR / path).exists():
            links.append({"label": label, "path": path})
    for folder, label in [
        (DOWNLOADS_DIR / notice_id, "downloaded docs folder"),
        (MANUAL_UPLOADS_DIR / notice_id, "manual uploads folder"),
        (AI_DRAFTS_DIR / notice_id, "AI drafts folder"),
    ]:
        rel = relative_existing(folder)
        if rel:
            links.append({"label": label, "path": rel})
    return links


def document_checklist(row):
    notice_id = safe_text(row.get("notice_id"))
    text = " ".join([
        safe_text(row.get("title")),
        safe_text(row.get("description")),
        safe_text(row.get("requirements")),
        safe_text(row.get("analysis_packet_path")),
        safe_text(row.get("pricing_schedule_path")),
        safe_text(row.get("sources_sought_plan_path")),
    ]).lower()
    for folder in [DOWNLOADS_DIR / notice_id, MANUAL_UPLOADS_DIR / notice_id]:
        if folder.exists():
            text += " " + " ".join(path.name.lower() for path in folder.iterdir() if path.is_file())
    checklist = []
    for key, hints in DOCUMENT_HINTS:
        detected = any(hint in text for hint in hints)
        checklist.append({"key": key, "status": "detected" if detected else "not available"})
    return checklist


def enriched_opportunities():
    _fieldnames, rows = read_state()
    notes_index = notes_by_notice()
    for row in rows:
        notice_id = safe_text(row.get("notice_id"))
        row["notes"] = notes_index.get(notice_id, [])
        row["related_links"] = related_links(row)
        row["document_checklist"] = document_checklist(row)
        row["data_readiness"] = infer_data_readiness(row)
    return rows


def safe_relative_path(raw_path):
    text = unquote(safe_text(raw_path))
    if not text or text.startswith("/") or ".." in Path(text).parts:
        return None
    candidate = (BASE_DIR / text).resolve()
    for root in ALLOWED_FILE_ROOTS:
        try:
            candidate.relative_to(root.resolve())
            return candidate
        except ValueError:
            continue
    return None


def sanitize_id(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", safe_text(value)).strip("_")[:120] or "unknown"


def sanitize_filename(value):
    name = Path(safe_text(value)).name
    name = re.sub(r"[^A-Za-z0-9._() -]+", "_", name)
    return name.strip() or "uploaded_file"


def row_for_notice(notice_id):
    _fieldnames, rows = read_state()
    for row in rows:
        if safe_text(row.get("notice_id")) == notice_id:
            return row
    return {}


def local_documents_exist(notice_id):
    return folder_has_files(DOWNLOADS_DIR / notice_id) or folder_has_files(MANUAL_UPLOADS_DIR / notice_id)


def supported_documents(folder):
    folder = Path(folder)
    if not folder.exists():
        return []
    return sorted(
        path for path in folder.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_ANALYSIS_EXTENSIONS
    )


def extract_zip_files(folder):
    folder = Path(folder)
    if not folder.exists():
        return [], ""
    extracted = []
    for zip_path in sorted(folder.rglob("*.zip")):
        target_dir = folder / "extracted"
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, "r") as archive:
                for member in archive.infolist():
                    member_path = Path(member.filename)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        return extracted, f"Unsafe ZIP entry rejected: {member.filename}"
                    target_path = (target_dir / member.filename).resolve()
                    try:
                        target_path.relative_to(target_dir.resolve())
                    except ValueError:
                        return extracted, f"Unsafe ZIP entry rejected: {member.filename}"
                archive.extractall(target_dir)
                extracted.append(str(zip_path.relative_to(BASE_DIR)))
        except zipfile.BadZipFile:
            return extracted, f"Invalid ZIP file: {zip_path.relative_to(BASE_DIR)}"
        except OSError as error:
            return extracted, f"ZIP extraction failed for {zip_path.relative_to(BASE_DIR)}: {error}"
    return extracted, ""


def working_documents_dir(notice_id):
    downloads_folder = DOWNLOADS_DIR / notice_id
    manual_folder = MANUAL_UPLOADS_DIR / notice_id
    if supported_documents(downloads_folder) or folder_has_files(downloads_folder):
        return "downloads", downloads_folder
    return "manual_uploads", manual_folder


def short_output(text, limit=3000):
    text = safe_text(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def run_backend_commands(notice_id, commands):
    summaries = []
    for command in commands:
        result = subprocess.run(
            command,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
        )
        summaries.append({
            "command": " ".join(command),
            "return_code": result.returncode,
            "stdout": short_output(result.stdout),
            "stderr": short_output(result.stderr),
        })
        if result.returncode != 0:
            return False, summaries
    return True, summaries


def refresh_row_after_artifacts(notice_id, stage):
    updates = {
        "macro_stage": stage,
        "last_operator_action": f"Artifacts checked after dashboard action at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    }
    artifact_map = {
        "analysis_packet_path": REPORTS_DIR / "analysis_packets" / f"{notice_id}.md",
        "decision_report_path": REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_decision_report.md",
        "compliance_matrix_path": REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_compliance_matrix.md",
        "pricing_schedule_path": REPORTS_DIR / "pricing" / f"{notice_id}_pricing_schedule.md",
        "pricing_table_path": REPORTS_DIR / "pricing" / f"{notice_id}_pricing_table.csv",
        "bid_price_sanity_path": REPORTS_DIR / "pricing" / f"{notice_id}_bid_price_sanity.md",
        "sources_sought_plan_path": REPORTS_DIR / "sources_sought" / f"{notice_id}_sources_sought_plan.md",
        "manual_review_path": REPORTS_DIR / "manual_review" / f"{notice_id}_manual_review.md",
    }
    for column, path in artifact_map.items():
        rel = relative_existing(path)
        if rel:
            updates[column] = rel
    update_state_row(notice_id, updates)


def action_prepare_docs(notice_id):
    row = row_for_notice(notice_id)
    url = safe_text(row.get("ui_link"))
    if not notice_id or not url:
        return {
            "status": "warning",
            "message": "SAM.gov/source URL missing. Manual lookup or document upload needed.",
        }
    command = [
        sys.executable,
        "src/process_opportunity.py",
        "--notice-id",
        notice_id,
        "--url",
        url,
    ]
    ok, summaries = run_backend_commands(notice_id, [command])
    if ok:
        next_stage = "AI Review" if local_documents_exist(notice_id) else "Intake"
        refresh_row_after_artifacts(notice_id, next_stage)
        append_note(notice_id, "general_note", "Prepare SAM Docs completed from dashboard.", next_stage, "dashboard_action")
        return {"status": "ok", "message": "Prepare SAM Docs completed.", "commands": summaries}
    append_note(notice_id, "risk_note", "Prepare SAM Docs failed; operator review required.", row.get("macro_stage", ""), "dashboard_action")
    return {"status": "warning", "message": "Prepare SAM Docs failed. No pass/failure was applied.", "commands": summaries}


def action_run_ai_review(notice_id):
    row = row_for_notice(notice_id)
    if not local_documents_exist(notice_id):
        return {
            "status": "error",
            "message": "No local documents found. Upload documents first before running AI review.",
        }

    downloads_dir, docs_folder = working_documents_dir(notice_id)
    extracted_zips, extraction_error = extract_zip_files(docs_folder)
    if extraction_error:
        return {
            "status": "error",
            "message": extraction_error,
            "extracted_zips": extracted_zips,
        }
    if not supported_documents(docs_folder):
        return {
            "status": "error",
            "message": "No supported local documents found after ZIP extraction. Upload PDF, DOCX, XLSX, CSV, or TXT files before running AI review.",
            "extracted_zips": extracted_zips,
        }

    commands = [
        [sys.executable, "src/local_document_extractor.py", "--notice-id", notice_id, "--downloads-dir", downloads_dir],
        [sys.executable, "src/bid_no_bid_analyzer.py", "--notice-id", notice_id],
        [sys.executable, "src/solicitation_parser.py", "--notice-id", notice_id],
        [sys.executable, "src/pricing_schedule_extractor.py", "--notice-id", notice_id, "--downloads-dir", downloads_dir],
    ]
    ok, summaries = run_backend_commands(notice_id, commands)
    if ok:
        refresh_row_after_artifacts(notice_id, "Development")
        append_note(notice_id, "general_note", "AI review completed from dashboard; moved to Development.", "Development", "dashboard_action")
        return {"status": "ok", "message": "AI review completed.", "commands": summaries, "extracted_zips": extracted_zips}
    append_note(notice_id, "risk_note", "AI review failed; operator review required.", row.get("macro_stage", ""), "dashboard_action")
    return {"status": "error", "message": "AI review failed. Opportunity was not advanced.", "commands": summaries, "extracted_zips": extracted_zips}


def action_draft_response(notice_id):
    row = row_for_notice(notice_id)
    context = " ".join([
        safe_text(row.get("title")),
        safe_text(row.get("route")),
        safe_text(row.get("triage_status")),
        safe_text(row.get("description")),
    ]).lower()
    if any(term in context for term in ["sources sought", "rfi", "request for information"]):
        command = [sys.executable, "src/sources_sought_planner.py", "--notice-id", notice_id]
        ok, summaries = run_backend_commands(notice_id, [command])
        if ok:
            refresh_row_after_artifacts(notice_id, "Development")
            append_note(notice_id, "general_note", "Sources-sought/RFI draft plan refreshed from dashboard.", "Development", "dashboard_action")
            return {"status": "ok", "message": "Sources-sought/RFI plan refreshed.", "commands": summaries}
        return {"status": "warning", "message": "Draft response action failed.", "commands": summaries}

    draft_dir = AI_DRAFTS_DIR / sanitize_id(notice_id)
    draft_dir.mkdir(parents=True, exist_ok=True)
    draft_path = draft_dir / "README.md"
    draft_path.write_text(
        "\n".join([
            f"# Draft Response Placeholder - {notice_id}",
            "",
            "Full proposal draft generation is not implemented in this MVP.",
            "Use this folder for operator notes, outlines, and later draft artifacts.",
        ]),
        encoding="utf-8",
    )
    rel = str(draft_path.relative_to(BASE_DIR))
    update_state_row(notice_id, {
        "macro_stage": "Development",
        "draft_path": rel,
        "last_operator_action": "Created proposal draft placeholder.",
    })
    append_note(notice_id, "general_note", f"Created draft placeholder at {rel}.", "Development", "dashboard_action")
    return {"status": "ok", "message": "Draft placeholder created.", "draft_path": rel}


def extract_sam_uuid(*values):
    for value in values:
        text = safe_text(value)
        if not text:
            continue
        match = re.search(r"/opp/([A-Za-z0-9]{20,})/view", text)
        if match:
            return match.group(1)
    return ""


def pipe_links(value):
    text = safe_text(value)
    if not text:
        return []
    links = [item.strip() for item in text.split(" | ") if item.strip()]
    links.extend(re.findall(r"https?://[^\s\"'<>]+", text))
    return links


def detail_links_for_synopsis(row, notice_id, sam_uuid):
    links = []
    for field in ["sam_detail_api_links", "sam_detail_raw_links", "sam_detail_raw_resource_links"]:
        links.extend(pipe_links(row.get(field)))
    if sam_uuid:
        links.append(f"https://api.sam.gov/prod/opportunities/v2/search?noticeid={sam_uuid}")
    links.append(f"https://api.sam.gov/prod/opportunities/v2/search?noticeid={notice_id}")
    seen = set()
    usable = []
    for link in links:
        if not link or link in seen:
            continue
        if "noticeid=" not in link.lower() and "noticeId=" not in link:
            continue
        usable.append(link)
        seen.add(link)
    return usable


def fetch_synopsis_from_sam(notice_id):
    try:
        api_key = get_sam_api_key()
    except RuntimeError:
        return {
            "status": "error",
            "message": "SAM API key not configured. Check your .env file.",
        }

    row = row_for_notice(notice_id)
    if not row:
        return {"status": "error", "message": "notice_id not found."}

    source_url = safe_text(row.get("source_url"))
    ui_link = safe_text(row.get("ui_link"))
    sam_uuid = extract_sam_uuid(source_url, ui_link)
    opportunity = dict(row)
    opportunity.update({
        "notice_id": notice_id,
        "noticeId": notice_id,
        "sam_notice_id": notice_id,
        "id": sam_uuid or notice_id,
        "sam_internal_id": sam_uuid,
        "uiLink": source_url or ui_link,
        "source_url": source_url,
        "ui_link": ui_link,
        "sam_detail_api_links": safe_text(row.get("sam_detail_api_links")),
    })

    try:
        with requests.Session() as session:
            synopsis = ""
            description_attempts = []
            if sam_uuid:
                description_attempts.append({
                    **opportunity,
                    "sam_notice_id": sam_uuid,
                    "noticeId": sam_uuid,
                    "id": sam_uuid,
                })
            description_attempts.append(opportunity)

            for attempt in description_attempts:
                synopsis = valid_synopsis_text(fetch_notice_description(session, api_key, attempt))
                if synopsis:
                    break

            if not synopsis:
                for detail_link in detail_links_for_synopsis(row, notice_id, sam_uuid):
                    _response_json, raw_detail, _error = fetch_sam_detail(session, api_key, detail_link)
                    if raw_detail:
                        synopsis = valid_synopsis_text(extract_description_from_detail(raw_detail))
                    if synopsis:
                        break

        if not synopsis:
            return {
                "status": "empty",
                "message": "SAM API returned no synopsis for this notice.",
            }

        if not update_synopsis_for_notice(notice_id, synopsis):
            return {"status": "error", "message": "notice_id not found."}

        return {"status": "ok", "synopsis": synopsis}
    except Exception as error:
        return {"status": "error", "message": safe_text(error)}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "GovConScoutDashboard/0.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self.serve_index()
        if parsed.path == "/api/opportunities":
            return json_response(self, {"opportunities": enriched_opportunities()})
        if parsed.path.startswith("/api/notes/"):
            notice_id = unquote(parsed.path.rsplit("/", 1)[-1])
            return json_response(self, {"notes": notes_by_notice().get(notice_id, [])})
        if parsed.path == "/api/config":
            return json_response(self, {
                "stage_enums": read_json(STAGE_ENUMS_PATH, {}),
                "naics_lanes": read_json(NAICS_LANES_PATH, {}),
                "business_rules": read_json(BUSINESS_RULES_PATH, {}),
            })
        if parsed.path == "/file":
            return self.serve_safe_file(parsed)
        return json_response(self, {"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/stage":
                return self.handle_stage()
            if parsed.path == "/api/note":
                return self.handle_note()
            if parsed.path == "/api/fetch-synopsis":
                return self.handle_fetch_synopsis()
            if parsed.path.startswith("/api/upload/"):
                notice_id = unquote(parsed.path.rsplit("/", 1)[-1])
                return self.handle_upload(notice_id)
            if parsed.path.startswith("/api/action/"):
                action = parsed.path.rsplit("/", 1)[-1]
                return self.handle_action(action)
            return json_response(self, {"error": "Not found"}, 404)
        except ValueError as error:
            return json_response(self, {"error": str(error)}, 400)

    def serve_index(self):
        if not WEB_INDEX_PATH.exists():
            return json_response(self, {"error": "Dashboard HTML not found"}, 500)
        body = WEB_INDEX_PATH.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def handle_stage(self):
        payload = self.read_json_body()
        notice_id = safe_text(payload.get("notice_id"))
        stage = safe_text(payload.get("macro_stage"))
        note_text = safe_text(payload.get("note_text"))
        note_type = safe_text(payload.get("note_type") or "general_note")
        operator_status = safe_text(payload.get("operator_status"))
        last_action = safe_text(payload.get("last_operator_action"))

        if not notice_id:
            raise ValueError("notice_id is required")
        if stage not in allowed_stages():
            raise ValueError("Invalid macro_stage")

        updates = {
            "macro_stage": stage,
            "last_operator_action": last_action or f"Moved to {stage}",
        }
        if operator_status:
            updates["operator_status"] = operator_status
        if not update_state_row(notice_id, updates):
            return json_response(self, {"error": "notice_id not found"}, 404)
        if note_text:
            append_note(notice_id, note_type, note_text, stage, "operator")
        return json_response(self, {"status": "ok", "notice_id": notice_id, "macro_stage": stage})

    def handle_note(self):
        payload = self.read_json_body()
        notice_id = safe_text(payload.get("notice_id"))
        note_type = safe_text(payload.get("note_type"))
        note_text = safe_text(payload.get("note_text"))
        stage = safe_text(payload.get("stage"))
        if not notice_id or not note_text:
            raise ValueError("notice_id and note_text are required")
        append_note(notice_id, note_type, note_text, stage, "operator")
        update_state_row(notice_id, {
            "last_operator_action": f"Saved {note_type} note.",
            "last_call_notes": note_text if note_type == "call_note" else row_for_notice(notice_id).get("last_call_notes", ""),
            "last_call_date": datetime.now().strftime("%Y-%m-%d") if note_type == "call_note" else row_for_notice(notice_id).get("last_call_date", ""),
        })
        return json_response(self, {"status": "ok"})

    def handle_fetch_synopsis(self):
        payload = self.read_json_body()
        notice_id = safe_text(payload.get("notice_id"))
        if not notice_id:
            raise ValueError("notice_id is required")
        return json_response(self, fetch_synopsis_from_sam(notice_id))

    def handle_upload(self, notice_id):
        if not notice_id:
            raise ValueError("notice_id is required")
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("multipart/form-data upload required")
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": content_type,
            },
        )
        upload = form["file"] if "file" in form else None
        if upload is None or not getattr(upload, "filename", ""):
            raise ValueError("file is required")
        folder = MANUAL_UPLOADS_DIR / sanitize_id(notice_id)
        folder.mkdir(parents=True, exist_ok=True)
        filename = sanitize_filename(upload.filename)
        target = folder / filename
        with target.open("wb") as file:
            shutil.copyfileobj(upload.file, file)
        update_state_row(notice_id, {
            "macro_stage": "AI Review",
            "operator_status": "documents_uploaded",
            "last_operator_action": "Manual document upload received; moved to AI Review.",
        })
        append_note(
            notice_id,
            "general_note",
            "Manual document upload received; moved to AI Review.",
            "AI Review",
            "dashboard_upload",
        )
        return json_response(self, {
            "status": "ok",
            "path": str(target.relative_to(BASE_DIR)),
            "macro_stage": "AI Review",
        })

    def handle_action(self, action):
        payload = self.read_json_body()
        notice_id = safe_text(payload.get("notice_id"))
        if not notice_id:
            raise ValueError("notice_id is required")
        if action == "prepare_docs":
            return json_response(self, action_prepare_docs(notice_id))
        if action == "run_ai_review":
            return json_response(self, action_run_ai_review(notice_id))
        if action == "draft_response":
            return json_response(self, action_draft_response(notice_id))
        return json_response(self, {"error": "Unknown action"}, 404)

    def serve_safe_file(self, parsed):
        query = parse_qs(parsed.query)
        raw_path = query.get("path", [""])[0]
        path = safe_relative_path(raw_path)
        if not path or not path.exists():
            return json_response(self, {"error": "File not found or not allowed"}, 404)
        if path.is_dir():
            entries = sorted(item.name for item in path.iterdir())
            return json_response(self, {"path": str(path.relative_to(BASE_DIR)), "entries": entries})
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[dashboard] {self.address_string()} - {format % args}")


def initialize():
    ensure_directories()
    ensure_config_files()
    ensure_notes_file()
    run_local_state_builder_if_needed()
    backup_path = backup_state_file()
    ensure_state_schema()
    write_json(DASHBOARD_STATE_PATH, {
        "last_startup": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "state_backup": backup_path,
    })
    return backup_path


def parse_args():
    parser = argparse.ArgumentParser(description="Run the local GovCon Scout Operator Dashboard.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def main():
    args = parse_args()
    backup_path = initialize()
    if backup_path:
        print(f"Backed up opportunity state: {backup_path}")
    server = ThreadingHTTPServer((HOST, args.port), DashboardHandler)
    print(f"GovCon Scout Operator Dashboard: http://localhost:{args.port}")
    print("This local dashboard does not call SAM.gov or USAspending unless an operator clicks an action that wraps an existing script.")
    server.serve_forever()


if __name__ == "__main__":
    main()
