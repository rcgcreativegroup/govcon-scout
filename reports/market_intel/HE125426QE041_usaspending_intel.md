# USAspending Intel - HE125426QE041

**Generated:** 2026-05-21
**API Grounding:** USAspending API V2; `/api/v2/search/spending_by_award/` Advanced Award Search endpoint; no API key used.

## Executive Summary

- **Awards found:** 17 deduplicated award(s) from finalist-only queries.
- **Total returned award value:** $42,129,891
- **Date range in returned awards:** 2012-03-30 to 2025-09-30
- This is market intelligence for validation, not a win-probability estimate.

## Opportunity Inputs

- **Notice ID:** HE125426QE041
- **Title:** Fort Campbell Integrated Pest Management (IPM) Services
- **Agency:** DEPT OF DEFENSE.DEPT OF DEFENSE EDUCATION ACTIVITY (DODEA).DOD EDUCATION ACTIVITY
- **Office:** 
- **NAICS:** 561710
- **PSC:** S207
- **Place of Performance:** 42223
- **Set-Aside:** Total Small Business Set-Aside (FAR 19.5)
- **Fit Score:** 65
- **Prime Reality Score:** 65

### Related GovCon Scout Outputs

- **Decision Report:** `reports/opportunity_reviews/HE125426QE041_decision_report.md`
- **Compliance Matrix:** `reports/opportunity_reviews/HE125426QE041_compliance_matrix.md`
- **Bid/No-Bid Review:** `reports/opportunity_reviews/HE125426QE041_bid_no_bid.md`
- **Pricing Schedule:** `reports/pricing/HE125426QE041_pricing_schedule.md`
- **Analysis Packet:** `reports/analysis_packets/HE125426QE041.md`

## USAspending Search Strategy

- **Endpoint:** `https://api.usaspending.gov/api/v2/search/spending_by_award/`
- **Limit per query:** 10
- **Lookback:** approximately 5 fiscal/calendar year(s) using API date filters.
- **Award type filter:** contract award type codes were used; IDVs are intentionally left for a later pass because USAspending requires award type filters from one group per request.

| Query | Filters | Results | Error |
|---|---|---:|---|
| Query A - NAICS + PSC | naics_codes=['561710']; psc_codes=['S207'] | 10 |  |
| Query B - NAICS only | naics_codes=['561710'] | 10 |  |
| Query C - PSC only | psc_codes=['S207'] | 10 |  |
| Query D - title/core keyword search | keywords=['fort', 'campbell', 'integrated', 'pest', 'management'] | 10 |  |
| Query E - agency + NAICS | naics_codes=['561710']; agencies=[{'type': 'awarding', 'tier': 'toptier', 'name': 'DEPT OF DEFENSE'}] | 0 |  |

## Historical Award Snapshot

- **Deduplicated awards found:** 17
- **Total value:** $42,129,891
- **Award date range:** 2012-03-30 to 2025-09-30
- **Top recipients by returned value/count:**
  - ALLEYMOR, INC.: 2 award(s), $5,939,708
  - ACCORD FEDERAL SERVICES, LLC: 2 award(s), $4,468,879
  - PESTMASTER SERVICES, L.P.: 1 award(s), $3,908,553
  - CDS SERVICES INC: 1 award(s), $3,426,964
  - POWER HOUSE TERMITE AND PEST CONTROL INC.: 1 award(s), $3,246,200
- **Most common awarding agencies/subagencies:**
  - Department of the Army: 10 award(s)
  - Department of Veterans Affairs: 3 award(s)
  - Public Buildings Service: 1 award(s)
  - Department of the Navy: 1 award(s)
  - U.S. Customs and Border Protection: 1 award(s)

## Award Range

- **Minimum:** $1,896,101
- **Median:** $2,192,913
- **Average:** $2,478,229
- **Maximum:** $3,908,553

## Top Recipients / Possible Incumbents

- **ALLEYMOR, INC.:** 2 award(s), $5,939,708
- **ACCORD FEDERAL SERVICES, LLC:** 2 award(s), $4,468,879
- **PESTMASTER SERVICES, L.P.:** 1 award(s), $3,908,553
- **CDS SERVICES INC:** 1 award(s), $3,426,964
- **POWER HOUSE TERMITE AND PEST CONTROL INC.:** 1 award(s), $3,246,200
- **SOLUTIONS A.E. INC.:** 1 award(s), $2,426,948
- **ALEXANDRIA PEST SERVICES, INC.:** 1 award(s), $2,392,125
- **SAFESKYS LIMITED:** 1 award(s), $2,192,913
- **CHUGACH CONSOLIDATED SOLUTIONS, LLC:** 1 award(s), $2,189,845
- **CHENEGA GOVERNMENT MISSION SOLUTIONS, LLC:** 1 award(s), $2,099,029

## Similar Award Table

| Recipient | Amount | Award Date | Agency / Subagency | Description | NAICS | PSC | Award ID / PIID |
|---|---:|---|---|---|---|---|---|
| PESTMASTER SERVICES, L.P. | $3,908,553 | 2017-09-15 | Department of the Army | 0001 SCHEDULED  MAINT INTERIOR IGF::OT::IGF | 561710 | S207 | W911SD17F0125 |
| ALLEYMOR, INC. | $3,618,505 | 2012-03-30 | Department of the Army | SCHLD PEST MANGT - INTERIOR | 561710 | S207 | W911SD12P0133 |
| CDS SERVICES INC | $3,426,964 | 2019-07-01 | Department of Veterans Affairs | THE CONTRACTOR PROVIDES PEST CONTROL SERVICES | 561710 | F105 | 36C26219C0115 |
| POWER HOUSE TERMITE AND PEST CONTROL INC. | $3,246,200 | 2022-10-15 | Public Buildings Service | AWARD FOR THE SNAFC PEST CONTROL. | 561710 | S207 | 47PE0222C0052 |
| SOLUTIONS A.E. INC. | $2,426,948 | 2023-12-04 | Department of the Army | 3ABCT AGWASH | 561710 | F999 | W911RZ24F0001 |
| ALEXANDRIA PEST SERVICES, INC. | $2,392,125 | 2019-04-26 | Department of the Army | IGF::OT::IGF PEST MANAGEMENT SERVICES | 561710 | S207 | W91QV119C0033 |
| ALLEYMOR, INC. | $2,321,203 | 2023-03-29 | Department of the Army | PEST MANAGEMENT SERVICES, WEST POINT, NY 10996 | 325320 | S207 | W911SD23F0059 |
| ACCORD FEDERAL SERVICES, LLC | $2,292,394 | 2019-10-01 | Department of Veterans Affairs | TVHS - CHILLER MAINTENANCE SERVICES | 561710 | J041 | 36C24920F0038 |
| SAFESKYS LIMITED | $2,192,913 | 2019-06-15 | Department of the Air Force | THE CONTRACTOR SHALL PROVIDE A COMPREHENSIVE BIRD/WILDLIFE HAZARD CONTROL PROGRAM, WITH FOCUS ON ELIMINATING OR MINIMIZING WILDLIFE HAZARDS FOR SAFE AIR AND GROUND SUPPORT OPERATIO | 561710 | F019 | FA558719CA006 |
| CHUGACH CONSOLIDATED SOLUTIONS, LLC | $2,189,845 | 2020-04-16 | Department of the Navy | FUNDING TASK ORDER MOSQUITO LARVAL AND PEST CONTROL: ABOARD MARI | 561710 | S207 | N6247320F4611 |
| ACCORD FEDERAL SERVICES, LLC | $2,176,485 | 2023-01-03 | Department of Veterans Affairs | GROUNDS MAINTENANCE SERVICES FOR LITTLE ROCK AND NORTH LITTLE ROCK CAMPUSES | 561710 | S208 | 36C25623F0084 |
| CHENEGA GOVERNMENT MISSION SOLUTIONS, LLC | $2,099,029 | 2025-09-30 | U.S. Customs and Border Protection | JANITORIAL, GROUNDS MAINTENANCE, REFUSE AND SNOW REMOVAL SERVICES | 561710 | S207 | 70B03C25C00000099 |
| INNOVATIVE PEST MANAGEMENT, LLC | $2,063,987 | 2014-09-15 | Department of the Army | UNSCHEDULED SERVICES | 561710 | S207 | W91QV114C0146 |
| GETEM MANUFACTURING COMPANY, INCORPORATED | $1,988,376 | 2020-01-16 | Department of the Army | SCHEDULED SERVICES | 561710 | S207 | W91QV120C0009 |
| BROWN POINT FACILITY MANAGEMENT SOLUTIONS, LLC | $1,973,966 | 2023-02-03 | Department of the Army | PEST MGMT SERVICES- | 561210 | S207 | W9115123F0079 |

## Pricing / Pursuit Implications

- Pricing schedule exists at `reports/pricing/HE125426QE041_pricing_schedule.md` with 32 extracted pricing line(s).
- Historical range suggests comparable awards span $1,896,101 to $3,908,553, with a median of $2,192,913. This requires validation against scope, period, location, and contract type.
- Do not infer a bid price from USAspending alone; use it to pressure-test labor, materials, incumbent context, and ceiling/option-year scale.

## Prime vs Teaming Notes

- Prime reality score from GovCon Scout: 65.
- Set-aside context: Total Small Business Set-Aside (FAR 19.5)
- Repeat/large recipients to validate as possible incumbents or teaming intelligence: ALLEYMOR, INC., ACCORD FEDERAL SERVICES, LLC, PESTMASTER SERVICES, L.P..
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
