# Decision Register — v1.0

**Approved by project owner in advance (2026-10-11):** Recommended model decisions, subject to testing and cross-contract audit.

| ID | Decision | Affected spec | Status |
|---|---|---|---|
| D01 | Effective-dated customer ownership; separate nullable salesperson credit | 03/04/09 | Approved |
| D02 | Separate home, ship-to, sales-territory geographic roles | 03/09/15 | Approved |
| D03 | Preserve transaction prices/costs; multiple partial returns capped | 03/04 | Approved |
| D04 | Canonical monthly salesperson targets, explicit unallocated country bucket | 03/04/09 | Approved |
| D05 | Primary + optional alternate effective-dated suppliers, no procurement system | 03/05 | Approved |
| D06 | Admin customer status separate from observed lifecycle | 03/10 | Approved |
| D07 | SCD2 customer segment, size and home geography; stable product categories | 03/09 | Approved |
| D08 | Date-only events, half-open validity intervals | 03/09 | Approved |
| D09 | Order states placed/fulfilled/cancelled; separate transition dates | 03/04 | Approved |
| D10 | Fixed-point money, integer qty, deterministic readable IDs | 03/04/05 | Approved |
| D11 | Date dimension may extend beyond reporting period, as-of cutoff remains fixed | 03/05/09 | Approved |
| D12 | Distinct operational versus DuckDB dimensional contracts | 03/08/09 | Approved |
| D13 | Financial posted line rounding, ex-VAT KPI basis, fulfilment/accepted-return posting | 04 | Advance-approved recommendation |
| D14 | Customer lifecycle lookback 12 months, transparent RFM | 10 | Advance-approved recommendation |
| D15 | PVM matched-SKU bridge with distinct additions/exits and return timing | 11 | Advance-approved recommendation |
| D16 | Baseline-first time-aware forecasting, explainable anomalies | 13 | Advance-approved recommendation |
| D17 | Weighted explainable opportunity score with overlap warnings | 14 | Advance-approved recommendation |
| D18 | Return lifecycle uses requested/accepted/rejected; requested quantity is distinct from accepted quantity; only accepted decision-date postings affect finance | 03/04/07/09 | Formalised from D03/D13 in readiness audit |
| D19 | Discount rate is the sole launch discount input; immutable posted sale/COGS/VAT fields are authoritative after line-level half-even rounding | 03/04/09 | Formalised from D03/D10/D13 in readiness audit |
| D20 | Country and company targets derive only from salesperson and unallocated-country leaf allocations; unallocated is included once in its country | 03/04/09 | Formalised from D04 in readiness audit |
| D21 | Finance home geography resolves customer SCD at each posting date; order intake, shipping destination and territory remain separate roles | 03/04/09/15 | Formalised from D02/D07/D13 in readiness audit |
| D22 | Geography launch catalogue uses sourced LT/EE counties and LV 2024 statistical/planning regions; LV reporting regions are not administrative claims | 03/05 | Formalised in readiness audit; external evidence recorded |
| D23 | Launch VAT rates are explicitly synthetic (`sim_standard` 20%, `sim_zero` 0%) and never inferred from geography | 03/04/05 | Formalised from D13 in readiness audit |
| D24 | Employment and relationship end dates are exclusive; fulfilled-order credit is checked on fulfilment date; target eligibility uses first eligible day of month | 03/04 | Formalised from D01/D08/D13 in readiness audit |
| D25 | Operational extension ends 2026-12-31 while the default historical cutoff remains 2026-09-30; later events are never projected backward | 03/04/05/09 | Formalised from D11/D13 in readiness audit |

## Implementation-readiness audit closure

- SPEC-03 and SPEC-04 now share identical posted line fields, accepted-return lifecycle, VAT snapshots, freight separation and target leaf aggregation.
- The geographic crosswalk and controlled enumerations are frozen in SPEC-03; evidence requirements govern future additions.
- Generator reference counts, explicit baseline tolerances and cohort censoring rules are documented in SPEC-05, SPEC-10 and SPEC-13.
- Source unit prices and costs use `DECIMAL(18,4)`; posting intermediates and cent rounding are fixed in SPEC-04.
- Numeric and temporal fixtures are frozen in `docs/audits/CONTRACT_TEST_MATRIX.md`; they have not yet been automated or executed.

These closures establish documentation contracts only. They do not authorise implementation or represent passing runtime tests.
