import argparse
import csv
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from detail_enrichment import extract_description_from_detail, fetch_sam_detail
from sam_client import fetch_notice_description, get_sam_api_key


BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data/opportunity_state.csv"
BACKUP_DIR = BASE_DIR / "data/backups"

EXCLUDED_STAGES = {"archive", "done", "pass", "ready to submit", "execution"}
EMPTY_VALUES = {"", "not available", "none", "null", "n/a", "na", "yes", "no", "true", "false"}


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def redact_sensitive(value):
    text = safe_text(value)
    text = re.sub(r"([?&]api_key=)[^&\s)]+", r"\1[REDACTED]", text)
    text = re.sub(r"SAM-[A-Za-z0-9-]+", "SAM-[REDACTED]", text)
    return text


def meaningful_text(value, min_len=50):
    text = safe_text(value)
    if text.lower() in EMPTY_VALUES:
        return ""
    return text if len(text) >= min_len else ""


def valid_synopsis_text(value):
    text = safe_text(value)
    if text.lower() in EMPTY_VALUES:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) >= 50 else ""


def extract_detail_description_safe(raw_detail):
    if not isinstance(raw_detail, dict):
        return ""
    return extract_description_from_detail(raw_detail)


def source_url(row):
    for field in ["source_url", "ui_link", "url", "link", "sam_url", "notice_url"]:
        text = safe_text(row.get(field))
        if text:
            return text
    return ""


def eligible_for_fetch(row):
    return (
        not meaningful_text(row.get("synopsis"))
        and not meaningful_text(row.get("description"))
        and bool(source_url(row))
        and safe_text(row.get("macro_stage")).lower() not in EXCLUDED_STAGES
        and bool(safe_text(row.get("notice_id")))
    )


def read_state():
    with STATE_PATH.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), list(reader)


def write_state(fieldnames, rows):
    tmp_path = STATE_PATH.with_suffix(".csv.tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(STATE_PATH)


def backup_state():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_before_synopsis_batch_{stamp}.csv"
    shutil.copy2(STATE_PATH, backup_path)
    return backup_path


def extract_sam_uuid(*values):
    for value in values:
        match = re.search(r"/opp/([A-Za-z0-9]{20,})/view", safe_text(value))
        if match:
            return match.group(1)
    return ""


def pipe_links(value):
    text = safe_text(value)
    links = [item.strip() for item in text.split(" | ") if item.strip()]
    links.extend(re.findall(r"https?://[^\s\"'<>]+", text))
    seen = set()
    unique = []
    for link in links:
        if link and link not in seen:
            unique.append(link)
            seen.add(link)
    return unique


def detail_links(row, notice_id, sam_uuid):
    links = []
    for field in ["sam_detail_api_links", "sam_detail_raw_links", "sam_detail_raw_resource_links"]:
        links.extend(pipe_links(row.get(field)))
    if sam_uuid:
        links.append(f"https://api.sam.gov/prod/opportunities/v2/search?noticeid={sam_uuid}")
    links.append(f"https://api.sam.gov/prod/opportunities/v2/search?noticeid={notice_id}")
    usable = []
    seen = set()
    for link in links:
        if link in seen:
            continue
        if "noticeid=" in link.lower():
            usable.append(link)
            seen.add(link)
    return usable


def fetch_synopsis(session, api_key, row):
    notice_id = safe_text(row.get("notice_id"))
    sam_uuid = extract_sam_uuid(row.get("source_url"), row.get("ui_link"))
    url = source_url(row)
    opportunity = dict(row)
    opportunity.update({
        "notice_id": notice_id,
        "noticeId": notice_id,
        "sam_notice_id": notice_id,
        "id": sam_uuid or notice_id,
        "sam_internal_id": sam_uuid,
        "uiLink": url,
        "source_url": url,
        "ui_link": safe_text(row.get("ui_link")) or url,
    })

    attempts = []
    if sam_uuid:
        attempts.append({**opportunity, "sam_notice_id": sam_uuid, "noticeId": sam_uuid, "id": sam_uuid})
    attempts.append(opportunity)

    for attempt in attempts:
        text = valid_synopsis_text(fetch_notice_description(session, api_key, attempt))
        if text:
            return text

    for link in detail_links(row, notice_id, sam_uuid):
        _response_json, raw_detail, _error = fetch_sam_detail(session, api_key, link)
        text = valid_synopsis_text(extract_detail_description_safe(raw_detail))
        if text:
            return text
    return ""


def parse_args():
    parser = argparse.ArgumentParser(description="Batch fetch missing SAM.gov synopsis text for local dashboard cards.")
    parser.add_argument("--dry-run", action="store_true", help="Show eligible rows without calling SAM.gov or writing files.")
    parser.add_argument("--write", action="store_true", help="Fetch and write successful synopsis/description values.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum rows to attempt unless --all is used.")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay between SAM.gov calls in seconds.")
    parser.add_argument("--all", action="store_true", help="Attempt all eligible rows.")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.write:
        args.dry_run = True

    fieldnames, rows = read_state()
    for field in ["synopsis", "description"]:
        if field not in fieldnames:
            fieldnames.append(field)

    eligible_indexes = [idx for idx, row in enumerate(rows) if eligible_for_fetch(row)]
    limit = len(eligible_indexes) if args.all else max(0, args.limit)
    selected_indexes = eligible_indexes[:limit]

    print(f"Rows eligible: {len(eligible_indexes)}")
    print(f"Rows selected: {len(selected_indexes)}")

    if args.dry_run:
        for idx in selected_indexes[:20]:
            row = rows[idx]
            print(f"- {safe_text(row.get('notice_id'))}: {safe_text(row.get('title'))[:90]}")
        print("Mode: dry-run; no SAM.gov calls made and no files modified.")
        return 0

    try:
        api_key = get_sam_api_key()
    except RuntimeError as error:
        print(f"ERROR: {redact_sensitive(error)}")
        return 1

    backup_path = backup_state()
    print(f"Backup created: {backup_path.relative_to(BASE_DIR)}")

    attempted = succeeded = empty = errored = skipped = 0
    with requests.Session() as session:
        for position, idx in enumerate(selected_indexes, start=1):
            row = rows[idx]
            notice_id = safe_text(row.get("notice_id"))
            if not eligible_for_fetch(row):
                skipped += 1
                continue
            attempted += 1
            try:
                synopsis = fetch_synopsis(session, api_key, row)
                if synopsis:
                    if not meaningful_text(row.get("synopsis")):
                        row["synopsis"] = synopsis
                    if not meaningful_text(row.get("description")):
                        row["description"] = synopsis
                    succeeded += 1
                    print(f"[{position}/{len(eligible_indexes)}] {notice_id} — ✓ Synopsis fetched ({len(synopsis)} chars)")
                else:
                    empty += 1
                    print(f"[{position}/{len(eligible_indexes)}] {notice_id} — empty")
            except Exception as error:
                errored += 1
                print(f"[{position}/{len(eligible_indexes)}] {notice_id} — ERROR: {redact_sensitive(error)}")
            if position < len(selected_indexes):
                time.sleep(max(0, args.delay))

    if succeeded:
        write_state(fieldnames, rows)
    print("")
    print("Summary:")
    print(f"- rows eligible: {len(eligible_indexes)}")
    print(f"- attempted: {attempted}")
    print(f"- succeeded: {succeeded}")
    print(f"- empty: {empty}")
    print(f"- errored: {errored}")
    print(f"- skipped: {skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
