"""
mybidmatch_parser.py — Parse saved MyBidMatch HTML files into structured
opportunity records for GovCon Scout.

Handles three page types automatically:
  directory    — the date-index landing page (no article data; outputs date metadata)
  daily_list   — a single day's article table (title, source, agency, FSG, keywords)
  article_detail — an individual article page (solicitation, NAICS, due date, SAM URL, …)

Usage:
  python src/mybidmatch_parser.py
  python src/mybidmatch_parser.py --input-dir data/mybidmatch/raw --output-csv data/mybidmatch/mybidmatch_opportunities.csv

Outputs:
  data/mybidmatch/mybidmatch_opportunities.csv
  reports/mybidmatch/mybidmatch_summary.md
"""

import argparse
import csv
import html as html_lib
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

try:
    from bs4 import BeautifulSoup, NavigableString
    _BS4 = True
except ImportError:
    _BS4 = False

# ─── Output paths ─────────────────────────────────────────────────────────────

DEFAULT_INPUT_DIR  = "data/mybidmatch/raw"
DEFAULT_OUTPUT_CSV = "data/mybidmatch/mybidmatch_opportunities.csv"
DEFAULT_REPORT     = "reports/mybidmatch/mybidmatch_summary.md"

OUTREACH_BASE = "https://mybidmatch.outreachsystems.com"

# ─── CSV schema ───────────────────────────────────────────────────────────────

COLUMNS = [
    "source_file", "page_type", "date_text",
    "title", "notice_id", "agency", "due_date",
    "naics", "psc", "set_aside", "place_of_performance",
    "source_url", "description",
    "fsg", "keywords", "source_publication",
    "article_url", "row_number",
]

# ─── Regex patterns ───────────────────────────────────────────────────────────

_SOL_LABELED = re.compile(
    r'(?:Solicitation|Sol\.?\s*No\.?|RFQ|RFP|IFB)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{5,29})\b',
    re.IGNORECASE,
)
_SOL_PATTERN = re.compile(r'\b([A-Z]{1,6}[0-9]{6,}[A-Z0-9\-]*)\b')
_NAICS       = re.compile(r'\bNAICS\s*(?:Code)?\s*[:#]?\s*(\d{5,6})\b', re.IGNORECASE)
_PSC         = re.compile(r'\bPSC\s*[:#]?\s*([A-Z][0-9]{3}|[A-Z]{1,2}[0-9]{3,4})\b', re.IGNORECASE)
_DUE         = re.compile(
    r'(?:due|response deadline|deadline|submit(?:ted)? by|closes?)\s*[:#]?\s*'
    r'(\w+ \d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4})',
    re.IGNORECASE,
)
_SET_ASIDE   = re.compile(
    r'(Total Small Business|Small Business Set.Aside|8\(a\)|SDVOSB|WOSB|HUBZone|VOSB|SBA)',
    re.IGNORECASE,
)
_POP         = re.compile(
    r'Place\s+of\s+Performance\s*[:#]?\s*(.{5,80}?)(?:\n|<|\Z)', re.IGNORECASE | re.DOTALL
)
_DATE_HDG    = re.compile(
    r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)'
    r',\s+\w+\s+\d{1,2},\s+\d{4}',
    re.IGNORECASE,
)
_SAM_URL     = re.compile(
    r'https?://(?:www\.)?sam\.gov/(?:opp|workspace/contract/opp)/[^\s"\'<>]+',
    re.IGNORECASE,
)

# ─── HTML utilities ───────────────────────────────────────────────────────────

def _parse(html_content):
    if _BS4:
        return BeautifulSoup(html_content, "html.parser")
    return None


def _text(tag):
    if tag is None:
        return ""
    if _BS4:
        return tag.get_text(separator=" ", strip=True)
    return re.sub(r'<[^>]+>', ' ', str(tag)).strip()


def _to_text(html_content):
    """Minimal HTML → plain text (stdlib only)."""
    t = re.sub(r'<(?:script|style)[^>]*>.*?</(?:script|style)>', '', html_content,
               flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r'<(?:br|p|div|tr|li|h[1-6])[^>]*/?\s*>', '\n', t, flags=re.IGNORECASE)
    t = re.sub(r'</(?:p|div|tr|li|h[1-6])>', '\n', t, flags=re.IGNORECASE)
    t = re.sub(r'<[^>]+>', ' ', t)
    t = html_lib.unescape(t)
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def _abs_url(href):
    if not href:
        return ""
    href = href.strip()
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return OUTREACH_BASE + href
    return href


# ─── Page type detection ──────────────────────────────────────────────────────

def detect_page_type(html_content, soup):
    """Return 'directory', 'daily_list', or 'article_detail'."""
    # Directory: "Welcome to mybidmatch.com for search profile"
    if "welcome to mybidmatch.com for search profile" in html_content.lower():
        return "directory"

    # Daily list: table with Source / Agency / FSG / Title / Keywords header cells
    if soup and _BS4:
        for tbl in soup.find_all("table"):
            ths = [th.get_text(strip=True).lower() for th in tbl.find_all(["th", "td"])[:12]]
            if sum(1 for h in ths if h in ("source", "agency", "fsg", "title", "keywords")) >= 3:
                return "daily_list"
    else:
        header_lower = html_content.lower()
        if (("<th" in header_lower or "<td" in header_lower) and
                "source" in header_lower and "agency" in header_lower and
                "fsg" in header_lower and "title" in header_lower):
            return "daily_list"

    return "article_detail"


# ─── Directory page parser ────────────────────────────────────────────────────

def parse_directory(html_content, soup, source_file):
    """
    Extract the date-index from a MyBidMatch directory page.
    Returns a list of metadata dicts (not opportunity records).
    """
    entries = []
    profile_m = re.search(r'search profile[:\s]+(\d+)', html_content, re.IGNORECASE)
    profile_id = profile_m.group(1) if profile_m else ""

    name_m = re.search(r'class="btn"[^>]*>.*?Robinson Creative Group', html_content, re.IGNORECASE | re.DOTALL)
    org_name = "Robinson Creative Group" if name_m else ""

    if soup and _BS4:
        for row in soup.find_all("tr"):
            cells = row.find_all("td")
            if not cells:
                continue
            link = cells[0].find("a", href=True) if cells else None
            if not link or "/go?doc=" not in link.get("href", ""):
                continue
            date_text     = link.get_text(strip=True)
            article_count = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            read_status   = cells[2].get_text(strip=True) if len(cells) > 2 else ""
            doc_url       = _abs_url(link["href"])
            entries.append({
                "date_text":     date_text,
                "article_count": article_count,
                "read_status":   read_status,
                "doc_url":       doc_url,
                "profile_id":    profile_id,
                "org_name":      org_name,
            })
    else:
        for m in re.finditer(
            r'<a href="(/go\?doc=[^"]+)"[^>]*>([^<]+)</a>\s*</td>'
            r'\s*<td[^>]*>\s*(\d+)\s*</td>'
            r'\s*<td[^>]*>\s*(.*?)\s*</td>',
            html_content, re.IGNORECASE | re.DOTALL,
        ):
            entries.append({
                "date_text":     m.group(2).strip(),
                "article_count": m.group(3).strip(),
                "read_status":   re.sub(r'<[^>]+>', '', m.group(4)).strip(),
                "doc_url":       _abs_url(m.group(1)),
                "profile_id":    profile_id,
                "org_name":      org_name,
            })

    return entries


# ─── Daily list parser ────────────────────────────────────────────────────────

def parse_daily_list(html_content, soup, source_file):
    """
    Extract article rows from a MyBidMatch daily listing page.
    Returns list of partial opportunity dicts.
    """
    # Find date heading
    date_m    = _DATE_HDG.search(html_content)
    date_text = date_m.group(0).strip() if date_m else ""

    records = []

    if soup and _BS4:
        target_table = None
        for tbl in soup.find_all("table"):
            ths = [th.get_text(strip=True).lower() for th in tbl.find_all(["th", "td"])[:10]]
            if sum(1 for h in ths if h in ("source", "agency", "fsg", "title", "keywords")) >= 3:
                target_table = tbl
                break

        if not target_table:
            return records

        rows = target_table.find_all("tr")
        # Identify column positions from header row
        header_row   = rows[0] if rows else None
        header_cells = header_row.find_all(["th", "td"]) if header_row else []
        headers      = [c.get_text(strip=True).lower().lstrip("#").strip() for c in header_cells]

        col = {}
        for i, h in enumerate(headers):
            col[h] = i

        num_i    = col.get("", col.get("num", 0))
        src_i    = col.get("source", 1)
        agency_i = col.get("agency", 2)
        fsg_i    = col.get("fsg", 3)
        title_i  = col.get("title", 4)
        kw_i     = col.get("keywords", 5)

        for row in rows[1:]:
            cells = row.find_all("td")
            if not cells:
                continue

            def ct(i):
                return cells[i].get_text(separator=" ", strip=True) if i < len(cells) else ""

            def ch(i):
                a = cells[i].find("a", href=True) if i < len(cells) else None
                return _abs_url(a["href"]) if a else ""

            title = ct(title_i)
            if not title:
                continue

            art_url = ch(title_i) or next(
                (_abs_url(c.find("a")["href"])
                 for c in cells if c.find("a", href=True)
                 and "outreachsystems" in c.find("a")["href"]),
                "",
            )

            records.append({
                "source_file":      source_file,
                "page_type":        "daily_list",
                "date_text":        date_text,
                "title":            title,
                "notice_id":        "",
                "agency":           ct(agency_i),
                "due_date":         "",
                "naics":            "",
                "psc":              "",
                "set_aside":        "",
                "place_of_performance": "",
                "source_url":       "",
                "description":      "",
                "fsg":              ct(fsg_i),
                "keywords":         ct(kw_i),
                "source_publication": ct(src_i),
                "article_url":      art_url,
                "row_number":       ct(num_i),
            })

    else:
        # Fallback: regex row extraction
        for i, m in enumerate(re.finditer(
            r'<tr[^>]*>(.*?)</tr>', html_content, re.DOTALL | re.IGNORECASE
        )):
            cells_html = re.findall(r'<td[^>]*>(.*?)</td>', m.group(1), re.DOTALL | re.IGNORECASE)
            if len(cells_html) < 4:
                continue
            def rc(idx):
                raw = cells_html[idx] if idx < len(cells_html) else ""
                return html_lib.unescape(re.sub(r'<[^>]+>', ' ', raw).strip())
            title = rc(4)
            if not title or title.lower() in ("title", "#"):
                continue
            href_m = re.search(r'href=["\']([^"\']+outreachsystems[^"\']*)["\']',
                                cells_html[4] if len(cells_html) > 4 else "", re.IGNORECASE)
            records.append({
                "source_file":      source_file,
                "page_type":        "daily_list",
                "date_text":        date_text,
                "title":            title,
                "notice_id":        "",
                "agency":           rc(2),
                "due_date":         "",
                "naics":            "",
                "psc":              "",
                "set_aside":        "",
                "place_of_performance": "",
                "source_url":       "",
                "description":      "",
                "fsg":              rc(3),
                "keywords":         rc(5) if len(cells_html) > 5 else "",
                "source_publication": rc(1),
                "article_url":      _abs_url(href_m.group(1)) if href_m else "",
                "row_number":       rc(0),
            })

    return records


# ─── Article detail parser ────────────────────────────────────────────────────

def parse_article_detail(html_content, soup, source_file):
    """
    Extract structured fields from a single MyBidMatch article detail page.
    Returns a list with one opportunity dict (or empty list on failure).
    """
    plain = _to_text(html_content)

    # Title: <h1> or <title>
    title = ""
    if soup and _BS4:
        h1 = soup.find("h1")
        if h1:
            title = _text(h1).strip()
        if not title:
            t = soup.find("title")
            if t:
                title = _text(t).strip()
    if not title:
        m = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.IGNORECASE | re.DOTALL)
        if m:
            title = html_lib.unescape(re.sub(r'<[^>]+>', ' ', m.group(1)).strip())
    if not title:
        m = re.search(r'<title[^>]*>(.*?)</title>', html_content, re.IGNORECASE | re.DOTALL)
        if m:
            title = html_lib.unescape(re.sub(r'<[^>]+>', ' ', m.group(1)).strip())

    # Date heading
    date_m    = _DATE_HDG.search(html_content)
    date_text = date_m.group(0).strip() if date_m else ""

    # All hrefs
    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html_content, re.IGNORECASE)
    hrefs = [_abs_url(h) for h in hrefs if h.strip()]

    # SAM URL
    sam_url = ""
    for h in hrefs:
        if _SAM_URL.match(h):
            sam_url = h
            break
    if not sam_url:
        m = _SAM_URL.search(plain)
        if m:
            sam_url = m.group(0)

    # Source URL (third-party portals)
    _THIRD_PARTY = [
        "bidnetdirect.com", "bonfirehub.com", "ionwave.net",
        "publicpurchase.com", "demandstar.com", "ebid.net",
        "bidsync.com", "planetbids.com", "govbuys.com",
    ]
    source_url = ""
    for h in hrefs:
        if any(d in h.lower() for d in _THIRD_PARTY):
            source_url = h
            break

    # Solicitation / notice_id
    notice_id = ""
    m = _SOL_LABELED.search(plain)
    if m:
        notice_id = m.group(1).strip()
    else:
        m = _SOL_PATTERN.search(plain)
        if m:
            cand = m.group(1)
            if len(cand) >= 8 and not cand.isdigit():
                notice_id = cand

    # Structured fields from plain text
    naics = _NAICS.search(plain)
    naics = naics.group(1) if naics else ""

    psc = _PSC.search(plain)
    psc = psc.group(1) if psc else ""

    due = _DUE.search(plain)
    due = due.group(1).strip() if due else ""

    sa = _SET_ASIDE.search(plain)
    sa = sa.group(1).strip() if sa else ""

    pop = _POP.search(plain)
    pop = pop.group(1).strip()[:100] if pop else ""

    # Agency — look for labeled field first, then table cell
    agency = ""
    for pattern in (
        r'Agency\s*[:#]\s*(.{5,80}?)(?:\n|<)',
        r'Department\s*[:#]\s*(.{5,80}?)(?:\n|<)',
        r'Issuing\s+Agency\s*[:#]\s*(.{5,80}?)(?:\n|<)',
    ):
        m = re.search(pattern, plain, re.IGNORECASE)
        if m:
            agency = m.group(1).strip()[:80]
            break

    # Description / excerpt
    desc = re.sub(r'\s+', ' ', plain[:600]).strip()

    return [{
        "source_file":          source_file,
        "page_type":            "article_detail",
        "date_text":            date_text,
        "title":                title,
        "notice_id":            notice_id,
        "agency":               agency,
        "due_date":             due,
        "naics":                naics,
        "psc":                  psc,
        "set_aside":            sa,
        "place_of_performance": pop,
        "source_url":           source_url or sam_url,
        "description":          desc,
        "fsg":                  "",
        "keywords":             "",
        "source_publication":   "",
        "article_url":          "",
        "row_number":           "",
    }]


# ─── Deduplication ────────────────────────────────────────────────────────────

def dedupe(records):
    seen, out = {}, []
    for r in records:
        key = r.get("notice_id", "").strip()
        if not key:
            key = "{}|{}".format(
                r.get("title", "").lower()[:60],
                r.get("agency", "").lower()[:30],
            )
        if key and key in seen:
            continue
        seen[key] = True
        out.append(r)
    return out


# ─── Main parse loop ──────────────────────────────────────────────────────────

def parse_all(input_dir):
    """
    Parse all .html/.htm files in input_dir.
    Returns (opportunity_records, directory_entries, file_results).
    """
    d = Path(input_dir)
    files = sorted(
        f for f in d.iterdir()
        if f.is_file() and f.suffix.lower() in (".html", ".htm")
    )

    all_records   = []
    dir_entries   = []   # metadata from directory pages
    file_results  = []   # per-file summary

    for f in files:
        print(f"  Parsing: {f.name}")
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"    [error] Could not read: {exc}")
            file_results.append({"file": f.name, "type": "error", "count": 0, "note": str(exc)})
            continue

        soup      = _parse(content)
        page_type = detect_page_type(content, soup)
        print(f"    Page type: {page_type}")

        if page_type == "directory":
            entries = parse_directory(content, soup, f.name)
            dir_entries.extend(entries)
            file_results.append({
                "file": f.name, "type": "directory",
                "count": len(entries),
                "note": f"{len(entries)} date entries (no article data in this file)",
            })

        elif page_type == "daily_list":
            records = parse_daily_list(content, soup, f.name)
            all_records.extend(records)
            file_results.append({
                "file": f.name, "type": "daily_list",
                "count": len(records),
                "note": f"{len(records)} article rows extracted",
            })

        else:  # article_detail
            records = parse_article_detail(content, soup, f.name)
            all_records.extend(records)
            file_results.append({
                "file": f.name, "type": "article_detail",
                "count": len(records),
                "note": f"{len(records)} opportunity record extracted",
            })

    all_records = dedupe(all_records)
    return all_records, dir_entries, file_results


# ─── CSV output ───────────────────────────────────────────────────────────────

def write_csv(records, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
    print(f"  CSV: {p}  ({len(records)} records)")


# ─── Markdown summary ─────────────────────────────────────────────────────────

def build_summary(records, dir_entries, file_results, output_csv):
    ts        = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    n_total   = len(records)
    n_no_id   = sum(1 for r in records if not r.get("notice_id", "").strip())
    n_files   = len(file_results)
    n_dir     = sum(1 for r in file_results if r["type"] == "directory")
    n_daily   = sum(1 for r in file_results if r["type"] == "daily_list")
    n_article = sum(1 for r in file_results if r["type"] == "article_detail")

    agency_ctr = Counter(
        r["agency"].strip() for r in records if r.get("agency", "").strip()
    )
    naics_ctr  = Counter(
        r["naics"].strip() for r in records if r.get("naics", "").strip()
    )

    out = [
        "# MyBidMatch Parser Summary", "",
        f"**Generated:** {ts}",
        f"**Input directory:** `{DEFAULT_INPUT_DIR}`",
        f"**Output CSV:** `{output_csv}`", "",
        "## File Parse Results", "",
        f"| File | Page Type | Records / Entries | Note |",
        f"|---|---|---|---|",
    ]
    for r in file_results:
        out.append(f"| {r['file']} | `{r['type']}` | {r['count']} | {r['note']} |")

    out += [
        "", "## Opportunity Record Counts", "",
        f"- **HTML files parsed:** {n_files}",
        f"  - Directory pages: {n_dir}",
        f"  - Daily list pages: {n_daily}",
        f"  - Article detail pages: {n_article}",
        f"- **Total opportunity records extracted:** {n_total}",
        f"- **Records missing notice_id:** {n_no_id}",
    ]

    # Directory date index
    if dir_entries:
        total_articles = sum(
            int(e.get("article_count", 0))
            for e in dir_entries
            if str(e.get("article_count", "")).isdigit()
        )
        new_count = sum(
            1 for e in dir_entries
            if e.get("read_status", "").lower() == "new"
        )
        out += [
            "", "## Available Dates (from Directory Page)", "",
            f"**{len(dir_entries)} dates found — {total_articles} total articles**  "
            f"({new_count} dates marked New / unread)", "",
            "| Date | Articles | Status | Daily Page URL |",
            "|---|---|---|---|",
        ]
        for e in dir_entries:
            url   = e.get("doc_url", "")
            lbl   = f"[open]({url})" if url else "—"
            read  = e.get("read_status", "")
            count = e.get("article_count", "")
            out.append(f"| {e['date_text']} | {count} | {read} | {lbl} |")

    # Opportunity detail tables (if any)
    if records:
        if agency_ctr:
            out += ["", "## Top Agencies", ""]
            for agency, cnt in agency_ctr.most_common(10):
                out.append(f"- {agency} ({cnt})")

        if naics_ctr:
            out += ["", "## Top NAICS Codes", ""]
            for naics, cnt in naics_ctr.most_common(10):
                out.append(f"- {naics} ({cnt})")

        out += ["", "## Sample Records (first 5)", ""]
        out += [
            "| Title | Agency | Notice ID | Due Date | NAICS | Source File |",
            "|---|---|---|---|---|---|",
        ]
        for r in records[:5]:
            t   = (r.get("title") or "")[:55].replace("|", "/")
            a   = (r.get("agency") or "")[:28].replace("|", "/")
            nid = (r.get("notice_id") or "")[:22]
            due = (r.get("due_date") or "")[:16]
            nai = (r.get("naics") or "")[:8]
            sf  = (r.get("source_file") or "")[:30]
            out.append(f"| {t} | {a} | {nid} | {due} | {nai} | {sf} |")

    # Recommended next action
    out += ["", "## Recommended Next Action", ""]

    if n_dir > 0 and n_daily == 0 and n_article == 0:
        # Only have directory pages — need daily pages
        out += [
            "**You have saved the directory page but not the daily article pages.**",
            "",
            "To extract actual opportunity records, save one or more daily pages:",
            "",
            "1. In your normal browser, click each date link in the directory.",
            "2. When the article table loads, go to **File → Save Page As → Webpage, HTML Only**.",
            "3. Save the file into `data/mybidmatch/raw/` (any filename ending in `.html`).",
            "4. Re-run: `python src/mybidmatch_parser.py`",
            "",
            "**Priority dates to save first** (most articles):",
        ]
        top_dates = sorted(dir_entries, key=lambda e: int(e.get("article_count", 0))
                           if str(e.get("article_count", "")).isdigit() else 0, reverse=True)
        for e in top_dates[:5]:
            url = e.get("doc_url", "")
            out.append(f"  - {e['date_text']} — {e.get('article_count', '?')} articles  ")
            if url:
                out.append(f"    URL: `{url}`")
        out += [
            "",
            "Alternatively, use the offline importer once you have daily pages:",
            "  `python src/mybidmatch_importer.py --html-dir data/mybidmatch/raw/`",
        ]
    elif n_total == 0:
        out += [
            "No opportunity records were extracted from the available files.",
            "Make sure you have saved MyBidMatch daily list pages (not just the directory).",
        ]
    else:
        n_sam   = sum(1 for r in records if r.get("source_url", "").startswith("http"))
        n_no_id = sum(1 for r in records if not r.get("notice_id", "").strip())
        out += [
            f"- **{n_total} records extracted** and written to `{output_csv}`.",
            f"- {n_no_id} records are missing a notice ID — use title + agency for matching.",
        ]
        if n_sam:
            out.append(
                f"- {n_sam} records have a source URL — confirm each is active on SAM.gov "
                "before scoring."
            )
        out += [
            "- To cross-reference with GovCon Scout pipeline:",
            "  `python src/process_opportunity.py --notice-id <ID>`",
            "- To run triage after confirming SAM availability:",
            "  `python src/triage_review_pack.py`",
        ]

    out.append("")
    return "\n".join(out)


# ─── CLI ──────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="Parse saved MyBidMatch HTML files into structured opportunity records."
    )
    p.add_argument("--input-dir",   default=DEFAULT_INPUT_DIR,
                   help=f"Folder containing saved .html files (default: {DEFAULT_INPUT_DIR})")
    p.add_argument("--output-csv",  default=DEFAULT_OUTPUT_CSV)
    p.add_argument("--report",      default=DEFAULT_REPORT)
    return p.parse_args()


def main():
    args = parse_args()

    input_dir  = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"[error] Input directory not found: {input_dir}")
        return

    html_files = [f for f in input_dir.iterdir()
                  if f.is_file() and f.suffix.lower() in (".html", ".htm")]
    if not html_files:
        print(f"[warn] No .html files found in {input_dir}")
        return

    print(f"\nMyBidMatch Parser")
    print(f"  Input:  {input_dir}  ({len(html_files)} HTML file(s))")
    print(f"  Output: {args.output_csv}")
    print(f"  Report: {args.report}")
    print()

    records, dir_entries, file_results = parse_all(input_dir)

    # Write CSV (may be 0 rows if only directory pages found)
    write_csv(records, args.output_csv)

    # Write summary report
    summary = build_summary(records, dir_entries, file_results, args.output_csv)
    rpt_path = Path(args.report)
    rpt_path.parent.mkdir(parents=True, exist_ok=True)
    rpt_path.write_text(summary, encoding="utf-8")
    print(f"  Report: {rpt_path}")

    # Terminal summary
    print(f"\n─── Parse Results ──────────────────────────────────────")
    for r in file_results:
        print(f"  [{r['type']:14s}] {r['file']}: {r['note']}")
    print(f"\n  Opportunity records: {len(records)}")
    if dir_entries:
        total_art = sum(int(e.get("article_count", 0))
                        for e in dir_entries
                        if str(e.get("article_count", "")).isdigit())
        print(f"  Directory dates:     {len(dir_entries)}  ({total_art} total articles available)")
        print(f"\n  ⚑ Next step: save daily HTML pages to get opportunity records.")
        print(f"    See: {args.report}")
    print(f"────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
