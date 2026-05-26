"""
Audit data/opportunity_state.csv for duplicate opportunity records.
Reports exact notice_id duplicates, source combinations, and field conflicts.
Writes: reports/audits/duplicate_opportunity_audit.md
"""

import csv
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATE_PATH = BASE_DIR / "data/opportunity_state.csv"
OUTPUT_PATH = BASE_DIR / "reports/audits/duplicate_opportunity_audit.md"

CONFLICT_FIELDS = [
    "title",
    "synopsis",
    "description",
    "requirements",
    "disqualifiers",
    "recommended_next_action",
    "source",
    "source_url",
    "due_date",
    "buyer_name",
    "buyer_email",
    "place_of_performance",
    "macro_stage",
    "ai_review_status",
]

SOURCE_PRIORITY = ["GovCon Scout", "SAM.gov"]


def safe_text(value):
    if value is None:
        return ""
    return str(value).strip()


def read_rows():
    if not STATE_PATH.exists():
        return []
    with STATE_PATH.open("r", encoding="utf-8", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def normalize_title(title):
    text = safe_text(title).lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def pick_primary(group):
    govcon = [r for r in group if "govcon" in safe_text(r.get("source")).lower()
              or "sam" in safe_text(r.get("source")).lower()]
    ai_reviewed = [r for r in govcon if safe_text(r.get("ai_review_status"))]
    if ai_reviewed:
        return ai_reviewed[0]
    if govcon:
        return govcon[0]
    ai_reviewed = [r for r in group if safe_text(r.get("ai_review_status"))]
    if ai_reviewed:
        return ai_reviewed[0]
    return group[0]


def conflicts(group):
    issues = []
    for field in CONFLICT_FIELDS:
        values = [safe_text(r.get(field)) for r in group]
        unique = list(dict.fromkeys(v for v in values if v))
        if len(unique) > 1:
            issues.append((field, unique))
    return issues


def run_audit():
    rows = read_rows()
    if not rows:
        print("No rows found in opportunity_state.csv")
        return

    by_id = defaultdict(list)
    for r in rows:
        nid = safe_text(r.get("notice_id"))
        if nid:
            by_id[nid].append(r)

    exact_dupes = {k: v for k, v in by_id.items() if len(v) > 1}

    # Title+due_date+agency near-duplicates (across different notice_ids)
    title_groups = defaultdict(list)
    for r in rows:
        key = (normalize_title(r.get("title", "")),
               safe_text(r.get("due_date")),
               safe_text(r.get("agency"))[:40].lower())
        if key[0]:
            title_groups[key].append(r)
    title_dupes = {k: v for k, v in title_groups.items() if len(v) > 1
                   and len(set(safe_text(r.get("notice_id")) for r in v)) > 1}

    # Source combinations
    from collections import Counter
    source_combos = Counter()
    has_ai = 0
    for nid, group in exact_dupes.items():
        sources = tuple(sorted(set(safe_text(r.get("source")) for r in group)))
        source_combos[sources] += 1
        if any(safe_text(r.get("ai_review_status")) for r in group):
            has_ai += 1

    lines = [
        "# Duplicate Opportunity Audit",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**Source file:** `data/opportunity_state.csv`",
        f"**Total rows:** {len(rows)}",
        "",
        "## Summary",
        "",
        f"- **Exact notice_id duplicates:** {len(exact_dupes)} groups ({sum(len(v) for v in exact_dupes.values()) - len(exact_dupes)} extra rows)",
        f"- **Near-duplicate groups (same title + due + agency, different IDs):** {len(title_dupes)}",
        f"- **Duplicate groups with AI Review applied:** {has_ai}",
        "",
        "## Source Combinations",
        "",
    ]
    for combo, count in source_combos.most_common():
        lines.append(f"- {' + '.join(combo) if combo else 'unknown'}: {count} groups")

    lines += ["", "## Exact notice_id Duplicates", ""]

    if not exact_dupes:
        lines.append("No exact duplicates found.")
    else:
        lines += [
            "| notice_id | Row count | Sources | Stages | AI Review | Field conflicts |",
            "|---|---|---|---|---|---|",
        ]
        for nid, group in sorted(exact_dupes.items()):
            sources = ", ".join(sorted(set(safe_text(r.get("source")) for r in group)))
            stages = ", ".join(sorted(set(safe_text(r.get("macro_stage")) for r in group)))
            ai_flags = ", ".join(sorted(set(safe_text(r.get("ai_review_status")) for r in group if safe_text(r.get("ai_review_status")))))
            conflict_list = conflicts(group)
            conflict_names = ", ".join(f[0] for f in conflict_list) if conflict_list else "none"
            lines.append(f"| {nid} | {len(group)} | {sources} | {stages} | {ai_flags or 'none'} | {conflict_names} |")

    lines += ["", "## Detailed Conflict Report (selected groups)", ""]

    shown = 0
    for nid, group in sorted(exact_dupes.items()):
        conflict_list = conflicts(group)
        if not conflict_list and shown > 5:
            continue
        primary = pick_primary(group)
        lines += [
            f"### {nid}",
            "",
            f"- **Primary row:** source={safe_text(primary.get('source'))}  stage={safe_text(primary.get('macro_stage'))}  ai_review={safe_text(primary.get('ai_review_status')) or 'none'}",
            f"- **Row count:** {len(group)}",
        ]
        for field, values in conflict_list:
            short_vals = [v[:120] for v in values]
            lines.append(f"- **{field} conflict:**")
            for i, v in enumerate(short_vals):
                src = safe_text(group[i].get("source")) if i < len(group) else "?"
                lines.append(f"  - [{src}]: {v}")
        lines.append("")
        shown += 1
        if shown >= 15:
            lines.append(f"_(remaining {len(exact_dupes) - shown} groups omitted — no additional conflicts)_")
            lines.append("")
            break

    lines += ["## Near-Duplicate Groups (different notice_ids, same title+due+agency)", ""]
    if not title_dupes:
        lines.append("None detected.")
    else:
        for (title_key, due, agency), group in list(title_dupes.items())[:10]:
            ids = ", ".join(safe_text(r.get("notice_id")) for r in group)
            sources = ", ".join(safe_text(r.get("source")) for r in group)
            lines.append(f"- **{title_key[:80]}** | due: {due} | agency: {agency}")
            lines.append(f"  - IDs: {ids}")
            lines.append(f"  - Sources: {sources}")
            lines.append("")

    lines += [
        "## Merge Policy",
        "",
        "When multiple rows share the same notice_id:",
        "",
        "1. **Primary row selection:** Prefer GovCon Scout / SAM.gov source. Among those, prefer AI-reviewed row.",
        "2. **merged_sources:** Computed in memory at dashboard load — primary row is tagged with all unique sources found.",
        "3. **Dashboard display:** Only the primary row is shown in queue tabs. Secondary rows remain in CSV (not deleted).",
        "4. **Apply to Card:** Updates the first matching row in CSV (which is the GovCon Scout / primary row for all known duplicates).",
        "5. **AI synopsis / requirements / disqualifiers:** Never overwritten by weaker MyBidMatch text.",
        "6. **Future builds:** `src/opportunity_state.py` will skip MyBidMatch rows whose notice_id already exists in GovCon Scout export.",
        "",
        "## Recommended Actions",
        "",
        "1. No immediate CSV modification required — dashboard deduplication handles display.",
        "2. Re-run `python src/opportunity_state.py` after next GovCon Scout export to rebuild with dedup logic.",
        "3. Review near-duplicate groups manually to confirm they are truly separate opportunities.",
    ]

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Audit written to: {OUTPUT_PATH.relative_to(BASE_DIR)}")
    print(f"Total rows: {len(rows)}")
    print(f"Exact duplicate groups: {len(exact_dupes)} ({sum(len(v) for v in exact_dupes.values()) - len(exact_dupes)} extra rows)")
    print(f"Source combos: {dict(source_combos.most_common())}")
    print(f"Groups with AI Review: {has_ai}")
    return exact_dupes


if __name__ == "__main__":
    run_audit()
