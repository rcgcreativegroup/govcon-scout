from pathlib import Path


DOCUMENT_EXTENSIONS = {
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


def make_safe_folder_name(value):
    text = safe_text(value) or "unknown"
    allowed = []

    for char in text:
        if char.isalnum() or char in ["-", "_"]:
            allowed.append(char)
        elif char in [" ", ".", "/"]:
            allowed.append("_")

    cleaned = "".join(allowed).strip("_")
    return cleaned[:120] or "unknown"


def get_notice_folder_name(opp):
    return make_safe_folder_name(
        opp.get("notice_id")
        or opp.get("solicitation_number")
        or opp.get("sam_notice_id")
        or "unknown"
    )


def classify_local_file(file_path):
    name = file_path.name
    lower_name = name.lower()
    extension = file_path.suffix.lower()

    likely_type = "Other"

    if "pws" in lower_name or "performance_work" in lower_name or "performance work" in lower_name:
        likely_type = "PWS/SOW"
    elif "sow" in lower_name or "statement_of_work" in lower_name or "statement of work" in lower_name:
        likely_type = "PWS/SOW"
    elif "sf1449" in lower_name or "sf_1449" in lower_name or "sf 1449" in lower_name:
        likely_type = "SF1449"
    elif "sf30" in lower_name or "sf_30" in lower_name or "sf 30" in lower_name:
        likely_type = "SF30 Amendment"
    elif "amendment" in lower_name or "amend" in lower_name:
        likely_type = "Amendment"
    elif "price" in lower_name or "pricing" in lower_name or "clin" in lower_name or "bid schedule" in lower_name:
        likely_type = "Pricing / CLIN Schedule"
    elif "wage" in lower_name or "sca" in lower_name or "wd" in lower_name:
        likely_type = "Wage Determination"
    elif "solicitation" in lower_name or "rfq" in lower_name or "rfp" in lower_name:
        likely_type = "Solicitation / RFQ / RFP"
    elif "q&a" in lower_name or "qa" in lower_name or "questions" in lower_name:
        likely_type = "Questions and Answers"
    elif "past performance" in lower_name or "experience" in lower_name:
        likely_type = "Past Performance / Experience"
    elif extension == ".zip":
        likely_type = "ZIP Attachment Package"

    return {
        "name": name,
        "path": str(file_path),
        "extension": extension,
        "likely_type": likely_type,
        "size_bytes": file_path.stat().st_size if file_path.exists() else 0,
    }


def scan_local_attachment_folder(folder_path):
    folder = Path(folder_path)

    if not folder.exists() or not folder.is_dir():
        return []

    files = []

    for file_path in sorted(folder.iterdir()):
        if not file_path.is_file():
            continue

        if file_path.name.startswith("."):
            continue

        if file_path.suffix.lower() not in DOCUMENT_EXTENSIONS:
            continue

        files.append(classify_local_file(file_path))

    return files


def summarize_local_files(files):
    counts = {
        "pdf": 0,
        "doc": 0,
        "docx": 0,
        "xls": 0,
        "xlsx": 0,
        "csv": 0,
        "zip": 0,
        "txt": 0,
        "other": 0,
    }

    likely_types = set()
    file_lines = []

    for item in files:
        extension = item.get("extension", "").replace(".", "").lower()
        if extension in counts:
            counts[extension] += 1
        else:
            counts["other"] += 1

        likely_type = item.get("likely_type", "Other")
        likely_types.add(likely_type)

        file_lines.append(
            f"{item.get('name')} — {likely_type} — {item.get('size_bytes', 0)} bytes"
        )

    return counts, sorted(likely_types), file_lines


def apply_local_attachment_scan_to_opportunity(opp, downloads_dir="downloads"):
    notice_folder = get_notice_folder_name(opp)
    folder_path = Path(downloads_dir) / notice_folder

    files = scan_local_attachment_folder(folder_path)
    counts, likely_types, file_lines = summarize_local_files(files)

    local_found = "Yes" if files else "No"

    opp["local_attachment_folder"] = str(folder_path)
    opp["local_attachments_found"] = local_found
    opp["local_attachment_count"] = len(files)
    opp["local_attachment_file_list"] = "\n".join(file_lines)
    opp["local_attachment_likely_types"] = ", ".join(likely_types)

    opp["local_pdf_count"] = counts.get("pdf", 0)
    opp["local_doc_count"] = counts.get("doc", 0)
    opp["local_docx_count"] = counts.get("docx", 0)
    opp["local_xls_count"] = counts.get("xls", 0)
    opp["local_xlsx_count"] = counts.get("xlsx", 0)
    opp["local_csv_count"] = counts.get("csv", 0)
    opp["local_zip_count"] = counts.get("zip", 0)
    opp["local_txt_count"] = counts.get("txt", 0)

    likely_type_text = " ".join(likely_types).lower()
    file_name_text = " ".join(item.get("name", "") for item in files).lower()
    combined = f"{likely_type_text} {file_name_text}"

    opp["local_pws_found"] = "Yes" if "pws" in combined or "sow" in combined or "statement of work" in combined else "No"
    opp["local_sf1449_found"] = "Yes" if "sf1449" in combined or "sf 1449" in combined else "No"
    opp["local_sf30_found"] = "Yes" if "sf30" in combined or "sf 30" in combined or "amendment" in combined else "No"
    opp["local_pricing_found"] = "Yes" if "pricing" in combined or "price" in combined or "clin" in combined else "No"
    opp["local_wage_determination_found"] = "Yes" if "wage" in combined or "sca" in combined else "No"
    opp["local_solicitation_found"] = "Yes" if "solicitation" in combined or "rfq" in combined or "rfp" in combined else "No"

    if local_found == "Yes":
        opp["local_attachment_next_action"] = (
            "Local attachments found. Review/classify documents and prepare bid/no-bid analysis. "
            "Confirm PWS/SOW, solicitation form, pricing schedule, amendments, and submission instructions."
        )
    else:
        opp["local_attachment_next_action"] = (
            f"No local files found. Download SAM.gov attachments manually into {folder_path}/, then rerun offline scan."
        )

    key_docs_found = (
        opp["local_pws_found"] == "Yes"
        or opp["local_solicitation_found"] == "Yes"
        or opp["local_sf1449_found"] == "Yes"
    )

    pricing_or_simple = (
        opp["local_pricing_found"] == "Yes"
        or score_value(opp, "local_attachment_count") > 0
    )

    opp["ready_for_bid_no_bid_analysis"] = "Yes" if key_docs_found and pricing_or_simple else "No"

    return opp


def apply_local_attachment_scan(scored_opportunities, downloads_dir="downloads"):
    return [
        apply_local_attachment_scan_to_opportunity(opp, downloads_dir=downloads_dir)
        for opp in scored_opportunities
    ]