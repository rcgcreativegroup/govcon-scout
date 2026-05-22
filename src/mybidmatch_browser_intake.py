"""
MyBidMatch browser intake — collects bid leads from the OutreachSystems
MyBidMatch directory, traces each article back to its source, classifies
leads using the prime-contractor control model, and outputs CSV + markdown
reports for GovCon Scout review.
"""

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


# ─── Constants ────────────────────────────────────────────────────────────────

DEFAULT_DIRECTORY_URL = (
    "https://mybidmatch.outreachsystems.com/go?sub=0FCE00BD-0DBB-4438-A624-DE3BE05AC6D1"
)
DEFAULT_OUTPUT_CSV     = "data/mybidmatch_browser_leads.csv"
DEFAULT_SAM_QUEUE      = "data/mybidmatch_sam_queue.csv"
DEFAULT_REPORT         = "reports/mybidmatch/mybidmatch_browser_leads.md"
DEFAULT_FOLLOWUP       = "reports/mybidmatch/mybidmatch_followup_queue.md"
DEFAULT_DEBUG_DIR      = "debug/mybidmatch"
DEFAULT_LIMIT_DAYS     = 1
DEFAULT_LIMIT_ARTICLES = 0
DEFAULT_STORAGE_STATE  = "mybidmatch_auth.json"

GOVCON_CSV = "exports/govcon_scout_opportunities_latest.csv"

# ─── Regex patterns ───────────────────────────────────────────────────────────

SAM_URL_RE = re.compile(
    r"https?://(?:www\.)?sam\.gov/(?:opp|workspace/contract/opp)/[^\s\"'<>]+",
    re.IGNORECASE,
)
SAM_REF_RE = re.compile(r"\bsam\.gov\b", re.IGNORECASE)

DATE_RE = re.compile(
    r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
    r",\s+\w+\s+\d{1,2},\s+\d{4}",
    re.IGNORECASE,
)

NAICS_RE   = re.compile(r"\bNAICS\s*(?:Code)?\s*[:#]?\s*(\d{5,6})\b", re.IGNORECASE)
DUE_RE     = re.compile(
    r"(?:due|response deadline|deadline|submit(?:ted)? by|closes?)\s*[:#]?\s*"
    r"(\w+ \d{1,2},\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)
SET_ASIDE_RE = re.compile(
    r"(Total Small Business|Small Business Set.Aside|8\(a\)|SDVOSB|WOSB|HUBZone|VOSB|SBA)",
    re.IGNORECASE,
)
POC_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
SOL_LABELED_RE = re.compile(
    r"(?:Solicitation|Sol\.?\s*No\.?|RFQ|RFP|IFB)\s*[:#]?\s*([A-Z0-9][A-Z0-9\-]{5,29})\b",
    re.IGNORECASE,
)
SOL_PATTERN_RE = re.compile(r"\b([A-Z]{1,6}[0-9]{6,}[A-Z0-9\-]*)\b")

# ─── Third-party portal fingerprints ─────────────────────────────────────────

THIRD_PARTY_DOMAINS = [
    ("bidnetdirect.com",     "third_party_portal"),
    ("bonfirehub.com",       "third_party_portal"),
    ("ionwave.net",          "third_party_portal"),
    ("publicpurchase.com",   "third_party_portal"),
    ("demandstar.com",       "third_party_portal"),
    ("ebid.net",             "third_party_portal"),
    ("bidsync.com",          "third_party_portal"),
    ("planetbids.com",       "third_party_portal"),
    ("govbuys.com",          "third_party_portal"),
    ("opengov.com",          "third_party_portal"),
    ("procurementnation.com","third_party_portal"),
    ("massbuys.com",         "purchasing_group"),
    ("mpc.umb.edu",          "purchasing_group"),
    ("massapc.com",          "purchasing_group"),
    ("njstart.gov",          "state_portal"),
    ("vendors.procurement",  "state_portal"),
]

WARNING_PHRASES = [
    "not associated with outreachsystems",
    "decide if the cost to obtain",
    "pay a fee",
    "registration required",
    "registration form",
    "log in to view",
    "third-party vendor",
    "third party vendor",
    "requires registration",
    "subscription required",
    "must register",
]

DIRECTORY_MARKERS = ["mybidmatch", "outreachsystems", "articles", "search profile"]

SOURCES_SOUGHT_TERMS = [
    "sources sought", "request for information", "rfi", "presolicitation",
    "pre-solicitation", "market survey", "capability statement",
]

# ─── Lane classification ──────────────────────────────────────────────────────

LANE_PATTERNS = [
    ("marketing_communications", [
        "marketing", "communications", "graphic design", "advertising",
        "public relations", "branding", "copywriting", "photography",
        "video production", "social media", "website design", "creative services",
        "media services", "outreach services",
    ]),
    ("ai_technology_training", [
        "artificial intelligence", "machine learning", "software development",
        "it services", "information technology", "training services",
        "instructional design", "documentation services", "curriculum",
        "cloud services", "cybersecurity", "help desk", "data analytics",
    ]),
    ("janitorial", [
        "janitorial", "custodial", "cleaning services", "housekeeping",
        "floor care", "window cleaning", "sanitation", "building cleaning",
        "custodian",
    ]),
    ("pest_control", [
        "pest control", "pest management", "ipm", "integrated pest",
        "extermination", "rodent control", "termite", "insect control",
    ]),
    ("trucking_transportation", [
        "trucking", "freight", "hauling services", "transportation services",
        "logistics", "delivery services", "box truck", "flatbed", "cargo",
        "moving services", "courier",
    ]),
    ("towing_hauling", [
        "towing", "roadside assistance", "vehicle recovery", "wrecker",
    ]),
    ("facilities_services", [
        "facilities management", "building maintenance", "grounds maintenance",
        "facility operations", "operations and maintenance", "o&m",
        "building operations", "property management",
    ]),
    ("roofing_hvac_trades", [
        "roofing", "hvac", "plumbing", "electrical services", "heating",
        "cooling", "ventilation", "air conditioning", "mechanical services",
        "sheet metal",
    ]),
    ("security_services", [
        "security guard", "security services", "access control", "surveillance",
        "armed guard", "unarmed guard", "physical security", "guard services",
    ]),
    ("commodities", [
        "office supplies", "equipment purchase", "furniture", "commodity",
        "supply purchase", "medical supplies", "hardware supplies", "parts",
    ]),
    ("construction", [
        "construction", "renovation", "remodeling", "building repair",
        "infrastructure", "retrofit", "demolition", "general contractor",
    ]),
    ("medical_scientific_specialized", [
        "medical", "scientific", "laboratory", "nuclear", "biomedical",
        "pharmaceutical", "environmental testing", "hazardous", "clinical",
        "pathology", "radiology",
    ]),
    ("legal_real_estate_other", [
        "legal services", "real estate", "appraisal", "attorney",
        "surveying", "title services",
    ]),
]

NAICS_LANE_MAP = {
    "541430": "marketing_communications",
    "541511": "ai_technology_training",
    "541512": "ai_technology_training",
    "611430": "ai_technology_training",
    "561720": "janitorial",
    "561710": "pest_control",
    "4841":   "trucking_transportation",
    "4842":   "trucking_transportation",
    "488410": "towing_hauling",
    "561210": "facilities_services",
    "238":    "roofing_hvac_trades",
    "561612": "security_services",
    "236":    "construction",
    "237":    "construction",
}

HIGH_RISK_TERMS = [
    "hazmat", "hazardous material", "nuclear", "radioactive",
    "mortuary", "body removal", "tank hauling", "classified",
    "top secret", "secret clearance", "ts/sci",
    "clinical trial", "pharmaceutical manufacturing",
]

# ─── Helpers ──────────────────────────────────────────────────────────────────

def safe_text(v):
    if v is None:
        return ""
    return str(v).strip()


def read_file(path):
    p = Path(path)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def save_debug_artifacts(page, debug_dir, label):
    try:
        d = Path(debug_dir)
        d.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%H%M%S")
        slug = re.sub(r"[^a-z0-9_-]", "_", label.lower())[:40]
        html_path = d / f"{ts}_{slug}.html"
        shot_path = d / f"{ts}_{slug}.png"
        html_path.write_text(page.content(), encoding="utf-8")
        page.screenshot(path=str(shot_path), full_page=True)
        print(f"    [debug] {html_path.name}, {shot_path.name}")
    except Exception as exc:
        print(f"    [debug] Could not save artifacts for {label}: {exc}")


def load_existing_govcon_ids():
    known = set()
    csv_path = Path(GOVCON_CSV)
    if csv_path.exists():
        with csv_path.open("r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                for key in ("notice_id", "solicitation_number"):
                    val = safe_text(row.get(key))
                    if val:
                        known.add(val.lower())

    for folder in (
        "reports/analysis_packets", "reports/manual_review",
        "reports/sources_sought", "reports/opportunity_reviews",
    ):
        p = Path(folder)
        if not p.exists():
            continue
        for f in p.glob("*.md"):
            stem = f.stem
            for suffix in (
                "_decision_report", "_compliance_matrix", "_bid_no_bid",
                "_manual_review", "_sources_sought_plan",
            ):
                stem = stem.replace(suffix, "")
            if stem:
                known.add(stem.lower())

    return known


# ─── Directory page ───────────────────────────────────────────────────────────

def check_directory_loaded(page):
    html = page.content().lower()
    return any(m in html for m in DIRECTORY_MARKERS)


def check_access_denied(page):
    html = page.content().lower()
    return "403 forbidden" in html or "access denied" in html or "403</h1>" in html


def extract_date_entries(page):
    entries = []
    try:
        raw = page.evaluate(r"""() => {
            const rows = [];
            const seen = new Set();
            const dayRe = /\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b/i;
            // Scan all table rows first
            document.querySelectorAll('tr').forEach(tr => {
                const link = tr.querySelector('a[href]');
                if (!link) return;
                const text = tr.textContent.trim();
                if (!dayRe.test(text)) return;
                const href = link.href;
                if (seen.has(href)) return;
                seen.add(href);
                const artMatch = text.match(/(\d+)\s+articles?/i);
                rows.push({
                    href,
                    linkText: link.textContent.trim(),
                    rowText:  text,
                    articleCount: artMatch ? parseInt(artMatch[1]) : null,
                });
            });
            // Also collect standalone links not in tables
            document.querySelectorAll('a[href]').forEach(a => {
                const text = a.textContent.trim();
                if (!dayRe.test(text)) return;
                if (seen.has(a.href)) return;
                seen.add(a.href);
                rows.push({href: a.href, linkText: text, rowText: text, articleCount: null});
            });
            return rows;
        }""")
    except Exception as exc:
        print(f"  [warn] JS evaluation failed on directory page: {exc}")
        return []

    for r in (raw or []):
        href = safe_text(r.get("href"))
        row_text = safe_text(r.get("rowText")) or safe_text(r.get("linkText"))
        date_match = DATE_RE.search(row_text) or DATE_RE.search(safe_text(r.get("linkText")))
        if not href or not date_match:
            continue
        read_status = "new" if "new" in row_text.lower() else (
            "read" if "read" in row_text.lower() else "unread"
        )
        entries.append({
            "date_text":     date_match.group(0).strip(),
            "article_count": r.get("articleCount"),
            "date_url":      href,
            "read_status":   read_status,
        })

    return entries


def load_directory(page, url, debug_dir, do_debug):
    errors = []
    print(f"Loading directory: {url}")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)
    except PlaywrightTimeoutError:
        errors.append(f"Timeout loading directory: {url}")
        return [], errors
    except Exception as exc:
        errors.append(f"Error loading directory: {exc}")
        return [], errors

    if do_debug:
        save_debug_artifacts(page, debug_dir, "directory")

    if check_access_denied(page):
        errors.append(
            "403 Forbidden — OutreachSystems requires an active session cookie to access "
            "the MyBidMatch directory. Save your session first:\n"
            "  python src/save_mybidmatch_login.py\n"
            "Then re-run with: --storage-state mybidmatch_auth.json"
        )
        return [], errors

    if not check_directory_loaded(page):
        errors.append(
            "Directory page loaded but MyBidMatch content not recognized. "
            "Page structure may have changed or URL is wrong."
        )
        if do_debug:
            save_debug_artifacts(page, debug_dir, "directory_unrecognized")
        return [], errors

    entries = extract_date_entries(page)
    if not entries:
        errors.append(
            "No date entries found on directory page. "
            "The date table structure may have changed."
        )
        if do_debug:
            save_debug_artifacts(page, debug_dir, "directory_no_dates")

    print(f"  Found {len(entries)} date entry/entries.")
    return entries, errors


# ─── Daily page ───────────────────────────────────────────────────────────────

def extract_daily_table(page, date_text):
    try:
        raw = page.evaluate(r"""() => {
            const tables = document.querySelectorAll('table');
            let targetTable = null;
            let headers = [];

            for (const tbl of tables) {
                const firstRow = tbl.querySelector('tr');
                if (!firstRow) continue;
                const texts = Array.from(firstRow.querySelectorAll('th, td'))
                    .map(c => c.textContent.trim().toLowerCase());
                if (texts.some(t => t === '#' || t === 'title' || t === 'source')) {
                    targetTable = tbl;
                    headers = texts;
                    break;
                }
            }

            if (!targetTable) return {headers: [], rows: []};

            const rows = [];
            Array.from(targetTable.querySelectorAll('tr')).slice(1).forEach(tr => {
                const cells = Array.from(tr.querySelectorAll('td')).map(td => {
                    const a = td.querySelector('a[href]');
                    return {text: td.textContent.trim(), href: a ? a.href : ''};
                });
                if (cells.length > 0) rows.push(cells);
            });
            return {headers, rows};
        }""")
    except Exception as exc:
        print(f"    [warn] JS table extraction failed: {exc}")
        return _fallback_links(page, date_text)

    if not raw or not raw.get("rows"):
        return _fallback_links(page, date_text)

    headers = raw.get("headers", [])
    col = {}
    for i, h in enumerate(headers):
        key = h.strip().lower().lstrip("#").strip()
        col[key] = i

    num_idx    = col.get("", col.get("num", 0))
    src_idx    = col.get("source", 1)
    agency_idx = col.get("agency", 2)
    fsg_idx    = col.get("fsg", 3)
    title_idx  = col.get("title", 4)
    kw_idx     = col.get("keywords", 5)

    def ct(cells, idx):
        return safe_text(cells[idx]["text"]) if idx < len(cells) else ""

    def ch(cells, idx):
        return safe_text(cells[idx]["href"]) if idx < len(cells) else ""

    articles = []
    for cells in raw.get("rows", []):
        title = ct(cells, title_idx)
        if not title:
            continue
        article_url = ch(cells, title_idx) or next(
            (safe_text(c["href"]) for c in cells if c.get("href")), ""
        )
        articles.append({
            "date_text":   date_text,
            "row_number":  ct(cells, num_idx),
            "source":      ct(cells, src_idx),
            "agency":      ct(cells, agency_idx),
            "fsg":         ct(cells, fsg_idx),
            "title":       title,
            "keywords":    ct(cells, kw_idx),
            "article_url": article_url,
        })
    return articles


def _fallback_links(page, date_text):
    articles = []
    try:
        links = page.evaluate(r"""() =>
            Array.from(document.querySelectorAll('a[href]'))
                .filter(a => a.href.includes('outreachsystems') || a.href.includes('go?'))
                .map(a => ({text: a.textContent.trim(), href: a.href}))
                .filter(a => a.text.length > 5)
        """)
        for i, lnk in enumerate(links or []):
            articles.append({
                "date_text":   date_text,
                "row_number":  str(i + 1),
                "source":      "",
                "agency":      "",
                "fsg":         "",
                "title":       safe_text(lnk.get("text")),
                "keywords":    "",
                "article_url": safe_text(lnk.get("href")),
            })
    except Exception as exc:
        print(f"    [warn] Fallback link extraction failed: {exc}")
    return articles


def load_daily_page(page, date_entry, debug_dir, do_debug):
    date_text = date_entry["date_text"]
    url       = date_entry["date_url"]
    print(f"  Daily page: {date_text}")
    errors = []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
    except PlaywrightTimeoutError:
        errors.append(f"Timeout loading daily page: {url}")
        return [], errors
    except Exception as exc:
        errors.append(f"Error loading daily page: {exc}")
        return [], errors

    if do_debug:
        slug = re.sub(r"[^a-z0-9]", "_", date_text.lower())[:30]
        save_debug_artifacts(page, debug_dir, f"daily_{slug}")

    articles = extract_daily_table(page, date_text)
    print(f"    {len(articles)} article(s) found.")
    return articles, errors


# ─── Article page ─────────────────────────────────────────────────────────────

def extract_sam_url(text, hrefs):
    for h in hrefs:
        if SAM_URL_RE.match(h):
            return h
    m = SAM_URL_RE.search(text)
    return m.group(0) if m else ""


def extract_source_url(text, hrefs):
    for h in hrefs:
        h_lower = h.lower()
        for domain, _ in THIRD_PARTY_DOMAINS:
            if domain in h_lower:
                return h
    for domain, _ in THIRD_PARTY_DOMAINS:
        if domain in text.lower():
            m = re.search(
                r"https?://[^\s\"'<>]*{}[^\s\"'<>]*".format(re.escape(domain)),
                text, re.IGNORECASE,
            )
            if m:
                return m.group(0)
    return ""


def detect_source_type(sam_url, source_url, text, hrefs):
    if sam_url:
        return "federal_sam"
    for h in hrefs:
        h_lower = h.lower()
        for domain, stype in THIRD_PARTY_DOMAINS:
            if domain in h_lower:
                return stype
    if SAM_REF_RE.search(text):
        return "federal_sam"
    newspaper_terms = [
        "newspaper", "public notice", "legal notice",
        "chronicle", "herald", "gazette", "tribune", "times",
    ]
    if any(t in text.lower() for t in newspaper_terms):
        return "newspaper_public_notice"
    state_re = re.compile(r"https?://[^\s]+\.(?:gov|us)\b", re.IGNORECASE)
    if any(state_re.search(h) for h in hrefs if h):
        return "state_portal"
    return "mybidmatch_only_unknown"


def detect_origin_trace(sam_url, source_url, text, has_warning):
    if sam_url:
        return "original_source_found"
    if SAM_REF_RE.search(text) and not sam_url:
        return "sam_reference_no_direct_url"
    if source_url:
        return "paywall_or_registration_possible" if has_warning else "needs_origin_trace"
    return "no_source_url_found"


def extract_article_fields(text):
    fields = dict(
        solicitation_number="", due_date="", set_aside="",
        naics="", poc_email="", agency_detail="", place_of_performance="",
    )
    m = NAICS_RE.search(text)
    if m:
        fields["naics"] = m.group(1)

    m = DUE_RE.search(text)
    if m:
        fields["due_date"] = m.group(1).strip()

    m = SET_ASIDE_RE.search(text)
    if m:
        fields["set_aside"] = m.group(1).strip()

    m = POC_EMAIL_RE.search(text)
    if m:
        fields["poc_email"] = m.group(0)

    m = SOL_LABELED_RE.search(text)
    if m:
        fields["solicitation_number"] = m.group(1).strip()
    else:
        m = SOL_PATTERN_RE.search(text)
        if m:
            cand = m.group(1)
            if len(cand) >= 8 and not cand.isdigit():
                fields["solicitation_number"] = cand

    m = re.search(r"Place\s+of\s+Performance\s*[:#]?\s*(.{5,80}?)(?:\n|$)", text, re.IGNORECASE)
    if m:
        fields["place_of_performance"] = m.group(1).strip()[:80]

    m = re.search(r"Agency\s*[:#]\s*(.{5,80}?)(?:\n|$)", text, re.IGNORECASE)
    if m:
        fields["agency_detail"] = m.group(1).strip()[:80]

    return fields


def load_article_page(page, article_url, debug_dir, do_debug):
    if not article_url:
        return {}
    try:
        page.goto(article_url, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1000)
    except PlaywrightTimeoutError:
        return {"parse_error": f"Timeout: {article_url}"}
    except Exception as exc:
        return {"parse_error": str(exc)}

    if do_debug:
        slug = re.sub(r"[^a-z0-9]", "_", urlparse(article_url).path)[:30]
        save_debug_artifacts(page, debug_dir, f"article_{slug}")

    try:
        full_text = page.locator("body").inner_text(timeout=5000)
    except Exception:
        full_text = ""

    try:
        html = page.content()
    except Exception:
        html = ""

    try:
        hrefs = page.evaluate(
            "() => Array.from(document.querySelectorAll('a[href]')).map(a => a.href)"
        )
    except Exception:
        hrefs = []

    combined = full_text + html
    sam_url    = extract_sam_url(combined, hrefs)
    source_url = extract_source_url(combined, hrefs)
    text_lower = full_text.lower()
    has_warning = any(phrase in text_lower for phrase in WARNING_PHRASES)
    source_type  = detect_source_type(sam_url, source_url, full_text, hrefs)
    origin_trace = detect_origin_trace(sam_url, source_url, full_text, has_warning)
    is_ss = any(t in text_lower for t in SOURCES_SOUGHT_TERMS)
    extracted = extract_article_fields(full_text)

    excerpt = re.sub(r"\s+", " ", full_text[:500]).strip()

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


# ─── Classification ───────────────────────────────────────────────────────────

def detect_lane(title, keywords, text_excerpt):
    combined = f"{title} {keywords} {text_excerpt}".lower()
    for lane, terms in LANE_PATTERNS:
        if any(t.lower() in combined for t in terms):
            return lane
    return "unknown"


def classify_lead(lead):
    title    = lead.get("title", "")
    keywords = lead.get("keywords", "")
    text     = lead.get("raw_text_excerpt", "")
    naics    = lead.get("naics", "")
    sam_url  = lead.get("sam_url", "")
    origin   = lead.get("origin_trace_status", "no_source_url_found")
    is_ss    = lead.get("is_sources_sought", False)
    warning  = lead.get("has_registration_warning", False)
    combined = f"{title} {keywords} {text}".lower()

    base_lane = detect_lane(title, keywords, text)
    for prefix, lane in NAICS_LANE_MAP.items():
        if naics.startswith(prefix):
            base_lane = lane
            break

    high_risk = any(t in combined for t in HIGH_RISK_TERMS)

    # Specialization
    if high_risk:
        spec = "highly_specialized_or_regulated"
    elif base_lane in ("medical_scientific_specialized",):
        spec = "highly_specialized_or_regulated"
    elif base_lane in ("legal_real_estate_other", "construction",
                       "ai_technology_training", "roofing_hvac_trades", "security_services"):
        spec = "moderately_specialized"
    elif base_lane in ("janitorial", "pest_control", "trucking_transportation",
                       "towing_hauling", "facilities_services", "commodities",
                       "marketing_communications"):
        spec = "routine_commercial"
    else:
        spec = "unknown"

    # Fulfillment + feasibility + risk
    if base_lane in ("marketing_communications", "ai_technology_training"):
        fp, feasibility, risk = "direct_prime", "easy_to_source", "low"
    elif high_risk or base_lane == "medical_scientific_specialized":
        fp, feasibility, risk = "prime_with_qualified_subcontractor", "rare_or_highly_regulated", "high"
    elif base_lane in ("janitorial", "pest_control", "trucking_transportation",
                       "towing_hauling", "facilities_services", "commodities"):
        fp, feasibility, risk = "prime_with_subcontractor", "easy_to_source", "low"
    elif base_lane in ("roofing_hvac_trades", "security_services", "construction"):
        fp, feasibility, risk = "prime_with_subcontractor", "moderate_to_source", "medium"
    elif base_lane == "legal_real_estate_other":
        fp, feasibility, risk = "manual_review", "unknown", "medium"
    else:
        fp, feasibility, risk = "manual_review", "unknown", "medium"

    # Recommended action
    if is_ss and (sam_url or origin == "original_source_found"):
        action = "route_to_sources_sought_planner"
    elif sam_url and fp == "direct_prime":
        action = "import_to_govcon_scout"
    elif sam_url:
        action = "queue_for_sam_processing"
    elif origin == "sam_reference_no_direct_url":
        action = "manual_review"
    elif warning or origin == "paywall_or_registration_possible":
        action = "trace_origin_then_decide_cost"
    elif fp in ("prime_with_subcontractor", "prime_with_qualified_subcontractor"):
        action = "find_subcontractor_then_bid_decision"
    elif fp == "direct_prime":
        action = "marketing_capability_review"
    elif fp == "commodity_sourcing":
        action = "commodity_source_and_quote"
    else:
        action = "manual_review"

    # Priority
    if base_lane in ("marketing_communications", "ai_technology_training"):
        priority = "High"
    elif sam_url and fp in ("direct_prime", "prime_with_subcontractor") and risk == "low":
        priority = "High"
    elif sam_url and risk == "medium":
        priority = "Medium"
    elif sam_url:
        priority = "Medium"
    elif warning or origin == "paywall_or_registration_possible":
        priority = "Low"
    elif risk == "high":
        priority = "Low"
    else:
        priority = "Low"

    return {
        "base_lane":               base_lane,
        "specialization_level":    spec,
        "fulfillment_path":        fp,
        "subcontractor_feasibility": feasibility,
        "prime_control_risk":      risk,
        "recommended_action":      action,
        "priority":                priority,
    }


# ─── Deduplication ────────────────────────────────────────────────────────────

def govcon_status(lead, known_ids):
    sam  = lead.get("sam_url", "").lower()
    sol  = lead.get("solicitation_number", "").lower()
    if (sam and sam in known_ids) or (sol and sol in known_ids):
        return "already_in_govcon_scout"
    if sol and any(sol in k for k in known_ids):
        return "already_in_govcon_scout"
    if lead.get("sam_url"):
        return "new_sam_ready"
    if lead.get("source_url") or lead.get("has_registration_warning"):
        return "non_sam_followup"
    return "unknown"


def dedupe_leads(leads):
    seen, out = {}, []
    for lead in leads:
        key = (
            lead.get("sam_url")
            or lead.get("solicitation_number")
            or "{}|{}|{}".format(
                lead.get("title", ""), lead.get("agency", ""), lead.get("date_text", "")
            )
        )
        key = safe_text(key).lower()
        if key and key in seen:
            continue
        if key:
            seen[key] = True
        out.append(lead)
    return out


# ─── CSV ──────────────────────────────────────────────────────────────────────

LEADS_COLUMNS = [
    "date_text", "row_number", "source", "agency", "fsg", "title", "keywords",
    "article_url", "sam_url", "source_url", "solicitation_number", "due_date",
    "set_aside", "naics", "poc_email", "base_lane", "specialization_level",
    "fulfillment_path", "subcontractor_feasibility", "prime_control_risk",
    "source_type", "origin_trace_status", "priority", "recommended_action",
    "govcon_status", "raw_text_excerpt",
]

SAM_QUEUE_COLUMNS = [
    "date_text", "title", "agency", "fsg", "sam_url", "solicitation_number",
    "due_date", "naics", "set_aside", "base_lane", "fulfillment_path",
    "prime_control_risk", "priority", "govcon_status", "recommended_action",
]


def write_csv(leads, path, columns):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(leads)
    print(f"  Written: {p}  ({len(leads)} rows)")


# ─── Markdown ─────────────────────────────────────────────────────────────────

def _row(lead):
    title   = safe_text(lead.get("title"))[:60].replace("|", "/")
    agency  = safe_text(lead.get("agency") or lead.get("source"))[:28].replace("|", "/")
    due     = safe_text(lead.get("due_date"))[:18]
    pri     = safe_text(lead.get("priority"))
    action  = safe_text(lead.get("recommended_action"))
    sam_lnk = f"[SAM]({lead['sam_url']})" if lead.get("sam_url") else "—"
    src_lnk = f"[src]({lead['source_url']})" if lead.get("source_url") else ""
    art_lnk = f"[article]({lead['article_url']})" if lead.get("article_url") else ""
    links   = " ".join(filter(None, [sam_lnk, src_lnk, art_lnk]))
    return f"| {title} | {agency} | {due} | {pri} | {action} | {links} |"


def _table(leads):
    if not leads:
        return "None."
    hdr = "| Title | Agency | Due | Priority | Action | Links |"
    sep = "|---|---|---|---|---|---|"
    return "\n".join([hdr, sep] + [_row(l) for l in leads])


def build_leads_report(leads, meta):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    errors = meta.get("errors", [])

    sam_new     = [l for l in leads if l.get("govcon_status") == "new_sam_ready"]
    sam_known   = [l for l in leads if l.get("govcon_status") == "already_in_govcon_scout"]
    high        = [l for l in leads if l.get("priority") == "High"]
    sub         = [l for l in leads if l.get("fulfillment_path") in (
                       "prime_with_subcontractor", "prime_with_qualified_subcontractor")]
    portal      = [l for l in leads if l.get("origin_trace_status") == "paywall_or_registration_possible"]
    ss          = [l for l in leads if l.get("is_sources_sought")]
    commodity   = [l for l in leads if l.get("fulfillment_path") == "commodity_sourcing"]
    hi_risk     = [l for l in leads if l.get("prime_control_risk") == "high"]
    followup    = [l for l in leads if l.get("govcon_status") == "non_sam_followup"]
    pass_watch  = [l for l in leads if l.get("recommended_action") == "pass_or_watch"]

    out = [
        "# MyBidMatch Browser Leads Report", "",
        f"**Generated:** {ts}",
        f"**Dates processed:** {meta.get('dates_processed', 0)}",
        f"**Articles processed:** {len(leads)}", "",
        "## Executive Summary", "",
        f"- **Total leads:** {len(leads)}",
        f"- **New SAM.gov leads:** {len(sam_new)}",
        f"- **Already in GovCon Scout:** {len(sam_known)}",
        f"- **High priority:** {len(high)}",
        f"- **Sources sought / RFI:** {len(ss)}",
        f"- **Third-party / paywall:** {len(portal)}",
        f"- **Non-SAM follow-up:** {len(followup)}",
    ]
    if errors:
        out.append(f"- **Parse errors / warnings:** {len(errors)}")
    out += [
        "",
        "MyBidMatch is a lead feed, not a source of truth. "
        "All federal leads should be traced to SAM.gov before scoring or processing.", "",
        "## SAM.gov Leads Ready for GovCon Scout", "", _table(sam_new), "",
        "## High Priority Leads", "", _table(high), "",
        "## Prime-With-Subcontractor Candidates", "", _table(sub), "",
        "## Third-Party Portal / Paywall Leads", "", _table(portal), "",
        "## Sources Sought / RFI Leads", "", _table(ss), "",
        "## Commodity Sourcing Leads", "", _table(commodity), "",
        "## High Prime-Control Risk / Specialist Review", "", _table(hi_risk), "",
        "## Non-SAM Follow-Up Queue", "", _table(followup), "",
        "## Pass / Watchlist", "", _table(pass_watch), "",
    ]

    if errors:
        out += ["## Errors / Parse Warnings", ""]
        for e in errors:
            out.append(f"- {e}")
        out.append("")

    out += ["## Recommended Next Actions", ""]
    out.append("1. **Verify and import SAM.gov leads** — confirm each notice is active before processing.")
    if sam_new:
        for lead in sam_new[:3]:
            sol = lead.get("solicitation_number") or "(unknown)"
            url = lead.get("sam_url") or ""
            out.append(f"   `python src/process_opportunity.py --notice-id {sol} --url \"{url}\"`")
    else:
        out.append("   No new SAM.gov leads this run.")
    out += [
        "2. **Trace third-party portal leads** before deciding if registration/payment is worth it.",
        "3. **Route sources-sought / RFI leads** to the sources-sought planner.",
        "4. **For prime-with-sub leads:** find 2–3 subcontractor quotes before bid/no-bid.",
        "5. **Re-run with `--limit-days 3`** to capture earlier dates.", "",
    ]

    return "\n".join(out)


def build_followup_report(leads):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    followup = [
        l for l in leads
        if l.get("govcon_status") == "non_sam_followup"
        or l.get("origin_trace_status") in (
            "paywall_or_registration_possible",
            "needs_origin_trace",
            "sam_reference_no_direct_url",
        )
    ]

    out = [
        "# MyBidMatch Follow-Up Queue", "",
        f"**Generated:** {ts}",
        f"**Items requiring follow-up:** {len(followup)}", "",
        "These leads were not directly traceable to a SAM.gov URL. "
        "Each requires a manual follow-up step before a bid/no-bid decision.", "",
    ]
    if not followup:
        out.append("No follow-up items this run.")
        return "\n".join(out)

    out += [
        "| Title | Source / Agency | Trace Status | Suggested Action | Links |",
        "|---|---|---|---|---|",
    ]
    for l in followup:
        title  = safe_text(l.get("title"))[:55].replace("|", "/")
        agency = safe_text(l.get("source") or l.get("agency"))[:25].replace("|", "/")
        trace  = safe_text(l.get("origin_trace_status"))
        action = safe_text(l.get("recommended_action"))
        art    = f"[article]({l['article_url']})" if l.get("article_url") else ""
        src    = f"[src]({l['source_url']})" if l.get("source_url") else ""
        links  = " ".join(filter(None, [art, src]))
        out.append(f"| {title} | {agency} | {trace} | {action} | {links} |")

    out.append("")
    return "\n".join(out)


# ─── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="MyBidMatch browser intake — collects and classifies bid leads via Playwright."
    )
    p.add_argument("--directory-url",    default=DEFAULT_DIRECTORY_URL,
                   help="MyBidMatch directory URL")
    p.add_argument("--limit-days",       type=int, default=DEFAULT_LIMIT_DAYS,
                   help="Number of date pages to process (default 1)")
    p.add_argument("--limit-articles",   type=int, default=DEFAULT_LIMIT_ARTICLES,
                   help="Max articles per date page (0 = no limit)")
    p.add_argument("--date",             default="",
                   help="Process a specific date, e.g. 'May 21, 2026'")
    p.add_argument("--output-csv",       default=DEFAULT_OUTPUT_CSV)
    p.add_argument("--sam-queue",        default=DEFAULT_SAM_QUEUE)
    p.add_argument("--report",           default=DEFAULT_REPORT)
    p.add_argument("--followup-report",  default=DEFAULT_FOLLOWUP)
    p.add_argument("--storage-state",    default=DEFAULT_STORAGE_STATE,
                   help="Playwright storage state (cookies) for OutreachSystems session")
    p.add_argument("--debug",            action="store_true",
                   help="Save HTML and screenshots to debug/mybidmatch/")
    p.add_argument("--headed",           action="store_true",
                   help="Run browser in headed mode for visual debugging")
    return p.parse_args()


def main():
    args      = parse_args()
    all_errors = []
    leads     = []
    dates_done = 0
    debug_dir = DEFAULT_DEBUG_DIR if args.debug else ""
    known_ids = load_existing_govcon_ids()

    print(f"GovCon Scout IDs loaded for dedup: {len(known_ids)}")
    if args.debug:
        Path(debug_dir).mkdir(parents=True, exist_ok=True)
        print(f"Debug artifacts → {debug_dir}/")

    storage_state = args.storage_state if Path(args.storage_state).exists() else None
    if storage_state:
        print(f"Session state loaded: {storage_state}")
    else:
        print(
            f"[warn] No session file found at '{args.storage_state}'. "
            "If the site returns 403, save your session first:\n"
            "  python src/save_mybidmatch_login.py"
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not args.headed,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx_kwargs = dict(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        page.add_init_script("delete Object.getPrototypeOf(navigator).webdriver")

        # ── Directory ──
        date_entries, dir_errors = load_directory(
            page, args.directory_url, debug_dir, args.debug
        )
        all_errors.extend(dir_errors)

        if not date_entries:
            print("\n[FAIL] No date entries found. Writing failure report and exiting.")
            fail_txt = (
                "# MyBidMatch Browser Leads Report\n\n"
                f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                "## Error\n\nNo date entries could be extracted from the directory page.\n\n"
                "**Possible causes:**\n"
                "- URL is wrong or has expired\n"
                "- Page structure has changed\n"
                "- Network/timeout issue\n\n"
                "**Errors:**\n" + "".join(f"- {e}\n" for e in all_errors)
            )
            out_p = Path(args.report)
            out_p.parent.mkdir(parents=True, exist_ok=True)
            out_p.write_text(fail_txt, encoding="utf-8")
            context.close()
            browser.close()
            return

        # ── Filter dates ──
        if args.date:
            filtered = [e for e in date_entries if args.date.lower() in e["date_text"].lower()]
            if not filtered:
                print(f"  [warn] No entries matched --date '{args.date}'. "
                      f"Available: {[e['date_text'] for e in date_entries[:5]]}")
            date_entries = filtered or date_entries[:1]
        else:
            date_entries = date_entries[:args.limit_days]

        print(f"Processing {len(date_entries)} date page(s).")

        # ── Daily + article pages ──
        for date_entry in date_entries:
            articles, day_errors = load_daily_page(
                page, date_entry, debug_dir, args.debug
            )
            all_errors.extend(day_errors)
            dates_done += 1

            if args.limit_articles > 0:
                articles = articles[:args.limit_articles]
                print(f"    Capped at {args.limit_articles} article(s).")

            for art in articles:
                short_title = safe_text(art.get("title"))[:55]
                print(f"    → {short_title}")
                detail = load_article_page(
                    page, art.get("article_url", ""), debug_dir, args.debug
                )
                if detail.get("parse_error"):
                    all_errors.append(
                        f"Article error [{art.get('title','?')[:40]}]: {detail['parse_error']}"
                    )

                art.update(detail)
                art.update(classify_lead(art))
                art["govcon_status"] = govcon_status(art, known_ids)
                leads.append(art)

        context.close()
        browser.close()

    leads     = dedupe_leads(leads)
    sam_queue = [l for l in leads if l.get("govcon_status") == "new_sam_ready"]

    # ── Write outputs ──
    write_csv(leads,     args.output_csv, LEADS_COLUMNS)
    write_csv(sam_queue, args.sam_queue,  SAM_QUEUE_COLUMNS)

    meta = {"dates_processed": dates_done, "errors": all_errors}

    for text, path in (
        (build_leads_report(leads, meta),  args.report),
        (build_followup_report(leads),     args.followup_report),
    ):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        print(f"  Written: {p}")

    # ── Terminal summary ──
    n_high    = sum(1 for l in leads if l.get("priority") == "High")
    n_known   = sum(1 for l in leads if l.get("govcon_status") == "already_in_govcon_scout")
    n_follow  = sum(1 for l in leads if l.get("govcon_status") == "non_sam_followup")
    hi_leads  = [l for l in leads if l.get("priority") == "High"]

    print("\n─── MyBidMatch Intake Summary ──────────────────────────")
    print(f"  Date pages processed:     {dates_done}")
    print(f"  Articles processed:       {len(leads)}")
    print(f"  New SAM-ready leads:      {len(sam_queue)}")
    print(f"  Already in GovCon Scout:  {n_known}")
    print(f"  Non-SAM follow-up:        {n_follow}")
    print(f"  High priority:            {n_high}")
    if hi_leads:
        print("  Top high-priority leads:")
        for l in hi_leads[:5]:
            print(f"    [{l.get('priority')}] {safe_text(l.get('title'))[:60]}")
    if all_errors:
        print(f"  Errors / warnings:        {len(all_errors)}")
        for e in all_errors[:5]:
            print(f"    - {e[:80]}")
    print(f"\n  Leads CSV:    {args.output_csv}")
    print(f"  SAM queue:    {args.sam_queue}")
    print(f"  Report:       {args.report}")
    print(f"  Follow-up:    {args.followup_report}")
    if args.debug:
        print(f"  Debug:        {debug_dir}/")
    print("────────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
