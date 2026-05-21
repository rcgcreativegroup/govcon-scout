import argparse
import csv
import re
from pathlib import Path

import openpyxl


DEFAULT_DOWNLOADS_DIR = "downloads"
DEFAULT_OUTPUT_DIR = "reports/pricing"
DEFAULT_CSV_PATH = "exports/govcon_scout_opportunities_latest.csv"


PRICING_FILENAME_HINTS = [
    "pricing",
    "price",
    "clin",
    "schedule",
]


CLIN_HINTS = [
    "clin",
    "contract line item",
    "line item",
    "item no",
    "item number",
]


PRICE_HINTS = [
    "unit price",
    "price",
    "amount",
    "total",
    "extended price",
]


QTY_HINTS = [
    "qty",
    "quantity",
    "estimated quantity",
]


UNIT_HINTS = [
    "unit",
    "u/i",
    "unit of issue",
]


PERIOD_HINTS = [
    "base",
    "option",
    "period",
    "year",
]


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)


def make_safe_name(value):
    text = safe_text(value) or "unknown"
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", text)
    return text.strip("_")[:120] or "unknown"


def load_opportunity_from_csv(notice_id, csv_path):
    path = Path(csv_path)

    if not path.exists():
        return {}

    with open(path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)

        for row in reader:
            if row.get("notice_id") == notice_id:
                return dict(row)

    return {}


def find_pricing_workbooks(notice_id, downloads_dir):
    folder = Path(downloads_dir) / notice_id

    if not folder.exists():
        return []

    workbooks = []

    for path in sorted(folder.glob("*.xlsx")):
        lower = path.name.lower()

        if any(hint in lower for hint in PRICING_FILENAME_HINTS):
            workbooks.append(path)

    if not workbooks:
        workbooks = sorted(folder.glob("*.xlsx"))

    return workbooks


def normalize_cell(value):
    text = safe_text(value)
    text = re.sub(r"\s+", " ", text)
    return text


def row_values(row):
    return [normalize_cell(cell.value) for cell in row]


def row_has_any(row, hints):
    combined = " ".join(row).lower()
    return any(hint in combined for hint in hints)


def detect_header_row(rows):
    best_index = None
    best_score = -1

    for index, row in enumerate(rows):
        lower_cells = [cell.lower() for cell in row]
        combined = " ".join(lower_cells)

        score = 0

        if any(hint in combined for hint in CLIN_HINTS):
            score += 3

        if any(hint in combined for hint in PRICE_HINTS):
            score += 3

        if any(hint in combined for hint in QTY_HINTS):
            score += 2

        if any(hint in combined for hint in UNIT_HINTS):
            score += 1

        if any(hint in combined for hint in PERIOD_HINTS):
            score += 1

        non_empty = len([cell for cell in row if cell])
        if non_empty >= 3:
            score += 1

        if score > best_score:
            best_score = score
            best_index = index

    if best_score <= 0:
        return None

    return best_index


def classify_column(header):
    lower = header.lower()

    if any(hint in lower for hint in CLIN_HINTS):
        return "clin"

    if "description" in lower or "supplies" in lower or "services" in lower:
        return "description"

    if any(hint in lower for hint in QTY_HINTS):
        return "quantity"

    if any(hint in lower for hint in UNIT_HINTS):
        return "unit"

    if "unit price" in lower:
        return "unit_price"

    if "extended" in lower or "amount" in lower or "total" in lower:
        return "total_price"

    if "price" in lower:
        return "price"

    if any(hint in lower for hint in PERIOD_HINTS):
        return "period"

    return ""


def build_column_map(header_row):
    column_map = {}

    for index, header in enumerate(header_row):
        label = classify_column(header)

        if label and label not in column_map:
            column_map[label] = index

    return column_map


def looks_like_pricing_line(row, column_map):
    combined = " ".join(row).lower()

    if not any(cell for cell in row):
        return False

    if "total" in combined and len([cell for cell in row if cell]) <= 2:
        return True

    if column_map.get("clin") is not None:
        clin_value = row[column_map["clin"]]
        if re.search(r"\b\d{4}\b|\b\d{3,}\b|\b[a-z]?\d{3,}", clin_value.lower()):
            return True

    if any(keyword in combined for keyword in ["base year", "option year", "option period", "clin"]):
        return True

    has_price_like = any("$" in cell or re.search(r"\b\d+\.\d{2}\b", cell) for cell in row)
    has_desc = len([cell for cell in row if cell]) >= 3

    return has_price_like and has_desc


def extract_sheet_pricing(sheet):
    rows = []

    for worksheet_row in sheet.iter_rows():
        values = row_values(worksheet_row)

        if any(values):
            rows.append(values)

    if not rows:
        return {
            "sheet_name": sheet.title,
            "header_row": [],
            "column_map": {},
            "pricing_lines": [],
            "raw_rows": [],
        }

    header_index = detect_header_row(rows)

    if header_index is None:
        header_row = []
        column_map = {}
        data_rows = rows
    else:
        header_row = rows[header_index]
        column_map = build_column_map(header_row)
        data_rows = rows[header_index + 1:]

    pricing_lines = []

    for row in data_rows:
        if not looks_like_pricing_line(row, column_map):
            continue

        pricing_lines.append(extract_pricing_line(row, column_map))

    return {
        "sheet_name": sheet.title,
        "header_row": header_row,
        "column_map": column_map,
        "pricing_lines": pricing_lines,
        "raw_rows": rows,
    }


def get_col(row, column_map, key):
    index = column_map.get(key)

    if index is None:
        return ""

    if index >= len(row):
        return ""

    return row[index]


def extract_pricing_line(row, column_map):
    description = get_col(row, column_map, "description")

    if not description:
        non_empty = [cell for cell in row if cell]
        description = " | ".join(non_empty[:4])

    return {
        "period": get_col(row, column_map, "period"),
        "clin": get_col(row, column_map, "clin"),
        "description": description,
        "quantity": get_col(row, column_map, "quantity"),
        "unit": get_col(row, column_map, "unit"),
        "unit_price": get_col(row, column_map, "unit_price") or get_col(row, column_map, "price"),
        "total_price": get_col(row, column_map, "total_price"),
        "raw": " | ".join([cell for cell in row if cell]),
    }


def extract_workbook(path):
    workbook = openpyxl.load_workbook(path, data_only=False)
    sheets = []

    for sheet in workbook.worksheets:
        sheets.append(extract_sheet_pricing(sheet))

    return sheets


def write_csv(notice_id, workbook_path, sheets, output_dir):
    output_path = Path(output_dir) / f"{notice_id}_pricing_table.csv"

    with open(output_path, "w", encoding="utf-8", newline="") as file:
        fieldnames = [
            "notice_id",
            "workbook",
            "sheet",
            "period",
            "clin",
            "description",
            "quantity",
            "unit",
            "unit_price",
            "total_price",
            "raw",
        ]

        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()

        for sheet in sheets:
            for line in sheet["pricing_lines"]:
                writer.writerow({
                    "notice_id": notice_id,
                    "workbook": workbook_path.name,
                    "sheet": sheet["sheet_name"],
                    **line,
                })

    return output_path


def write_markdown(notice_id, opportunity, workbook_path, sheets, output_dir):
    output_path = Path(output_dir) / f"{notice_id}_pricing_schedule.md"

    title = safe_text(opportunity.get("title") or notice_id)
    agency = safe_text(opportunity.get("department_ind_agency"))
    deadline = safe_text(opportunity.get("due_date_user_local") or opportunity.get("response_deadline"))
    fit_score = safe_text(opportunity.get("fit_score"))
    prime_reality = safe_text(opportunity.get("prime_reality_score"))

    total_lines = sum(len(sheet["pricing_lines"]) for sheet in sheets)

    lines = []
    lines.append(f"# Pricing Schedule Extraction — {notice_id}")
    lines.append("")
    lines.append("## Opportunity Summary")
    lines.append("")
    lines.append(f"- **Title:** {title}")
    lines.append(f"- **Agency:** {agency}")
    lines.append(f"- **Deadline:** {deadline}")
    lines.append(f"- **Fit Score:** {fit_score}")
    lines.append(f"- **Prime Reality Score:** {prime_reality}")
    lines.append(f"- **Workbook:** `{workbook_path}`")
    lines.append(f"- **Extracted Pricing Lines:** {total_lines}")
    lines.append("")
    lines.append("## Pricing Readiness")
    lines.append("")

    if total_lines > 0:
        lines.append("- **Status:** Pricing schedule was detected and line items were extracted.")
    else:
        lines.append("- **Status:** Pricing workbook was found, but no confident pricing lines were extracted.")
        lines.append("- **Action:** Manually inspect workbook formatting and update parser rules if needed.")

    lines.append("")
    lines.append("## Bid Pricing Warnings")
    lines.append("")
    lines.append("- Confirm whether prices must include all labor, tools, materials, supplies, supervision, reporting, travel, insurance, and contingency.")
    lines.append("- Confirm whether base year and option year pricing are required.")
    lines.append("- Confirm whether CLIN structure may be changed. Assume no unless solicitation says otherwise.")
    lines.append("- Confirm whether emergency/callback/after-hours work is included in the fixed price.")
    lines.append("- Do not submit until unit prices and totals reconcile with the solicitation instructions.")
    lines.append("")

    for sheet in sheets:
        lines.append(f"## Sheet: {sheet['sheet_name']}")
        lines.append("")

        if sheet["header_row"]:
            lines.append("**Detected Header Row:**")
            lines.append("")
            lines.append("```text")
            lines.append(" | ".join(sheet["header_row"]))
            lines.append("```")
            lines.append("")

        lines.append(f"**Detected Column Map:** `{sheet['column_map']}`")
        lines.append("")
        lines.append("### Extracted Pricing Lines")
        lines.append("")

        if not sheet["pricing_lines"]:
            lines.append("- No confident pricing lines extracted from this sheet.")
            lines.append("")
            continue

        lines.append("| Period | CLIN | Description | Qty | Unit | Unit Price | Total |")
        lines.append("|---|---|---|---:|---|---:|---:|")

        for line in sheet["pricing_lines"]:
            lines.append(
                f"| {line['period']} | {line['clin']} | {line['description'].replace('|', '/')} | "
                f"{line['quantity']} | {line['unit']} | {line['unit_price']} | {line['total_price']} |"
            )

        lines.append("")

    lines.append("## Suggested Next Pricing Steps")
    lines.append("")
    lines.append("1. Open the Excel workbook and verify the extracted CLINs against the original file.")
    lines.append("2. Determine whether each CLIN is monthly, annual, one-time, or event-based.")
    lines.append("3. Build labor assumptions for each CLIN.")
    lines.append("4. Add material/supply assumptions.")
    lines.append("5. Add travel, mobilization, insurance, compliance, and contingency.")
    lines.append("6. Compare proposed pricing to historical awards once USAspending intelligence is added.")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")

    return output_path


def extract_pricing_schedule(notice_id, downloads_dir, output_dir, csv_path):
    notice_id = make_safe_name(notice_id)
    ensure_dir(output_dir)

    workbooks = find_pricing_workbooks(notice_id, downloads_dir)

    if not workbooks:
        print("")
        print(f"No pricing workbooks found for {notice_id}.")
        print(f"Expected folder: {Path(downloads_dir) / notice_id}")
        print("")
        return []

    opportunity = load_opportunity_from_csv(notice_id, csv_path)

    outputs = []

    for workbook_path in workbooks:
        print(f"Extracting pricing workbook: {workbook_path}")

        sheets = extract_workbook(workbook_path)

        markdown_path = write_markdown(
            notice_id=notice_id,
            opportunity=opportunity,
            workbook_path=workbook_path,
            sheets=sheets,
            output_dir=output_dir,
        )

        csv_path_out = write_csv(
            notice_id=notice_id,
            workbook_path=workbook_path,
            sheets=sheets,
            output_dir=output_dir,
        )

        outputs.extend([str(markdown_path), str(csv_path_out)])

        print(f"Pricing markdown: {markdown_path}")
        print(f"Pricing CSV: {csv_path_out}")

    return outputs


def parse_args():
    parser = argparse.ArgumentParser(
        description="Extract pricing/CLIN structure from downloaded GovCon pricing schedules."
    )

    parser.add_argument("--notice-id", required=True)
    parser.add_argument("--downloads-dir", default=DEFAULT_DOWNLOADS_DIR)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--csv", default=DEFAULT_CSV_PATH)

    return parser.parse_args()


def main():
    args = parse_args()

    extract_pricing_schedule(
        notice_id=args.notice_id,
        downloads_dir=args.downloads_dir,
        output_dir=args.output_dir,
        csv_path=args.csv,
    )


if __name__ == "__main__":
    main()