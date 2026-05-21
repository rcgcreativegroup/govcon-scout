# Pricing Schedule Extraction — HE125426QE041

## Opportunity Summary

- **Title:** Fort Campbell Integrated Pest Management (IPM) Services
- **Agency:** DEPT OF DEFENSE.DEPT OF DEFENSE EDUCATION ACTIVITY (DODEA).DOD EDUCATION ACTIVITY
- **Deadline:** 2026-06-02 03:00 PM CDT
- **Fit Score:** 65
- **Prime Reality Score:** 65
- **Workbook:** `downloads/HE125426QE041/ATT_4__Pricing_Schedule.xlsx`
- **Extracted Pricing Lines:** 32

## Pricing Readiness

- **Status:** Pricing schedule was detected and line items were extracted.

## Bid Pricing Warnings

- Confirm whether prices must include all labor, tools, materials, supplies, supervision, reporting, travel, insurance, and contingency.
- Confirm whether base year and option year pricing are required.
- Confirm whether CLIN structure may be changed. Assume no unless solicitation says otherwise.
- Confirm whether emergency/callback/after-hours work is included in the fixed price.
- Do not submit until unit prices and totals reconcile with the solicitation instructions.

## Sheet: Sheet1

**Detected Header Row:**

```text
 | CLIN | Period of Performance | Description | Quantity | Unit | Unit Price | Extended Price
```

**Detected Column Map:** `{'clin': 1, 'period': 2, 'description': 3, 'quantity': 4, 'unit': 5, 'total_price': 7}`

### Extracted Pricing Lines

| Period | CLIN | Description | Qty | Unit | Unit Price | Total |
|---|---|---|---:|---|---:|---:|
| 7/1/2026-6/30/2027 | 0001 | Ant/Roach Treatment | 2 | Each |  | =E6*G6 |
| 7/1/2026-6/30/2027 | 0002 | Field/Turf Treatment | 12 | Months |  | =E7*G7 |
| 7/1/2026-6/30/2027 | 0003 | Termite Treatment | 1 | Each |  | =E8*G8 |
| 7/1/2026-6/30/2027 | 0004 | Wasp/Bee Treatment | 6 | Bimonthly |  | =E9*G9 |
| 7/1/2026-6/30/2027 | 0005 | Rodent/Small Animal Control | 4 | Each |  | =E10*G10 |
|  |  | Base Period Total / =SUM(H6:H10) |  | Base Period Total |  | =SUM(H6:H10) |
| 7/1/2027-6/30/2028 | 1001 | Ant/Roach Treatment | 2 | Each |  | =E12*G12 |
| 7/1/2027-6/30/2028 | 1002 | Field/Turf Treatment | 12 | Months |  | =E13*G13 |
| 7/1/2027-6/30/2028 | 1003 | Termite Treatment | 1 | Each |  | =E14*G14 |
| 7/1/2027-6/30/2028 | 1004 | Wasp/Bee Treatment | 6 | Bimonthly |  | =E15*G15 |
| 7/1/2027-6/30/2028 | 1005 | Rodent/Small Animal Control | 4 | Each |  | =E16*G16 |
|  |  | Option Period One Total / =SUM(H12:H16) |  | Option Period One Total |  | =SUM(H12:H16) |
| 7/1/2028-6/30/2029 | 2001 | Ant/Roach Treatment | 2 | Each |  | =E18*G18 |
| 7/1/2028-6/30/2029 | 2002 | Field/Turf Treatment | 12 | Months |  | =E19*G19 |
| 7/1/2028-6/30/2029 | 2003 | Termite Treatment | 1 | Each |  | =E20*G20 |
| 7/1/2028-6/30/2029 | 2004 | Wasp/Bee Treatment | 6 | Bimonthly |  | =E21*G21 |
| 7/1/2028-6/30/2029 | 2005 | Rodent/Small Animal Control | 4 | Each |  | =E22*G22 |
|  |  | Option Period Two Total / =SUM(H18:H22) |  | Option Period Two Total |  | =SUM(H18:H22) |
| 7/1/2029-6/30/2030 | 3001 | Ant/Roach Treatment | 2 | Each |  | =E24*G24 |
| 7/1/2029-6/30/2030 | 3002 | Field/Turf Treatment | 12 | Months |  | =E25*G25 |
| 7/1/2029-6/30/2030 | 3003 | Termite Treatment | 1 | Each |  | =E26*G26 |
| 7/1/2029-6/30/2030 | 3004 | Wasp/Bee Treatment | 6 | Bimonthly |  | =E27*G27 |
| 7/1/2029-6/30/2030 | 3005 | Rodent/Small Animal Control | 4 | Each |  | =E28*G28 |
|  |  | Option Period Three Total / =SUM(H24:H28) |  | Option Period Three Total |  | =SUM(H24:H28) |
| 7/1/2030-6/30/2031 | 4001 | Ant/Roach Treatment | 2 | Each |  | =E30*G30 |
| 7/1/2030-6/30/2031 | 4002 | Field/Turf Treatment | 12 | Months |  | =E31*G31 |
| 7/1/2030-6/30/2031 | 4003 | Termite Treatment | 1 | Each |  | =E32*G32 |
| 7/1/2030-6/30/2031 | 4004 | Wasp/Bee Treatment | 6 | Bimonthly |  | =E33*G33 |
| 7/1/2030-6/30/2031 | 4005 | Rodent/Small Animal Control | 4 | Each |  | =E34*G34 |
|  |  | Option Period Four Total / =SUM(H30:H34) |  | Option Period Four Total |  | =SUM(H30:H34) |
| 7/1/2031-12/31/2031 | 5000 | Option to Extend Services | 6 | Months |  | =E36*G36 |
|  |  | Contract Total / =H11+H17+H23+H29+H35+H36 |  | Contract Total |  | =H11+H17+H23+H29+H35+H36 |

## Suggested Next Pricing Steps

1. Open the Excel workbook and verify the extracted CLINs against the original file.
2. Determine whether each CLIN is monthly, annual, one-time, or event-based.
3. Build labor assumptions for each CLIN.
4. Add material/supply assumptions.
5. Add travel, mobilization, insurance, compliance, and contingency.
6. Compare proposed pricing to historical awards once USAspending intelligence is added.
