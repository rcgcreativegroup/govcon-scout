"""
MyBidMatch offline importer — parse saved email text, daily page HTML, and
article HTML files without requiring a live browser session or auth cookies.

Usage:
  python src/mybidmatch_importer.py --email-file "data/mybidmatch/raw/email.txt"
  python src/mybidmatch_importer.py --daily-html  data/mybidmatch/raw/daily.html
  python src/mybidmatch_importer.py --article-file data/mybidmatch/raw/article.html
  python src/mybidmatch_importer.py --html-dir     data/mybidmatch/raw/

All modes produce the same output as the browser intake:
  data/mybidmatch_browser_leads.csv
  data/mybidmatch_sam_queue.csv
  reports/mybidmatch/mybidmatch_browser_leads.md
  reports/mybidmatch/mybidmatch_followup_queue.md
"""

import argparse
import html as html_lib
import re
import sys
from datetime import datetime
from pathlib import Path

# Shared utilities from browser intake (Playwright is imported there but
# never called from this module — all parsing here is pure Python).
_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from mybidmatch_browser_intake import (
    # Regex patterns
    SAM_URL_RE, SAM_REF_RE, DATE_RE, NAICS_RE, DUE_RE, SET_ASIDE_RE,
    POC_EMAIL_RE, SOL_LABELED_RE, SOL_PATTERN_RE,
    # Fingerprints / warning phrases
    THIRD_PARTY_DOMAINS, WARNING_PHRASES, SOURCES_SOUGHT_TERMS,
    # Output paths and column lists
    DEFAULT_OUTPUT_CSV, DEFAULT_SAM_QUEUE, DEFAULT_REPORT, DEFAULT_FOLLOWUP,
    LEADS_COLUMNS, SAM_QUEUE_COLUMNS,
    # Instructions for Local Capture section
    LOCAL_CAPTURE_INSTRUCTIONS,
    # Helpers
    safe_text, load_existing_govcon_ids,
    # Article-level extraction
    extract_sam_url, extract_source_url, detect_source_type,
    detect_origin_trace, extract_article_fields,
    # Classification / dedup / output
    classify_lead, govcon_status, dedupe_leads,
    write_csv, build_leads_report, build_followup_report,
)


# ─── HTML utilities ───────────────────────────────────────────────────────────

def _tables_raw(html):
    """Return list of raw HTML strings for each <table> block (non-nested)."""
    return re.findall(r'<table[^>]*>(.*?)</table>', html, re.DOTALL | re.IGNORECASE)


def _rows_raw(table_html):
    return re.findall(r'<tr[^>]*>(.*?)</tr>', table_html, re.DOTALL | re.IGNORECASE)


def _parse_cell(cell_html):
    href_m = re.search(r'<a[^>]+href=["\']([^"\']+)["\']', cell_html, re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', cell_html)
    text = html_lib.unescape(re.sub(r'\s+', ' ', text).strip())
    return {"text": text, "href": href_m.group(1) if href_m else ""}


def _parse_row(row_html):
    return [
        _parse_cell(m.group(1))
        for m in re.finditer(
            r'<(?:td|th)[^>]*>(.*?)</(?:td|th)>', row_html,
            re.DOTALL | re.IGNORECASE,
        )
    ]


def find_article_table(html):
    """
    Locate the MyBidMatch article table ( # / Source / Agency / FSG / Title / Keywords ).
    Returns (rows_raw_list, header_texts_list) or ([], []) if not found.
    """
    for tbl_html in _tables_raw(html):
        rows = _rows_raw(tbl_html)
        if not rows:
            continue
        header_cells = _parse_row(rows[0])
        htexts = [c["text"].lower().strip() for c in header_cells]
        if sum(1 for h in htexts if h in ("source", "agency", "fsg", "title", "keywords")) >= 2:
            return rows, htexts
    return [], []


def table_rows_to_leads(rows, headers, date_text):
    """Convert parsed table rows into a list of partial lead dicts."""
    col = {}
    for i, h in enumerate(headers):
        col[h.strip().lstrip("#").strip()] = i

    num_i    = col.get("", col.get("num", 0))
    src_i    = col.get("source", 1)
    agency_i = col.get("agency", 2)
    fsg_i    = col.get("fsg", 3)
    title_i  = col.get("title", 4)
    kw_i     = col.get("keywords", 5)

    def ct(cells, i):
        return safe_text(cells[i]["text"]) if i < len(cells) else ""

    def ch(cells, i):
        return safe_text(cells[i]["href"]) if i < len(cells) else ""

    leads = []
    for row_html in rows[1:]:  # skip header row
        cells = _parse_row(row_html)
        title = ct(cells, title_i)
        if not title:
            continue
        art_url = ch(cells, title_i) or next(
            (c["href"] for c in cells
             if c.get("href") and "outreachsystems" in c["href"].lower()),
            "",
        )
        leads.append({
            "date_text":   date_text,
            "row_number":  ct(cells, num_i),
            "source":      ct(cells, src_i),
            "agency":      ct(cells, agency_i),
            "fsg":         ct(cells, fsg_i),
            "title":       title,
            "keywords":    ct(cells, kw_i),
            "article_url": art_url,
        })
    return leads


def find_all_links(html):
    """Return list of {text, href} for every <a> tag."""
    links = []
    for m in re.finditer(
        r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        href = m.group(1).strip()
        text = html_lib.unescape(
            re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', m.group(2))).strip()
        )
        if href:
            links.append({"text": text, "href": href})
    return links


def html_to_text(html_content):
    """Minimal HTML → plain-text conversion (no external deps)."""
    text = re.sub(
        r'<(?:script|style)[^>]*>.*?</(?:script|style)>', '',
        html_content, flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r'<(?:br|p|div|tr|li|h[1-6])[^>]*/?\s*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'</(?:p|div|tr|li|h[1-6])>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html_lib.unescape(text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def read_file(path):
    p = Path(path)
    if not p.exists():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


# ─── Article content parser (shared by all modes) ────────────────────────────

def parse_article_content(content, is_html=True):
    """
    Parse article detail content (HTML or plain text).
    Returns a partial lead dict: sam_url, source_url, source_type,
    origin_trace_status, has_registration_warning, is_sources_sought,
    solicitation_number, due_date, naics, set_aside, poc_email, raw_text_excerpt.
    """
    if is_html:
        full_text = html_to_text(content)
        hrefs = [l["href"] for l in find_all_links(content)]
    else:
        full_text = content
        hrefs = SAM_URL_RE.findall(content)
        hrefs += re.findall(r'https?://[^\s"\'<>]+', content)

    combined = full_text + " " + content
    sam_url    = extract_sam_url(combined, hrefs)
    source_url = extract_source_url(combined, hrefs)
    text_lower = full_text.lower()
    has_warning  = any(p in text_lower for p in WARNING_PHRASES)
    source_type  = detect_source_type(sam_url, source_url, full_text, hrefs)
    origin_trace = detect_origin_trace(sam_url, source_url, full_text, has_warning)
    is_ss        = any(t in text_lower for t in SOURCES_SOUGHT_TERMS)
    extracted    = extract_article_fields(full_text)
    excerpt      = re.sub(r"\s+", " ", full_text[:500]).strip()

    return {
        "sam_url":                  sam_url,
        "source_url":               source_url,
        "source_type":              source_type,
        "origin_trace_status":      origin_trace,
        "has_registration_warning": has_warning,
        "is_sources_sought":        is_ss,
        "raw_text_excerpt":         excerpt,
        **extracted,
    }


# ─── Intake-method tagging ────────────────────────────────────────────────────

def tag_lead(lead, intake_method, access_status="ok"):
    lead["intake_method"]       = intake_method
    lead["access_status"]       = access_status
    lead["local_capture_needed"] = "No"
    return lead


# ─── Email file parser ────────────────────────────────────────────────────────

_OUTREACH_URL_RE = re.compile(
    r'https?://(?:mybidmatch\.)?outreachsystems\.com/go\?[^\s"\'<>\]]+',
    re.IGNORECASE,
)


def parse_email_file(path):
    """
    Parse a MyBidMatch email body (HTML or plain text).
    Tries the HTML article table first; falls back to link extraction,
    then plain-text row detection.
    """
    content = read_file(path)
    if not content:
        print(f"  [warn] Could not read: {path}")
        return []

    suffix   = Path(path).suffix.lower()
    is_html  = suffix in (".html", ".htm") or bool(
        re.search(r'<html|<body|<table', content[:800], re.IGNORECASE)
    )
    date_m   = DATE_RE.search(content)
    date_text = date_m.group(0).strip() if date_m else ""

    leads = []

    if is_html:
        rows, headers = find_article_table(content)
        if rows and headers:
            leads = table_rows_to_leads(rows, headers, date_text)
            if leads:
                print(f"  Email HTML table: {len(leads)} articles.")
                return leads

        # Fallback: outreachsystems links from HTML
        for lnk in find_all_links(content):
            href = lnk.get("href", "")
            text = lnk.get("text", "")
            if "outreachsystems" not in href.lower() and "mybidmatch" not in href.lower():
                continue
            if len(text) < 5:
                continue
            leads.append({
                "date_text":   date_text,
                "row_number":  str(len(leads) + 1),
                "source": "", "agency": "", "fsg": "",
                "title":       text,
                "keywords":    "",
                "article_url": href,
            })
        print(f"  Email HTML links: {len(leads)} articles.")
        return leads

    # Plain text — try whitespace/tab-delimited rows
    for line in content.splitlines():
        parts = [p.strip() for p in re.split(r'\t|\s{3,}|\|', line) if p.strip()]
        if len(parts) >= 5 and (parts[0].isdigit() or len(parts) >= 5):
            row_num = parts[0] if parts[0].isdigit() else ""
            idx     = 1 if row_num else 0
            source  = parts[idx]     if len(parts) > idx     else ""
            agency  = parts[idx + 1] if len(parts) > idx + 1 else ""
            fsg     = parts[idx + 2] if len(parts) > idx + 2 else ""
            title   = parts[idx + 3] if len(parts) > idx + 3 else ""
            kw      = parts[idx + 4] if len(parts) > idx + 4 else ""
            if title:
                leads.append({
                    "date_text": date_text,
                    "row_number": row_num,
                    "source": source, "agency": agency, "fsg": fsg,
                    "title": title, "keywords": kw, "article_url": "",
                })

    if leads:
        print(f"  Email text rows: {len(leads)} articles.")
        return leads

    # Last resort: extract bare outreachsystems URLs
    for i, m in enumerate(_OUTREACH_URL_RE.finditer(content)):
        leads.append({
            "date_text":   date_text,
            "row_number":  str(i + 1),
            "source": "", "agency": "", "fsg": "",
            "title":       f"MyBidMatch Article {i + 1}",
            "keywords":    "",
            "article_url": m.group(0),
        })
    print(f"  Email plain-text URLs: {len(leads)} articles.")
    return leads


# ─── Daily page HTML parser ───────────────────────────────────────────────────

def parse_daily_html(path):
    """
    Parse a saved MyBidMatch daily page HTML file.
    Extracts the article table and returns a list of partial lead dicts.
    """
    content = read_file(path)
    if not content:
        print(f"  [warn] Could not read: {path}")
        return []

    date_m    = DATE_RE.search(content)
    date_text = date_m.group(0).strip() if date_m else Path(path).stem

    rows, headers = find_article_table(content)
    if rows and headers:
        leads = table_rows_to_leads(rows, headers, date_text)
        print(f"  Daily HTML table: {len(leads)} articles.")
        return leads

    # Fallback: any outreachsystems links in the page
    leads = []
    for lnk in find_all_links(content):
        href = lnk.get("href", "")
        text = lnk.get("text", "")
        if "outreachsystems" not in href.lower():
            continue
        if len(text) < 5:
            continue
        leads.append({
            "date_text":   date_text,
            "row_number":  str(len(leads) + 1),
            "source": "", "agency": "", "fsg": "",
            "title":       text,
            "keywords":    "",
            "article_url": href,
        })
    print(f"  Daily HTML links fallback: {len(leads)} articles.")
    return leads


# ─── Article file parser ──────────────────────────────────────────────────────

def parse_article_html(path, meta=None):
    """
    Parse one saved MyBidMatch article detail page (HTML or plain text).
    Returns a single lead dict.  Title/agency come from meta if supplied.
    """
    content = read_file(path)
    if not content:
        print(f"  [warn] Could not read: {path}")
        return {}

    suffix  = Path(path).suffix.lower()
    is_html = suffix in (".html", ".htm") or bool(
        re.search(r'<html|<body', content[:400], re.IGNORECASE)
    )
    detail = parse_article_content(content, is_html=is_html)

    # Try to find a title from the HTML <title> tag or <h1>/<h2>
    title = ""
    if is_html:
        m = re.search(r'<title[^>]*>(.*?)</title>', content, re.IGNORECASE | re.DOTALL)
        if m:
            title = html_lib.unescape(re.sub(r'<[^>]+>', '', m.group(1)).strip())
        if not title:
            m = re.search(r'<h[12][^>]*>(.*?)</h[12]>', content, re.IGNORECASE | re.DOTALL)
            if m:
                title = html_lib.unescape(re.sub(r'<[^>]+>', '', m.group(1)).strip())

    lead = {
        "date_text":   "",
        "row_number":  "",
        "source":      "saved_file",
        "agency":      detail.get("agency_detail", ""),
        "fsg":         "",
        "title":       title or Path(path).stem,
        "keywords":    "",
        "article_url": str(path),
    }
    if meta:
        lead.update(meta)
    lead.update(detail)
    return lead


# ─── File type detection ──────────────────────────────────────────────────────

_EMAIL_MARKERS = [
    "your mybidmatch search profile results",
    "from: outreachsystems",
    "from:outreachsystems",
    "subject:",
    "outreachsystems.com",
]


def detect_file_type(path, content):
    """Return 'email', 'daily_page', or 'article'."""
    fname   = Path(path).name.lower()
    clower  = content.lower()

    # Filename hints
    if any(k in fname for k in ("email", "mail", "inbox", "message")):
        return "email"
    if any(k in fname for k in ("daily", "results", "date_", "articles")):
        return "daily_page"
    if any(k in fname for k in ("article", "detail", "lead")):
        return "article"

    # Content: article table with typical MyBidMatch columns
    rows, headers = find_article_table(content)
    if rows and headers and len(rows) > 2:
        return "daily_page"

    # Email content markers
    if any(m in clower for m in _EMAIL_MARKERS):
        return "email"

    # Default to article
    return "article"


# ─── Folder (html-dir) mode ───────────────────────────────────────────────────

_PARSEABLE_EXTENSIONS = {".html", ".htm", ".txt"}


def parse_html_dir(dir_path, limit_articles=0):
    """
    Parse all .html/.htm/.txt files in dir_path, auto-detecting type.
    Returns a flat list of partial lead dicts (before classification).
    """
    d = Path(dir_path)
    if not d.is_dir():
        print(f"  [error] Not a directory: {dir_path}")
        return []

    files = sorted(
        f for f in d.iterdir()
        if f.is_file() and f.suffix.lower() in _PARSEABLE_EXTENSIONS
    )
    if not files:
        print(f"  [warn] No .html/.htm/.txt files found in {dir_path}")
        return []

    print(f"  Found {len(files)} file(s) in {dir_path}")

    # Separate daily pages from article files so we can report counts
    daily_leads  = []
    article_leads = []
    other_leads   = []

    for f in files:
        content   = read_file(f)
        file_type = detect_file_type(f, content)
        print(f"    [{file_type}] {f.name}")

        if file_type == "daily_page":
            leads = parse_daily_html(f)
            daily_leads.extend(leads)
        elif file_type == "email":
            leads = parse_email_file(f)
            other_leads.extend(leads)
        else:  # article
            lead = parse_article_html(f)
            if lead:
                article_leads.append(lead)

    # Try to enrich daily-page leads with matching article file details
    # (match by article URL path fragment or title similarity)
    _enrich_from_articles(daily_leads, article_leads)

    combined = daily_leads + other_leads
    # Any article leads not merged into daily rows go in as standalone
    used_paths = {l.get("article_url", "") for l in combined}
    for al in article_leads:
        if al.get("article_url", "") not in used_paths:
            combined.append(al)

    if limit_articles > 0:
        combined = combined[:limit_articles]

    return combined


def _enrich_from_articles(daily_leads, article_leads):
    """
    Attempt to merge article detail leads into matching daily-page leads in place.
    Matching: article_url path fragment matches a daily lead's article_url.
    """
    if not article_leads:
        return
    # Build index by URL path fragment and by title
    by_path = {}
    by_title = {}
    for al in article_leads:
        url = al.get("article_url", "")
        if url:
            by_path[url] = al
            # path fragment key
            slug = re.sub(r'[^a-z0-9]', '', Path(url).stem.lower())
            if slug:
                by_path[slug] = al
        title = re.sub(r'[^a-z0-9]', '', al.get("title", "").lower())
        if title:
            by_title[title] = al

    for lead in daily_leads:
        url   = lead.get("article_url", "")
        title = re.sub(r'[^a-z0-9]', '', lead.get("title", "").lower())
        match = by_path.get(url) or by_path.get(
            re.sub(r'[^a-z0-9]', '', Path(url).stem.lower()), None
        ) or by_title.get(title)
        if match:
            for k, v in match.items():
                if k not in ("title", "agency", "source", "fsg", "date_text",
                             "row_number", "keywords", "article_url") and v:
                    lead[k] = v


# ─── Importer runner ──────────────────────────────────────────────────────────

def run_import(args):
    known_ids = load_existing_govcon_ids()
    print(f"GovCon Scout IDs loaded for dedup: {len(known_ids)}")

    raw_leads = []
    intake_method = "unknown"

    if args.email_file:
        intake_method = "email_file"
        print(f"\nParsing email file: {args.email_file}")
        raw_leads = parse_email_file(args.email_file)

    elif args.daily_html:
        intake_method = "daily_html"
        print(f"\nParsing daily HTML: {args.daily_html}")
        raw_leads = parse_daily_html(args.daily_html)

    elif args.article_file:
        intake_method = "article_file"
        print(f"\nParsing article file: {args.article_file}")
        lead = parse_article_html(args.article_file)
        raw_leads = [lead] if lead else []

    elif args.html_dir:
        intake_method = "html_dir"
        print(f"\nParsing folder: {args.html_dir}")
        raw_leads = parse_html_dir(args.html_dir, limit_articles=args.limit_articles)

    if not raw_leads:
        print("\n[warn] No leads extracted. Check file path and content format.")
        _write_empty_report(args, intake_method)
        return

    # Classify and tag each lead
    leads = []
    for art in raw_leads:
        # If no article content was fetched (daily/email mode), run classification
        # on the title+keywords+fsg fields we have
        if not art.get("origin_trace_status"):
            art.update(parse_article_content(
                art.get("raw_text_excerpt", "") + " " + art.get("title", "") + " " + art.get("keywords", ""),
                is_html=False,
            ))
        art.update(classify_lead(art))
        tag_lead(art, intake_method)
        art["govcon_status"] = govcon_status(art, known_ids)
        leads.append(art)

    leads     = dedupe_leads(leads)
    sam_queue = [l for l in leads if l.get("govcon_status") == "new_sam_ready"]

    # Write CSV outputs
    write_csv(leads,     args.output_csv, LEADS_COLUMNS)
    write_csv(sam_queue, args.sam_queue,  SAM_QUEUE_COLUMNS)

    # Write markdown reports
    meta = {
        "dates_processed": len({l.get("date_text", "") for l in leads if l.get("date_text")}),
        "errors":          [],
        "intake_method":   intake_method,
    }
    for text, path in (
        (build_leads_report(leads, meta),  args.report),
        (build_followup_report(leads),     args.followup_report),
    ):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"  Written: {p}")

    # Terminal summary
    n_high   = sum(1 for l in leads if l.get("priority") == "High")
    n_sam    = len(sam_queue)
    n_follow = sum(1 for l in leads if l.get("govcon_status") == "non_sam_followup")

    print("\n─── MyBidMatch Import Summary ──────────────────────────")
    print(f"  Intake method:           {intake_method}")
    print(f"  Articles extracted:      {len(leads)}")
    print(f"  New SAM-ready leads:     {n_sam}")
    print(f"  Non-SAM follow-up:       {n_follow}")
    print(f"  High priority:           {n_high}")
    print(f"\n  Leads CSV:   {args.output_csv}")
    print(f"  SAM queue:   {args.sam_queue}")
    print(f"  Report:      {args.report}")
    print(f"  Follow-up:   {args.followup_report}")
    print("────────────────────────────────────────────────────────")


def _write_empty_report(args, intake_method):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    txt = (
        "# MyBidMatch Importer Report\n\n"
        f"**Generated:** {ts}\n"
        f"**Intake method:** `{intake_method}`\n\n"
        "## No Leads Extracted\n\n"
        "The file(s) could not be parsed or contained no article data.\n\n"
        "**Check:**\n"
        "- Is the file a saved MyBidMatch daily page or email body?\n"
        "- For HTML files, make sure the full page was saved (not just text).\n\n"
        "## Local Capture Instructions\n\n"
        + LOCAL_CAPTURE_INSTRUCTIONS
    )
    p = Path(args.report)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(txt, encoding="utf-8")
    print(f"  Written: {p}")


# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "MyBidMatch offline importer — parse saved email or HTML files. "
            "No browser session or auth cookies required."
        )
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--email-file", metavar="PATH",
        help="Parse a saved MyBidMatch email body (HTML or plain text)",
    )
    mode.add_argument(
        "--daily-html", metavar="PATH",
        help="Parse a saved MyBidMatch daily page HTML file",
    )
    mode.add_argument(
        "--article-file", metavar="PATH",
        help="Parse one saved MyBidMatch article detail page",
    )
    mode.add_argument(
        "--html-dir", metavar="DIR",
        help="Parse all .html/.htm/.txt files in a folder (auto-detects type)",
    )
    p.add_argument("--output-csv",      default=DEFAULT_OUTPUT_CSV)
    p.add_argument("--sam-queue",       default=DEFAULT_SAM_QUEUE)
    p.add_argument("--report",          default=DEFAULT_REPORT)
    p.add_argument("--followup-report", default=DEFAULT_FOLLOWUP)
    p.add_argument(
        "--limit-articles", type=int, default=0,
        help="Max articles to process (0 = no limit; applies to --html-dir)",
    )
    return p.parse_args()


def main():
    run_import(parse_args())


if __name__ == "__main__":
    main()
