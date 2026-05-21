# USAspending Intel - W912BV26QA059

**Generated:** 2026-05-21
**API Grounding:** USAspending API V2; `/api/v2/search/spending_by_award/` Advanced Award Search endpoint; no API key used.

## Executive Summary

- **Awards found:** 23 deduplicated award(s) from finalist-only queries.
- **Total returned award value:** $105,813,659,229
- **Date range in returned awards:** 2005-06-01 to 2022-01-01
- This is market intelligence for validation, not a win-probability estimate.

## Opportunity Inputs

- **Notice ID:** W912BV26QA059
- **Title:** Tulsa Resident Office Janitorial Services
- **Agency:** DEPT OF DEFENSE.DEPT OF THE ARMY.US ARMY CORPS OF ENGINEERS.ENGINEER DIVISION SOUTHWESTERN.ENDIST TULSA.W076 ENDIST TULSA
- **Office:** 
- **NAICS:** 561720
- **PSC:** S201
- **Place of Performance:** {"streetAddress": "", "zip": ""}
- **Set-Aside:** Total Small Business Set-Aside (FAR 19.5)
- **Fit Score:** 59
- **Prime Reality Score:** 59

### Related GovCon Scout Outputs

- **Decision Report:** `reports/opportunity_reviews/W912BV26QA059_decision_report.md`
- **Compliance Matrix:** `reports/opportunity_reviews/W912BV26QA059_compliance_matrix.md`
- **Bid/No-Bid Review:** `reports/opportunity_reviews/W912BV26QA059_bid_no_bid.md`
- **Analysis Packet:** `reports/analysis_packets/W912BV26QA059.md`

## USAspending Search Strategy

- **Endpoint:** `https://api.usaspending.gov/api/v2/search/spending_by_award/`
- **Limit per query:** 10
- **Lookback:** approximately 5 fiscal/calendar year(s) using API date filters.
- **Award type filter:** contract award type codes were used; IDVs are intentionally left for a later pass because USAspending requires award type filters from one group per request.

| Query | Filters | Results | Error |
|---|---|---:|---|
| Query A - NAICS + PSC | naics_codes=['561720']; psc_codes=['S201'] | 10 |  |
| Query B - NAICS only | naics_codes=['561720'] | 10 |  |
| Query C - PSC only | psc_codes=['S201'] | 10 |  |
| Query D - title/core keyword search | keywords=['tulsa', 'resident', 'office', 'janitorial'] | 10 |  |
| Query E - agency + NAICS | naics_codes=['561720']; agencies=[{'type': 'awarding', 'tier': 'toptier', 'name': 'DEPT OF DEFENSE'}] | 0 |  |

## Historical Award Snapshot

- **Deduplicated awards found:** 23
- **Total value:** $105,813,659,229
- **Award date range:** 2005-06-01 to 2022-01-01
- **Top recipients by returned value/count:**
  - LAWRENCE LIVERMORE NATIONAL SECURITY, LLC: 1 award(s), $40,927,152,397
  - BELL BOEING JOINT PROJECT OFFICE: 3 award(s), $24,975,682,772
  - THE REGENTS OF THE UNIVERSITY OF CALIFORNIA: 1 award(s), $19,558,423,796
  - THE BOEING COMPANY: 1 award(s), $10,477,704,426
  - CALIFORNIA INSTITUTE OF TECHNOLOGY: 3 award(s), $5,805,909,329
- **Most common awarding agencies/subagencies:**
  - National Aeronautics and Space Administration: 5 award(s)
  - Defense Health Agency: 3 award(s)
  - Public Buildings Service: 2 award(s)
  - Department of the Army: 2 award(s)
  - Department of the Navy: 2 award(s)

## Award Range

- **Minimum:** $48,435,910
- **Median:** $111,766,954
- **Average:** $4,600,593,880
- **Maximum:** $40,927,152,397

## Top Recipients / Possible Incumbents

- **LAWRENCE LIVERMORE NATIONAL SECURITY, LLC:** 1 award(s), $40,927,152,397
- **BELL BOEING JOINT PROJECT OFFICE:** 3 award(s), $24,975,682,772
- **THE REGENTS OF THE UNIVERSITY OF CALIFORNIA:** 1 award(s), $19,558,423,796
- **THE BOEING COMPANY:** 1 award(s), $10,477,704,426
- **CALIFORNIA INSTITUTE OF TECHNOLOGY:** 3 award(s), $5,805,909,329
- **SPACE EXPLORATION TECHNOLOGIES CORP.:** 1 award(s), $3,029,850,124
- **FEDCAP REHABILITATION SERVICES, INC:** 2 award(s), $219,109,737
- **J & J MAINTENANCE INC:** 1 award(s), $174,791,086
- **B & O JOINT VENTURE LLC:** 1 award(s), $111,766,954
- **LB & B ASSOCIATES INC:** 1 award(s), $77,632,500

## Similar Award Table

| Recipient | Amount | Award Date | Agency / Subagency | Description | NAICS | PSC | Award ID / PIID |
|---|---:|---|---|---|---|---|---|
| LAWRENCE LIVERMORE NATIONAL SECURITY, LLC | $40,927,152,397 | 2007-05-09 | Department of Energy | TAS::89 0240::TAS THIS PERFORMANCE-BASED MANAGEMENT CONTRACT (PBMC) IS FOR THE MANAGEMENT AND OPERATION OF THE LAWRENCE LIVERMORE NATIONAL LABORATORY (LLNL). THE CONTRACTOR SHALL,  | 541710 | AZ11 | DEAC5207NA27344 |
| THE REGENTS OF THE UNIVERSITY OF CALIFORNIA | $19,558,423,796 | 2005-06-01 | Department of Energy | THIS PERFORMANCE-BASED MANAGEMENT CONTRACT (PBMC) IS FOR THE MANAGEMENT AND OPERATION OF THE ERNEST ORLANDO LAWRENCE BERKELEY NATIONAL LABORATORY (LBNL).  THE CONTRACTOR SHALL, IN  | 541710 | M181 | DEAC0205CH11231 |
| BELL BOEING JOINT PROJECT OFFICE | $11,040,980,157 | 2007-04-02 | Defense Contract Management Agency | MV-22 AIRCRAFT - FY08 (LOT 12)* | 336411 | 1520 | N0001907C0001 |
| THE BOEING COMPANY | $10,477,704,426 | 2007-09-01 | National Aeronautics and Space Administration | PROVIDE DEVELOPMENTAL HARDWARE AND TEST ARTICLES, AND MANUFACTURE AND ASSEMBLE ARES I UPPER STAGES. THE UPPER STAGE (US) ELEMENT IS AN INTEGRAL PART OF THE ARES I LAUNCH VEHICLE AN | 336414 | AR11 | NNM07AB03C |
| BELL BOEING JOINT PROJECT OFFICE | $7,337,457,222 | 2011-12-29 | Defense Contract Management Agency | PROCUREMENT OF V-22 LOT 17 LONG LEAD-TIME ITEMS | 336411 | 1510 | N0001912C2001 |
| BELL BOEING JOINT PROJECT OFFICE | $6,597,245,393 | 2016-12-28 | Department of the Navy | CMV-22 PRODUCTION LOT 22 LONG LEAD-TIME ITEMS | 336411 | 1510 | N0001917C0015 |
| SPACE EXPLORATION TECHNOLOGIES CORP. | $3,029,850,124 | 2016-12-30 | National Aeronautics and Space Administration | IGF::OT::IGF THE COMMERCIAL CREW PROGRAM (CCP) COMMERCIAL CREW TRANSPORTATION CAPABILITY (CCTCAP) CONTRACT WILL PROVIDE COMPLETION OF THE DESIGN, DEVELOPMENT, TEST, EVALUATION, AND | 336414 | V126 | NNK17MA01T |
| CALIFORNIA INSTITUTE OF TECHNOLOGY | $2,945,898,289 | 2018-10-01 | National Aeronautics and Space Administration | EUROPA CLIPPER PROJECT  THE CONTRACT IS THE SPONSORING AGREEMENT BETWEEN THE NATIONAL AERONAUTICS AND SPACE ADMINISTRATION NASA AND THE CALIFORNIA INSTITUTE OF TECHNOLOGY-CONTRACTO | 541715 | AR22 | 80NM0018F0615 |
| CALIFORNIA INSTITUTE OF TECHNOLOGY | $1,456,629,449 | 2018-10-01 | National Aeronautics and Space Administration | DEEP SPACE NETWORK (DSN)  THE CONTRACT IS THE SPONSORING AGREEMENT BETWEEN THE NATIONAL AERONAUTICS AND SPACE ADMINISTRATION (NASA) AND THE CALIFORNIA INSTITUTE OF TECHNOLOGY (CONT | 541715 | AR22 | 80NM0018F0850 |
| CALIFORNIA INSTITUTE OF TECHNOLOGY | $1,403,381,590 | 2013-07-24 | National Aeronautics and Space Administration | IGF::CL::IGF 2020 MARS SCIENCE ROVER PROJECT - PHASE A THE CONTRACT IS THE SPONSORING AGREEMENT BETWEEN THE NATIONAL AERONAUTICS AND SPACE ADMINISTRATION (NASA) AND THE CALIFORNIA  | 541712 | AR22 | NNN13D496T |
| J & J MAINTENANCE INC | $174,791,086 | 2021-02-01 | Department of the Army | HEALTHCARE HOUSEKEEPING SERVICES, BROOKE ARMY MEDICAL CENTER, FORT SAM HOUSTON, TX | 561720 | S299 | W81K0421C0001 |
| B & O JOINT VENTURE LLC | $111,766,954 | 2018-09-01 | Federal Law Enforcement Training Center | IGF::CT::IGF DORM MANAGEMENT SERVICES CONTRACT, GLYNCO, GA | 561720 | S201 | 70LGLY18CGLB00003 |
| FEDCAP REHABILITATION SERVICES, INC | $109,895,372 | 2022-01-01 | Public Buildings Service | BASE PERIOD JANITORIAL SERVICES CONTRACT FOR MANHATTAN, GSA PBS REGION 2. | 561720 | S201 | 47PC0622F0003 |
| FEDCAP REHABILITATION SERVICES, INC | $109,214,365 | 2016-12-01 | Public Buildings Service | IGF::OT::IGF MANHATTAN CAMPUS CUSTODIAL SERVICES | 561720 | S201 | GSP0217PV0044 |
| LB & B ASSOCIATES INC | $77,632,500 | 2016-03-01 | National Archives and Records Administration | COMPLETE FACILITIES MAINTENANCE (CFM) SERVICES AT ARCHIVES I AND II  IGF::OT::IGF | 561210 | S201 | NAMA16F0028 |

## Pricing / Pursuit Implications

- No pricing schedule artifact was found; compare historical award values only at a rough market-sizing level.
- Historical range suggests comparable awards span $48,435,910 to $40,927,152,397, with a median of $111,766,954. This requires validation against scope, period, location, and contract type.
- Do not infer a bid price from USAspending alone; use it to pressure-test labor, materials, incumbent context, and ceiling/option-year scale.

## Prime vs Teaming Notes

- Prime reality score from GovCon Scout: 59.
- Set-aside context: Total Small Business Set-Aside (FAR 19.5)
- Repeat/large recipients to validate as possible incumbents or teaming intelligence: LAWRENCE LIVERMORE NATIONAL SECURITY, LLC, BELL BOEING JOINT PROJECT OFFICE, THE REGENTS OF THE UNIVERSITY OF CALIFORNIA.
- Treat prime vs teaming as a decision requiring validation, not as a probability estimate.

## Data Limitations

- USAspending records are useful for market sizing and incumbent research, but they may not mirror the exact solicitation scope, period of performance, location, options, or set-aside strategy.
- Award amounts can represent obligations, ceilings, base plus options, or modifications depending on award type and record structure.
- The lookback filter can surface parent awards with older start dates when related spending activity falls inside the requested period; validate action dates before treating a record as current-market evidence.
- Contract award type filters were used conservatively. IDV award history may still matter and should be checked separately for finalists where task-order context is important.
- The official USAspending documentation identifies V2 as current and V1 as deprecated, and documents `/api/v2/search/spending_by_award/` as the Spending by Award Advanced Search endpoint.

## Recommended Next Actions

1. Validate whether the returned awards are truly comparable in scope, location, and period of performance.
2. Identify repeat recipients that may be incumbents, partners, or benchmarks.
3. Compare historical award range against the pricing schedule and solicitation scope; do not infer a bid price directly.
4. Review decision/compliance outputs before deciding prime, teaming, sources-sought response, or pass.
5. If results are sparse or noisy, refine with agency-specific terms, PSC/NAICS alternatives, or manual FPDS/SAM award checks.
