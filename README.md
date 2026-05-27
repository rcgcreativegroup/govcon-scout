# govcon-scout

## Current Workflow

GovCon Scout searches SAM.gov opportunities, scores them against the company profile, routes the best items into the right workflow, and produces working reports for pursuit decisions.

### Core commands

Run a fresh SAM.gov scan and generate reports:

```bash
python src/main.py
```

Regenerate reports from the latest exported CSV without calling SAM.gov:

```bash
python src/main.py --offline
```

Reset the live noVNC desktop for interactive SAM.gov/Login.gov work:

```bash
source scripts/novnc_reset.sh
```

Check the live desktop before running browser processing:

```bash
export DISPLAY=:99
scripts/novnc_check.sh
```

Run a controlled smart/live batch:

```bash
python src/process_shortlist.py --limit 10 --smart --live --retry-manual
```

Build the working triage board:

```bash
python src/triage_board.py
```

The triage board is written to:

```text
reports/triage/govcon_triage_board.md
```

### Operating notes

- Saved `auth.json` sessions are unreliable for SAM.gov/Login.gov because MFA and session state can expire or fail in headless browser runs.
- Live noVNC mode is the fallback for interactive login. Use `scripts/novnc_reset.sh`, open forwarded port `6080`, choose `vnc.html`, connect, and complete SAM.gov/Login.gov in the browser.
- Sources sought, RFI, market research, special notice, and pre-solicitation items are early-stage strategy opportunities. They should route to `sources_sought_planner.py`, not be treated as failed solicitations.
- Live infrastructure failures exit with code `86` and should not create manual-review reports. Manual-review reports are for opportunity/download/document issues.
- Do not run broad live batches until the triage board shows which manual-review items are worth retrying or improving selectors for.

### Dashboard startup safety

- Operator dashboard startup is intended to be read-only.
- Mutating actions should only occur through explicit operator actions or API requests.
- `data/opportunity_state.csv` should not be auto-repaired or rewritten on startup.
- Maintenance and schema-repair helpers must be invoked intentionally, with CSV mutation reviewed before use.

### Dashboard verification commands

Use these checks before dashboard UI/backend refactors:

```bash
python -m py_compile src/operator_dashboard.py
python -m py_compile scripts/dashboard_startup_write_check.py
python -m py_compile scripts/dashboard_route_inventory.py
python -m py_compile scripts/dashboard_smoke_test.py
python -m py_compile scripts/dependency_audit.py
python -m py_compile scripts/dashboard_frontend_audit.py
python scripts/dashboard_startup_write_check.py
python scripts/dashboard_route_inventory.py
python scripts/dependency_audit.py
python scripts/dashboard_frontend_audit.py
```

With the dashboard already running on `http://127.0.0.1:8765`, run:

```bash
python scripts/dashboard_smoke_test.py
```

### Dashboard route and audit helpers

- `scripts/dashboard_smoke_test.py` checks core local routes without SAM.gov, noVNC, or USAspending.
- `scripts/dashboard_route_inventory.py` compares dashboard backend routes with frontend `fetch()` usage and reports unmatched routes for review.
- `scripts/dependency_audit.py` compares `requirements.txt` with imports in `src/*.py` and `scripts/*.py`; optional or lazy imports should be reviewed before changing dependencies.
- `scripts/dashboard_frontend_audit.py` reports possible unused CSS classes, duplicate JavaScript functions, console logging, timer usage, and global state candidates. It is an inventory tool, not proof that a function or style can be deleted.

### Dashboard file security

- The `/file?path=...` route only serves explicit files under approved local roots and refuses directory listing.
- Sensitive paths are denied even if they are under an approved root, including `auth.json`, `.env` files, `data/backups/`, `data/opportunity_state.csv`, `conversation_history.json`, browser/session files, `__pycache__`, and `*.pyc`.
- Report and download links should point to explicit files, not directories.

### Notice ID filesystem safety

- Dashboard filesystem paths use strict `notice_id` validation where local folders or report paths are built.
- Valid notice IDs may contain letters, numbers, dots, dashes, and underscores.
- Empty values, absolute paths, path separators, and traversal patterns are rejected with `{"status": "error", "message": "Invalid notice_id."}`.
- Display labels and CSV values are not rewritten for presentation; sanitization is applied at filesystem/API boundaries.

### Generated file caution

- Do not blindly commit generated reports, downloads, exports, manual uploads, backups, raw MyBidMatch files, browser/session files, `auth.json`, `.env`, or `data/opportunity_state.csv`.
- Review generated outputs intentionally and keep secrets or workflow state out of commits.

### Streamlit Operator Console

A Python-first parallel interface for SAM.gov document automation. Runs alongside the existing HTML dashboard — does not replace it.

Start the console:

```bash
source scripts/novnc_reset.sh
export DISPLAY=:99
streamlit run streamlit_app.py --server.port 8501 --server.address 127.0.0.1
```

Then open the forwarded ports:
- **Port 8501** — Streamlit Operator Console
- **Port 6080** — noVNC (`vnc.html`) for SAM.gov login
- **Port 8765** — existing HTML dashboard (still available)

Use the Streamlit console to:
- Check noVNC and SAM.gov session readiness
- Open SAM.gov login in noVNC
- Filter and select opportunities
- Download documents (single or queue batch)
- Run AI Review and apply proposed updates
- View analysis packets, pricing reports, batch logs

`.browser/sam-profile` is local-only and is never exposed through the UI.
`auth.json` is a fallback session file; the persistent profile is preferred.

### Deferred dashboard cleanup

- Stage order is currently duplicated between `config/stage_enums.json` and the frontend stage ribbon/keyboard logic. Backend validation reads the config; a frontend refactor should be done separately with browser verification.
- `/api/notes/{notice_id}` has no current frontend caller but remains available for manual/legacy note inspection.
- `src/operator_dashboard.py` remains intentionally monolithic for now; future extraction should start with pure helpers and route inventory tests before changing endpoint behavior.
