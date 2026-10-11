# SPEC-15 — Streamlit Application

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose and experience
Deliver an executive-grade **read-only** analytical product for synthetic Baltic Trade Supplies UAB. Strong information hierarchy and reproducible numbers take precedence over flashy graphics. All pages disclose fictional/synthetic status clearly.

## Navigation and page contract
| Page | Primary business question | Required content |
|---|---|---|
| Executive | How healthy is performance? | net revenue, GP%, YoY, risk, top evidence-backed insights |
| Revenue & Growth | What changed? | comparable trend, growth bridge, PVM with reconciliation |
| Profitability | Where is value eroding? | GP, cost inflation, discounts, product/freight split |
| Customers | Who stays, grows or leaves? | cohorts, RFM, churn risk, CLV (historical) |
| Products | Which categories deserve attention? | ABC/XYZ, baskets, mix, returns |
| Sales Team | Who creates profitable growth? | credited performance, retention, discount, target performance |
| Geography | Where and on which basis? | home, ship-to, territory role-switch and separate totals |
| Returns | What is quality costing? | accepted rate, refund, cohort maturity, reasons |
| Forecasting | What's plausible next? | naive vs statistical, errors, interval and origin |
| Insights & Opportunities | What to investigate? | explainable ranked cards with evidence and assumptions |
| Data Quality | Can numbers be trusted? | batches, repair/quarantine, lineage and missingness |

## UX and interaction contract
- Universal context filter: as-of date (≤2026-09-30 for default reported results), country role, market, segment, category, channel and complete/partial period. Filter choices must not silently change metric definitions; show active filter chips and period comparability flags.
- Every KPI displays unit, sign, period basis (fulfilment vs return event), denominator where relevant, data freshness/batch and optional hover definition. Default currency EUR and `en-GB` formatting, ISO dates in exports.
- Where event-based return adjustments can make net revenue negative, display negative honestly and suppress invalid ratio.
- Responsive desktop layout; 11 pages as separate modules, common theme/navigation/filter utilities. Streamlit app **must not** reimplement SQL, churn definitions, predictions or generator rules inside page files.
- Plotly charts accessible with descriptive labels, useful annotation and restrained palette; tables allow CSV export with synthetic disclosure, clearly separated observed vs estimated values; annotate model uncertainty, insufficient data, and no-data gracefully.
- Query/cache key includes warehouse build ID, filter specification, method version and as-of date; stale data warns rather than silently mixing batches.

## Smoke tests and acceptance
- Headless app import and per-page query contract tests; navigate filters using small fixture warehouse, missing/empty dataset fallback, no generated business data in Git.
- Page-to-page headline totals exactly reconcile under identical filters and date bases; role-based geography prevents double counting.
- Synthetic disclaimer visible on home, exported analytic extracts and methodology page; no fake testimonials, clients or revenue guarantees.

## Governance, dependencies, and change control

**Project contract:** All identities and commercial events are synthetic. Analysis covers LT/LV/EE in EUR, reporting 2022-01-01 through 2026-09-30, without implying an actual client engagement. SPEC-01 and SPEC-02 define intent; SPEC-03 governs source grain and identities; SPEC-04 governs economics; SPEC-05/06 govern generated distributions and private truth; SPEC-07–09 govern transport and warehouse; SPEC-10–15 govern analytical outputs; SPEC-16 governs verification.

Any change to a grain, formula, inclusion criterion, as-of rule, enum, field name, or security boundary requires a documented decision and regeneration of dependent fixtures. Do not silently reinterpret an approved contract inside code.

## Failure handling and edge cases

- Null denominators, zero activity, and insufficient observation windows produce explicit `NULL`/not-estimable values and explanatory flags, never disguised zeros.
- Historical calculations exclude any observation after the stated as-of cutoff, even if the synthetic generator knows the future.
- Duplicate source records, orphan relationships, impossible dates and unreconciled amounts fail validation or enter a traceable quarantine; they are not silently removed.
- Outputs must state unit, currency, grain, period, filters, and whether results represent observations, estimates, or synthetic scenario validation.

## Approval and implementation gate

**Design recommendation:** Accepted under project-owner advance approval dated 2026-10-11. The documentation consistency audit passed; automated integrity, reconciliation and release tests remain unexecuted. **Implementation is not authorised merely by writing this document.**
