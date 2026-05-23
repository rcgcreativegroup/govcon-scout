import argparse
import ast
import csv
import json
import re
import shutil
from collections import Counter
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data/opportunity_state.csv"
BACKUP_DIR = BASE_DIR / "data/backups"

TARGET_FIELDS = [
    "place_of_performance_city",
    "place_of_performance_state",
    "place_of_performance_country",
]

PROTECTED_FIELDS = {
    "macro_stage",
    "triage_status",
    "flagged",
    "recommended_next_action",
    "operator_status",
    "last_operator_action",
    "notes",
    "last_updated",
    "current_stage",
    "manual_review_status",
    "sources_sought_status",
    "processed_status",
    "bid_price_sanity_status",
}

US_COUNTRY_CODES = {"USA", "US"}


def clean(value):
    text = str(value or "").strip()
    if text.lower() in {"", "none", "null", "n/a", "na", "not available"}:
        return ""
    return text


def parse_structured(value):
    text = clean(value)
    if not text or re.fullmatch(r"\d+", text):
        return None
    if "{" not in text and "[" not in text:
        return None
    for candidate in (text, f"[{text}]"):
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            return ast.literal_eval(candidate)
        except (SyntaxError, ValueError, TypeError):
            pass
    return None


def empty_structured(value):
    if value is None:
        return True
    if isinstance(value, dict):
        return not any(not empty_structured(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return not any(not empty_structured(item) for item in value)
    return clean(value) == ""


def scalar(value):
    if empty_structured(value):
        return ""
    if isinstance(value, dict):
        for key in ("name", "code", "city", "state", "province", "region", "country"):
            text = scalar(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = scalar(item)
            if text:
                return text
        return ""
    return clean(value)


def classify_dict(item):
    code = scalar(item.get("code")) if isinstance(item, dict) else ""
    name = scalar(item.get("name")) if isinstance(item, dict) else ""
    if not code and not name:
        return {}
    if re.fullmatch(r"\d+", code) and name:
        return {"city": name}
    if re.fullmatch(r"[A-Z]{2}", code):
        return {"state": name or code}
    if re.fullmatch(r"[A-Z]{3}", code):
        return {"country": name or code}
    if name and not code:
        return {"city": name}
    return {}


def extract_location_parts(value):
    structured = parse_structured(value)
    if structured is None or empty_structured(structured):
        return {}
    parts = {"city": "", "state": "", "country": ""}
    if isinstance(structured, dict):
        city = scalar(structured.get("city"))
        state = scalar(structured.get("state")) or scalar(structured.get("province")) or scalar(structured.get("region"))
        country = scalar(structured.get("country"))
        if city:
            parts["city"] = city
        if state:
            parts["state"] = state
        if country:
            parts["country"] = country
        if not any(parts.values()):
            parts.update(classify_dict(structured))
    elif isinstance(structured, (list, tuple)):
        for item in structured:
            if not isinstance(item, dict):
                continue
            classified = classify_dict(item)
            for key, value in classified.items():
                if value and not parts[key]:
                    parts[key] = value
    if parts["country"].upper() in US_COUNTRY_CODES:
        parts["country"] = "UNITED STATES"
    return {key: value for key, value in parts.items() if value}


def read_state():
    with STATE_PATH.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), list(reader)


def backup_state():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_before_pop_parse_{timestamp}.csv"
    shutil.copy2(STATE_PATH, backup_path)
    return backup_path


def run(write=False):
    headers, rows = read_state()
    for field in TARGET_FIELDS:
        if field not in headers:
            headers.append(field)
            for row in rows:
                row[field] = ""

    updated = []
    field_counts = Counter()
    skipped_existing = 0
    skipped_unparseable = 0

    for row in rows:
        if any(clean(row.get(field)) for field in TARGET_FIELDS):
            skipped_existing += 1
            continue
        raw = clean(row.get("place_of_performance"))
        parts = extract_location_parts(raw)
        if not parts:
            skipped_unparseable += 1
            continue
        before = {field: row.get(field, "") for field in ["notice_id", "title", "place_of_performance", *TARGET_FIELDS]}
        for target, source in [
            ("place_of_performance_city", "city"),
            ("place_of_performance_state", "state"),
            ("place_of_performance_country", "country"),
        ]:
            if parts.get(source) and not clean(row.get(target)):
                row[target] = parts[source]
                field_counts[target] += 1
        after = {field: row.get(field, "") for field in ["notice_id", "title", "place_of_performance", *TARGET_FIELDS]}
        updated.append((before, after))

    backup_path = None
    if write and updated:
        backup_path = backup_state()
        with STATE_PATH.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=headers, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    return {
        "mode": "write" if write else "dry-run",
        "rows_inspected": len(rows),
        "rows_updated": len(updated),
        "field_counts": dict(field_counts),
        "examples": updated[:5],
        "skipped_existing": skipped_existing,
        "skipped_unparseable": skipped_unparseable,
        "backup_path": str(backup_path) if backup_path else "",
    }


def print_summary(summary):
    print(f"Mode: {summary['mode']}")
    print(f"Rows inspected: {summary['rows_inspected']}")
    print(f"Rows that would be updated: {summary['rows_updated']}" if summary["mode"] == "dry-run" else f"Rows updated: {summary['rows_updated']}")
    print("Fields that would be filled:" if summary["mode"] == "dry-run" else "Fields filled:")
    if summary["field_counts"]:
        for field, count in sorted(summary["field_counts"].items()):
            print(f"- {field}: {count}")
    else:
        print("- none")
    print(f"Rows skipped because city/state already existed: {summary['skipped_existing']}")
    print(f"Rows skipped because place_of_performance was numeric-only or unparseable: {summary['skipped_unparseable']}")
    if summary["backup_path"]:
        print(f"Backup created: {summary['backup_path']}")
    print("Example before/after rows:")
    if not summary["examples"]:
        print("- none")
    for before, after in summary["examples"]:
        print(f"- {before.get('notice_id') or 'no notice_id'}")
        print(f"  before: city={before.get('place_of_performance_city', '')!r}, state={before.get('place_of_performance_state', '')!r}, country={before.get('place_of_performance_country', '')!r}, raw={before.get('place_of_performance', '')!r}")
        print(f"  after: city={after.get('place_of_performance_city', '')!r}, state={after.get('place_of_performance_state', '')!r}, country={after.get('place_of_performance_country', '')!r}")


def main():
    parser = argparse.ArgumentParser(description="Parse structured place-of-performance values into city/state/country fields.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")
    mode.add_argument("--write", action="store_true", help="Write parsed fields to opportunity_state.csv.")
    args = parser.parse_args()
    print_summary(run(write=args.write))


if __name__ == "__main__":
    main()
