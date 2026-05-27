"""
GovCon Scout — Streamlit Operator Console v1

Parallel interface alongside the existing HTML dashboard (port 8765).
Run:
    export DISPLAY=:99
    streamlit run streamlit_app.py --server.port 8501 --server.address 127.0.0.1
"""

import csv as csv_module
import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import streamlit as st

# ── Path setup ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

STATE_CSV = ROOT / "data" / "opportunity_state.csv"
BACKUP_DIR = ROOT / "data" / "backups"
DOWNLOADS_DIR = ROOT / "downloads"
REPORTS_DIR = ROOT / "reports"
PROFILE_DIR = ROOT / ".browser" / "sam-profile"
AUTH_JSON = ROOT / "auth.json"
DEBUG_DIR = ROOT / "downloads" / "_debug"

# ── Module-level batch state (written by background thread) ──────────────────
_batch: dict = {
    "running": False,
    "done": False,
    "error": "",
    "total": 0,
    "completed": 0,
    "results": [],
    "log_lines": [],
    "report_path": "",
    "mode": "",           # "single" | "queue"
    "notice_id": "",
}
_batch_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def load_state() -> list[dict]:
    if not STATE_CSV.exists():
        return []
    with STATE_CSV.open("r", encoding="utf-8", newline="") as f:
        return list(csv_module.DictReader(f))


def save_state_with_backup(notice_id: str, updates: dict) -> str:
    """
    Write updates for a single row identified by notice_id.
    Creates a timestamped backup before any write.
    Only updates fields present in `updates`; never removes other fields.
    Returns backup path string.
    """
    if not STATE_CSV.exists():
        raise FileNotFoundError(f"State CSV not found: {STATE_CSV}")

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_before_stage_update_{stamp}.csv"
    shutil.copy2(STATE_CSV, backup_path)

    with STATE_CSV.open("r", encoding="utf-8", newline="") as f:
        reader = csv_module.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for row in rows:
        if row.get("notice_id") == notice_id:
            row.update(updates)
            row["last_updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            for field in updates:
                if field not in fieldnames:
                    fieldnames.append(field)
            break

    with STATE_CSV.open("w", encoding="utf-8", newline="") as f:
        writer = csv_module.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    return str(backup_path)


def format_bullets(text: str) -> list[str]:
    """Split bullet-text field into a clean list of strings."""
    if not text:
        return []
    lines = []
    for line in text.splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if line:
            lines.append(line)
    return lines


def find_reports(notice_id: str) -> dict:
    """Locate known report paths for a notice_id. Returns only existing files."""
    nid = notice_id.strip()
    found = {}

    checks = {
        "Analysis Packet": REPORTS_DIR / "analysis_packets" / f"{nid}.md",
        "AI Review": REPORTS_DIR / "ai_reviews" / f"{nid}_ai_review.md",
        "Card Update JSON": REPORTS_DIR / "ai_reviews" / f"{nid}_card_update.json",
        "Bid / No-Bid": REPORTS_DIR / "opportunity_reviews" / f"{nid}_bid_no_bid.md",
        "Decision Report": REPORTS_DIR / "opportunity_reviews" / f"{nid}_decision_report.md",
        "Compliance Matrix": REPORTS_DIR / "opportunity_reviews" / f"{nid}_compliance_matrix.md",
        "Pricing Schedule": REPORTS_DIR / "pricing" / f"{nid}_pricing_schedule.md",
        "Bid Price Sanity": REPORTS_DIR / "pricing" / f"{nid}_bid_price_sanity.md",
        "Sources Sought Plan": REPORTS_DIR / "sources_sought" / f"{nid}_sources_sought_plan.md",
        "Manual Review": REPORTS_DIR / "manual_review" / f"{nid}_manual_review.md",
        "Document Extracts": REPORTS_DIR / "document_extracts" / nid,
        "Downloads Folder": DOWNLOADS_DIR / nid,
    }
    for label, path in checks.items():
        if path.exists():
            found[label] = path
    return found


def run_cmd(args: list, timeout: int = 30, extra_env: dict | None = None) -> tuple[int, str, str]:
    """
    Run a subprocess safely. Returns (returncode, stdout, stderr).
    Never uses shell=True.
    """
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":99")}
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            cwd=str(ROOT),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError as exc:
        return -1, "", f"Command not found: {exc}"
    except Exception as exc:
        return -1, "", str(exc)[:200]


def _s(key: str, default=None):
    """Get session_state value with a default."""
    return st.session_state.get(key, default)


def _set(key: str, value):
    st.session_state[key] = value


def init_session_state():
    defaults = {
        "selected_id": None,
        "batch_started": False,
        "batch_last_snap": {},
        "ai_proposal": None,
        "ai_result_msg": "",
        "ai_running": False,
        "cmd_output": "",
        "cmd_label": "",
        "stage_msg": "",
        "note_text": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def status_box(label: str, value: str, color: str = "#4fc3f7"):
    st.markdown(
        f"""<div style="background:#1a1a2e;border-left:3px solid {color};
        padding:8px 12px;border-radius:4px;margin-bottom:6px;">
        <span style="color:#888;font-size:11px;">{label}</span><br>
        <span style="color:{color};font-size:13px;font-weight:600;">{value}</span>
        </div>""",
        unsafe_allow_html=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# Background batch thread
# ═══════════════════════════════════════════════════════════════════════════

def _start_batch_thread(mode: str, notice_id: str = "", limit: int = 10, force: bool = False):
    """
    Launch a background thread that calls run_batch (queue mode) or
    process_candidate (single mode). Writes progress to module-level _batch.
    """
    with _batch_lock:
        if _batch["running"]:
            return False, "A download is already running."
        _batch.update(
            running=True, done=False, error="", results=[], total=0,
            completed=0, log_lines=[], report_path="", mode=mode, notice_id=notice_id,
        )

    def _log(msg: str):
        with _batch_lock:
            _batch["log_lines"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _run_queue():
        try:
            from batch_download_docs import (
                run_batch, write_batch_report,
                BATCH_STAGES, DEFAULT_AUTH_STATE, DEFAULT_PROFILE_DIR,
                DEFAULT_DOWNLOADS_DIR, DEFAULT_EXTRACTS_DIR, DEFAULT_BATCH_RUNS_DIR,
            )
            _log(f"Starting queue batch — stages: {', '.join(sorted(BATCH_STAGES))} limit: {limit}")

            def cb(step, total, results_so_far):
                last = results_so_far[-1] if results_so_far else {}
                with _batch_lock:
                    _batch["total"] = total
                    _batch["completed"] = step
                    _batch["results"] = list(results_so_far)
                _log(f"[{step}/{total}] {last.get('notice_id','?')} — {last.get('status','?')}")

            results, error = run_batch(
                limit=limit,
                force=force,
                live=True,
                progress_callback=cb,
            )

            report_path = ""
            if results:
                stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
                rp = ROOT / "reports" / "batch_runs" / f"batch_download_docs_{stamp}.md"
                write_batch_report(results, str(rp), list(BATCH_STAGES), limit, force, True)
                report_path = str(rp.relative_to(ROOT))
                _log(f"Report: {report_path}")

            with _batch_lock:
                _batch.update(running=False, done=True, error=error, results=results, report_path=report_path)
        except Exception as exc:
            _log(f"Error: {exc}")
            with _batch_lock:
                _batch.update(running=False, done=True, error=str(exc)[:300])

    def _run_single():
        try:
            from batch_download_docs import (
                process_candidate, DEFAULT_PROFILE_DIR, DEFAULT_AUTH_STATE,
                DEFAULT_DOWNLOADS_DIR, profile_ready,
            )
            rows = load_state()
            row = next((r for r in rows if r.get("notice_id") == notice_id), None)
            if not row:
                with _batch_lock:
                    _batch.update(running=False, done=True, error=f"{notice_id} not found in state CSV.")
                return

            url = ""
            for field in ("source_url", "ui_link"):
                v = (row.get(field) or "").strip()
                if v.startswith("http"):
                    url = v
                    break
            if not url:
                with _batch_lock:
                    _batch.update(running=False, done=True, error=f"No source URL for {notice_id}.")
                return

            with _batch_lock:
                _batch["total"] = 1
            _log(f"Downloading {notice_id} from {url[:60]}…")

            prof = str(DEFAULT_PROFILE_DIR)
            result = process_candidate(
                notice_id=notice_id,
                url=url,
                auth_state=str(DEFAULT_AUTH_STATE),
                downloads_dir=str(DEFAULT_DOWNLOADS_DIR),
                headless=False,
                profile_dir=prof if profile_ready(prof) else "",
            )
            full_result = {**result, "notice_id": notice_id, "title": (row.get("title") or "")[:80]}
            _log(f"Result: {result.get('status','?')} — {result.get('attachment_count',0)} files")
            with _batch_lock:
                _batch.update(running=False, done=True, results=[full_result], completed=1)
        except Exception as exc:
            _log(f"Error: {exc}")
            with _batch_lock:
                _batch.update(running=False, done=True, error=str(exc)[:300])

    target = _run_queue if mode == "queue" else _run_single
    t = threading.Thread(target=target, daemon=True)
    t.start()
    return True, "Download started in background."


def snap_batch() -> dict:
    """Thread-safe snapshot of _batch for rendering."""
    with _batch_lock:
        return dict(_batch)


# ═══════════════════════════════════════════════════════════════════════════
# Page config and global CSS
# ═══════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="GovCon Scout — Operator Console",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  [data-testid="stAppViewContainer"] { background: #0d0d1a; }
  [data-testid="stSidebar"] { background: #111122; }
  h1, h2, h3 { color: #c8c8ff; }
  .stTabs [data-baseweb="tab-list"] { background: #111122; border-radius:6px; }
  .stTabs [data-baseweb="tab"] { color: #8888aa; }
  .stTabs [aria-selected="true"] { color: #00ff88 !important; }
  .stButton > button {
    background: #1a2a1a; color: #00cc66; border: 1px solid #00cc66;
    border-radius: 4px; font-size: 13px;
  }
  .stButton > button:hover { background: #00cc66; color: #000; }
  .stTextInput > div > input, .stSelectbox > div > div {
    background: #1a1a2e; color: #e0e0ff; border-color: #333366;
  }
  .stDataFrame { border: 1px solid #333366; border-radius: 4px; }
  div[data-testid="metric-container"] {
    background: #111130; border: 1px solid #222255;
    border-radius: 6px; padding: 8px;
  }
</style>
""", unsafe_allow_html=True)

init_session_state()


# ═══════════════════════════════════════════════════════════════════════════
# Sidebar — Browser / SAM Controls
# ═══════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("### 🎯 GovCon Scout")
    st.markdown("**Operator Console v1**")
    st.divider()

    # Live status indicators
    display_val = os.environ.get("DISPLAY", "")
    profile_exists = PROFILE_DIR.exists()
    auth_exists = AUTH_JSON.exists()

    status_box("DISPLAY", display_val or "⚠ not set", "#ffaa00" if not display_val else "#00cc66")
    status_box("SAM Profile", "✓ exists" if profile_exists else "✗ missing", "#00cc66" if profile_exists else "#ff5555")
    status_box("auth.json", "present (fallback)" if auth_exists else "absent", "#4fc3f7" if auth_exists else "#666688")

    st.divider()
    st.markdown("#### noVNC Controls")

    if st.button("Check noVNC Status", key="btn_novnc_check"):
        rc, out, err = run_cmd(["bash", "scripts/novnc_check.sh"], timeout=20)
        _set("cmd_output", (out + err).strip() or "(no output)")
        _set("cmd_label", "noVNC Status")

    if st.button("Reset noVNC", key="btn_novnc_reset"):
        _set("cmd_output", (
            "Run manually in terminal to avoid blocking Streamlit:\n\n"
            "  bash scripts/novnc_reset.sh\n"
            "  export DISPLAY=:99\n\n"
            "Then reload this page."
        ))
        _set("cmd_label", "noVNC Reset")

    st.divider()
    st.markdown("#### SAM.gov Login")

    if st.button("Open SAM.gov Login", key="btn_sam_open"):
        rc, out, err = run_cmd(
            ["bash", "scripts/open_sam_login_browser.sh", str(PROFILE_DIR)],
            timeout=8,
        )
        msg = out.strip() or (f"Error: {err.strip()}" if err.strip() else "Browser launched.")
        _set("cmd_output", msg)
        _set("cmd_label", "Open SAM Login")

    if st.button("Check SAM Session", key="btn_sam_check"):
        rc, out, err = run_cmd(
            ["python", "scripts/check_sam_profile_session.py", "--profile-dir", str(PROFILE_DIR)],
            timeout=60,
        )
        _set("cmd_output", (out + err).strip() or "(no output)")
        _set("cmd_label", "SAM Session Check")

    if st.button("Show Debug Screenshot Path", key="btn_debug_ss"):
        png = DEBUG_DIR / "sam_profile_check.png"
        html = DEBUG_DIR / "sam_profile_check.html"
        lines = []
        if png.exists():
            lines.append(f"PNG:  {png.relative_to(ROOT)}")
        if html.exists():
            lines.append(f"HTML: {html.relative_to(ROOT)}")
        _set("cmd_output", "\n".join(lines) or "No debug artifacts found.")
        _set("cmd_label", "Debug Artifacts")

    if _s("cmd_output"):
        st.markdown(f"**{_s('cmd_label', 'Output')}**")
        st.code(_s("cmd_output"), language="text")

    st.divider()
    st.caption("HTML dashboard: port 8765")
    st.caption("noVNC: port 6080 → vnc.html")
    st.caption(".browser/sam-profile is local-only")
    st.caption("auth.json is fallback only")


# ═══════════════════════════════════════════════════════════════════════════
# Header
# ═══════════════════════════════════════════════════════════════════════════

st.markdown("# 🎯 GovCon Scout — Operator Console")

rows_all = load_state()
total_rows = len(rows_all)
active_rows = [r for r in rows_all if r.get("macro_stage") not in {"Pass", "Archive", "Done", "Triage"}]
dev_rows = [r for r in rows_all if r.get("macro_stage") == "Development"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Opportunities", total_rows)
col2.metric("Active (non-Triage)", len(active_rows))
col3.metric("Development", len(dev_rows))
col4.metric("AI Review Pending", sum(1 for r in rows_all if r.get("ai_review_status") in {"", None, "pending"}
                                     and r.get("macro_stage") in {"AI Review", "Development"}))

st.divider()


# ═══════════════════════════════════════════════════════════════════════════
# Tabs
# ═══════════════════════════════════════════════════════════════════════════

tab_queue, tab_workbench, tab_docs, tab_ai, tab_reports = st.tabs([
    "📋 Opportunity Queue",
    "🔍 Workbench",
    "📥 Documents & Batch",
    "🤖 AI Review",
    "📁 Reports",
])


# ═══════════════════════════════════════════════════════════════════════════
# Tab 1 — Opportunity Queue
# ═══════════════════════════════════════════════════════════════════════════

with tab_queue:
    st.markdown("### Opportunity Queue")

    # Filters
    fc1, fc2, fc3, fc4 = st.columns([3, 2, 2, 2])
    search_text = fc1.text_input("Search title / notice_id / agency", key="filter_search").lower()

    all_stages = sorted({r.get("macro_stage", "") for r in rows_all if r.get("macro_stage")})
    stage_filter = fc2.multiselect("Stage", all_stages, default=[])

    all_sources = sorted({r.get("source", "") for r in rows_all if r.get("source")})
    source_filter = fc3.multiselect("Source", all_sources, default=[])

    hide_inactive = fc4.checkbox("Hide Triage/Pass/Archive/Done", value=True, key="filter_hide")

    HIDE_STAGES = {"Triage", "Pass", "Archive", "Done"}

    def row_matches(r):
        stage = r.get("macro_stage", "")
        if hide_inactive and stage in HIDE_STAGES:
            return False
        if stage_filter and stage not in stage_filter:
            return False
        if source_filter and r.get("source", "") not in source_filter:
            return False
        if search_text:
            haystack = " ".join([
                r.get("notice_id", ""), r.get("title", ""), r.get("agency", ""),
            ]).lower()
            if search_text not in haystack:
                return False
        return True

    filtered = [r for r in rows_all if row_matches(r)]
    st.caption(f"{len(filtered)} of {total_rows} rows shown")

    if not filtered:
        st.info("No rows match current filters.")
    else:
        display_cols = [
            "notice_id", "title", "macro_stage", "due_date", "source",
            "ai_review_status", "document_status", "recommended_next_action",
        ]
        import pandas as pd
        df = pd.DataFrame(filtered)[
            [c for c in display_cols if c in (filtered[0] if filtered else {})]
        ]
        # Trim long text columns for display
        for col in ["title", "recommended_next_action", "document_status"]:
            if col in df.columns:
                df[col] = df[col].str.slice(0, 80)

        st.dataframe(df, use_container_width=True, hide_index=True, height=320)

        # Selection
        id_labels = [f"{r['notice_id']} — {(r.get('title') or '')[:60]}" for r in filtered]
        selected_label = st.selectbox(
            "Select opportunity",
            options=["(none)"] + id_labels,
            key="queue_select",
        )
        if selected_label != "(none)":
            selected_nid = selected_label.split(" — ")[0].strip()
            if selected_nid != _s("selected_id"):
                _set("selected_id", selected_nid)
                _set("ai_proposal", None)
                _set("ai_result_msg", "")
                _set("stage_msg", "")
                _set("note_text", "")
            st.success(f"Selected: **{_s('selected_id')}** — switch to Workbench tab.")


# ═══════════════════════════════════════════════════════════════════════════
# Tab 2 — Workbench
# ═══════════════════════════════════════════════════════════════════════════

with tab_workbench:
    nid = _s("selected_id")
    if not nid:
        st.info("Select an opportunity in the Queue tab first.")
    else:
        row = next((r for r in rows_all if r.get("notice_id") == nid), None)
        if not row:
            st.error(f"notice_id {nid} not found in state CSV.")
        else:
            st.markdown(f"### {row.get('title') or nid}")

            # Card columns
            c1, c2 = st.columns(2)
            with c1:
                status_box("Notice ID", nid)
                status_box("Stage", row.get("macro_stage") or "—")
                status_box("Due Date", row.get("due_date") or "—", "#ffaa00")
                status_box("AI Review", row.get("ai_review_status") or "—")
                status_box("Docs", (row.get("document_status") or "—")[:60])
            with c2:
                status_box("Agency", (row.get("agency") or "—")[:70])
                status_box("Source", row.get("source") or "—")
                status_box("Place of Performance", row.get("place_of_performance") or "—")
                status_box("Set-Aside", row.get("set_aside") or "—", "#cc88ff")
                status_box("Fit Score", row.get("fit_score") or "—", "#4fc3f7")

            # Synopsis
            synopsis = row.get("synopsis") or row.get("description") or ""
            if synopsis:
                with st.expander("Synopsis / Description"):
                    st.write(synopsis[:2000])

            # AI Summary
            ai_sum = row.get("ai_summary") or ""
            if ai_sum:
                with st.expander("AI Summary"):
                    st.write(ai_sum[:1500])

            # Requirements / Disqualifiers
            req_bullets = format_bullets(row.get("requirements") or "")
            dis_bullets = format_bullets(row.get("disqualifiers") or "")
            rb1, rb2 = st.columns(2)
            with rb1:
                if req_bullets:
                    with st.expander(f"Requirements ({len(req_bullets)})"):
                        for b in req_bullets:
                            st.markdown(f"- {b}")
            with rb2:
                if dis_bullets:
                    with st.expander(f"Disqualifiers ({len(dis_bullets)})"):
                        for b in dis_bullets:
                            st.markdown(f"- ⚠ {b}")

            # Recommended next action
            rna = row.get("recommended_next_action") or ""
            if rna:
                st.markdown("**Recommended Next Action**")
                st.info(rna[:600])

            # Operator questions
            oq = row.get("operator_questions") or ""
            if oq:
                with st.expander("Operator Questions"):
                    for b in format_bullets(oq):
                        st.markdown(f"- {b}")

            st.divider()
            st.markdown("#### Stage Actions")

            STAGE_MOVES = [
                ("Move to Intake", "Intake"),
                ("Move to Manual Review", "Manual Review"),
                ("Move to AI Review", "AI Review"),
                ("Move to Development", "Development"),
                ("Mark Pass", "Pass"),
                ("Mark Archive", "Archive"),
            ]

            btn_cols = st.columns(len(STAGE_MOVES))
            for i, (label, new_stage) in enumerate(STAGE_MOVES):
                current_stage = row.get("macro_stage", "")
                if btn_cols[i].button(
                    label,
                    key=f"stage_{new_stage}",
                    disabled=(current_stage == new_stage),
                    help=f"Move to {new_stage}",
                ):
                    try:
                        backup = save_state_with_backup(nid, {
                            "macro_stage": new_stage,
                            "last_operator_action": f"stage_set_{new_stage.lower().replace(' ','_')}",
                        })
                        _set("stage_msg", f"✓ Moved to {new_stage}. Backup: {Path(backup).name}")
                        st.rerun()
                    except Exception as exc:
                        _set("stage_msg", f"Error: {exc}")

            if _s("stage_msg"):
                st.success(_s("stage_msg"))

            # Watch list toggle
            is_watched = (row.get("watch_list") or "").lower() in {"1", "true", "yes", "watch", "watched", "on"}
            watch_label = "Remove from Watch List" if is_watched else "Add to Watch List"
            if st.button(watch_label, key="btn_watch"):
                new_val = "" if is_watched else "watch"
                try:
                    save_state_with_backup(nid, {"watch_list": new_val})
                    _set("stage_msg", f"✓ Watch list updated.")
                    st.rerun()
                except Exception as exc:
                    _set("stage_msg", f"Error: {exc}")

            # Note
            st.divider()
            st.markdown("#### Add Operator Note")
            note_text = st.text_area("Note", value=_s("note_text"), key="note_input", height=80)
            if st.button("Save Note", key="btn_save_note"):
                if note_text.strip():
                    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                    existing = (row.get("last_call_notes") or "").strip()
                    combined = f"{existing}\n[{stamp}] {note_text.strip()}".strip()
                    try:
                        save_state_with_backup(nid, {
                            "last_call_notes": combined,
                            "last_operator_action": "note_added",
                        })
                        _set("stage_msg", "✓ Note saved.")
                        _set("note_text", "")
                        st.rerun()
                    except Exception as exc:
                        _set("stage_msg", f"Error: {exc}")


# ═══════════════════════════════════════════════════════════════════════════
# Tab 3 — Documents & Batch
# ═══════════════════════════════════════════════════════════════════════════

with tab_docs:
    nid = _s("selected_id")

    st.markdown("### Document Downloads")

    # Safety warnings
    if not os.environ.get("DISPLAY"):
        st.warning("DISPLAY is not set. Run `export DISPLAY=:99` before starting downloads.")
    if not PROFILE_DIR.exists() and not AUTH_JSON.exists():
        st.warning("No SAM.gov session found. Open SAM.gov Login first.")

    dc1, dc2 = st.columns(2)

    with dc1:
        st.markdown("#### Single Opportunity")
        if not nid:
            st.info("Select an opportunity in the Queue tab first.")
        else:
            st.markdown(f"Selected: **{nid}**")
            force_single = st.checkbox("Force re-download", key="force_single")
            if st.button("Download Docs for Selected", key="btn_dl_single"):
                snap = snap_batch()
                if snap["running"]:
                    st.warning("A download is already running. Check Batch Progress below.")
                else:
                    ok, msg = _start_batch_thread("single", notice_id=nid, force=force_single)
                    _set("batch_started", True)
                    st.success(msg) if ok else st.error(msg)

    with dc2:
        st.markdown("#### Queue Batch")
        from batch_download_docs import BATCH_STAGES as _BATCH_STAGES
        st.caption(f"Stages: {', '.join(sorted(_BATCH_STAGES))}")
        limit_val = st.number_input("Limit", min_value=1, max_value=50, value=5, key="batch_limit")
        force_queue = st.checkbox("Force re-download", key="force_queue")
        if st.button("Download Docs for Eligible Queue", key="btn_dl_queue"):
            snap = snap_batch()
            if snap["running"]:
                st.warning("A download is already running. Check Batch Progress below.")
            else:
                ok, msg = _start_batch_thread("queue", limit=int(limit_val), force=force_queue)
                _set("batch_started", True)
                st.success(msg) if ok else st.error(msg)

    st.divider()
    st.markdown("### Batch Progress")

    bsnap = snap_batch()

    if not _s("batch_started") and not bsnap["running"] and not bsnap["done"]:
        st.caption("No batch started in this session.")
    else:
        bc1, bc2, bc3, bc4 = st.columns(4)
        if bsnap["running"]:
            bc1.metric("Status", "Running ▶")
        elif bsnap["done"] and not bsnap["error"]:
            bc1.metric("Status", "Done ✓")
        elif bsnap["done"] and bsnap["error"]:
            bc1.metric("Status", "Done (error)")
        else:
            bc1.metric("Status", "Idle")

        bc2.metric("Total", bsnap["total"])
        bc3.metric("Completed", bsnap["completed"])
        failed_count = sum(1 for r in bsnap["results"] if (r.get("status") or "").startswith("failed_"))
        bc4.metric("Failed", failed_count)

        if bsnap["error"]:
            st.error(f"Error: {bsnap['error']}")

        if bsnap["report_path"]:
            st.success(f"Report: `{bsnap['report_path']}`")

        if bsnap["log_lines"]:
            with st.expander("Log", expanded=bsnap["running"]):
                st.code("\n".join(bsnap["log_lines"][-40:]), language="text")

        if bsnap["results"]:
            import pandas as pd
            res_df = pd.DataFrame(bsnap["results"])[[
                c for c in ["notice_id", "status", "attachment_count", "error"]
                if c in bsnap["results"][0]
            ]]
            st.dataframe(res_df, use_container_width=True, hide_index=True)

        colr, colc = st.columns(2)
        if colr.button("Refresh Status", key="btn_batch_refresh"):
            _set("batch_last_snap", snap_batch())
            st.rerun()

        if bsnap["running"]:
            if colc.button("Cancel (stop after current)", key="btn_batch_cancel"):
                st.warning("Cancel not supported in Streamlit v1. Wait for current download to finish.")

    st.divider()
    st.markdown("### SAM.gov Login Gate")
    st.markdown(
        "Keep the noVNC tab open. Inside noVNC, complete SAM.gov / Login.gov / MFA. "
        "When SAM.gov shows you are signed in, **close only the Chromium/SAM.gov window "
        "inside noVNC** (do not close the noVNC tab).",
    )
    login_col1, login_col2 = st.columns(2)
    if login_col1.button("Open SAM.gov Login (noVNC)", key="btn_docs_sam_open"):
        rc, out, err = run_cmd(
            ["bash", "scripts/open_sam_login_browser.sh", str(PROFILE_DIR)],
            timeout=8,
        )
        msg = out.strip() or (f"Error: {err.strip()}" if err.strip() else "Browser launched.")
        _set("cmd_output", msg)
        _set("cmd_label", "Open SAM Login")
        st.success(msg[:200])

    if login_col2.button("Check SAM Session", key="btn_docs_sam_check"):
        rc, out, err = run_cmd(
            ["python", "scripts/check_sam_profile_session.py", "--profile-dir", str(PROFILE_DIR)],
            timeout=60,
        )
        result_text = (out + err).strip()
        if "FAIL" in result_text or rc != 0:
            st.error(result_text[:400] or "Session check failed.")
        else:
            st.success(result_text[:400] or "Session check passed.")


# ═══════════════════════════════════════════════════════════════════════════
# Tab 4 — AI Review
# ═══════════════════════════════════════════════════════════════════════════

with tab_ai:
    nid = _s("selected_id")
    st.markdown("### AI Review")

    if not nid:
        st.info("Select an opportunity in the Queue tab first.")
    else:
        row = next((r for r in rows_all if r.get("notice_id") == nid), None)
        if not row:
            st.error(f"{nid} not found.")
        else:
            st.markdown(f"**{row.get('title') or nid}** — Stage: `{row.get('macro_stage','?')}` — AI Status: `{row.get('ai_review_status') or 'none'}`")

            # Check API key
            if not os.environ.get("ANTHROPIC_API_KEY") and not (ROOT / ".env").exists():
                st.warning("ANTHROPIC_API_KEY is not set. AI Review will fail without it.")

            ai_col1, ai_col2 = st.columns(2)

            if ai_col1.button("Run AI Review", key="btn_ai_run", disabled=_s("ai_running")):
                _set("ai_running", True)
                _set("ai_proposal", None)
                _set("ai_result_msg", "")
                with st.spinner("Running AI Review (this may take 30–90 seconds)…"):
                    try:
                        from ai_review_generator import run_ai_review
                        result, status_code = run_ai_review(nid)
                        if status_code == 200:
                            _set("ai_proposal", result.get("proposed_update"))
                            review_path = result.get("review_path") or ""
                            _set("ai_result_msg", f"✓ Review complete. Report: {review_path}")
                        else:
                            _set("ai_result_msg", f"Error ({status_code}): {result.get('message','?')}")
                    except Exception as exc:
                        _set("ai_result_msg", f"Exception: {exc}")
                _set("ai_running", False)

            if _s("ai_result_msg"):
                if "Error" in _s("ai_result_msg") or "Exception" in _s("ai_result_msg"):
                    st.error(_s("ai_result_msg"))
                else:
                    st.success(_s("ai_result_msg"))

            # Show existing review files
            ai_md = REPORTS_DIR / "ai_reviews" / f"{nid}_ai_review.md"
            ai_json = REPORTS_DIR / "ai_reviews" / f"{nid}_card_update.json"

            if ai_col2.button("View AI Review Markdown", key="btn_ai_view_md"):
                if ai_md.exists():
                    with st.expander("AI Review Markdown", expanded=True):
                        st.markdown(ai_md.read_text(encoding="utf-8"))
                else:
                    st.warning(f"No AI review file found: {ai_md.name}")

            if st.button("View Card Update JSON", key="btn_ai_view_json"):
                if ai_json.exists():
                    with st.expander("Card Update JSON", expanded=True):
                        st.json(json.loads(ai_json.read_text(encoding="utf-8")))
                else:
                    st.warning(f"No card update JSON found: {ai_json.name}")

            # Proposed update panel
            proposal = _s("ai_proposal")
            if proposal:
                st.divider()
                st.markdown("#### Proposed Card Update")

                for field in ["ai_summary", "recommended_next_action"]:
                    if proposal.get(field):
                        st.markdown(f"**{field}**")
                        st.info(proposal[field][:600])

                for field in ["requirements", "disqualifiers", "documents_missing", "operator_questions"]:
                    bullets = format_bullets(proposal.get(field) or "")
                    if bullets:
                        with st.expander(f"{field} ({len(bullets)})"):
                            for b in bullets:
                                st.markdown(f"- {b}")

                for field in ["pricing_status", "site_visit_status", "submission_status",
                               "prime_or_teaming_recommendation", "hard_disqualifier_summary"]:
                    if proposal.get(field):
                        status_box(field, proposal[field][:80])

                st.divider()
                if st.button("Apply AI Review to Card", key="btn_ai_apply", type="primary"):
                    try:
                        from ai_review_generator import apply_ai_review
                        result, status_code = apply_ai_review(nid, proposal)
                        if status_code == 200:
                            updated = result.get("updated_fields", [])
                            backup = result.get("backup_path", "")
                            _set("ai_result_msg", f"✓ Applied {len(updated)} fields. Backup: {Path(backup).name if backup else 'n/a'}")
                            _set("ai_proposal", None)
                            st.rerun()
                        else:
                            st.error(f"Apply failed ({status_code}): {result.get('message','?')}")
                    except Exception as exc:
                        st.error(f"Apply exception: {exc}")

                if st.button("Discard Proposal", key="btn_ai_discard"):
                    _set("ai_proposal", None)
                    _set("ai_result_msg", "Proposal discarded.")
                    st.rerun()


# ═══════════════════════════════════════════════════════════════════════════
# Tab 5 — Reports
# ═══════════════════════════════════════════════════════════════════════════

with tab_reports:
    nid = _s("selected_id")
    st.markdown("### Reports")

    if not nid:
        st.info("Select an opportunity in the Queue tab first.")
    else:
        st.markdown(f"Reports for **{nid}**")
        found = find_reports(nid)

        if not found:
            st.warning(f"No report files found for {nid}.")
        else:
            for label, path in found.items():
                c1, c2 = st.columns([3, 1])
                is_dir = path.is_dir()
                rel = str(path.relative_to(ROOT))
                c1.markdown(f"**{label}** — `{rel}`")

                if not is_dir and path.suffix in {".md", ".txt"}:
                    if c2.button("View", key=f"view_{label}_{nid}"):
                        with st.expander(label, expanded=True):
                            content = path.read_text(encoding="utf-8", errors="replace")
                            if path.suffix == ".md":
                                st.markdown(content[:8000])
                            else:
                                st.code(content[:8000], language="text")
                elif not is_dir and path.suffix == ".json":
                    if c2.button("View", key=f"view_{label}_{nid}"):
                        with st.expander(label, expanded=True):
                            try:
                                st.json(json.loads(path.read_text(encoding="utf-8")))
                            except Exception:
                                st.code(path.read_text(encoding="utf-8")[:4000])
                elif is_dir:
                    files = sorted(path.iterdir())
                    c2.caption(f"{len(files)} files")
                    if c2.button("List", key=f"list_{label}_{nid}"):
                        with st.expander(f"{label} contents", expanded=True):
                            for f in files[:50]:
                                st.caption(f"  {f.name}  ({f.stat().st_size // 1024} KB)")

        st.divider()
        st.markdown("#### Recent Batch Reports")
        batch_runs = sorted((REPORTS_DIR / "batch_runs").glob("batch_download_docs_*.md"), reverse=True)
        for rp in batch_runs[:8]:
            if st.button(rp.name, key=f"batchrpt_{rp.name}"):
                with st.expander(rp.name, expanded=True):
                    st.markdown(rp.read_text(encoding="utf-8")[:6000])
