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
