import csv
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data/opportunity_state.csv"
BACKUP_DIR = BASE_DIR / "data/backups"
REPORTS_DIR = BASE_DIR / "reports"
DOWNLOADS_DIR = BASE_DIR / "downloads"
MANUAL_UPLOADS_DIR = BASE_DIR / "manual_uploads"
AI_REVIEWS_DIR = REPORTS_DIR / "ai_reviews"
SUBPROCESS_TIMEOUT_SECONDS = 120
CONTEXT_CHAR_LIMIT = 180000
FILE_CHAR_LIMIT = 14000
HISTORY_MESSAGE_LIMIT = 20

REVIEW_SCHEMA_KEYS = [
    "notice_id",
    "review_timestamp",
    "ai_summary",
    "improved_synopsis",
    "requirements",
    "disqualifiers",
    "hard_disqualifier_found",
    "hard_disqualifier_summary",
    "documents_found",
    "documents_missing",
    "site_visit_status",
    "submission_status",
    "pricing_status",
    "prime_or_teaming_recommendation",
    "recommended_next_action",
    "operator_questions",
    "place_of_performance",
    "source_basis",
    "confidence",
]

PROTECTED_FIELDS = {
    "flagged",
    "triage_status",
    "operator_status",
    "set_aside",
    "buyer_name",
    "buyer_email",
    "buyer_phone",
    "source_url",
}


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


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


def read_csv_rows(path):
    if not path.exists():
        return [], []
    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def load_state_row(notice_id):
    fieldnames, rows = read_csv_rows(STATE_PATH)
    for row in rows:
        if safe_text(row.get("notice_id")) == notice_id:
            return fieldnames, rows, row
    return fieldnames, rows, {}


def relative(path):
    return str(path.relative_to(BASE_DIR))


def read_text(path, limit=FILE_CHAR_LIMIT):
    if not path.exists() or not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit] + "\n...[truncated]"
    return text


def supported_document_paths(notice_id):
    paths = []
    for folder in [DOWNLOADS_DIR / notice_id, MANUAL_UPLOADS_DIR / notice_id]:
        if folder.exists():
            paths.extend(path for path in sorted(folder.rglob("*")) if path.is_file())
    return paths


def pricing_source_exists(notice_id):
    existing = [
        REPORTS_DIR / "pricing" / f"{notice_id}_pricing_schedule.md",
        REPORTS_DIR / "pricing" / f"{notice_id}_pricing_table.csv",
    ]
    if any(path.exists() for path in existing):
        return True
    pricing_terms = ("price", "pricing", "clin", "schedule", "quote")
    return any(any(term in path.name.lower() for term in pricing_terms) for path in supported_document_paths(notice_id))


def run_command(command):
    try:
        result = subprocess.run(
            command,
            cwd=BASE_DIR,
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_SECONDS,
        )
        return {
            "command": " ".join(command),
            "return_code": result.returncode,
            "stdout": safe_text(result.stdout)[:2000],
            "stderr": safe_text(result.stderr)[:2000],
        }
    except subprocess.TimeoutExpired as error:
        return {
            "command": " ".join(command),
            "return_code": "timeout",
            "stdout": safe_text(error.stdout)[:2000],
            "stderr": f"Command timed out after {SUBPROCESS_TIMEOUT_SECONDS} seconds.",
        }


def working_documents_label(notice_id):
    downloads = DOWNLOADS_DIR / notice_id
    manual = MANUAL_UPLOADS_DIR / notice_id
    if downloads.exists() and any(path.is_file() for path in downloads.rglob("*")):
        return "downloads"
    if manual.exists() and any(path.is_file() for path in manual.rglob("*")):
        return "manual_uploads"
    return "downloads"


def run_deterministic_prerequisites(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return []
    downloads_dir = working_documents_label(notice_id)
    commands = [
        [sys.executable, "src/local_document_extractor.py", "--notice-id", notice_id, "--downloads-dir", downloads_dir],
        [sys.executable, "src/bid_no_bid_analyzer.py", "--notice-id", notice_id],
        [sys.executable, "src/solicitation_parser.py", "--notice-id", notice_id],
    ]
    if pricing_source_exists(notice_id):
        commands.append([
            sys.executable,
            "src/pricing_schedule_extractor.py",
            "--notice-id",
            notice_id,
            "--downloads-dir",
            downloads_dir,
        ])
    if (
        (REPORTS_DIR / "pricing" / f"{notice_id}_pricing_schedule.md").exists()
        or (REPORTS_DIR / "pricing" / f"{notice_id}_pricing_table.csv").exists()
        or (REPORTS_DIR / "market_intel" / f"{notice_id}_usaspending_intel.md").exists()
    ):
        commands.append([sys.executable, "src/bid_price_sanity.py", "--notice-id", notice_id])
    return [run_command(command) for command in commands]


def collect_artifacts(notice_id):
    paths = [
        REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_bid_no_bid.md",
        REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_decision_report.md",
        REPORTS_DIR / "opportunity_reviews" / f"{notice_id}_compliance_matrix.md",
        REPORTS_DIR / "pricing" / f"{notice_id}_pricing_schedule.md",
        REPORTS_DIR / "pricing" / f"{notice_id}_pricing_table.csv",
        REPORTS_DIR / "pricing" / f"{notice_id}_bid_price_sanity.md",
        REPORTS_DIR / "market_intel" / f"{notice_id}_usaspending_intel.md",
        REPORTS_DIR / "analysis_packets" / f"{notice_id}.md",
        REPORTS_DIR / "sources_sought" / f"{notice_id}_sources_sought_plan.md",
    ]
    extract_dir = REPORTS_DIR / "document_extracts" / notice_id
    if extract_dir.exists():
        paths.extend(sorted(path for path in extract_dir.iterdir() if path.is_file() and path.suffix.lower() in {".txt", ".md"}))

    artifacts = []
    for path in paths:
        text = read_text(path)
        if text:
            artifacts.append({"path": relative(path), "content": text})
    return artifacts


def load_workspace_history(notice_id):
    path = REPORTS_DIR / "opportunity_workspaces" / notice_id / "conversation_history.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [
        {
            "role": safe_text(message.get("role")) if isinstance(message, dict) else "",
            "content": safe_text(message.get("content"))[:3000] if isinstance(message, dict) else "",
        }
        for message in data[-HISTORY_MESSAGE_LIMIT:]
    ]


def load_notes(notice_id):
    notes_path = BASE_DIR / "data/opportunity_notes.csv"
    _fields, rows = read_csv_rows(notes_path)
    return [
        {
            "timestamp": safe_text(row.get("timestamp")),
            "note_type": safe_text(row.get("note_type")),
            "stage": safe_text(row.get("stage")),
            "note_text": safe_text(row.get("note_text")),
        }
        for row in rows
        if safe_text(row.get("notice_id")) == notice_id
    ][-20:]


def build_ai_review_context(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return None
    _fieldnames, _rows, row = load_state_row(notice_id)
    if not row:
        return None
    docs = [relative(path) for path in supported_document_paths(notice_id)]
    return {
        "notice_id": notice_id,
        "current_card": row,
        "local_documents": docs,
        "artifacts": collect_artifacts(notice_id),
        "workspace_history": load_workspace_history(notice_id),
        "operator_notes": load_notes(notice_id),
    }


def compact_context(context):
    text = json.dumps(context, indent=2)
    if len(text) > CONTEXT_CHAR_LIMIT:
        return text[:CONTEXT_CHAR_LIMIT] + "\n...[context truncated]"
    return text


def ai_review_system_prompt():
    return (
        "You are a conservative government-contracting document reviewer. "
        "Use extracted documents and deterministic local reports as the source of truth. "
        "Workspace history and operator notes are supplementary only. "
        "Do not invent missing facts. Use language such as appears, requires validation, "
        "potential disqualifier, based on available documents, and operator should confirm. "
        "Return only valid JSON matching the requested schema. No markdown."
    )


def ai_review_user_prompt(context):
    schema = {key: "" for key in REVIEW_SCHEMA_KEYS}
    schema.update({
        "requirements": [],
        "disqualifiers": [],
        "hard_disqualifier_found": False,
        "documents_found": [],
        "documents_missing": [],
        "operator_questions": [],
        "source_basis": {
            "ai_summary": "documents + deterministic reports",
            "requirements": "PWS/extracts/compliance matrix",
            "disqualifiers": "solicitation/compliance matrix",
            "pricing_status": "pricing schedule + bid price sanity",
            "recommended_next_action": "synthesis",
        },
        "confidence": {
            "ai_summary": "high|medium|low",
            "requirements": "high|medium|low",
            "disqualifiers": "high|medium|low",
            "pricing_status": "high|medium|low",
            "recommended_next_action": "high|medium|low",
        },
    })
    return (
        "Build a structured AI Review for this dashboard card. Return valid JSON only.\n\n"
        f"Required schema:\n{json.dumps(schema, indent=2)}\n\n"
        "Rules:\n"
        "- Preserve uncertainty and identify missing documents.\n"
        "- If a hard disqualifier appears, flag it but do not recommend automatic pass/archive.\n"
        "- Requirements and disqualifiers must be arrays of concise bullet-ready strings.\n"
        "- Limit requirements, disqualifiers, documents_found, documents_missing, and operator_questions to the 8 most important items each.\n"
        "- recommended_next_action must be operator-actionable.\n"
        "- place_of_performance: extract from solicitation documents or local reports when available. "
        "Prefer specific human-readable place names over codes. If unknown, return empty string.\n\n"
        f"Context:\n{compact_context(context)}"
    )


def call_claude_for_ai_review(context):
    from dotenv import load_dotenv

    load_dotenv()
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "", {"status": "error", "message": "ANTHROPIC_API_KEY is not configured. Add it to your .env file."}
    try:
        import anthropic
    except ImportError:
        return "", {"status": "error", "message": "anthropic package is not installed."}

    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=model,
            max_tokens=6000,
            system=ai_review_system_prompt(),
            messages=[{"role": "user", "content": ai_review_user_prompt(context)}],
        )
        return response.content[0].text, None
    except Exception as error:
        message = str(error)
        if "401" in message or "authentication" in message.lower() or "api_key" in message.lower():
            message = "Anthropic API key invalid or expired. Check ANTHROPIC_API_KEY in .env."
        elif "model" in message.lower() and ("not found" in message.lower() or "invalid" in message.lower()):
            message = f"Configured model '{model}' is not available. Check ANTHROPIC_MODEL in .env."
        else:
            message = f"AI review failed: {message[:200]}"
        return "", {"status": "error", "message": message}


def parse_ai_review_json(raw_response):
    text = safe_text(raw_response)
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    text = re.sub(r",(\s*[}\]])", r"\1", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        return None, f"AI review JSON parse failed: {error}"
    if not isinstance(parsed, dict):
        return None, "AI review JSON parse failed: top-level value was not an object."
    for key in REVIEW_SCHEMA_KEYS:
        parsed.setdefault(key, [] if key in {"requirements", "disqualifiers", "documents_found", "documents_missing", "operator_questions"} else "")
    return parsed, ""


def normalize_list(value):
    if isinstance(value, list):
        return [safe_text(item) for item in value if safe_text(item)]
    text = safe_text(value)
    if not text:
        return []
    return [item.strip(" -•\t") for item in re.split(r"\n+|\|", text) if item.strip(" -•\t")]


def bullet_text(value):
    return "\n".join(f"- {item}" for item in normalize_list(value))


def build_card_update(review_json, current_row):
    update = {
        "ai_summary": safe_text(review_json.get("ai_summary")),
        "requirements": bullet_text(review_json.get("requirements")),
        "disqualifiers": bullet_text(review_json.get("disqualifiers")),
        "recommended_next_action": safe_text(review_json.get("recommended_next_action")),
        "document_status": bullet_text(review_json.get("documents_found")),
        "next_data_step": safe_text(review_json.get("recommended_next_action")),
        "pricing_status": safe_text(review_json.get("pricing_status")),
        "ai_review_status": "proposal_ready",
        "review_timestamp": safe_text(review_json.get("review_timestamp")) or datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "site_visit_status": safe_text(review_json.get("site_visit_status")),
        "submission_status": safe_text(review_json.get("submission_status")),
        "prime_or_teaming_recommendation": safe_text(review_json.get("prime_or_teaming_recommendation")),
        "hard_disqualifier_found": "true" if review_json.get("hard_disqualifier_found") else "",
        "hard_disqualifier_summary": safe_text(review_json.get("hard_disqualifier_summary")),
        "documents_missing": bullet_text(review_json.get("documents_missing")),
        "operator_questions": bullet_text(review_json.get("operator_questions")),
    }
    pop = safe_text(review_json.get("place_of_performance"))
    if pop and pop.lower() not in {"unknown", ""}:
        update["place_of_performance"] = pop
    improved = safe_text(review_json.get("improved_synopsis"))
    if improved:
        update["synopsis"] = improved
        update["description"] = improved
        original = safe_text(current_row.get("synopsis")) or safe_text(current_row.get("description"))
        if original and not safe_text(current_row.get("synopsis_original")):
            update["synopsis_original"] = original
    return {key: value for key, value in update.items() if safe_text(value)}


def write_ai_review_outputs(notice_id, review_json, proposed_update):
    AI_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    review_path = AI_REVIEWS_DIR / f"{notice_id}_ai_review.md"
    json_path = AI_REVIEWS_DIR / f"{notice_id}_card_update.json"
    review_path.write_text(render_review_markdown(review_json), encoding="utf-8")
    json_path.write_text(json.dumps(proposed_update, indent=2), encoding="utf-8")
    return relative(review_path), relative(json_path)


def write_raw_response(notice_id, raw_response):
    AI_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    raw_path = AI_REVIEWS_DIR / f"{notice_id}_ai_review_raw.txt"
    raw_path.write_text(raw_response, encoding="utf-8")
    return relative(raw_path)


def render_review_markdown(review_json):
    lines = [
        f"# AI Review - {safe_text(review_json.get('notice_id'))}",
        "",
        f"- Review timestamp: {safe_text(review_json.get('review_timestamp'))}",
        f"- Hard disqualifier found: {bool(review_json.get('hard_disqualifier_found'))}",
        f"- Hard disqualifier summary: {safe_text(review_json.get('hard_disqualifier_summary')) or 'Not identified'}",
        "",
        "## Summary",
        safe_text(review_json.get("ai_summary")) or "Not available",
        "",
        "## Improved Synopsis",
        safe_text(review_json.get("improved_synopsis")) or "Not available",
        "",
        "## Requirements",
    ]
    lines.extend(f"- {item}" for item in normalize_list(review_json.get("requirements")) or ["Not available"])
    lines.extend(["", "## Disqualifiers"])
    lines.extend(f"- {item}" for item in normalize_list(review_json.get("disqualifiers")) or ["Not available"])
    lines.extend([
        "",
        "## Operational Status",
        f"- Site visit: {safe_text(review_json.get('site_visit_status')) or 'Not available'}",
        f"- Submission: {safe_text(review_json.get('submission_status')) or 'Not available'}",
        f"- Pricing: {safe_text(review_json.get('pricing_status')) or 'Not available'}",
        f"- Prime/team: {safe_text(review_json.get('prime_or_teaming_recommendation')) or 'Not available'}",
        f"- Next action: {safe_text(review_json.get('recommended_next_action')) or 'Not available'}",
        "",
        "## Documents Found",
    ])
    lines.extend(f"- {item}" for item in normalize_list(review_json.get("documents_found")) or ["Not available"])
    lines.extend(["", "## Documents Missing"])
    lines.extend(f"- {item}" for item in normalize_list(review_json.get("documents_missing")) or ["Not available"])
    lines.extend(["", "## Operator Questions"])
    lines.extend(f"- {item}" for item in normalize_list(review_json.get("operator_questions")) or ["None"])
    return "\n".join(lines).rstrip() + "\n"


def run_ai_review(notice_id):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return {"status": "error", "message": "Invalid notice_id."}, 400
    fieldnames, _rows, row = load_state_row(notice_id)
    if not fieldnames or not row:
        return {"status": "error", "message": "notice_id not found."}, 404

    deterministic = run_deterministic_prerequisites(notice_id)
    context = build_ai_review_context(notice_id)
    if not context:
        return {"status": "error", "message": "Unable to build AI review context.", "deterministic": deterministic}, 400

    raw_response, error = call_claude_for_ai_review(context)
    if error:
        error["deterministic"] = deterministic
        return error, 400

    review_json, parse_error = parse_ai_review_json(raw_response)
    if parse_error:
        raw_path = write_raw_response(notice_id, raw_response)
        return {
            "status": "error",
            "message": parse_error,
            "raw_path": raw_path,
            "deterministic": deterministic,
        }, 400

    review_json["notice_id"] = notice_id
    review_json["review_timestamp"] = safe_text(review_json.get("review_timestamp")) or datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    proposed_update = build_card_update(review_json, row)
    review_path, json_path = write_ai_review_outputs(notice_id, review_json, proposed_update)
    return {
        "status": "ok",
        "notice_id": notice_id,
        "review_path": review_path,
        "json_path": json_path,
        "proposed_update": proposed_update,
        "review": review_json,
        "deterministic": deterministic,
    }, 200


def backup_state_for_ai_review_apply():
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_before_ai_review_apply_{stamp}.csv"
    shutil.copy2(STATE_PATH, backup_path)
    return relative(backup_path)


def apply_ai_review(notice_id, proposed_update):
    notice_id = safe_notice_id(notice_id)
    if not notice_id:
        return {"status": "error", "message": "Invalid notice_id."}, 400
    if not isinstance(proposed_update, dict):
        return {"status": "error", "message": "proposed_update must be an object."}, 400

    fieldnames, rows, current_row = load_state_row(notice_id)
    if not current_row:
        return {"status": "error", "message": "notice_id not found."}, 404

    mapped = build_apply_mapping(proposed_update, current_row)
    if not mapped:
        return {"status": "error", "message": "No mapped AI review fields to apply."}, 400

    backup_path = backup_state_for_ai_review_apply()
    for field in mapped:
        if field not in fieldnames:
            fieldnames.append(field)

    for row in rows:
        if safe_text(row.get("notice_id")) == notice_id:
            row.update(mapped)
            row["ai_review_status"] = "applied"
            row["last_operator_action"] = "ai_review_applied"
            if "ai_review_status" not in fieldnames:
                fieldnames.append("ai_review_status")
            if "last_operator_action" not in fieldnames:
                fieldnames.append("last_operator_action")
            break
    write_csv(STATE_PATH, fieldnames, rows)
    return {
        "status": "ok",
        "notice_id": notice_id,
        "updated_fields": sorted(mapped.keys()),
        "backup_path": backup_path,
        "card_data": mapped,
    }, 200


def build_apply_mapping(proposed_update, current_row):
    allowed = {
        "ai_summary",
        "synopsis",
        "description",
        "requirements",
        "disqualifiers",
        "recommended_next_action",
        "document_status",
        "next_data_step",
        "pricing_status",
        "ai_review_status",
        "review_timestamp",
        "site_visit_status",
        "submission_status",
        "prime_or_teaming_recommendation",
        "hard_disqualifier_found",
        "hard_disqualifier_summary",
        "documents_missing",
        "operator_questions",
        "synopsis_original",
        "place_of_performance",
    }
    mapped = {}
    for key, value in proposed_update.items():
        if key in PROTECTED_FIELDS or key not in allowed:
            continue
        text = safe_text(value)
        if not text:
            continue
        if key == "place_of_performance":
            if text.lower() in {"unknown", ""}:
                continue
            existing = safe_text(current_row.get("place_of_performance"))
            existing_is_weak = (
                not existing
                or existing.lower() in {"unknown", "not available", "name not available"}
                or existing.lower().startswith("location code:")
            )
            if not existing_is_weak and existing == text:
                continue
        mapped[key] = text
    if "synopsis_original" not in mapped:
        original = safe_text(current_row.get("synopsis")) or safe_text(current_row.get("description"))
        if original and not safe_text(current_row.get("synopsis_original")) and (
            mapped.get("synopsis") or mapped.get("description")
        ):
            mapped["synopsis_original"] = original
    return mapped
