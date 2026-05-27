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

import anthropic
import streamlit as st

# ── Path setup ───────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

STATE_JSON      = ROOT / "data" / "opportunity_state.json"
BACKUP_DIR      = ROOT / "data" / "backups"
DOWNLOADS_DIR   = ROOT / "downloads"
REPORTS_DIR     = ROOT / "reports"
PROFILE_DIR     = ROOT / ".browser" / "sam-profile"
AUTH_JSON       = ROOT / "auth.json"
DEBUG_DIR       = ROOT / "downloads" / "_debug"
BATCH_RUNS_DIR  = ROOT / "reports" / "batch_runs"
BATCH_STATUS_FILE = BATCH_RUNS_DIR / "streamlit_batch_status.json"

# ── Module-level in-memory batch state (written by background thread) ─────────
_batch: dict = {
    "running": False, "done": False, "error": "", "total": 0,
    "completed": 0, "results": [], "log_lines": [], "report_path": "",
    "mode": "", "notice_id": "",
    "auto_analyze": False, "session_expired": False,
}
_batch_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════
# Durable batch status — survives page reloads and session restarts
# ═══════════════════════════════════════════════════════════════════════════

def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def write_batch_status(updates: dict):
    """Merge updates into the durable JSON status file."""
    BATCH_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    current = read_batch_status()
    current.update(updates)
    current["updated_at"] = _now_iso()
    try:
        BATCH_STATUS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
    except Exception:
        pass  # never let a status write crash the thread


def read_batch_status() -> dict:
    defaults = {
        "status": "idle",
        "started_at": "",
        "updated_at": "",
        "notice_id": "",
        "mode": "",
        "message": "",
        "report_path": "",
        "downloads_dir": "",
        "downloaded_count": 0,
        "skipped_count": 0,
        "failed_count": 0,
        "results": [],
        "log_lines": [],
        "debug_html": "",
        "debug_png": "",
        "error": "",
        "session_expired": False,
    }
    if not BATCH_STATUS_FILE.exists():
        return defaults
    try:
        data = json.loads(BATCH_STATUS_FILE.read_text(encoding="utf-8"))
        defaults.update(data)
        return defaults
    except Exception:
        return defaults


def is_stale_running(status: dict) -> bool:
    """
    Returns True if the JSON says 'running' but no background thread is
    active in this process and no recent batch report was created.
    """
    if status.get("status") != "running":
        return False
    with _batch_lock:
        if _batch["running"]:
            return False  # a real thread is running in this process
    started = status.get("started_at", "")
    if started:
        try:
            age = (datetime.now() - datetime.fromisoformat(started)).total_seconds()
            if age < 30:
                return False  # too fresh to call stale
        except Exception:
            pass
    return True


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def load_state() -> list[dict]:
    if not STATE_JSON.exists():
        return []
    try:
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_state_with_backup(notice_id: str, updates: dict) -> str:
    rows = load_state()
    if not rows and not STATE_JSON.exists():
        # Check for legacy CSV to migrate
        legacy_csv = STATE_JSON.with_suffix(".csv")
        if legacy_csv.exists():
             with legacy_csv.open("r", encoding="utf-8") as f:
                 rows = list(csv_module.DictReader(f))

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"opportunity_state_before_update_{stamp}.json"
    if STATE_JSON.exists():
        shutil.copy2(STATE_JSON, backup_path)

    for row in rows:
        if row.get("notice_id") == notice_id:
            row.update(updates)
            row["last_updated"] = _now_iso()
            break
    STATE_JSON.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return str(backup_path)


def format_bullets(text: str) -> list[str]:
    if not text:
        return []
    lines = []
    for line in text.splitlines():
        line = line.strip().lstrip("-•* ").strip()
        if line:
            lines.append(line)
    return lines


def find_reports(notice_id: str) -> dict:
    nid = notice_id.strip()
    checks = {
        "Analysis Packet":  REPORTS_DIR / "analysis_packets" / f"{nid}.md",
        "AI Review":        REPORTS_DIR / "ai_reviews" / f"{nid}_ai_review.md",
        "Card Update JSON": REPORTS_DIR / "ai_reviews" / f"{nid}_card_update.json",
        "Bid / No-Bid":     REPORTS_DIR / "opportunity_reviews" / f"{nid}_bid_no_bid.md",
        "Decision Report":  REPORTS_DIR / "opportunity_reviews" / f"{nid}_decision_report.md",
        "Compliance Matrix":REPORTS_DIR / "opportunity_reviews" / f"{nid}_compliance_matrix.md",
        "Pricing Schedule": REPORTS_DIR / "pricing" / f"{nid}_pricing_schedule.md",
        "Bid Price Sanity": REPORTS_DIR / "pricing" / f"{nid}_bid_price_sanity.md",
        "Sources Sought":   REPORTS_DIR / "sources_sought" / f"{nid}_sources_sought_plan.md",
        "Manual Review":    REPORTS_DIR / "manual_review" / f"{nid}_manual_review.md",
        "Document Extracts":REPORTS_DIR / "document_extracts" / nid,
        "Downloads Folder": DOWNLOADS_DIR / nid,
    }
    return {lbl: p for lbl, p in checks.items() if p.exists()}


def find_recent_files_for_notice(notice_id: str) -> dict:
    """
    Returns a dict of category → list-of-(path, size_kb) for every
    download/extract/review file associated with notice_id.
    Also includes debug artifacts.
    """
    nid = (notice_id or "").strip()
    result: dict[str, list] = {}

    def _list(d: Path, glob: str = "*") -> list:
        if not d.exists():
            return []
        files = sorted(d.glob(glob), key=lambda p: p.stat().st_mtime, reverse=True)
        return [(p, max(1, p.stat().st_size // 1024)) for p in files if p.is_file()]

    if nid:
        dl = _list(DOWNLOADS_DIR / nid)
        if dl:
            result["downloads/" + nid] = dl

        ex = _list(REPORTS_DIR / "document_extracts" / nid)
        if ex:
            result["document_extracts/" + nid] = ex

        or_files = _list(REPORTS_DIR / "opportunity_reviews", f"{nid}_*.md")
        if or_files:
            result["opportunity_reviews"] = or_files

    # Recent batch reports (last 10 minutes)
    batch_reports = []
    for p in BATCH_RUNS_DIR.glob("batch_download_docs_*.md"):
        try:
            age = (datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)).total_seconds()
            if age < 600:
                batch_reports.append((p, max(1, p.stat().st_size // 1024)))
        except Exception:
            pass
    if batch_reports:
        result["batch_reports (last 10 min)"] = sorted(batch_reports, key=lambda x: x[0].stat().st_mtime, reverse=True)

    # Debug artifacts
    debug_files = []
    for name in ("sam_profile_check.png", "sam_profile_check.html"):
        p = DEBUG_DIR / name
        if p.exists():
            debug_files.append((p, max(1, p.stat().st_size // 1024)))
    if debug_files:
        result["debug (session check only)"] = debug_files

    return result


def parse_sam_session_output(stdout: str, stderr: str, returncode: int) -> dict:
    """
    Parse output from scripts/check_sam_profile_session.py into a
    structured result dict with keys: ok, code_label, message, debug_html, debug_png.
    """
    combined = (stdout + "\n" + stderr).strip()
    lines = [l.strip() for l in combined.splitlines() if l.strip()]

    ok = returncode == 0 and any("OK" in l for l in lines)
    code_label = "unknown"
    message_lines = []
    debug_html = ""
    debug_png = ""

    for line in lines:
        if "Result" in line:
            if "[OK]" in line:
                ok = True
                code_label = line.split("]", 1)[-1].strip() if "]" in line else "logged_in"
            elif "[FAIL]" in line:
                ok = False
                code_label = line.split("]", 1)[-1].strip() if "]" in line else "failed"
        elif "Message" in line:
            message_lines.append(line.split(":", 1)[-1].strip())
        elif "Debug HTML" in line:
            debug_html = line.split(":", 1)[-1].strip()
        elif "Debug PNG" in line or "Debug PNG" in line:
            debug_png = line.split(":", 1)[-1].strip()
        elif line and not any(x in line for x in ["===", "---", "Running check", "Profile dir", "Display", "Check URL"]):
            message_lines.append(line)

    if not ok and returncode != 0 and not message_lines:
        message_lines = [combined[:300] or "Session check failed."]

    return {
        "ok": ok,
        "code_label": code_label,
        "message": " ".join(message_lines).strip()[:400] or combined[:400],
        "debug_html": debug_html,
        "debug_png": debug_png,
        "raw": combined[:800],
    }


def run_cmd(args: list, timeout: int = 30, extra_env: dict | None = None) -> tuple[int, str, str]:
    env = {**os.environ, "DISPLAY": os.environ.get("DISPLAY", ":99")}
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout,
            env=env, cwd=str(ROOT),
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return -1, "", f"Command timed out after {timeout}s"
    except FileNotFoundError as exc:
        return -1, "", f"Command not found: {exc}"
    except Exception as exc:
        return -1, "", str(exc)[:200]


def _s(key: str, default=None):
    return st.session_state.get(key, default)


def _set(key: str, value):
    st.session_state[key] = value


def init_session_state():
    defaults = {
        "selected_id": None,
        "batch_started": False,
        "ai_proposal": None,
        "ai_result_msg": "",
        "ai_running": False,
        "cmd_output": "",
        "cmd_label": "",
        "stage_msg": "",
        "note_text": "",
        "sam_check_result": None,
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

def save_workspace_history(notice_id: str, history: list):
    """Save the conversation history to the AI Workspace folder."""
    ws_dir = ROOT / "reports" / "opportunity_workspaces" / notice_id
    ws_dir.mkdir(parents=True, exist_ok=True)
    history_path = ws_dir / "conversation_history.json"
    history_path.write_text(json.dumps(history, indent=2), encoding="utf-8")

def get_context_bundle(notice_id: str) -> str:
    """Replicate the 'Context Bundle' logic by gathering all local data for the AI."""
    rows = load_state()
    row = next((r for r in rows if r.get("notice_id") == notice_id), {})
    
    # Basic metadata
    bundle = f"OPPORTUNITY METADATA:\n{json.dumps(row, indent=2)}\n\n"
    
    # Add AI Review report if it exists
    ai_review_path = REPORTS_DIR / "ai_reviews" / f"{notice_id}_ai_review.md"
    if ai_review_path.exists():
        bundle += f"PREVIOUS AI REVIEW:\n{ai_review_path.read_text(encoding='utf-8')[:5000]}\n\n"

    # Add document extracts if they exist
    extracts_dir = REPORTS_DIR / "document_extracts" / notice_id
    if extracts_dir.exists():
        bundle += "DOCUMENT EXTRACTS:\n"
        # Limit files and content size to avoid hitting API token limits
        for p in list(extracts_dir.glob("*.txt"))[:8]:
            bundle += f"--- {p.name} ---\n{p.read_text(encoding='utf-8', errors='replace')[:3000]}\n\n"
            
    return bundle

def get_workspace_history(notice_id: str) -> list:
    """Load the conversation history from the AI Workspace folder."""
    history_path = ROOT / "reports" / "opportunity_workspaces" / notice_id / "conversation_history.json"
    if history_path.exists():
        try:
            return json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


# ═══════════════════════════════════════════════════════════════════════════
# Background batch thread
# ═══════════════════════════════════════════════════════════════════════════

def _start_batch_thread(mode: str, notice_id: str = "", limit: int = 10, force: bool = False, auto_analyze: bool = False):
    with _batch_lock:
        if _batch["running"]:
            return False, "A download is already running."
        _batch.update(
            running=True, done=False, error="", results=[], total=0,
            completed=0, log_lines=[], report_path="", mode=mode, notice_id=notice_id,
            auto_analyze=auto_analyze, session_expired=False,
        )

    stamp = _now_iso()
    write_batch_status({
        "status": "running",
        "started_at": stamp,
        "notice_id": notice_id,
        "mode": mode,
        "message": f"Started {mode} download{' for ' + notice_id if notice_id else ''}.",
        "report_path": "", "downloads_dir": str(DOWNLOADS_DIR / notice_id) if notice_id else str(DOWNLOADS_DIR),
        "session_expired": False,
        "downloaded_count": 0, "skipped_count": 0, "failed_count": 0,
        "results": [], "log_lines": [], "error": "",
        "debug_html": str(DEBUG_DIR / "sam_profile_check.html") if (DEBUG_DIR / "sam_profile_check.html").exists() else "",
        "debug_png":  str(DEBUG_DIR / "sam_profile_check.png")  if (DEBUG_DIR / "sam_profile_check.png").exists()  else "",
    })

    def _log(msg: str):
        with _batch_lock:
            _batch["log_lines"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        # mirror to JSON incrementally (best-effort)
        try:
            current = read_batch_status()
            current.setdefault("log_lines", []).append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
            current["updated_at"] = _now_iso()
            BATCH_STATUS_FILE.write_text(json.dumps(current, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _update_csv_stage(notice_id, new_stage):
        """Helper to update the main state CSV from the background thread."""
        try:
            rows = load_state()
            found = False
            for r in rows:
                if r.get("notice_id") == notice_id:
                    r["macro_stage"] = new_stage
                    r["last_updated"] = _now_iso()
                    found = True
                    break
            if found:
                save_state_with_backup(notice_id, {"macro_stage": new_stage})
        except Exception as e:
            _log(f"CSV Update Failed for {notice_id}: {e}")

    def _finish(results, error="", report_path=""):
        downloaded = sum(1 for r in results if (r.get("status") or "").startswith("downloaded"))
        skipped    = sum(1 for r in results if (r.get("status") or "").startswith("skipped"))
        failed     = sum(1 for r in results if (r.get("status") or "").startswith("failed"))
        
        # Detect session expiration from individual result statuses or overall thread error
        expired = any((r.get("status") == "failed_login") for r in results)
        if error and any(x in error.lower() for x in ["login", "session", "auth", "unauthorized"]):
            expired = True

        with _batch_lock:
            _batch.update(running=False, done=True, error=error, results=results,
                          report_path=report_path, completed=len(results),
                          session_expired=expired)

        final_status = "failed" if (error and not results) else "completed"
        write_batch_status({
            "status": final_status,
            "message": error if error else f"Done. Downloaded: {downloaded}  Skipped: {skipped}  Failed: {failed}",
            "report_path": report_path,
            "downloaded_count": downloaded,
            "skipped_count": skipped,
            "failed_count": failed,
            "results": [
                {k: r.get(k, "") for k in ("notice_id", "status", "attachment_count", "error")}
                for r in results
            ],
            "error": error,
            "session_expired": expired,
            "auto_analyze": _batch.get("auto_analyze", False),
            "debug_html": str(DEBUG_DIR / "sam_profile_check.html") if (DEBUG_DIR / "sam_profile_check.html").exists() else "",
            "debug_png":  str(DEBUG_DIR / "sam_profile_check.png")  if (DEBUG_DIR / "sam_profile_check.png").exists()  else "",
        })

    def _run_auto_pipeline(nid):
        """Chain extraction and AI analysis."""
        _log(f"Auto-Analyzing {nid}...")
        # 1. Extraction
        rc, out, err = run_cmd(["python", "src/extract_documents.py", "--notice-id", nid], timeout=60)
        if rc != 0:
            _log(f"Extraction failed for {nid}: {err[:100]}")
            return
        # 2. AI Review
        from ai_review_generator import run_ai_review
        try:
            res, code = run_ai_review(nid)
            if code == 200:
                _log(f"AI Review generated for {nid}.")
                _update_csv_stage(nid, "AI Review")
            else:
                _log(f"AI Review error for {nid}: {res.get('message','')}")
        except Exception as e:
            _log(f"AI Review exception for {nid}: {e}")

    def _run_queue():
        try:
            from batch_download_docs import (
                run_batch, write_batch_report, BATCH_STAGES,
                DEFAULT_DOWNLOADS_DIR, DEFAULT_BATCH_RUNS_DIR,
            )
            _log(f"Queue batch — stages: {', '.join(sorted(BATCH_STAGES))}  limit: {limit}")

            def cb(step, total, results_so_far):
                last = results_so_far[-1] if results_so_far else {}
                with _batch_lock:
                    _batch["total"] = total
                    _batch["completed"] = step
                    _batch["results"] = list(results_so_far)
                _log(f"[{step}/{total}] {last.get('notice_id','?')} — {last.get('status','?')}")
                
                # Real-time session check: if we see a login failure, flag it immediately
                if last.get("status") == "failed_login":
                    with _batch_lock: _batch["session_expired"] = True
                    write_batch_status({"session_expired": True})
                
                # Auto-advance stage if downloaded
                if last.get("status") == "downloaded":
                    _update_csv_stage(last.get("notice_id"), "AI Review")
                    
                    # If auto-analyze is enabled, chain the extraction and AI review
                    if auto_analyze:
                        _run_auto_pipeline(last.get("notice_id"))


            results, error = run_batch(limit=limit, force=force, live=True, progress_callback=cb)

            report_path = ""
            if results:
                s2 = datetime.now().strftime("%Y-%m-%d_%H%M")
                rp = BATCH_RUNS_DIR / f"batch_download_docs_{s2}.md"
                write_batch_report(results, str(rp), list(BATCH_STAGES), limit, force, True)
                report_path = str(rp.relative_to(ROOT))
                _log(f"Report: {report_path}")

            if not results and not error:
                error = "No eligible candidates found for the selected stages."
            _finish(results or [], error, report_path)

        except Exception as exc:
            _log(f"Exception: {exc}")
            _finish([], error=str(exc)[:400])

    def _run_single():
        try:
            from batch_download_docs import (
                process_candidate, DEFAULT_PROFILE_DIR, DEFAULT_AUTH_STATE,
                DEFAULT_DOWNLOADS_DIR, profile_ready,
            )
            rows = load_state()
            row = next((r for r in rows if r.get("notice_id") == notice_id), None)
            if not row:
                _finish([], error=f"notice_id {notice_id!r} not found in state CSV.")
                return

            url = ""
            for field in ("source_url", "ui_link"):
                v = (row.get(field) or "").strip()
                if v.startswith("http"):
                    url = v
                    break
            if not url:
                _finish([], error=f"No source URL found for {notice_id}.")
                return

            with _batch_lock:
                _batch["total"] = 1
            _log(f"Downloading {notice_id}  url={url[:70]}")

            prof = str(DEFAULT_PROFILE_DIR)
            env_display = os.environ.get("DISPLAY", ":99")
            os.environ["DISPLAY"] = env_display   # ensure visible to Playwright in this thread

            result = process_candidate(
                notice_id=notice_id,
                url=url,
                auth_state=str(DEFAULT_AUTH_STATE),
                downloads_dir=str(DEFAULT_DOWNLOADS_DIR),
                headless=False,
                profile_dir=prof if profile_ready(prof) else "",
            )
            full = {**result, "notice_id": notice_id, "title": (row.get("title") or "")[:80]}
            _log(f"Result: {result.get('status','?')}  attachments={result.get('attachment_count',0)}")
            if result.get("error"):
                _log(f"Error detail: {result['error'][:200]}")
            
            if result.get("status") == "downloaded":
                _update_csv_stage(notice_id, "AI Review")
                
            _finish([full])

        except Exception as exc:
            _log(f"Exception: {exc}")
            _finish([], error=str(exc)[:400])

    t = threading.Thread(target=_run_queue if mode == "queue" else _run_single, daemon=True)
    t.start()
    return True, "Download started in background. Use Refresh Status to update."


def snap_batch() -> dict:
    with _batch_lock:
        return dict(_batch)


# ═══════════════════════════════════════════════════════════════════════════
# Page config and CSS
# ═══════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="GovCon Scout — Operator Console",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  [data-testid="stAppViewContainer"] { background:#0d0d1a; }
  [data-testid="stSidebar"]          { background:#111122; }
  h1,h2,h3 { color:#c8c8ff; }
  .stTabs [data-baseweb="tab-list"]  { background:#111122; border-radius:6px; }
  .stTabs [data-baseweb="tab"]       { color:#8888aa; }
  .stTabs [aria-selected="true"]     { color:#00ff88 !important; }
  .stButton>button {
    background:#1a2a1a; color:#00cc66; border:1px solid #00cc66;
    border-radius:4px; font-size:13px;
  }
  .stButton>button:hover { background:#00cc66; color:#000; }
  .stTextInput>div>input,.stSelectbox>div>div {
    background:#1a1a2e; color:#e0e0ff; border-color:#333366;
  }
  .stDataFrame       { border:1px solid #333366; border-radius:4px; }
  div[data-testid="metric-container"] {
    background:#111130; border:1px solid #222255; border-radius:6px; padding:8px;
  }
</style>
""", unsafe_allow_html=True)

init_session_state()


# ═══════════════════════════════════════════════════════════════════════════
# Sidebar — noVNC + SAM Controls
# ═══════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("### 🎯 GovCon Scout")
    st.markdown("**Operator Console v1**")

    # ── Session Alert System ──────────────────────────────────────────
    bstatus = read_batch_status()
    if bstatus.get("session_expired"):
        st.error("🚨 **SAM.gov Session Expired**")
        st.caption("Automation failed due to authentication. Please log in via noVNC.")
        if st.button("Dismiss & Clear Session Alert", key="btn_dismiss_session"):
            write_batch_status({"session_expired": False})
            with _batch_lock: _batch["session_expired"] = False
            if "last_session_alert" in st.session_state: del st.session_state["last_session_alert"]
            st.rerun()
            
        if "last_session_alert" not in st.session_state:
            st.toast("SAM.gov Session Expired!", icon="🚨")
            st.session_state.last_session_alert = True

    st.divider()

    display_val    = os.environ.get("DISPLAY", "")
    profile_exists = PROFILE_DIR.exists()
    auth_exists    = AUTH_JSON.exists()

    status_box("DISPLAY",    display_val or "⚠ not set",       "#ffaa00" if not display_val else "#00cc66")
    status_box("SAM Profile","✓ exists" if profile_exists else "✗ missing", "#00cc66" if profile_exists else "#ff5555")
    status_box("auth.json",  "present (fallback)" if auth_exists else "absent", "#4fc3f7" if auth_exists else "#666688")

    st.divider()
    st.markdown("#### noVNC Controls")
    st.caption(
        "Keep the noVNC tab open at all times. The automation may open or "
        "close a Chromium/SAM.gov window inside noVNC. If the noVNC viewer "
        "disconnects, reopen forwarded port 6080 and reconnect — do not reset "
        "noVNC unless the check fails."
    )

    if st.button("Check noVNC Status", key="btn_novnc_check"):
        rc, out, err = run_cmd(["bash", "scripts/novnc_check.sh"], timeout=20)
        _set("cmd_output", (out + err).strip() or "(no output)")
        _set("cmd_label", "noVNC Status")

    if st.button("Reset noVNC (instructions)", key="btn_novnc_reset"):
        _set("cmd_output", (
            "Run manually in terminal (do not run from Streamlit —\n"
            "it would block the server):\n\n"
            "  bash scripts/novnc_reset.sh\n"
            "  export DISPLAY=:99\n\n"
            "Then reload this page."
        ))
        _set("cmd_label", "noVNC Reset Instructions")

    st.divider()
    st.markdown("#### SAM.gov Login")
    st.caption(
        "Close **only** the Chromium/SAM.gov window inside noVNC when done. "
        "Do not close the noVNC tab itself."
    )

    if st.button("Open SAM.gov Login", key="btn_sam_open"):
        rc, out, err = run_cmd(
            ["bash", "scripts/open_sam_login_browser.sh", str(PROFILE_DIR)],
            timeout=8,
        )
        msg = out.strip() or (f"Error: {err.strip()}" if err.strip() else "Browser launch attempted.")
        _set("cmd_output", msg)
        _set("cmd_label", "Open SAM Login")

    if st.button("Check SAM Session", key="btn_sam_check"):
        with st.spinner("Checking SAM session (headless browser)…"):
            rc, out, err = run_cmd(
                ["python", "scripts/check_sam_profile_session.py",
                 "--profile-dir", str(PROFILE_DIR)],
                timeout=90,
            )
        parsed = parse_sam_session_output(out, err, rc)
        _set("sam_check_result", parsed)
        _set("cmd_output", None)  # suppress generic output box

    if _s("sam_check_result") is not None:
        r = _s("sam_check_result")
        if r["ok"]:
            st.success(f"SAM Session: logged in  ({r['code_label']})")
        else:
            st.error(f"SAM Session: {r['code_label']}")
            if r["message"]:
                st.caption(r["message"])
        if r.get("debug_png"):
            st.caption(f"Debug PNG:  {r['debug_png']}")
        if r.get("debug_html"):
            st.caption(f"Debug HTML: {r['debug_html']}")
        with st.expander("Raw check output"):
            st.code(r.get("raw", ""), language="text")

    if _s("cmd_output"):
        st.markdown(f"**{_s('cmd_label','Output')}**")
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

rows_all   = load_state()
total_rows = len(rows_all)
active_rows = [r for r in rows_all if r.get("macro_stage") not in {"Pass","Archive","Done","Triage"}]
dev_rows    = [r for r in rows_all if r.get("macro_stage") == "Development"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Opportunities", total_rows)
col2.metric("Active (non-Triage)", len(active_rows))
col3.metric("Development", len(dev_rows))
col4.metric("AI Review Pending", sum(
    1 for r in rows_all
    if r.get("ai_review_status") in {"","None","pending",None}
    and r.get("macro_stage") in {"AI Review","Development"}
))
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

    fc1, fc2, fc3, fc4 = st.columns([3,2,2,2])
    search_text  = fc1.text_input("Search title / notice_id / agency", key="filter_search").lower()
    all_stages   = sorted({r.get("macro_stage","") for r in rows_all if r.get("macro_stage")})
    stage_filter = fc2.multiselect("Stage", all_stages, default=[])
    all_sources  = sorted({r.get("source","") for r in rows_all if r.get("source")})
    source_filter= fc3.multiselect("Source", all_sources, default=[])
    hide_inactive= fc4.checkbox("Hide Triage/Pass/Archive/Done", value=True, key="filter_hide")

    HIDE_STAGES = {"Triage","Pass","Archive","Done"}

    def row_matches(r):
        stage = r.get("macro_stage","")
        if hide_inactive and stage in HIDE_STAGES:     return False
        if stage_filter  and stage not in stage_filter: return False
        if source_filter and r.get("source","") not in source_filter: return False
        if search_text:
            hay = " ".join([r.get("notice_id",""),r.get("title",""),r.get("agency","")]).lower()
            if search_text not in hay:                  return False
        return True

    filtered = [r for r in rows_all if row_matches(r)]
    st.caption(f"{len(filtered)} of {total_rows} rows shown")

    if not filtered:
        st.info("No rows match current filters.")
    else:
        import pandas as pd
        display_cols = ["notice_id","title","macro_stage","due_date","source",
                        "ai_review_status","document_status","recommended_next_action"]
        df = pd.DataFrame(filtered)[[c for c in display_cols if c in filtered[0]]]
        for col in ["title","recommended_next_action","document_status"]:
            if col in df.columns:
                df[col] = df[col].str.slice(0, 80)
        st.dataframe(df, use_container_width=True, hide_index=True, height=320)

        id_labels = [f"{r['notice_id']} — {(r.get('title') or '')[:60]}" for r in filtered]
        selected_label = st.selectbox("Select opportunity", ["(none)"] + id_labels, key="queue_select")
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
            c1, c2 = st.columns(2)
            with c1:
                status_box("Notice ID", nid)
                status_box("Stage",     row.get("macro_stage") or "—")
                status_box("Due Date",  row.get("due_date")    or "—", "#ffaa00")
                status_box("AI Review", row.get("ai_review_status") or "—")
                status_box("Docs",     (row.get("document_status") or "—")[:60])
            with c2:
                status_box("Agency",   (row.get("agency") or "—")[:70])
                status_box("Source",    row.get("source") or "—")
                status_box("Place of Performance", row.get("place_of_performance") or "—")
                status_box("Set-Aside", row.get("set_aside") or "—", "#cc88ff")
                status_box("Fit Score", row.get("fit_score") or "—", "#4fc3f7")

            synopsis = row.get("synopsis") or row.get("description") or ""
            if synopsis:
                with st.expander("Synopsis / Description"):
                    st.write(synopsis[:2000])

            ai_sum = row.get("ai_summary") or ""
            if ai_sum:
                with st.expander("AI Summary"):
                    st.write(ai_sum[:1500])

            req_b = format_bullets(row.get("requirements") or "")
            dis_b = format_bullets(row.get("disqualifiers") or "")
            rb1, rb2 = st.columns(2)
            with rb1:
                if req_b:
                    with st.expander(f"Requirements ({len(req_b)})"):
                        for b in req_b: st.markdown(f"- {b}")
            with rb2:
                if dis_b:
                    with st.expander(f"Disqualifiers ({len(dis_b)})"):
                        for b in dis_b: st.markdown(f"- ⚠ {b}")

            rna = row.get("recommended_next_action") or ""
            if rna:
                st.markdown("**Recommended Next Action**")
                st.info(rna[:600])

            oq = row.get("operator_questions") or ""
            if oq:
                with st.expander("Operator Questions"):
                    for b in format_bullets(oq): st.markdown(f"- {b}")

            st.divider()
            st.markdown("#### AI Workspace Chat")
            history = get_workspace_history(nid)
            if history:
                for msg in history:
                    with st.chat_message(msg["role"]):
                        st.markdown(msg["content"])
            else:
                st.info("No AI conversation history found. Run an AI Review to start the workspace.")

            if prompt := st.chat_input("Ask Claude about this opportunity..."):
                current_history = list(history)
                if not current_history:
                    bundle = get_context_bundle(nid)
                    current_history.append({"role": "user", "content": f"{bundle}\n\nUSER QUESTION: {prompt}"})
                else:
                    current_history.append({"role": "user", "content": prompt})
                save_workspace_history(nid, current_history)
                with st.chat_message("assistant"):
                    with st.spinner("Claude is thinking..."):
                        try:
                            api_key = os.environ.get("ANTHROPIC_API_KEY")
                            if not api_key:
                                st.error("ANTHROPIC_API_KEY not found in environment.")
                            else:
                                client = anthropic.Anthropic(api_key=api_key)
                                model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
                                response = client.messages.create(
                                    model=model,
                                    max_tokens=4000,
                                    messages=[{"role": m["role"], "content": m["content"]} for m in current_history],
                                )
                                ans = response.content[0].text
                                current_history.append({"role": "assistant", "content": ans})
                                save_workspace_history(nid, current_history)
                                st.rerun()
                        except Exception as e:
                            st.error(f"AI Error: {e}")

            if st.button("Open AI Workspace in New Window", key="btn_workspace_link"):
                # Placeholder for linking back to the operator dashboard if still desired
                st.markdown(f'<a href="http://localhost:8765/workspace?notice_id={nid}" target="_blank">Click here to open legacy workspace</a>', unsafe_allow_html=True)

            st.divider()
            st.markdown("#### Stage Actions")
            STAGE_MOVES = [
                ("Intake","Intake"),("Manual Review","Manual Review"),
                ("AI Review","AI Review"),("Development","Development"),
                ("Pass","Pass"),("Archive","Archive"),
            ]
            btn_cols = st.columns(len(STAGE_MOVES))
            for i, (label, new_stage) in enumerate(STAGE_MOVES):
                cur = row.get("macro_stage","")
                if btn_cols[i].button(label, key=f"stage_{new_stage}", disabled=(cur==new_stage)):
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

            is_watched = (row.get("watch_list") or "").lower() in {"1","true","yes","watch","watched","on"}
            if st.button("Remove from Watch List" if is_watched else "Add to Watch List", key="btn_watch"):
                try:
                    save_state_with_backup(nid, {"watch_list": "" if is_watched else "watch"})
                    _set("stage_msg", "✓ Watch list updated.")
                    st.rerun()
                except Exception as exc:
                    _set("stage_msg", f"Error: {exc}")

            st.divider()
            st.markdown("#### Add Operator Note")
            note_text = st.text_area("Note", value=_s("note_text"), key="note_input", height=80)
            if st.button("Save Note", key="btn_save_note"):
                if note_text.strip():
                    stamp    = _now_iso()
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

    if not os.environ.get("DISPLAY"):
        st.warning("DISPLAY is not set. Run `export DISPLAY=:99` before starting downloads.")
    if not PROFILE_DIR.exists() and not AUTH_JSON.exists():
        st.warning("No SAM.gov session found. Open SAM.gov Login in the sidebar first.")

    # ── SAM login gate reminder ───────────────────────────────────────────
    with st.expander("SAM.gov Login Instructions", expanded=False):
        st.markdown(
            "1. Keep the **noVNC tab open** at all times.\n"
            "2. Click **Open SAM.gov Login** in the sidebar to open the login browser in noVNC.\n"
            "3. Complete SAM.gov / Login.gov / MFA inside the Chromium window in noVNC.\n"
            "4. When SAM.gov shows you are signed in, **close only the Chromium/SAM.gov window** "
            "inside noVNC.\n"
            "5. Do not close the noVNC tab — the automation reuses the same profile.\n"
            "6. Use **Check SAM Session** in the sidebar to confirm login before downloading."
        )

    st.divider()
    dc1, dc2 = st.columns(2)

    # ── Single download ───────────────────────────────────────────────────
    with dc1:
        st.markdown("#### Download Docs for Selected")
        if not nid:
            st.info("Select an opportunity in the Queue tab first.")
        else:
            bstatus = read_batch_status()
            bsnap   = snap_batch()

            currently_running = bsnap["running"] or bstatus.get("status") == "running"
            stale             = is_stale_running(bstatus)

            st.markdown(f"Selected: **{nid}**")
            force_single = st.checkbox("Force re-download", key="force_single")
            auto_analyze_single = st.checkbox("Auto-Analyze (Extract + AI)", value=True, key="auto_single")

            if currently_running and not stale:
                st.warning("A download is already running — see Batch Progress below.")
            else:
                if st.button("Download Docs for Selected", key="btn_dl_single"):
                    ok, msg = _start_batch_thread("single", notice_id=nid, force=force_single, auto_analyze=auto_analyze_single)
                    _set("batch_started", True)
                    if ok:
                        st.success(msg)
                    else:
                        st.error(msg)

    # ── Queue batch ───────────────────────────────────────────────────────
    with dc2:
        st.markdown("#### Download Docs for Queue")
        from batch_download_docs import BATCH_STAGES as _BATCH_STAGES
        st.caption(f"Stages: {', '.join(sorted(_BATCH_STAGES))}")
        limit_val   = st.number_input("Limit", min_value=1, max_value=50, value=5, key="batch_limit")
        force_queue = st.checkbox("Force re-download", key="force_queue")
        auto_analyze_queue = st.checkbox("Auto-Analyze (Extract + AI)", value=True, key="auto_queue")

        bstatus = read_batch_status()
        bsnap   = snap_batch()
        currently_running = bsnap["running"] or bstatus.get("status") == "running"
        stale             = is_stale_running(bstatus)

        if currently_running and not stale:
            st.warning("A download is already running — see Batch Progress below.")
        else:
            if st.button("Download Docs for Eligible Queue", key="btn_dl_queue"):
                ok, msg = _start_batch_thread("queue", limit=int(limit_val), force=force_queue, auto_analyze=auto_analyze_queue)
                _set("batch_started", True)
                if ok: st.success(msg)
                else:  st.error(msg)

    # ── Batch Progress ────────────────────────────────────────────────────
    st.divider()
    st.markdown("### Batch Progress")

    bstatus = read_batch_status()
    bsnap   = snap_batch()
    stale   = is_stale_running(bstatus)

    # Determine display status from both sources (in-memory wins for live run)
    if bsnap["running"]:
        display_status = "running"
    elif bsnap["done"]:
        display_status = "failed" if bsnap["error"] and not bsnap["results"] else "completed"
    else:
        display_status = bstatus.get("status", "idle")

    # ── Status metrics row ────────────────────────────────────────────────
    bc1, bc2, bc3, bc4 = st.columns(4)
    status_labels = {
        "idle":      "Idle —",
        "running":   "Running ▶",
        "completed": "Done ✓",
        "failed":    "Failed ✗",
        "stuck":     "Stuck ⚠",
    }
    bc1.metric("Status",    status_labels.get(display_status, display_status))
    bc2.metric("Downloaded", bstatus.get("downloaded_count", bsnap.get("completed", 0)))
    bc3.metric("Skipped",    bstatus.get("skipped_count", 0))
    bc4.metric("Failed",     bstatus.get("failed_count", sum(
        1 for r in bsnap["results"] if (r.get("status") or "").startswith("failed_"))))

    # ── Metadata ──────────────────────────────────────────────────────────
    if bstatus.get("notice_id") or bstatus.get("mode"):
        mode_label = bstatus.get("mode","")
        nid_label  = bstatus.get("notice_id","")
        st.caption(
            f"Mode: {mode_label}"
            + (f"  ·  Notice: {nid_label}" if nid_label else "")
            + (f"  ·  Started: {bstatus.get('started_at','')}" if bstatus.get("started_at") else "")
            + (f"  ·  Updated: {bstatus.get('updated_at','')}" if bstatus.get("updated_at") else "")
        )

    # ── Stale running detection ───────────────────────────────────────────
    if stale:
        st.warning(
            "⚠ Status shows 'running' but no active download process was found. "
            "The session check ran (SAM profile debug files exist) but no opportunity "
            "documents were downloaded. The previous batch likely failed silently."
        )
        if st.button("Reset Stuck Batch Status", key="btn_reset_stuck"):
            write_batch_status({
                "status": "idle",
                "message": "Manually reset by operator after stale running state detected.",
                "error": "",
            })
            with _batch_lock:
                _batch.update(running=False, done=False)
            _set("batch_started", False)
            st.success("Batch status reset to idle.")
            st.rerun()

    # ── Error / message ───────────────────────────────────────────────────
    err_msg = bsnap["error"] if bsnap.get("error") else bstatus.get("error","")
    msg     = bstatus.get("message","")
    if err_msg:
        st.error(f"Error: {err_msg}")
    elif msg and display_status not in {"idle","running"}:
        st.info(msg)

    # ── Report path ───────────────────────────────────────────────────────
    rpt = bsnap.get("report_path") or bstatus.get("report_path","")
    if rpt:
        st.success(f"Batch report: `{rpt}`")
    elif display_status == "completed":
        st.warning("No batch report was created (likely all candidates skipped).")

    # ── Results table ─────────────────────────────────────────────────────
    results_src = bsnap["results"] if bsnap["results"] else bstatus.get("results",[])
    if results_src:
        import pandas as pd
        res_df = pd.DataFrame(results_src)[[
            c for c in ["notice_id","status","attachment_count","error"]
            if c in results_src[0]
        ]]
        st.dataframe(res_df, use_container_width=True, hide_index=True)

    # ── Debug artifacts note ──────────────────────────────────────────────
    only_debug = (display_status in {"completed","failed","idle"} and not results_src
                  and (DEBUG_DIR/"sam_profile_check.html").exists())
    if only_debug:
        st.warning(
            "SAM session check ran (debug files exist in downloads/_debug/), "
            "but no opportunity documents were downloaded. "
            "Verify that the download actually ran and check the error above."
        )
        d_png  = DEBUG_DIR / "sam_profile_check.png"
        d_html = DEBUG_DIR / "sam_profile_check.html"
        if d_png.exists():
            st.caption(f"Debug PNG:  {d_png.relative_to(ROOT)}")
        if d_html.exists():
            st.caption(f"Debug HTML: {d_html.relative_to(ROOT)}")

    # ── Log ───────────────────────────────────────────────────────────────
    log_src = bsnap["log_lines"] if bsnap["log_lines"] else bstatus.get("log_lines",[])
    if log_src:
        with st.expander("Log", expanded=bsnap["running"]):
            st.code("\n".join(log_src[-40:]), language="text")

    # ── Proof: recent files ───────────────────────────────────────────────
    proof_nid = bstatus.get("notice_id") or nid or ""
    if proof_nid or display_status not in {"idle"}:
        recent = find_recent_files_for_notice(proof_nid)
        if recent:
            with st.expander("Files found after last run", expanded=(display_status in {"completed","failed"})):
                for category, files in recent.items():
                    st.markdown(f"**{category}**")
                    for p, kb in files[:20]:
                        rel = str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)
                        st.caption(f"  {rel}  ({kb} KB)")
        elif display_status == "completed":
            st.info(f"No download files found for notice_id='{proof_nid}'.")

    # ── Controls ──────────────────────────────────────────────────────────
    ctrl1, ctrl2 = st.columns(2)
    if ctrl1.button("Refresh Status", key="btn_batch_refresh"):
        st.rerun()
    if bsnap["running"]:
        ctrl2.info("Download running — Refresh to update progress.")

    if display_status not in {"running"} and not stale:
        if ctrl2.button("Reset Status to Idle", key="btn_reset_idle"):
            write_batch_status({"status":"idle","message":"Reset by operator.","error":""})
            with _batch_lock:
                _batch.update(running=False, done=False)
            _set("batch_started", False)
            st.rerun()


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
            st.markdown(
                f"**{row.get('title') or nid}** — "
                f"Stage: `{row.get('macro_stage','?')}` — "
                f"AI Status: `{row.get('ai_review_status') or 'none'}`"
            )

            if not os.environ.get("ANTHROPIC_API_KEY") and not (ROOT / ".env").exists():
                st.warning("ANTHROPIC_API_KEY not found. AI Review will fail without it.")

            ai_col1, ai_col2 = st.columns(2)
            if ai_col1.button("Run AI Review", key="btn_ai_run", disabled=_s("ai_running")):
                _set("ai_running", True)
                _set("ai_proposal", None)
                _set("ai_result_msg", "")
                with st.spinner("Running AI Review (30–90 seconds)…"):
                    try:
                        from ai_review_generator import run_ai_review
                        result, status_code = run_ai_review(nid)
                        if status_code == 200:
                            _set("ai_proposal", result.get("proposed_update"))
                            _set("ai_result_msg", f"✓ Review complete. Report: {result.get('review_path','')}")
                        else:
                            _set("ai_result_msg", f"Error ({status_code}): {result.get('message','?')}")
                    except Exception as exc:
                        _set("ai_result_msg", f"Exception: {exc}")
                _set("ai_running", False)

            if _s("ai_result_msg"):
                msg = _s("ai_result_msg")
                (st.error if ("Error" in msg or "Exception" in msg) else st.success)(msg)

            ai_md   = REPORTS_DIR / "ai_reviews" / f"{nid}_ai_review.md"
            ai_json = REPORTS_DIR / "ai_reviews" / f"{nid}_card_update.json"

            if ai_col2.button("View AI Review Markdown", key="btn_ai_view_md"):
                if ai_md.exists():
                    with st.expander("AI Review Markdown", expanded=True):
                        st.markdown(ai_md.read_text(encoding="utf-8"))
                else:
                    st.warning(f"No AI review file: {ai_md.name}")

            if st.button("View Card Update JSON", key="btn_ai_view_json"):
                if ai_json.exists():
                    with st.expander("Card Update JSON", expanded=True):
                        st.json(json.loads(ai_json.read_text(encoding="utf-8")))
                else:
                    st.warning(f"No card update JSON: {ai_json.name}")

            proposal = _s("ai_proposal")
            if proposal:
                st.divider()
                st.markdown("#### Proposed Card Update")
                for field in ["ai_summary","recommended_next_action"]:
                    if proposal.get(field):
                        st.markdown(f"**{field}**")
                        st.info(proposal[field][:600])
                for field in ["requirements","disqualifiers","documents_missing","operator_questions"]:
                    bullets = format_bullets(proposal.get(field) or "")
                    if bullets:
                        with st.expander(f"{field} ({len(bullets)})"):
                            for b in bullets: st.markdown(f"- {b}")
                for field in ["pricing_status","site_visit_status","submission_status",
                               "prime_or_teaming_recommendation","hard_disqualifier_summary"]:
                    if proposal.get(field):
                        status_box(field, proposal[field][:80])

                st.divider()
                if st.button("Apply AI Review to Card", key="btn_ai_apply", type="primary"):
                    try:
                        from ai_review_generator import apply_ai_review
                        result, status_code = apply_ai_review(nid, proposal)
                        if status_code == 200:
                            updated = result.get("updated_fields",[])
                            backup  = result.get("backup_path","")
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
                c1, c2 = st.columns([3,1])
                is_dir = path.is_dir()
                rel    = str(path.relative_to(ROOT))
                c1.markdown(f"**{label}** — `{rel}`")
                if not is_dir and path.suffix in {".md",".txt"}:
                    if c2.button("View", key=f"view_{label}_{nid}"):
                        with st.expander(label, expanded=True):
                            content = path.read_text(encoding="utf-8", errors="replace")
                            st.markdown(content[:8000]) if path.suffix==".md" else st.code(content[:8000])
                elif not is_dir and path.suffix == ".json":
                    if c2.button("View", key=f"view_{label}_{nid}"):
                        with st.expander(label, expanded=True):
                            try: st.json(json.loads(path.read_text(encoding="utf-8")))
                            except Exception: st.code(path.read_text(encoding="utf-8")[:4000])
                elif is_dir:
                    files = sorted(path.iterdir())
                    c2.caption(f"{len(files)} files")
                    if c2.button("List", key=f"list_{label}_{nid}"):
                        with st.expander(f"{label} contents", expanded=True):
                            for f in files[:50]:
                                st.caption(f"  {f.name}  ({f.stat().st_size//1024} KB)")

        st.divider()
        st.markdown("#### Recent Batch Reports")
        batch_runs = sorted(BATCH_RUNS_DIR.glob("batch_download_docs_*.md"), reverse=True)
        for rp in batch_runs[:8]:
            if st.button(rp.name, key=f"batchrpt_{rp.name}"):
                with st.expander(rp.name, expanded=True):
                    st.markdown(rp.read_text(encoding="utf-8")[:6000])
