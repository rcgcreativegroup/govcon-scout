import argparse
import warnings

warnings.filterwarnings("ignore", message="'cgi' is deprecated.*", category=DeprecationWarning)

import ast
import cgi
import csv
import errno
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

from ai_review_generator import apply_ai_review as run_ai_review_apply
from ai_review_generator import run_ai_review as run_structured_ai_review
from detail_enrichment import extract_description_from_detail, fetch_sam_detail
from sam_client import fetch_notice_description, get_sam_api_key

try:
    import pytz
except ImportError:
    pytz = None


# CONFIG / PATH CONSTANTS
BASE_DIR = Path(__file__).resolve().parent.parent
HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DEFAULT_PORT = 8765
SUBPROCESS_TIMEOUT_SECONDS = 120
MAX_JSON_BODY_BYTES = 1_000_000
MAX_UPLOAD_BYTES = 50_000_000

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

DENIED_FILE_NAMES = {
    ".env",
    "auth.json",
    "conversation_history.json",
    "mybidmatch_auth.json",
}
DENIED_FILE_SUFFIXES = {".pyc"}
DENIED_PATH_PARTS = {
    "__pycache__",
    ".browser",
    "data/backups",
}

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
    "watch_list",
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


def redact_sensitive(value):
    text = safe_text(value)
    text = re.sub(r"([?&]api_key=)[^&\s)]+", r"\1[REDACTED]", text)
    text = re.sub(r"SAM-[A-Za-z0-9-]+", "SAM-[REDACTED]", text)
    return text


def boolish(value):
    return safe_text(value).lower() in {"true", "1", "yes", "y"}


def json_response(handler, payload, status=200):
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class RequestTooLarge(ValueError):
    def __init__(self, message, status=413):
        super().__init__(message)
        self.status = status


def invalid_notice_response():
    return {"status": "error", "message": "Invalid notice_id."}


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


def backup_state_file_for_stage_override():
    if not OPPORTUNITY_STATE_PATH.exists():
        return ""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_before_stage_override_{stamp}.csv"
    shutil.copy2(OPPORTUNITY_STATE_PATH, backup_path)
    return str(backup_path.relative_to(BASE_DIR))


def backup_state_file_for_watch_column():
    if not OPPORTUNITY_STATE_PATH.exists():
        return ""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_before_watch_list_column_{stamp}.csv"
    shutil.copy2(OPPORTUNITY_STATE_PATH, backup_path)
    return str(backup_path.relative_to(BASE_DIR))


def backup_state_file_for_watch_toggle():
    if not OPPORTUNITY_STATE_PATH.exists():
        return ""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_before_watch_toggle_{stamp}.csv"
    shutil.copy2(OPPORTUNITY_STATE_PATH, backup_path)
    return str(backup_path.relative_to(BASE_DIR))


def backup_state_file_for_pastdue_archive():
    if not OPPORTUNITY_STATE_PATH.exists():
        return ""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_before_pastdue_archive_{stamp}.csv"
    shutil.copy2(OPPORTUNITY_STATE_PATH, backup_path)
    return str(backup_path.relative_to(BASE_DIR))


def run_local_state_builder_if_needed():
    if OPPORTUNITY_STATE_PATH.exists():
        return
    subprocess.run(
        [sys.executable, "src/opportunity_state.py"],
        cwd=BASE_DIR,
        check=False,
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )


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
    if len(meaningful) < 50:
        return ""
    return text


def extract_detail_description_safe(raw_detail):
    if not isinstance(raw_detail, dict):
        return ""
    return extract_description_from_detail(raw_detail)


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
    """Explicit maintenance helper for repairing dashboard state schema.

    This function can create or rewrite data/opportunity_state.csv. It must not
    be called from dashboard startup; invoke it only from an intentional
    maintenance flow where CSV mutation is expected and reviewed.
    """
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
    fallback = [
        "Triage",
        "Intake",
        "Manual Review",
        "AI Review",
        "Development",
        "Ready to Submit",
        "Execution",
        "Archive",
        "Done",
    ]
    return read_json(STAGE_ENUMS_PATH, {}).get("stages", fallback) or fallback


def set_stage_override(notice_id, stage):
    notice_id = safe_notice_id(notice_id)
    stage = safe_text(stage)
    if not notice_id:
        return invalid_notice_response()
    if stage not in allowed_stages():
        return {"status": "error", "message": "Invalid stage"}

    fieldnames, rows = read_state()
    found = False
    for row in rows:
        if safe_text(row.get("notice_id")) == notice_id:
            row["macro_stage"] = stage
            row["last_operator_action"] = "manual_stage_override"
            found = True
            break
    if not found:
        return {"status": "error", "message": "notice_id not found"}

    backup_path = backup_state_file_for_stage_override()
    tmp_path = OPPORTUNITY_STATE_PATH.with_suffix(".csv.tmp")
    try:
        write_csv_preserve(tmp_path, fieldnames, rows)
        tmp_path.replace(OPPORTUNITY_STATE_PATH)
    except OSError as error:
        if tmp_path.exists():
            tmp_path.unlink()
        return {"status": "error", "message": f"CSV write failed: {error}"}
    return {
        "status": "ok",
        "notice_id": notice_id,
        "stage": stage,
        "backup_path": backup_path,
    }


def set_watch_list(notice_id, watch_value):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return invalid_notice_response()

    fieldnames, rows = read_state()
    schema_backup = ""
    if "watch_list" not in fieldnames:
        schema_backup = backup_state_file_for_watch_column()
        fieldnames.append("watch_list")
        for row in rows:
            row["watch_list"] = ""

    watch_enabled = boolish(watch_value)
    found = False
    for row in rows:
        if safe_text(row.get("notice_id")) == notice_id:
            row["watch_list"] = "true" if watch_enabled else ""
            found = True
            break
    if not found:
        return {"status": "error", "message": "notice_id not found"}

    backup_path = backup_state_file_for_watch_toggle()
    tmp_path = OPPORTUNITY_STATE_PATH.with_suffix(".csv.tmp")
    try:
        write_csv_preserve(tmp_path, fieldnames, rows)
        tmp_path.replace(OPPORTUNITY_STATE_PATH)
    except OSError as error:
        if tmp_path.exists():
            tmp_path.unlink()
        return {"status": "error", "message": f"CSV write failed: {error}"}
    return {
        "status": "ok",
        "notice_id": notice_id,
        "watch_list": watch_enabled,
        "backup_path": backup_path,
        "schema_backup_path": schema_backup,
    }


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
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return False
    folders = [
        DOWNLOADS_DIR / notice_id,
        MANUAL_UPLOADS_DIR / notice_id,
    ]
    return any(path_has_files(folder) for folder in folders)


def local_document_folder_exists(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return False
    return (DOWNLOADS_DIR / notice_id).exists() or (MANUAL_UPLOADS_DIR / notice_id).exists()


def report_file_exists(notice_id, *paths):
    for path in paths:
        if path and path.exists():
            return True
    return False


def infer_data_readiness(row):
    notice_id = safe_notice_id(row.get("notice_id"))
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
    notice_id = safe_notice_id(row.get("notice_id"))
    links = []
    for column, label in RELATED_PATH_COLUMNS:
        path = safe_text(row.get(column))
        if path and (BASE_DIR / path).exists():
            links.append({"label": label, "path": path})
    if not notice_id:
        return links
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
    notice_id = safe_notice_id(row.get("notice_id"))
    text = " ".join([
        safe_text(row.get("title")),
        safe_text(row.get("description")),
        safe_text(row.get("requirements")),
        safe_text(row.get("analysis_packet_path")),
        safe_text(row.get("pricing_schedule_path")),
        safe_text(row.get("sources_sought_plan_path")),
    ]).lower()
    if notice_id:
        for folder in [DOWNLOADS_DIR / notice_id, MANUAL_UPLOADS_DIR / notice_id]:
            if folder.exists():
                text += " " + " ".join(path.name.lower() for path in folder.iterdir() if path.is_file())
    checklist = []
    for key, hints in DOCUMENT_HINTS:
        detected = any(hint in text for hint in hints)
        checklist.append({"key": key, "status": "detected" if detected else "not available"})
    return checklist


def _is_govcon_source(row):
    src = safe_text(row.get("source")).lower()
    return "govcon" in src or "sam" in src


def _ai_review_weight(row):
    status = safe_text(row.get("ai_review_status")).lower()
    if status == "applied":
        return 3
    if status == "proposal_ready":
        return 2
    if status:
        return 1
    return 0


def _data_weight(row):
    important = ["ai_summary", "requirements", "disqualifiers", "synopsis", "description"]
    return sum(1 for f in important if len(safe_text(row.get(f))) > 30)


def _primary_row(group):
    def key(r):
        return (_is_govcon_source(r), _ai_review_weight(r), _data_weight(r))
    return max(group, key=key)


def dedup_rows_by_notice_id(rows):
    from collections import defaultdict
    by_id = defaultdict(list)
    seen_without_id = []
    for row in rows:
        nid = safe_text(row.get("notice_id"))
        if nid:
            by_id[nid].append(row)
        else:
            seen_without_id.append(row)

    result = []
    for nid, group in by_id.items():
        primary = _primary_row(group)
        if len(group) > 1:
            all_sources = sorted(set(safe_text(r.get("source")) for r in group if safe_text(r.get("source"))))
            if len(all_sources) > 1:
                primary["merged_sources"] = ", ".join(all_sources)
                primary["source_count"] = str(len(all_sources))
            else:
                primary["merged_sources"] = all_sources[0] if all_sources else ""
                primary["source_count"] = str(len(group))
        result.append(primary)
    result.extend(seen_without_id)
    return result


def enriched_opportunities():
    _fieldnames, rows = read_state()
    rows = dedup_rows_by_notice_id(rows)
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
    if not text or text.startswith("/") or "\\" in text or ".." in Path(text).parts:
        return None
    candidate = (BASE_DIR / text).resolve()
    for root in ALLOWED_FILE_ROOTS:
        try:
            candidate.relative_to(root.resolve())
            return candidate
        except ValueError:
            continue
    return None


def file_path_denied(path):
    try:
        rel = path.resolve().relative_to(BASE_DIR)
    except ValueError:
        return True
    parts = rel.parts
    rel_posix = rel.as_posix()
    name = path.name
    if name in DENIED_FILE_NAMES or name.startswith(".env."):
        return True
    if path.suffix in DENIED_FILE_SUFFIXES:
        return True
    if "data/backups" in rel_posix:
        return True
    if rel_posix == "data/opportunity_state.csv":
        return True
    if any(part in DENIED_PATH_PARTS for part in parts):
        return True
    if "session" in name.lower() and path.suffix.lower() in {".json", ".sqlite", ".db"}:
        return True
    return False


def safe_notice_id(value):
    text = safe_text(value)
    if not text:
        return ""
    if text.startswith("/") or "\\" in text or "/" in text:
        return ""
    if text in {".", ".."} or ".." in text or ".." in Path(text).parts or ".." in text.split("\\"):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", text):
        return ""
    return text


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
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return False
    return folder_has_files(DOWNLOADS_DIR / notice_id) or folder_has_files(MANUAL_UPLOADS_DIR / notice_id)


def local_reports_exist(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return False
    report_paths = [
        REPORTS_DIR / "analysis_packets" / f"{notice_id}.md",
        REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_bid_no_bid.md",
        REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_decision_report.md",
        REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_compliance_matrix.md",
        REPORTS_DIR / "sources_sought" / f"{notice_id}_sources_sought_plan.md",
        REPORTS_DIR / "manual_review" / f"{notice_id}_manual_review.md",
        REPORTS_DIR / "pricing" / f"{notice_id}_bid_price_sanity.md",
        REPORTS_DIR / "pricing" / f"{notice_id}_pricing_schedule.md",
    ]
    return any(path.exists() for path in report_paths)


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
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return "manual_uploads", MANUAL_UPLOADS_DIR / "unknown"
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
        try:
            result = subprocess.run(
                command,
                cwd=BASE_DIR,
                capture_output=True,
                text=True,
                timeout=SUBPROCESS_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            summaries.append({
                "command": " ".join(command),
                "return_code": "timeout",
                "stdout": short_output(error.stdout),
                "stderr": f"Command timed out after {SUBPROCESS_TIMEOUT_SECONDS} seconds.",
            })
            return False, summaries
        summaries.append({
            "command": " ".join(command),
            "return_code": result.returncode,
            "stdout": short_output(result.stdout),
            "stderr": short_output(result.stderr),
        })
        if result.returncode != 0:
            return False, summaries
    return True, summaries


def parse_backfill_fields(stdout):
    fields = []
    in_fields = False
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped == "Fields filled:":
            in_fields = True
            continue
        if in_fields:
            if not stripped.startswith("- "):
                break
            field = stripped[2:].split(":", 1)[0].strip()
            if field and field != "none":
                fields.append(field)
    return fields


def card_data_for_notice(notice_id):
    row = row_for_notice(notice_id)
    fields = [
        "ai_summary",
        "requirements",
        "disqualifiers",
        "document_status",
        "next_data_step",
        "recommended_next_action",
    ]
    return {field: safe_text(row.get(field)) for field in fields if safe_text(row.get(field))}


def run_card_field_backfill(notice_id, stage, success_prefix):
    if not local_documents_exist(notice_id) and not local_reports_exist(notice_id):
        return {
            "status": "skipped",
            "message": f"{success_prefix} — no local documents or reports found for backfill",
            "fields_filled": [],
            "card_data": {},
        }

    command = [
        sys.executable,
        "src/backfill_opportunity_card_fields.py",
        "--notice-id",
        notice_id,
        "--write",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        error_text = f"Command timed out after {SUBPROCESS_TIMEOUT_SECONDS} seconds."
        summary = {
            "command": " ".join(command),
            "return_code": "timeout",
            "stdout": short_output(error.stdout),
            "stderr": error_text,
        }
        append_note(
            notice_id,
            "risk_note",
            f"Card field backfill timed out after dashboard action: {error_text}",
            stage,
            "dashboard_action",
        )
        return {
            "status": "failed",
            "message": f"{success_prefix} — card field backfill timed out; see logs",
            "fields_filled": [],
            "card_data": {},
            "backfill_error": error_text,
            "summary": summary,
        }
    summary = {
        "command": " ".join(command),
        "return_code": result.returncode,
        "stdout": short_output(result.stdout),
        "stderr": short_output(result.stderr),
    }
    if result.returncode != 0:
        error_text = short_output(result.stderr or result.stdout)
        append_note(
            notice_id,
            "risk_note",
            f"Card field backfill failed after dashboard action: {error_text}",
            stage,
            "dashboard_action",
        )
        return {
            "status": "failed",
            "message": f"{success_prefix} — card field backfill failed; see logs",
            "fields_filled": [],
            "card_data": {},
            "backfill_error": error_text,
            "summary": summary,
        }
    fields_filled = parse_backfill_fields(result.stdout)
    if not fields_filled:
        return {
            "status": "no_fields",
            "message": f"{success_prefix} — no new fields extracted from reports",
            "fields_filled": [],
            "card_data": {},
            "summary": summary,
        }
    visible_fields = [
        field for field in fields_filled
        if field in {"ai_summary", "requirements", "disqualifiers", "document_status", "next_data_step", "artifact_backfill_source"}
    ]
    return {
        "status": "updated",
        "message": f"{success_prefix} — {len(visible_fields)} card fields updated",
        "fields_filled": visible_fields,
        "card_data": card_data_for_notice(notice_id),
        "summary": summary,
    }


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
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return invalid_notice_response()
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
        backfill = run_card_field_backfill(notice_id, next_stage, "Documents prepared") if local_documents_exist(notice_id) or local_reports_exist(notice_id) else {
            "status": "skipped",
            "message": "Documents prepared.",
            "fields_filled": [],
            "card_data": {},
        }
        append_note(notice_id, "general_note", "Prepare SAM Docs completed from dashboard.", next_stage, "dashboard_action")
        message = backfill["message"] if backfill["status"] in {"updated", "no_fields", "failed"} else "Prepare SAM Docs completed."
        return {
            "status": "ok",
            "message": message,
            "commands": summaries,
            "backfill": backfill,
            "fields_filled": backfill.get("fields_filled", []),
            "card_data": backfill.get("card_data", {}),
            "backfill_error": backfill.get("backfill_error", ""),
            "macro_stage": next_stage,
        }
    append_note(notice_id, "risk_note", "Prepare SAM Docs failed; operator review required.", row.get("macro_stage", ""), "dashboard_action")
    return {"status": "warning", "message": "Prepare SAM Docs failed. No pass/failure was applied.", "commands": summaries}


def action_run_ai_review(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return invalid_notice_response()
    if not local_documents_exist(notice_id):
        return {
            "status": "error",
            "message": "No local documents found. Upload documents first before running AI review.",
        }

    _downloads_dir, docs_folder = working_documents_dir(notice_id)
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

    result, _status = run_structured_ai_review(notice_id)
    result["extracted_zips"] = extracted_zips
    return result


def action_extract_documents(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return invalid_notice_response()
    folders = [
        ("downloads", DOWNLOADS_DIR / notice_id),
        ("manual_uploads", MANUAL_UPLOADS_DIR / notice_id),
    ]
    existing_folders = [(label, folder) for label, folder in folders if folder.exists() and folder_has_files(folder)]
    if not existing_folders:
        return {
            "status": "error",
            "message": f"No downloaded or uploaded files found for this notice_id. Place files in downloads/{notice_id}/ first.",
        }

    zip_count = 0
    extracted_zip_count = 0
    errors = []
    skipped_files = []
    commands = []

    for _label, folder in existing_folders:
        zip_files = sorted(folder.rglob("*.zip"))
        zip_count += len(zip_files)
        if zip_files:
            extracted, error = extract_zip_files(folder)
            extracted_zip_count += len(extracted)
            if error:
                errors.append(error)

    if (BASE_DIR / "src/extract_documents.py").exists():
        command = [sys.executable, "src/extract_documents.py", "--notice-id", notice_id]
        ok, summaries = run_backend_commands(notice_id, [command])
        commands.extend(summaries)
    elif (BASE_DIR / "src/local_document_extractor.py").exists():
        ok = True
        for label, folder in existing_folders:
            if not supported_documents(folder):
                continue
            command = [
                sys.executable,
                "src/local_document_extractor.py",
                "--notice-id",
                notice_id,
                "--downloads-dir",
                label,
            ]
            command_ok, summaries = run_backend_commands(notice_id, [command])
            commands.extend(summaries)
            ok = ok and command_ok
    else:
        return {
            "status": "error",
            "message": "No document extraction script found. Expected src/extract_documents.py or src/local_document_extractor.py.",
        }

    extract_folder = REPORTS_DIR / "document_extracts" / notice_id
    source_file_count = sum(len(supported_documents(folder)) for _label, folder in existing_folders)
    extract_file_count = len(list(extract_folder.glob("*.txt"))) if extract_folder.exists() else 0
    for _label, folder in existing_folders:
        for path in sorted(folder.rglob("*")):
            if path.is_file() and path.suffix.lower() not in SUPPORTED_ANALYSIS_EXTENSIONS and path.suffix.lower() != ".zip":
                skipped_files.append(str(path.relative_to(BASE_DIR)))

    if not ok:
        errors.append("Document extraction command failed.")
        status = "error"
    else:
        status = "ok"

    return {
        "status": status,
        "notice_id": notice_id,
        "download_folder": f"downloads/{notice_id}",
        "extract_folder": f"reports/document_extracts/{notice_id}",
        "source_file_count": source_file_count,
        "extract_file_count": extract_file_count,
        "zip_count": zip_count,
        "extracted_zip_count": extracted_zip_count,
        "skipped_files": skipped_files,
        "errors": errors,
        "commands": commands,
    }


def action_draft_response(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return invalid_notice_response()
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

    draft_dir = AI_DRAFTS_DIR / notice_id
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
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return invalid_notice_response()
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
                        synopsis = valid_synopsis_text(extract_detail_description_safe(raw_detail))
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
        return {"status": "error", "message": redact_sensitive(error)}


# ── AI WORKSPACE ─────────────────────────────────────────────────────────────

WORKSPACE_DIR = REPORTS_DIR / "opportunity_workspaces"
REPORT_CONTENT_LIMIT = 3000
TOTAL_CONTEXT_BUDGET = 600000
SYSTEM_PROMPT_RESERVE = 50000
DOCUMENT_BUDGET = TOTAL_CONTEXT_BUDGET - SYSTEM_PROMPT_RESERVE
MIN_FILE_CHARS = 5000

DRAFT_TYPE_TO_FILENAME = {
    "buyer_email": "buyer_email_draft.md",
    "vendor_quote_request": "vendor_quote_request.md",
    "compliance_checklist": "compliance_checklist.md",
    "proposal_outline": "proposal_outline.md",
    "source_sought_response": "source_sought_response.md",
    "general_notes": "workspace_notes.md",
}


class ContextBundle(dict):
    def __init__(self, payload, error=None):
        super().__init__(payload)
        self.error = error

    def __iter__(self):
        yield self
        yield self.error


def document_priority(filename):
    name = filename.lower()
    if "amd" in name or "amendment" in name:
        return 1
    if "qa" in name or "q_a" in name or "q&a" in name:
        return 2
    if "sow" in name or "pws" in name:
        return 3
    if "sol" in name or "solicitation" in name:
        return 4
    if "pricing" in name or "schedule" in name or "clin" in name:
        return 5
    return 6


def truncate_at_paragraph(text, limit):
    if len(text) <= limit:
        return text, False
    if limit <= 0:
        return "", True
    chunk = text[:limit]
    paragraph_break = max(chunk.rfind("\n\n"), chunk.rfind("\r\n\r\n"))
    if paragraph_break > max(500, int(limit * 0.6)):
        return chunk[:paragraph_break].rstrip() + "\n...[truncated]", True
    sentence_breaks = [chunk.rfind(". "), chunk.rfind("? "), chunk.rfind("! "), chunk.rfind("\n")]
    sentence_break = max(sentence_breaks)
    if sentence_break > max(500, int(limit * 0.6)):
        return chunk[:sentence_break + 1].rstrip() + "\n...[truncated]", True
    return chunk.rstrip() + "\n...[truncated]", True


def build_document_extract_bundle(notice_id):
    notice_id = safe_notice_id(notice_id)
    extract_dir = REPORTS_DIR / "document_extracts" / notice_id
    bundle = {
        "document_extract_files": [],
        "document_extract_total_chars": 0,
        "document_extract_budget_used": 0,
        "document_extract_truncated": False,
        "document_extract_files_count": 0,
        "document_extract_files_found": 0,
        "document_extract_available": False,
        "document_extract_content": "",
    }
    if not notice_id:
        return bundle
    if not extract_dir.exists():
        print_context_bundle_summary(notice_id, bundle)
        return bundle

    files = [
        path for path in extract_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".txt", ".md"}
    ]
    bundle["document_extract_files_found"] = len(files)
    files.sort(key=lambda path: (document_priority(path.name), path.stat().st_size, path.name.lower()))

    budget_used = 0
    total_available = 0
    combined = []
    truncated_files = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        chars_available = len(text)
        total_available += chars_available
        priority = document_priority(path.name)
        remaining = DOCUMENT_BUDGET - budget_used
        if chars_available <= MIN_FILE_CHARS:
            included_text = text
            truncated = False
        elif remaining <= 0:
            break
        else:
            included_text, truncated = truncate_at_paragraph(text, remaining)
        chars_included = len(included_text)
        budget_used += chars_included
        record = {
            "filename": path.name,
            "priority": priority,
            "chars_available": chars_available,
            "chars_included": chars_included,
            "truncated": truncated,
            "content": included_text,
        }
        bundle["document_extract_files"].append(record)
        combined.append(f"\n\n--- {path.name} ---\n{included_text}")
        if truncated:
            truncated_files.append(record)
        if budget_used >= DOCUMENT_BUDGET:
            break

    bundle.update({
        "document_extract_total_chars": total_available,
        "document_extract_budget_used": budget_used,
        "document_extract_truncated": bool(truncated_files),
        "document_extract_files_count": len(bundle["document_extract_files"]),
        "document_extract_available": bool(bundle["document_extract_files"]),
        "document_extract_content": "".join(combined).strip(),
    })
    print_context_bundle_summary(notice_id, bundle)
    return bundle


def print_context_bundle_summary(notice_id, bundle):
    files = bundle.get("document_extract_files", [])
    amendments = [
        f"✓ {record['filename']} ({record['chars_included']:,})"
        for record in files
        if record["priority"] == 1
    ]
    print("─────────────────────────────────────────")
    print(f"Context bundle: {notice_id}")
    print(f"  Files found:     {bundle.get('document_extract_files_found', bundle.get('document_extract_files_count', 0))}")
    print(f"  Files included:  {len(files)}")
    print(f"  Total available: {bundle.get('document_extract_total_chars', 0):,} chars")
    print(f"  Budget used:     {bundle.get('document_extract_budget_used', 0):,} chars")
    print(f"  Budget limit:    {DOCUMENT_BUDGET:,} chars")
    print(f"  Truncation:      {'Some files truncated' if bundle.get('document_extract_truncated') else 'None needed'}")
    print(f"  Amendments:      {' '.join(amendments) if amendments else 'None detected'}")
    for record in files:
        if record.get("truncated"):
            print(f"  TRUNCATED: {record['filename']} ({record['chars_available']:,} → {record['chars_included']:,} chars)")
    print("─────────────────────────────────────────")


def build_workspace_context(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return None, "Invalid notice_id."

    _fieldnames, rows = read_state()
    row = {}
    for r in rows:
        if safe_text(r.get("notice_id")) == notice_id:
            row = r
            break
    if not row:
        return None, f"notice_id {notice_id!r} not found in opportunity_state.csv"

    notes = notes_by_notice().get(notice_id, [])
    operator_notes = [
        {
            "timestamp": safe_text(n.get("timestamp")),
            "note_type": safe_text(n.get("note_type")),
            "note_text": safe_text(n.get("note_text")),
            "stage": safe_text(n.get("stage")),
        }
        for n in notes[:20]
    ]

    report_paths = {
        "bid_no_bid": REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_bid_no_bid.md",
        "decision_report": REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_decision_report.md",
        "compliance_matrix": REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_compliance_matrix.md",
        "sources_sought_plan": REPORTS_DIR / "sources_sought" / f"{notice_id}_sources_sought_plan.md",
        "manual_review": REPORTS_DIR / "manual_review" / f"{notice_id}_manual_review.md",
        "pricing_sanity": REPORTS_DIR / "pricing" / f"{notice_id}_bid_price_sanity.md",
    }
    reports_available = {k: v.exists() for k, v in report_paths.items()}
    report_content = {}
    for k, path in report_paths.items():
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="ignore")
            report_content[k] = text[:REPORT_CONTENT_LIMIT] + ("\n...[truncated]" if len(text) > REPORT_CONTENT_LIMIT else "")
        else:
            report_content[k] = ""

    docs = []
    for folder in [DOWNLOADS_DIR / notice_id, MANUAL_UPLOADS_DIR / notice_id]:
        if folder.exists():
            docs.extend(sorted(f.name for f in folder.iterdir() if f.is_file()))

    document_bundle = build_document_extract_bundle(notice_id)

    workspace_path = WORKSPACE_DIR / notice_id
    saved_drafts = []
    if workspace_path.exists():
        saved_drafts = sorted(
            f.name for f in workspace_path.iterdir()
            if f.is_file() and f.name != "conversation_history.json"
        )

    conv_history_path = workspace_path / "conversation_history.json"

    payload = {
        "notice_id": notice_id,
        "title": safe_text(row.get("title")),
        "agency": safe_text(row.get("agency")),
        "due_date": safe_text(row.get("due_date")),
        "deadline_display": (safe_text(row.get("deadline_time", "")) + " " + safe_text(row.get("deadline_tz", ""))).strip(),
        "set_aside": safe_text(row.get("set_aside")),
        "place_of_performance": safe_text(row.get("place_of_performance")),
        "source_url": safe_text(row.get("source_url") or row.get("ui_link")),
        "macro_stage": safe_text(row.get("macro_stage")),
        "operator_status": safe_text(row.get("operator_status")),
        "synopsis": safe_text(row.get("synopsis") or row.get("description")),
        "ai_summary": safe_text(row.get("ai_summary")),
        "requirements": safe_text(row.get("requirements")),
        "disqualifiers": safe_text(row.get("disqualifiers")),
        "document_status": safe_text(row.get("document_status")),
        "next_data_step": safe_text(row.get("next_data_step") or row.get("recommended_next_action")),
        "buyer": {
            "name": safe_text(row.get("buyer_name")),
            "email": safe_text(row.get("buyer_email")),
            "phone": safe_text(row.get("buyer_phone")),
        },
        "operator_notes": operator_notes,
        "reports_available": reports_available,
        "report_content": report_content,
        "documents_available": docs,
        "document_extracts_available": document_bundle["document_extract_available"],
        "document_extract_content": document_bundle["document_extract_content"],
        "document_extract_files": document_bundle["document_extract_files"],
        "document_extract_total_chars": document_bundle["document_extract_total_chars"],
        "document_extract_budget_used": document_bundle["document_extract_budget_used"],
        "document_extract_truncated": document_bundle["document_extract_truncated"],
        "document_extract_files_count": document_bundle["document_extract_files_count"],
        "document_extract_files_found": document_bundle["document_extract_files_found"],
        "document_extract_available": document_bundle["document_extract_available"],
        "conversation_history_available": conv_history_path.exists(),
        "saved_drafts": saved_drafts,
        "context_assembled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return ContextBundle(payload)


def format_context_for_prompt(ctx):
    if pytz:
        now_utc = datetime.now(pytz.utc)
        now_cst = now_utc.astimezone(pytz.timezone("America/Chicago"))
        now_est = now_utc.astimezone(pytz.timezone("America/New_York"))
        date_block = f"""
CURRENT DATE AND TIME:
- Today: {now_cst.strftime('%A, %B %d, %Y')}
- Current time CST: {now_cst.strftime('%I:%M %p %Z')}
- Current time EST: {now_est.strftime('%I:%M %p %Z')}
- UTC: {now_utc.strftime('%Y-%m-%d %H:%M %Z')}

Always use this date when calculating deadlines, days remaining, and urgency.
Never assume or guess the current date.
""".strip()
    else:
        now_local = datetime.now()
        date_block = f"""
CURRENT DATE AND TIME:
- Today: {now_local.strftime('%A, %B %d, %Y')}
- Current local system time: {now_local.strftime('%I:%M %p')}
- Timezone note: pytz is not installed; this is local system time.

Always use this date when calculating deadlines, days remaining, and urgency.
Never assume or guess the current date.
""".strip()

    lines = [
        date_block,
        "",
        f"NOTICE ID: {ctx['notice_id']}",
        f"TITLE: {ctx['title']}",
        f"AGENCY: {ctx['agency']}",
        f"DUE DATE: {ctx['due_date']} {ctx['deadline_display']}".strip(),
        f"SET-ASIDE: {ctx['set_aside']}",
        f"PLACE OF PERFORMANCE: {ctx['place_of_performance']}",
        f"STAGE: {ctx['macro_stage']}",
        f"OPERATOR STATUS: {ctx['operator_status']}",
        f"SOURCE URL: {ctx['source_url']}",
        "",
        "BUYER:",
        f"  Name: {ctx['buyer']['name']}",
        f"  Email: {ctx['buyer']['email']}",
        f"  Phone: {ctx['buyer']['phone']}",
        "",
    ]
    if ctx.get("synopsis"):
        lines += ["SYNOPSIS:", ctx["synopsis"][:2000], ""]
    if ctx.get("ai_summary"):
        lines += ["AI SUMMARY:", ctx["ai_summary"][:2000], ""]
    if ctx.get("requirements"):
        lines += ["REQUIREMENTS:", ctx["requirements"][:2000], ""]
    if ctx.get("disqualifiers"):
        lines += ["DISQUALIFIERS / RISKS:", ctx["disqualifiers"][:2000], ""]
    if ctx.get("next_data_step"):
        lines += ["NEXT DATA STEP:", ctx["next_data_step"], ""]
    for key, label in [
        ("bid_no_bid", "BID/NO-BID REVIEW"),
        ("decision_report", "DECISION REPORT"),
        ("compliance_matrix", "COMPLIANCE MATRIX"),
        ("sources_sought_plan", "SOURCES SOUGHT PLAN"),
        ("manual_review", "MANUAL REVIEW NOTES"),
        ("pricing_sanity", "PRICING SANITY CHECK"),
    ]:
        if ctx["report_content"].get(key):
            lines += [f"--- {label} ---", ctx["report_content"][key], ""]
    if ctx["documents_available"]:
        lines += ["DOWNLOADED DOCUMENTS:", "\n".join(f"  - {d}" for d in ctx["documents_available"]), ""]
    if ctx.get("document_extract_files"):
        lines.append("DOCUMENT EXTRACTS:")
        for record in ctx["document_extract_files"]:
            truncated = "Yes" if record.get("truncated") else "No"
            lines += [
                "========================================",
                f"DOCUMENT: {record.get('filename', '')}",
                f"SIZE: {record.get('chars_included', 0):,} chars | TRUNCATED: {truncated}",
                "========================================",
                record.get("content", ""),
                "",
            ]
    if ctx["operator_notes"]:
        lines.append("OPERATOR NOTES:")
        for note in ctx["operator_notes"][:10]:
            lines.append(f"  [{note['timestamp']}] [{note['note_type']}] {note['note_text']}")
        lines.append("")
    if ctx["saved_drafts"]:
        lines += ["SAVED DRAFTS:", "\n".join(f"  - {d}" for d in ctx["saved_drafts"]), ""]
    return "\n".join(lines)


def load_conversation_history(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return []
    path = WORKSPACE_DIR / notice_id / "conversation_history.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def workspace_history_payload(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return invalid_notice_response()
    path = WORKSPACE_DIR / notice_id / "conversation_history.json"
    if not path.exists():
        return {
            "status": "empty",
            "notice_id": notice_id,
            "history": [],
            "message_count": 0,
            "last_updated": "",
        }
    history = load_conversation_history(notice_id)
    last_updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "status": "ok" if history else "empty",
        "notice_id": notice_id,
        "history": history,
        "message_count": len(history),
        "last_updated": last_updated,
    }


def workspace_sessions_payload():
    titles = {}
    _fieldnames, rows = read_state()
    for row in rows:
        notice_id = safe_text(row.get("notice_id"))
        if notice_id:
            titles[notice_id] = safe_text(row.get("title")) or notice_id

    sessions = []
    if WORKSPACE_DIR.exists():
        for folder in sorted(WORKSPACE_DIR.iterdir()):
            if not folder.is_dir():
                continue
            notice_id = safe_notice_id(folder.name)
            if not notice_id:
                continue
            history_file = folder / "conversation_history.json"
            if not history_file.exists():
                continue
            history = load_conversation_history(notice_id)
            draft_count = sum(
                1 for path in folder.iterdir()
                if path.is_file() and path.name != "conversation_history.json"
            )
            sessions.append({
                "notice_id": notice_id,
                "title": titles.get(notice_id, notice_id),
                "message_count": len(history),
                "draft_count": draft_count,
                "last_updated": datetime.fromtimestamp(history_file.stat().st_mtime).strftime("%Y-%m-%dT%H:%M:%S"),
            })
    sessions.sort(key=lambda item: item.get("last_updated") or "", reverse=True)
    return {"status": "ok", "sessions": sessions}


def post_ai_status_payload(raw_notice_id):
    notice_id = safe_notice_id(raw_notice_id)
    if not notice_id:
        return {"status": "error", "message": "Invalid notice_id."}, 400

    report_paths = {
        "bid_no_bid": REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_bid_no_bid.md",
        "decision_report": REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_decision_report.md",
        "compliance_matrix": REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_compliance_matrix.md",
        "sources_sought_plan": REPORTS_DIR / "sources_sought" / f"{notice_id}_sources_sought_plan.md",
        "bid_price_sanity": REPORTS_DIR / "pricing" / f"{notice_id}_bid_price_sanity.md",
        "ai_review": REPORTS_DIR / "ai_reviews" / f"{notice_id}_ai_review.md",
    }
    reports_found = [label for label, path in report_paths.items() if path.exists()]
    definitely_post_ai = bool(reports_found)
    probably_post_ai = False

    if not definitely_post_ai:
        history_file = WORKSPACE_DIR / notice_id / "conversation_history.json"
        if history_file.exists():
            try:
                history = json.loads(history_file.read_text(encoding="utf-8", errors="ignore"))
                long_assistant = [
                    message for message in history
                    if isinstance(message, dict)
                    and message.get("role") == "assistant"
                    and len(safe_text(message.get("content"))) > 500
                ]
                probably_post_ai = len(history) >= 3 and bool(long_assistant)
            except (json.JSONDecodeError, OSError):
                probably_post_ai = False

    return {
        "status": "ok",
        "notice_id": notice_id,
        "is_post_ai": definitely_post_ai or probably_post_ai,
        "definitely_post_ai": definitely_post_ai,
        "probably_post_ai": probably_post_ai,
        "reports_found": reports_found,
    }, 200


def parse_archive_due_date(value):
    text = safe_text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def auto_archive_pastdue_payload(preview=False):
    fieldnames, rows = read_state()
    today = datetime.now().date()
    archived_count = 0
    protected_flagged = 0
    protected_watch_list = 0
    already_final = 0
    invalid_or_missing_due_date = 0
    eligible_indexes = []

    for index, row in enumerate(rows):
        due = parse_archive_due_date(row.get("due_date"))
        if due is None:
            invalid_or_missing_due_date += 1
            continue
        if due >= today:
            continue
        if boolish(row.get("flagged")):
            protected_flagged += 1
            continue
        if boolish(row.get("watch_list")):
            protected_watch_list += 1
            continue
        if safe_text(row.get("macro_stage")).lower() in {"archive", "done", "pass"}:
            already_final += 1
            continue
        eligible_indexes.append(index)

    backup_path = ""
    if not preview and eligible_indexes:
        backup_path = backup_state_file_for_pastdue_archive()
        for index in eligible_indexes:
            rows[index]["macro_stage"] = "Archive"
            rows[index]["last_operator_action"] = "auto_archived_past_due"
        write_csv_preserve(OPPORTUNITY_STATE_PATH, fieldnames, rows)

    archived_count = len(eligible_indexes)
    return {
        "status": "ok",
        "preview": preview,
        "archived_count": archived_count,
        "protected_flagged": protected_flagged,
        "protected_watch_list": protected_watch_list,
        "already_final": already_final,
        "invalid_or_missing_due_date": invalid_or_missing_due_date,
        "backup_path": backup_path,
    }


def save_conversation_history(notice_id, history):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return False
    ws_path = WORKSPACE_DIR / notice_id
    ws_path.mkdir(parents=True, exist_ok=True)
    (ws_path / "conversation_history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")
    return True


def workspace_chat(notice_id, message):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return invalid_notice_response()
    from dotenv import load_dotenv
    load_dotenv()

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {
            "status": "error",
            "message": "ANTHROPIC_API_KEY is not configured. Add it to your .env file.",
            "notice_id": notice_id,
        }

    ctx, err = build_workspace_context(notice_id)
    if err:
        return {"status": "error", "message": err, "notice_id": notice_id}

    context_text = format_context_for_prompt(ctx)
    system_prompt = (
        "You are a government contracting advisor for Robinson Creative Group / JPTR Enterprises LLC.\n"
        "You are helping the operator work a specific opportunity that has already been moved into a focused execution queue.\n\n"
        "COMPANY CONTEXT:\n"
        "- Company: Robinson Creative Group / JPTR Enterprises LLC\n"
        "- UEI: QJZTCE6MKBG5\n"
        "- CAGE: 20AE1\n"
        "- Set-aside eligibility: Total Small Business, Small Business\n"
        "- Core lanes: Marketing, Advertising, Pest Control through subcontractors, Janitorial through subcontractors, "
        "Security, Transportation, AI Services, Training, Video Production\n"
        "- Approach: Prime where possible, subcontract for licensed trades and specialized labor\n"
        "- Owner: Travis Robinson, Founder and Managing Member\n"
        "- Email: travis@robinsoncreativegroup.com\n\n"
        f"OPPORTUNITY CONTEXT:\n{context_text}\n\n"
        "Your job:\n"
        "- Explain what this opportunity requires.\n"
        "- Identify the next action.\n"
        "- Capture dashboard-ready facts in a structured way: summary, requirements, disqualifiers, missing documents, "
        "site visit, submission method, pricing readiness, vendor/subcontractor needs, prime/team/pass recommendation, "
        "and next action.\n"
        "- Draft buyer emails, vendor quote requests, compliance checklists, proposal outlines, "
        "source-sought responses, and submission materials.\n"
        "- Flag risks, disqualifiers, licensing requirements, past performance issues, site visit requirements, "
        "bonding, insurance, wage determination, and submission traps.\n"
        "- Give direct, actionable advice.\n"
        "- Do not give generic government contracting advice when the opportunity context provides specifics.\n"
        "- If context is missing, say exactly what is missing and what the operator should fetch, upload, or verify next.\n"
        "- When drafting sendable materials, make them ready to use with minimal editing.\n"
        "- Reference the specific opportunity details, buyer name, due date, place of performance, "
        "and submission instructions when available.\n"
        "- Be concise. Lead with the most important information first."
    )

    history = load_conversation_history(notice_id)
    try:
        import anthropic as _anthropic
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        client = _anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=2500,
            system=system_prompt,
            messages=history + [{"role": "user", "content": message}],
        )
        assistant_text = response.content[0].text
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": assistant_text})
        if not save_conversation_history(notice_id, history):
            return invalid_notice_response()
        return {
            "status": "ok",
            "response": assistant_text,
            "notice_id": notice_id,
            "history_count": len(history),
        }
    except Exception as exc:
        print(f"[workspace-chat] Anthropic error for {notice_id}: {exc}")
        msg = str(exc)
        if "401" in msg or "authentication" in msg.lower() or "api_key" in msg.lower():
            return {
                "status": "error",
                "message": "Anthropic API key invalid or expired. Check ANTHROPIC_API_KEY in .env",
                "notice_id": notice_id,
            }
        model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        if "model" in msg.lower() and ("not found" in msg.lower() or "invalid" in msg.lower()):
            return {
                "status": "error",
                "message": f"Configured model '{model}' is not available. Check ANTHROPIC_MODEL in .env",
                "notice_id": notice_id,
            }
        return {
            "status": "error",
            "message": f"AI response failed: {msg[:200]}",
            "notice_id": notice_id,
        }


def workspace_save_draft_file(notice_id, draft_type, content):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return invalid_notice_response()
    if draft_type not in DRAFT_TYPE_TO_FILENAME:
        return {"status": "error", "message": f"Unknown draft_type: {draft_type!r}"}
    filename = DRAFT_TYPE_TO_FILENAME[draft_type]
    ws_path = WORKSPACE_DIR / notice_id
    ws_path.mkdir(parents=True, exist_ok=True)
    file_path = ws_path / filename
    file_path.write_text(content, encoding="utf-8")
    return {"status": "ok", "path": str(file_path.relative_to(BASE_DIR)), "filename": filename}


def workspace_read_draft_file(notice_id, draft_type):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return invalid_notice_response()
    if draft_type not in DRAFT_TYPE_TO_FILENAME:
        return {"status": "error", "message": f"Unknown draft_type: {draft_type!r}"}
    filename = DRAFT_TYPE_TO_FILENAME[draft_type]
    file_path = WORKSPACE_DIR / notice_id / filename
    if not file_path.exists():
        return {"status": "not_found", "message": f"{filename} not found for {notice_id}"}
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    return {"status": "ok", "content": content, "filename": filename, "notice_id": notice_id, "draft_type": draft_type}


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "GovConScoutDashboard/0.1"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self.serve_index()
        if parsed.path == "/api/opportunities":
            return json_response(self, {"opportunities": enriched_opportunities()})
        if parsed.path.startswith("/api/notes/"):
            notice_id = safe_notice_id(unquote(parsed.path.rsplit("/", 1)[-1]))
            if not notice_id:
                return json_response(self, invalid_notice_response(), 400)
            return json_response(self, {"notes": notes_by_notice().get(notice_id, [])})
        if parsed.path == "/api/config":
            return json_response(self, {
                "stage_enums": read_json(STAGE_ENUMS_PATH, {}),
                "naics_lanes": read_json(NAICS_LANES_PATH, {}),
                "business_rules": read_json(BUSINESS_RULES_PATH, {}),
            })
        if parsed.path.startswith("/api/workspace-context/"):
            notice_id = safe_notice_id(unquote(parsed.path.rsplit("/", 1)[-1]))
            if not notice_id:
                return json_response(self, invalid_notice_response(), 400)
            ctx, err = build_workspace_context(notice_id)
            if err:
                return json_response(self, {"error": err}, 404)
            return json_response(self, ctx)
        if parsed.path == "/api/workspace-sessions":
            return json_response(self, workspace_sessions_payload())
        if parsed.path.startswith("/api/workspace-history/"):
            notice_id = safe_notice_id(unquote(parsed.path.rsplit("/", 1)[-1]))
            if not notice_id:
                return json_response(self, invalid_notice_response(), 400)
            return json_response(self, workspace_history_payload(notice_id))
        if parsed.path.startswith("/api/post-ai-status/"):
            notice_id = unquote(parsed.path.rsplit("/", 1)[-1])
            payload, status = post_ai_status_payload(notice_id)
            return json_response(self, payload, status)
        if parsed.path.startswith("/api/workspace-draft/"):
            parts = parsed.path.split("/")
            if len(parts) >= 5:
                notice_id = safe_notice_id(unquote(parts[-2]))
                if not notice_id:
                    return json_response(self, invalid_notice_response(), 400)
                draft_type = unquote(parts[-1])
                return json_response(self, workspace_read_draft_file(notice_id, draft_type))
            return json_response(self, {"error": "Invalid path"}, 400)
        if parsed.path == "/file":
            return self.serve_safe_file(parsed)
        return json_response(self, {"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/stage":
                return self.handle_stage()
            if parsed.path == "/api/set-stage":
                return self.handle_set_stage()
            if parsed.path == "/api/watch":
                return self.handle_watch()
            if parsed.path == "/api/note":
                return self.handle_note()
            if parsed.path == "/api/fetch-synopsis":
                return self.handle_fetch_synopsis()
            if parsed.path == "/api/extract-documents":
                return self.handle_extract_documents()
            if parsed.path == "/api/run-ai-review":
                return self.handle_run_ai_review()
            if parsed.path == "/api/apply-ai-review":
                return self.handle_apply_ai_review()
            if parsed.path == "/api/auto-archive-pastdue":
                return self.handle_auto_archive_pastdue()
            if parsed.path.startswith("/api/upload/"):
                notice_id = unquote(parsed.path.rsplit("/", 1)[-1])
                return self.handle_upload(notice_id)
            if parsed.path.startswith("/api/action/"):
                action = parsed.path.rsplit("/", 1)[-1]
                return self.handle_action(action)
            if parsed.path == "/api/workspace-chat":
                return self.handle_workspace_chat()
            if parsed.path == "/api/workspace-save-draft":
                return self.handle_workspace_save_draft()
            return json_response(self, {"error": "Not found"}, 404)
        except RequestTooLarge as error:
            return json_response(self, {"status": "error", "message": str(error)}, error.status)
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
        if length > MAX_JSON_BODY_BYTES:
            raise RequestTooLarge("Request body too large.")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def handle_stage(self):
        payload = self.read_json_body()
        notice_id = safe_notice_id(payload.get("notice_id"))
        stage = safe_text(payload.get("macro_stage"))
        note_text = safe_text(payload.get("note_text"))
        note_type = safe_text(payload.get("note_type") or "general_note")
        operator_status = safe_text(payload.get("operator_status"))
        last_action = safe_text(payload.get("last_operator_action"))

        if not notice_id:
            return json_response(self, invalid_notice_response(), 400)
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

    def handle_set_stage(self):
        payload = self.read_json_body()
        result = set_stage_override(payload.get("notice_id"), payload.get("stage"))
        if result.get("status") != "ok":
            if result == invalid_notice_response():
                return json_response(self, result, 400)
            return json_response(self, {"error": result.get("message", "Stage override failed")}, 400)
        return json_response(self, result)

    def handle_watch(self):
        payload = self.read_json_body()
        result = set_watch_list(payload.get("notice_id"), payload.get("watch_list"))
        if result.get("status") != "ok":
            if result == invalid_notice_response():
                return json_response(self, result, 400)
            return json_response(self, {"error": result.get("message", "Watch List update failed")}, 400)
        return json_response(self, result)

    def handle_note(self):
        payload = self.read_json_body()
        notice_id = safe_notice_id(payload.get("notice_id"))
        note_type = safe_text(payload.get("note_type"))
        note_text = safe_text(payload.get("note_text"))
        stage = safe_text(payload.get("stage"))
        if not notice_id:
            return json_response(self, invalid_notice_response(), 400)
        if not note_text:
            raise ValueError("note_text is required")
        append_note(notice_id, note_type, note_text, stage, "operator")
        update_state_row(notice_id, {
            "last_operator_action": f"Saved {note_type} note.",
            "last_call_notes": note_text if note_type == "call_note" else row_for_notice(notice_id).get("last_call_notes", ""),
            "last_call_date": datetime.now().strftime("%Y-%m-%d") if note_type == "call_note" else row_for_notice(notice_id).get("last_call_date", ""),
        })
        return json_response(self, {"status": "ok"})

    def handle_fetch_synopsis(self):
        payload = self.read_json_body()
        notice_id = safe_notice_id(payload.get("notice_id"))
        if not notice_id:
            return json_response(self, invalid_notice_response(), 400)
        return json_response(self, fetch_synopsis_from_sam(notice_id))

    def handle_extract_documents(self):
        payload = self.read_json_body()
        notice_id = safe_notice_id(payload.get("notice_id"))
        if not notice_id:
            return json_response(self, invalid_notice_response(), 400)
        result = action_extract_documents(notice_id)
        status_code = 200 if result.get("status") == "ok" else 400
        return json_response(self, result, status_code)

    def handle_run_ai_review(self):
        payload = self.read_json_body()
        notice_id = safe_notice_id(payload.get("notice_id"))
        if not notice_id:
            return json_response(self, invalid_notice_response(), 400)
        if not local_documents_exist(notice_id):
            return json_response(self, {
                "status": "error",
                "message": "No local documents found. Upload documents first before running AI review.",
            }, 400)
        result = action_run_ai_review(notice_id)
        status_code = 200 if result.get("status") == "ok" else 400
        return json_response(self, result, status_code)

    def handle_apply_ai_review(self):
        payload = self.read_json_body()
        notice_id = safe_notice_id(payload.get("notice_id"))
        if not notice_id:
            return json_response(self, invalid_notice_response(), 400)
        result, status_code = run_ai_review_apply(notice_id, payload.get("proposed_update"))
        return json_response(self, result, status_code)

    def handle_upload(self, notice_id):
        if not notice_id:
            raise ValueError("notice_id is required")
        notice_id = safe_notice_id(notice_id)
        if not notice_id:
            return json_response(self, invalid_notice_response(), 400)
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ValueError("multipart/form-data upload required")
        content_length = int(self.headers.get("Content-Length", "0") or 0)
        if content_length > MAX_UPLOAD_BYTES:
            raise RequestTooLarge("Upload too large.")
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
        folder = MANUAL_UPLOADS_DIR / notice_id
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
        notice_id = safe_notice_id(payload.get("notice_id"))
        if not notice_id:
            return json_response(self, invalid_notice_response(), 400)
        if action == "prepare_docs":
            return json_response(self, action_prepare_docs(notice_id))
        if action == "run_ai_review":
            return json_response(self, action_run_ai_review(notice_id))
        if action == "draft_response":
            return json_response(self, action_draft_response(notice_id))
        return json_response(self, {"error": "Unknown action"}, 404)

    def handle_auto_archive_pastdue(self):
        payload = self.read_json_body()
        preview = boolish(payload.get("preview"))
        return json_response(self, auto_archive_pastdue_payload(preview=preview))

    def handle_workspace_chat(self):
        payload = self.read_json_body()
        notice_id = safe_notice_id(payload.get("notice_id"))
        message = safe_text(payload.get("message"))
        if not notice_id:
            return json_response(self, invalid_notice_response(), 400)
        if not message:
            return json_response(self, {"status": "error", "message": "message is required"}, 400)
        return json_response(self, workspace_chat(notice_id, message))

    def handle_workspace_save_draft(self):
        payload = self.read_json_body()
        notice_id = safe_notice_id(payload.get("notice_id"))
        draft_type = safe_text(payload.get("draft_type"))
        content = safe_text(payload.get("content"))
        if not notice_id:
            return json_response(self, invalid_notice_response(), 400)
        if not draft_type:
            return json_response(self, {"status": "error", "message": "draft_type is required"}, 400)
        return json_response(self, workspace_save_draft_file(notice_id, draft_type, content))

    def serve_safe_file(self, parsed):
        query = parse_qs(parsed.query)
        raw_path = query.get("path", [""])[0]
        path = safe_relative_path(raw_path)
        if not path or not path.exists():
            return json_response(self, {"error": "File not found or not allowed"}, 404)
        if path.is_dir():
            return json_response(self, {"error": "Directory listing is not allowed"}, 403)
        if file_path_denied(path):
            return json_response(self, {"error": "File not allowed"}, 403)
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
    """Initialize dashboard process without mutating local workflow data.

    Startup may read CSV/config files or prepare in-memory state only. It must
    not call schema repair, backup, enrichment, backfill, or CSV write helpers;
    explicit operator actions/API requests own all writes.
    """
    return ""


def parse_args():
    parser = argparse.ArgumentParser(description="Run the local GovCon Scout Operator Dashboard.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def port_was_explicitly_requested():
    return any(arg == "--port" or arg.startswith("--port=") for arg in sys.argv[1:])


def create_dashboard_server(port, allow_fallback=True):
    candidate = port
    last_error = None
    for _attempt in range(30 if allow_fallback else 1):
        try:
            server = ThreadingHTTPServer((HOST, candidate), DashboardHandler)
            if candidate != port:
                print(f"Port {port} is already in use; using {candidate} instead.")
            return server, candidate
        except OSError as error:
            if error.errno != errno.EADDRINUSE:
                raise
            last_error = error
            if not allow_fallback:
                break
            candidate += 1
    raise OSError(
        errno.EADDRINUSE,
        f"Port {port} is already in use. Try --port {port + 1} or stop the existing dashboard process.",
    ) from last_error


def main():
    args = parse_args()
    try:
        server, port = create_dashboard_server(
            args.port,
            allow_fallback=not port_was_explicitly_requested(),
        )
    except OSError as error:
        print(f"Could not start GovCon Scout Operator Dashboard: {error}", file=sys.stderr)
        return 1
    initialize()
    print(f"GovCon Scout Operator Dashboard: http://localhost:{port}")
    print("This local dashboard does not call SAM.gov or USAspending unless an operator clicks an action that wraps an existing script.")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
