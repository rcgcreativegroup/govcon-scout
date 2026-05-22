# USAspending Intel - W912BV26QA059

**Generated:** 2026-05-22
**API Grounding:** USAspending API V2; `/api/v2/search/spending_by_award/` Advanced Award Search endpoint; no API key used.

## Executive Summary

- **Awards found:** 28 deduplicated award(s) from finalist-only queries.
- **Total returned award value:** $1,647,402,684
- **Date range in returned awards:** 2007-07-01 to 2025-07-01
- This is market intelligence for validation, not a win-probability estimate.

## Opportunity Metadata

- **Notice ID:** W912BV26QA059
- **Title:** Tulsa Resident Office Janitorial Services
- **Agency:** DEPT OF DEFENSE.DEPT OF THE ARMY.US ARMY CORPS OF ENGINEERS.ENGINEER DIVISION SOUTHWESTERN.ENDIST TULSA.W076 ENDIST TULSA
- **Office:** 
- **NAICS:** 561720
- **PSC:** S201
- **Place of Performance:** {"streetAddress": "", "zip": ""}
- **Set-Aside:** Total Small Business Set-Aside (FAR 19.5)
- **Matched Keywords:** 
- **Fit Score:** 59
- **Prime Reality Score:** 59
- **Recommendation:** Watch / Possible Subcontractor
- **Conditional Recommendation:** Review as Prime or Teaming Candidate

### Related GovCon Scout Outputs

- **Decision Report:** `reports/opportunity_reviews/W912BV26QA059_decision_report.md`
- **Compliance Matrix:** `reports/opportunity_reviews/W912BV26QA059_compliance_matrix.md`
- **Bid/No-Bid Review:** `reports/opportunity_reviews/W912BV26QA059_bid_no_bid.md`
- **Analysis Packet:** `reports/analysis_packets/W912BV26QA059.md`

## Query Strategy Used

- **Endpoint:** `https://api.usaspending.gov/api/v2/search/spending_by_award/`
- **Limit per query:** 25
- **Lookback:** approximately 5 fiscal/calendar year(s) using API date filters.
- **Award type filter:** contract award type codes were used; IDVs are intentionally left for a later pass because USAspending requires award type filters from one group per request.

| Query | Filters | Results | Error |
|---|---|---:|---|
| Query A - NAICS + agency | naics_codes=['561720']; agencies=[{'type': 'awarding', 'tier': 'toptier', 'name': 'DEPT OF DEFENSE'}] | 0 |  |
| Query B - PSC + agency | psc_codes=['S201']; agencies=[{'type': 'awarding', 'tier': 'toptier', 'name': 'DEPT OF DEFENSE'}] | 0 |  |
| Query C - NAICS + title/lane terms | naics_codes=['561720']; keywords=['tulsa', 'resident', 'office', 'janitorial'] | 25 |  |
| Query D - PSC + title/lane terms | psc_codes=['S201']; keywords=['tulsa', 'resident', 'office', 'janitorial'] | 25 |  |

## Historical Award Summary

- **Deduplicated awards found:** 28
- **Total value:** $1,647,402,684
- **Award date range:** 2007-07-01 to 2025-07-01
- **Top recipients by returned value/count:**
  - FEDCAP REHABILITATION SERVICES, INC: 2 award(s), $219,109,737
  - J & J MAINTENANCE INC: 1 award(s), $174,791,086
  - B & O JOINT VENTURE LLC: 1 award(s), $111,766,954
  - HUNTSVILLE REHABILITATION FOUNDATION: 2 award(s), $110,293,980
  - GOODWILL INDUSTRIES OF NORTH GEORGIA, INC.: 2 award(s), $82,753,200
- **Most common awarding agencies/subagencies:**
  - Defense Health Agency: 5 award(s)
  - Department of the Army: 4 award(s)
  - Federal Law Enforcement Training Center: 2 award(s)
  - Public Buildings Service: 2 award(s)
  - Washington Headquarters Services: 2 award(s)
- **Most common returned NAICS:**
  - 561720: 25 award(s)
  - 561210: 3 award(s)
- **Most common returned PSC:**
  - S201: 27 award(s)
  - S299: 1 award(s)

## Award Value Range

- **Minimum:** $33,479,102
- **Median:** $48,351,691
- **Average:** $58,835,810
- **Maximum:** $174,791,086

## Top Recipients / Possible Incumbents

- **FEDCAP REHABILITATION SERVICES, INC:** 2 award(s), $219,109,737
- **J & J MAINTENANCE INC:** 1 award(s), $174,791,086
- **B & O JOINT VENTURE LLC:** 1 award(s), $111,766,954
- **HUNTSVILLE REHABILITATION FOUNDATION:** 2 award(s), $110,293,980
- **GOODWILL INDUSTRIES OF NORTH GEORGIA, INC.:** 2 award(s), $82,753,200
- **LB & B ASSOCIATES INC:** 1 award(s), $77,632,500
- **EMCOR GOVERNMENT SERVICES, INC:** 1 award(s), $69,574,457
- **OS-DB-JV-2 LLC:** 1 award(s), $60,409,485
- **THE VICTOR GROUP, INC.:** 1 award(s), $55,173,846
- **DALE ROGERS TRAINING CENTER, INC.:** 1 award(s), $53,944,047

## Similar Award Examples

| Recipient | Amount | Award Date | Agency / Subagency | Description | NAICS | PSC | Award ID / PIID |
|---|---:|---|---|---|---|---|---|
| J & J MAINTENANCE INC | $174,791,086 | 2021-02-01 | Department of the Army | HEALTHCARE HOUSEKEEPING SERVICES, BROOKE ARMY MEDICAL CENTER, FORT SAM HOUSTON, TX | 561720 | S299 | W81K0421C0001 |
| B & O JOINT VENTURE LLC | $111,766,954 | 2018-09-01 | Federal Law Enforcement Training Center | IGF::CT::IGF DORM MANAGEMENT SERVICES CONTRACT, GLYNCO, GA | 561720 | S201 | 70LGLY18CGLB00003 |
| FEDCAP REHABILITATION SERVICES, INC | $109,895,372 | 2022-01-01 | Public Buildings Service | BASE PERIOD JANITORIAL SERVICES CONTRACT FOR MANHATTAN, GSA PBS REGION 2. | 561720 | S201 | 47PC0622F0003 |
| FEDCAP REHABILITATION SERVICES, INC | $109,214,365 | 2016-12-01 | Public Buildings Service | IGF::OT::IGF MANHATTAN CAMPUS CUSTODIAL SERVICES | 561720 | S201 | GSP0217PV0044 |
| LB & B ASSOCIATES INC | $77,632,500 | 2016-03-01 | National Archives and Records Administration | COMPLETE FACILITIES MAINTENANCE (CFM) SERVICES AT ARCHIVES I AND II  IGF::OT::IGF | 561210 | S201 | NAMA16F0028 |
| EMCOR GOVERNMENT SERVICES, INC | $69,574,457 | 2021-10-01 | National Archives and Records Administration | THIS TASK ORDER IS FOR COMPLETE FACILITIES MAINTENANCE (CFM) AT NATIONAL ARCHIVES I, WASHINGTON, DC AND ARCHIVES II, COLLEGE PARK, MD FACILITIES. | 561210 | S201 | 88310321F00171 |
| HUNTSVILLE REHABILITATION FOUNDATION | $66,953,547 | 2020-11-01 | Department of the Army | CUSTODIAL SERVICE  BASIC (B)  NEW CONTRACT FOR CUSTODIAL | 561720 | S201 | W9124P21C0002 |
| OS-DB-JV-2 LLC | $60,409,485 | 2021-04-01 | Department of Veterans Affairs | TO PROVIDE JANITORIAL SERVICES AT THE VA CARIBBEAN HEALTHCARE SYSTEM (VACHS), SOUTH BED TOWER (SBT), PONCE OUTPATIENT CLINIC (POPC), AND MAYAGUEZ OUTPATIENT CLINIC (MOPC). | 561720 | S201 | 36C24821C0013 |
| THE VICTOR GROUP, INC. | $55,173,846 | 2021-09-01 | Defense Health Agency | HEALTHCARE ASEPTIC MANAGEMENT SERVICES | 561720 | S201 | HT001521C5005 |
| DALE ROGERS TRAINING CENTER, INC. | $53,944,047 | 2018-05-01 | Department of the Air Force | ABW CUSTODIAL CONTRACT AWARD BASE: 1 MAY 18 - 30 SEP 18 | 561720 | S201 | FA810118C0001 |
| DIDLAKE INC | $53,326,158 | 2022-05-27 | Washington Headquarters Services | ABILITYONE CUSTODIAL SERVICES, PENTAGON (FLOORS 1, 5, BASEMENT, MEZZANINE, PENTAGON LIBRARY AND CONFERENCE CENTER, NORTH VILLAGE/COMPOUND, PENTAGON ATHLETIC CENTER) | 561720 | S201 | HQ003422C0059 |
| MAIN BUILDING MAINTENANCE, INC. | $50,586,729 | 2021-09-01 | Defense Health Agency | HEALTHCARE ASEPTIC MANAGEMENT SERVICES - NORTHWEST REGION | 561720 | S201 | HT001521C5003 |
| TITAN FACILITY SERVICES LLC | $50,558,089 | 2021-09-01 | Defense Health Agency | HEALTHCARE ASEPTIC MANAGEMENT SERVICES, SOUTHEAST REGION | 561720 | S201 | HT001521C5004 |
| JOB OPTIONS, INCORPORATED | $48,435,910 | 2007-09-29 | Department of the Navy | CUSTODIAL SERVICES FOR NAVAL MEDICAL CENTER, SAN DIEGO, CA | 561720 | S201 | 0007 |
| LOCKWOOD HILLS FEDERAL LLC | $48,267,473 | 2017-06-22 | National Aeronautics and Space Administration | IGF::OT::IGF LOGISTICS MANAGEMENT SERVICES- THE LOGISTICS OFFICE SUPPORTS NASA'S MISSION BY PROVIDING INSTITUTIONAL SERVICES FOR ARC, WHICH INCLUDES CONTRACTOR AND GOVERNMENT STAFF | 561210 | S201 | 80ARC017C0001 |

## Pricing / Bid Realism Notes

- No pricing schedule artifact was found; compare historical award values only at a rough market-sizing level.
- Historical range suggests comparable awards span $33,479,102 to $174,791,086, with a median of $48,351,691. This requires validation against scope, period, location, and contract type.
- Do not infer a bid price from USAspending alone; use it to pressure-test labor, materials, incumbent context, and ceiling/option-year scale.

## Prime vs Teaming Implications

- Prime reality score from GovCon Scout: 59.
- Set-aside context: Total Small Business Set-Aside (FAR 19.5)
- Repeat/large recipients to validate as possible incumbents or teaming intelligence: FEDCAP REHABILITATION SERVICES, INC, J & J MAINTENANCE INC, B & O JOINT VENTURE LLC.
- Treat prime vs teaming as a decision requiring validation, not as a probability estimate.

## Source API Notes

- USAspending records are useful for market sizing and incumbent research, but they may not mirror the exact solicitation scope, period of performance, location, options, or set-aside strategy.
- Award amounts can represent obligations, ceilings, base plus options, or modifications depending on award type and record structure.
- The lookback filter can surface parent awards with older start dates when related spending activity falls inside the requested period; validate action dates before treating a record as current-market evidence.
- Contract award type filters were used conservatively. IDV award history may still matter and should be checked separately for finalists where task-order context is important.
- The official USAspending documentation identifies V2 as current and V1 as deprecated, and documents `/api/v2/search/spending_by_award/` as the Spending by Award Advanced Search endpoint.

## Recommended Next Action

1. Validate whether the returned awards are truly comparable in scope, location, and period of performance.
2. Identify repeat recipients that may be incumbents, partners, or benchmarks.
3. Compare historical award range against the pricing schedule and solicitation scope; do not infer a bid price directly.
4. Review decision/compliance outputs before deciding prime, teaming, sources-sought response, or pass.
5. If results are sparse or noisy, refine with agency-specific terms, PSC/NAICS alternatives, or manual FPDS/SAM award checks.
