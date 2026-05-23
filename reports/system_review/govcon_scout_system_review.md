# GovCon Scout System Review

**Generated:** 2026-05-23

## Executive Summary

GovCon Scout has crossed the important line from "scanner" to operating system. It now supports discovery, scoring, routing, live SAM.gov/noVNC processing, document analysis, pricing extraction, USAspending finalist research, MyBidMatch intake/resolution, and a daily action board. The core strategy is sound: search broadly, narrow locally, process only worthwhile items, reserve USAspending for finalists, and keep final pricing decisions human-led.

The strongest next move is not another data source. The system now needs a modest state layer and command wrapper so every script writes to the same opportunity record instead of relying on markdown parsing and filename discovery. The current artifact-first design works, but the number of reports has grown enough that daily operation will become noisy unless state is centralized.

Recommended operating view: `reports/triage/finalist_action_board.md` should be the daily dashboard. `govcon_triage_review_pack.md` should remain the deeper analyst packet. The older triage board should remain a source artifact, not the main human workflow.

No SAM.gov API search, USAspending API call, document download, or code modification was performed for this review. Only this report was created.

## Files Inspected

Core scripts inspected:
- `src/main.py`
- `src/scorer.py`
- `src/pipeline.py`
- `src/process_shortlist.py`
- `src/process_opportunity.py`
- `src/process_opportunity_live.py`
- `src/sam_browser_downloader.py`
- `src/triage_board.py`
- `src/triage_review_pack.py`
- `src/finalist_action_board.py`
- `src/mybidmatch_triage.py`
- `src/mybidmatch_sam_resolver.py`
- `src/usaspending_intel.py`
- `src/bid_price_sanity.py`
- `src/pricing_schedule_extractor.py`
- `src/sources_sought_planner.py`

Support files inspected:
- `scripts/novnc_check.sh`
- `scripts/novnc_reset.sh`
- `.gitignore`
- `README.md`
- `requirements.txt`

Output/state surfaces reviewed by path and structure:
- `data/pipeline.csv`
- `exports/govcon_scout_opportunities_latest.csv`
- `data/mybidmatch/mybidmatch_resolved.csv`
- `reports/triage/`
- `reports/opportunity_reviews/`
- `reports/sources_sought/`
- `reports/manual_review/`
- `reports/pricing/`
- `reports/market_intel/`
- `reports/mybidmatch/`
- `reports/analysis_packets/`

## 1. System Architecture

### What Is Working Well

- The repo now has a clear end-to-end business workflow: discover, score, shortlist, route, process, analyze, price-gate, market-check, and assign next human action.
- `process_shortlist.py` is doing the right strategic split between solicitations and sources-sought/RFI notices.
- `process_opportunity.py` and `process_opportunity_live.py` preserve the full processing chain: download, unzip, local scan, extraction, bid/no-bid, decision report, compliance matrix, pricing extraction.
- noVNC reliability is much stronger than before. Live infrastructure has a dedicated check path, and live infrastructure failures use exit code `86` instead of becoming fake opportunity failures.
- MyBidMatch is now properly treated as a lead source. The parser/triage/resolver model prevents unresolved third-party leads from being pushed into SAM.gov processing prematurely.
- The Finalist Action Board is the right daily operator view because it collapses many report surfaces into one next-action list.

### Core Scripts Now

These are now core operating scripts:
- `src/main.py`: SAM.gov/offline scan and report generator.
- `src/process_shortlist.py`: batch router and live/saved-auth gate.
- `src/process_opportunity.py`: saved-auth solicitation processor.
- `src/process_opportunity_live.py`: live noVNC solicitation processor.
- `src/triage_board.py`: raw triage board from CSV/artifacts.
- `src/triage_review_pack.py`: deeper finalist review pack.
- `src/finalist_action_board.py`: daily operator board.
- `src/sources_sought_planner.py`: early-stage response strategy.
- `src/pricing_schedule_extractor.py`: pricing/CLIN extraction.
- `src/usaspending_intel.py`: finalist-only market intel.
- `src/bid_price_sanity.py`: pricing gate after local/market artifacts exist.
- `src/mybidmatch_triage.py`: MyBidMatch business-fit filter.
- `src/mybidmatch_sam_resolver.py`: MyBidMatch to GovCon/SAM matching.
- `scripts/novnc_reset.sh` and `scripts/novnc_check.sh`: live-mode infrastructure.

### Experimental, Duplicate, Or Consolidation Candidates

- `src/bid_price_sanity.py` and `src/bid_price_sanity_check.py` appear duplicative by name and output family. Keep one canonical module and migrate any useful logic into it.
- `src/mybidmatch_browser_intake.py`, `src/mybidmatch_importer.py`, `src/mybidmatch_parser.py`, `src/save_mybidmatch_login.py`, and `src/mybidmatch_session_setup.py` may be valid, but the boundary is not obvious from the filenames. They should be documented as either active intake commands or experimental browser helpers.
- `src/save_sam_login.py` and `src/save_sam_profile_login.py` should be clearly marked as optional/session utilities now that live noVNC is the reliable fallback.
- `scripts/test-sam-session.js` looks like a diagnostic utility. It should remain outside the main workflow unless it is still required.
- The generated `reports/govcon_scout_*_latest.md` and timestamped copies are useful historically but noisy. The main workflow now points more naturally to `reports/triage/`.

### Shared Helper Modules Needed

The same helper ideas are repeated across scripts. These should become shared modules before more features are added:

- `src/paths.py`: canonical paths for downloads, exports, reports, artifacts, and notice-specific filenames.
- `src/opportunity_state.py`: load/update one opportunity state record and resolve notice IDs across SAM/MyBidMatch.
- `src/artifacts.py`: discover whether decision, compliance, pricing, USAspending, sanity, manual review, and sources-sought artifacts exist.
- `src/markdown_tables.py`: parse/write markdown tables consistently.
- `src/text_match.py`: shared normalization, token overlap, title similarity, agency similarity.
- `src/classification.py`: shared lane, fulfillment path, prime-control, and poor-fit classification language.
- `src/commands.py`: common `run_command`, return-code handling, and infrastructure error conventions.

### Folder Structure

The current folders are still workable:
- `src/` for scripts/modules.
- `scripts/` for shell/noVNC utilities.
- `config/` for profile/company configuration.
- `data/` for pipeline/source-state CSVs.
- `exports/` for scan outputs.
- `downloads/` for local solicitation files.
- `reports/` for human-facing outputs.

The pressure point is not folder names. It is that state is spread across `exports/govcon_scout_opportunities_latest.csv`, `data/pipeline.csv`, markdown reports, existence of artifact files, and MyBidMatch CSVs. That should be solved with a state layer before changing directories heavily.

## 2. Workflow Efficiency

### Remaining Manual Work

- SAM.gov/Login.gov authentication still requires human login through noVNC. That should remain manual unless the platform becomes stable enough for saved auth again.
- MyBidMatch confirmed and possible matches still require human validation before being treated as processable SAM.gov records.
- Vendor/subcontractor quotes are still manual, especially pest control, janitorial, facilities, security, transportation, and other subcontractor-led lanes.
- Final bid/no-bid and pricing decisions remain human judgment calls.
- State/local MyBidMatch leads have no workflow yet, so they are parked.

### Safe Automation Next

- One command to rebuild the local command center from existing artifacts:
  `python src/operator_refresh.py`
  It should run triage board, triage review pack, MyBidMatch resolver if inputs changed, bid price sanity if prerequisites exist, and finalist action board. It should not call external APIs by default.
- Automatic artifact status writeback to `data/pipeline.csv` or a replacement state file.
- Automatic "next missing step" detection per finalist: pricing missing, USAspending missing, quote needed, source match unvalidated, manual retry needed.
- MyBidMatch confirmed-match promotion into the local state table without processing/downloading.

### Steps That Should Stay Manual

- SAM.gov/Login.gov login and MFA.
- Deciding whether to retry a manual-review item when the source is an external portal or selector gap.
- Confirming MyBidMatch/SAM matches before processing.
- Choosing actual subcontractors/vendors and validating licenses, insurance, geography, access, and capacity.
- Building bid price and margin.
- Deciding pass/no-bid on compliance-risk or set-aside issues.

### Better Daily Command Flow

Recommended daily flow:

```bash
python src/main.py --offline --scan-local-attachments
python src/triage_board.py
python src/triage_review_pack.py
python src/finalist_action_board.py
```

Recommended live retry flow:

```bash
source scripts/novnc_reset.sh
export DISPLAY=:99
scripts/novnc_check.sh
python src/process_shortlist.py --limit 10 --smart --live --retry-manual
python src/triage_board.py
python src/triage_review_pack.py
python src/finalist_action_board.py
```

Recommended finalist intel flow:

```bash
python src/usaspending_intel.py --notice-id <NOTICE_ID> --limit 25 --years 5
python src/bid_price_sanity.py --notice-id <NOTICE_ID>
python src/finalist_action_board.py
```

## 3. Accuracy / Decision Quality

### Likely False Positives

- Broad "services" opportunities where the title matches a lane but scope is highly specialized, regulated, or product-heavy.
- Sources-sought items that are technically early-stage but strategically weak, such as commodity-only buys.
- MyBidMatch confirmed matches where title similarity is high but the underlying source is unrelated or stale.
- USAspending comparables where NAICS/PSC are correct but scope, period, location, or size are not comparable.
- Facility, transportation, and security items that appear subcontractable but have hidden licensing, access, bonding, or supervision risks.

### Likely False Negatives

- State/local marketing, event, janitorial, pest-control, and training work that lacks SAM.gov notice IDs but is still strategically useful.
- Solicitations with weak titles but strong documents after download.
- Sources-sought/RFI notices with low immediate revenue value but high agency relationship value.
- Commodity-sourcing opportunities that could be practical if supply chain is easy and compliance burden is low.

### Scoring And Routing Width

The prime-control classifier moved the system in the right direction by treating JPTR/RCG as a prime-control and subcontractor-management business, not just a self-performance shop. The next refinement should be not more broad keyword matching, but clearer "why this is actionable today" classification:

- `pursue_now`: processed, documents available, bid path visible.
- `price_after_quote`: processed but vendor/subcontractor quote required.
- `sources_sought_response`: early-stage, strategically aligned.
- `teaming_target`: eligibility, specialization, or incumbent dynamics suggest partner-first.
- `manual_lookup`: good lead but unresolved identity/source.
- `manual_retry`: automation or login/download issue, worth another live attempt.
- `pass_not_ready`: weak fit, missing source, hard gate, or not enough data.

## 4. Report Quality

### Genuinely Useful Reports

- `reports/triage/finalist_action_board.md`: best daily operating board.
- `reports/triage/govcon_triage_review_pack.md`: best deeper analyst view.
- `reports/triage/govcon_triage_board.md`: useful intermediate grouping.
- `reports/opportunity_reviews/*_decision_report.md`: core bid/no-bid context.
- `reports/opportunity_reviews/*_compliance_matrix.md`: essential before proposal work.
- `reports/pricing/*_pricing_schedule.md` and `*_pricing_table.csv`: essential when present.
- `reports/pricing/*_bid_price_sanity.md`: right guardrail before pricing.
- `reports/market_intel/*_usaspending_intel.md`: useful finalist market context with caveats.
- `reports/sources_sought/*_sources_sought_plan.md`: useful because it treats RFIs as positioning assets, not failed solicitations.
- `reports/mybidmatch/mybidmatch_sam_resolution.md`: useful bridge between MyBidMatch and SAM/GovCon Scout.

### Redundant Or Noisy Reports

- Multiple timestamped `govcon_scout_*` reports in the root `reports/` folder are historically useful but not good daily working surfaces.
- Multiple batch-run markdown files are useful logs but should not be manually scanned daily.
- Duplicate naming around `bid_price_sanity` and `bid_price_sanity_check` creates confusion.
- Analysis packets are useful source material but should be linked from dashboards rather than inspected one by one.

### Dashboard Recommendation

The Finalist Action Board is now the right daily operating view. It should be treated as the front door. Every downstream report should be one click away from it, and every action row should be traceable to its source evidence.

Next report-quality improvement: make every report include a standard "Related Outputs" block that links:
- action board
- triage review pack
- analysis packet
- decision report
- compliance matrix
- pricing schedule/table
- USAspending intel
- bid price sanity
- sources-sought plan
- manual-review report

## 5. Automation Safety

### noVNC / Live Mode

The noVNC scripts are a strong reliability improvement. `process_shortlist.py` checks live mode before processing, and `process_opportunity_live.py` exits with code `86` when live infrastructure is unavailable. That is the right boundary: infrastructure failures should not create manual-review reports.

Recommended safety polish:
- Standardize the wording between `novnc_check.sh`, `process_shortlist.py`, and `process_opportunity_live.py`.
- Add a tiny `src/live_preflight.py` helper or shared function to avoid drift.
- Confirm executable bit in Git for both noVNC shell scripts during commits.

### Manual Review Boundaries

The current logic correctly avoids manual-review creation for live infrastructure failure in `process_shortlist.py`. Manual-review reports are still created when opportunity processing fails after a real solicitation attempt, which is correct.

Watch point: `process_opportunity.py` saved-auth mode still creates manual-review reports when no local files exist. That is appropriate if saved auth passed or the user skipped auth check intentionally. It should not be called blindly when saved auth is known unreliable.

### Secrets And Local Files

`.gitignore` protects:
- `auth.json`
- `mybidmatch_auth.json`
- `.env`
- `.env.*`
- `.browser/`
- Python caches
- debug folders
- `downloads/_debug/`
- local session screenshots
- `node_modules/`

Local secret/session files exist in the workspace but are ignored. Do not print or commit them.

### Generated File Safety

The system already avoids overwriting last useful reports when a scan returns zero scored opportunities. That is good.

Current risk: several report writers regenerate "latest" files and timestamped artifacts, while other tools infer state from whichever files exist. This can make stale output look current. A state file with run timestamp, source hash, and artifact freshness would reduce this risk.

## 6. Data Layer

### Is `data/pipeline.csv` Enough?

Not for the next phase. `data/pipeline.csv` is useful, but it does not yet fully connect:
- SAM.gov scan rows
- MyBidMatch resolved records
- processing status
- sources-sought plans
- manual-review status
- USAspending status
- pricing sanity status
- next human action
- owner/action due date
- artifacts and timestamps

The system should centralize state before adding another external source.

### CSV, SQLite, Or JSON?

Recommendation: start with a single structured JSON or SQLite state store, not another loosely defined CSV.

Practical path:
1. Build `data/opportunity_state.json` first because it is simple, diffable, and standard-library friendly.
2. Use one record per `canonical_id`.
3. Store source IDs: `sam_notice_id`, `mybidmatch_source_url`, `mybidmatch_title`, `usaspending_report_path`.
4. Store artifact paths and status timestamps.
5. Later migrate to SQLite only if querying/filtering becomes painful.

Suggested state fields:
- `canonical_id`
- `source_systems`
- `title`
- `agency`
- `base_lane`
- `fulfillment_path`
- `prime_control_risk`
- `triage_status`
- `processing_status`
- `manual_review_status`
- `sources_sought_status`
- `usaspending_status`
- `pricing_status`
- `bid_price_sanity_action`
- `mybidmatch_resolution_status`
- `recommended_next_action`
- `owner`
- `artifact_paths`
- `last_updated`

### Status Writeback

Every major report-generating script should update the state record after writing its output. The action board should read state first, then fall back to artifact discovery.

## 7. MyBidMatch Integration

The MyBidMatch flow is strategically useful. It adds another top-of-funnel source without pretending every third-party lead is a SAM.gov opportunity.

Current flow:
- parser extracts 385 records.
- triage narrows into priority/possible/team/ignore.
- resolver compares to GovCon Scout rows and classifies confirmed, possible, state/local, manual lookup, and duplicate items.

### Confirmed GovCon Matches

Next step: confirmed matches should be promoted into the shared state table and linked to the existing GovCon/SAM record. They should not automatically trigger downloads. The human should confirm title/agency first from the Finalist Action Board.

### Possible Matches

Possible matches should stay in "validate match" until the operator confirms:
- title alignment
- agency alignment
- source URL/date
- whether it is the same notice or a related/reposted opportunity

### State/Local Leads

State/local leads deserve their own later workflow, but not yet. The right future workflow is:
- source verification
- due-date extraction
- buyer/source link capture
- state/local bid portal login status
- document/manual package tracking
- no USAspending unless there is a federal/SAM connection

## 8. USAspending / Pricing Intelligence

The repo uses USAspending API v2, which is correct. The question is not whether v1 is good enough; v1 should not be used. The current v2 finalist module is good enough for early market context, not for final pricing.

### What To Improve Before Relying Heavily

- Add visible data-quality scoring for each query: close comparable, broad comparable, weak comparable, no useful data.
- Separate agency-specific results from general NAICS/PSC market results.
- Add query trace into the CSV/report so bad comparables are easy to reject.
- Add exclusion terms or domain-specific filters for obvious unrelated results.
- Add a manual "keep/reject comparable" field later.

### Bid Price Sanity Evolution

`bid_price_sanity.py` is correctly conservative. It should continue to avoid creating a bid price.

Next useful improvements:
- Compare base/options count from pricing table against apparent award periods in historical data.
- Parse contract type and site visit/access obligations from compliance report more reliably.
- Add "quote package checklist" for subcontractors.
- Add a "priceability score" based on CLIN clarity, blank unit prices, historical data quality, and vendor dependency.

### Human Review Before Pricing

Always keep human review for:
- final price
- margin
- subcontractor/vendor quotes
- site visit assumptions
- base access
- compliance forms
- socioeconomic/set-aside eligibility
- insurance/licensing
- past performance/case report requirements

## 9. Next Strategic Builds

### 1. Shared Opportunity State Store

- **Why it matters:** This will make every board/report read the same truth instead of scraping markdown and checking file existence.
- **Files likely to change:** new `src/opportunity_state.py`, `src/artifacts.py`, `data/opportunity_state.json`, `src/triage_review_pack.py`, `src/finalist_action_board.py`, `src/process_shortlist.py`.
- **Complexity:** Medium.
- **Risk:** Medium. Bad migration could mislabel items, so start with read-only generation from current artifacts.
- **Recommended priority:** 1.
- **Build timing:** Build now.

### 2. Operator Refresh Command

- **Why it matters:** One command should rebuild all local dashboards without external calls.
- **Files likely to change:** new `src/operator_refresh.py`, maybe `README.md`.
- **Complexity:** Low.
- **Risk:** Low if it defaults to local-only.
- **Recommended priority:** 2.
- **Build timing:** Build now.

### 3. Artifact Registry / Related Output Links

- **Why it matters:** Reports should link cleanly to one another without every script reimplementing filenames.
- **Files likely to change:** new `src/artifacts.py`, `src/triage_review_pack.py`, `src/finalist_action_board.py`, report scripts.
- **Complexity:** Low/Medium.
- **Risk:** Low.
- **Recommended priority:** 3.
- **Build timing:** Build now.

### 4. MyBidMatch Confirmed Match Promotion

- **Why it matters:** Confirmed MyBidMatch/SAM matches should become operator tasks tied to existing notice IDs.
- **Files likely to change:** `src/mybidmatch_sam_resolver.py`, `src/finalist_action_board.py`, shared state module.
- **Complexity:** Medium.
- **Risk:** Medium because false confirmations can waste time.
- **Recommended priority:** 4.
- **Build timing:** Build after state store skeleton.

### 5. Manual Review Triage Enhancer

- **Why it matters:** Manual-review items need better reasons: login issue, no link, external portal, selector gap, source text-only, or low value.
- **Files likely to change:** `src/manual_review_report.py`, `src/triage_board.py`, `src/finalist_action_board.py`.
- **Complexity:** Medium.
- **Risk:** Low.
- **Recommended priority:** 5.
- **Build timing:** Build now/later after state store.

### 6. USAspending Data Quality Scoring

- **Why it matters:** Market intel should distinguish close comparables from broad noise.
- **Files likely to change:** `src/usaspending_intel.py`, `src/bid_price_sanity.py`, `src/triage_review_pack.py`.
- **Complexity:** Medium.
- **Risk:** Medium because too-strict filters may hide useful context.
- **Recommended priority:** 6.
- **Build timing:** Later, after current finalists are reviewed.

### 7. Subcontractor Quote Package Checklist

- **Why it matters:** For pest, janitorial, facilities, towing, hauling, and security, the next business step is quote coverage.
- **Files likely to change:** new `src/vendor_quote_packet.py`, `src/bid_price_sanity.py`, `reports/pricing/`.
- **Complexity:** Medium.
- **Risk:** Low.
- **Recommended priority:** 7.
- **Build timing:** Build after state store or for Fort Campbell first.

### 8. Report Noise Cleanup Policy

- **Why it matters:** Generated reports are accumulating and making Git status hard to interpret.
- **Files likely to change:** `.gitignore`, `README.md`, possibly output naming in `src/main.py`.
- **Complexity:** Low.
- **Risk:** Medium if useful proof-of-work files are ignored or hidden.
- **Recommended priority:** 8.
- **Build timing:** Build now as a small cleanup.

### 9. State/Local Lead Workflow

- **Why it matters:** MyBidMatch has many state/local leads that may be valuable but should not be jammed into SAM.gov processing.
- **Files likely to change:** new `src/state_local_triage.py`, MyBidMatch scripts, reports under `reports/state_local/`.
- **Complexity:** High.
- **Risk:** Medium/high because portals differ heavily.
- **Recommended priority:** 9.
- **Build timing:** Later.

### 10. Downloader Selector Improvement Board

- **Why it matters:** Instead of blind selector work, this would rank recurring download failures worth fixing.
- **Files likely to change:** new `src/downloader_failure_board.py`, `src/sam_browser_downloader.py`, `src/process_opportunity_live.py`.
- **Complexity:** Medium.
- **Risk:** Medium because SAM/PIEE UI changes can break selectors.
- **Recommended priority:** 10.
- **Build timing:** Later, after current manual-review queue is reviewed.

## 10. Immediate Cleanup Recommendations

- Consolidate `bid_price_sanity.py` and `bid_price_sanity_check.py` naming/output conventions.
- Add `reports/system_review/` to the documented report map.
- Add `reports/triage/finalist_action_board.md` to README as the daily operating board.
- Add a local-only command section to README that explicitly says it does not call external APIs.
- Add a short comment/header to experimental login/session scripts so the operator knows they are utilities, not the primary path.
- Add `reports/*_2026-05-21_*.md` or a broader timestamped-report ignore rule only if those outputs should remain local noise. Be careful: some dated reports are proof-of-work.
- Fix `requirements.txt` formatting if `playwrightpypdf` is unintended. It currently reads like `playwright` and `pypdf` may have been joined.
- Standardize `USAspending` capitalization across reports.
- Add "generated from source file X at time Y" lines to action board and review pack.
- Add a stale-artifact warning when a report is older than the latest CSV or processing batch.

## 11. Do Not Overbuild Yet

Do not build these yet:

- Full CRM/contact management. A simple owner/action field is enough for now.
- Automated SAM.gov login/session renewal. Login.gov/SAM.gov MFA makes this brittle and risky.
- Bulk USAspending for every lead. This violates the finalist-only strategy and creates noise.
- Full proposal generator. The system should first produce quote checklists, compliance gates, and response outlines.
- State/local portal automation. State/local should get a separate workflow later.
- Heavy database/admin UI. A JSON state layer or SQLite file is enough before a web app.
- Complex machine-learning classifier. The current keyword/lane/prime-control logic is transparent and easier to correct.
- Automatic bid pricing. The correct role is sanity checking and quote readiness, not inventing prices.
- Broad downloader selector rewrites before the manual-review board identifies high-value recurring patterns.
- Historical award "win probability" scoring. It would overstate what the data can support.

## Recommended Next Action

Build the shared opportunity state store and local operator refresh command next. This is the highest-leverage step because it will make every later feature safer: MyBidMatch promotion, USAspending status, pricing sanity, manual retry, and the action board can all read/write one record.

Suggested next build:

```bash
python src/operator_refresh.py
```

Default behavior should be local-only:
- rebuild triage board
- rebuild triage review pack
- rebuild finalist action board
- refresh artifact status/state
- do not call SAM.gov
- do not call USAspending
- do not download documents

## Recommended Commit Message

`Add GovCon Scout system review`

