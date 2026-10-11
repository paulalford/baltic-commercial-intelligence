# Requirements Traceability Matrix

**Audit date:** 2026-10-11  
**Status:** Documentation trace complete; implementation evidence pending

This matrix maps governing business requirements to source fields, transformations, outputs and planned verification. `CT-*` references are definitions in `CONTRACT_TEST_MATRIX.md`, not executed tests.

| Requirement | Business requirement | Data fields / reference contract | Transformation / rule | Analytical output | Verification | Owner |
|---|---|---|---|---|---|---|
| SCOPE-01 | Fictional Baltic B2B case; no real-client claim | Dataset manifests; public disclosure metadata | Synthetic-only generation; no real identity ingestion | All pages/exports/README | SPEC-16 G12; disclosure review | SPEC-01, 02, 05, 15, 16 |
| SCOPE-02 | LT/LV/EE, EUR, 2022-01-01–2026-09-30 | `country_code`, currency, date dimension flags | Default cutoff filter; extension excluded | All finance/analytics marts | CT-FIN-007 | SPEC-01, 03, 04 |
| BIZ-01 | Valid home country-region-city | `countries`, `regions`, `cities`; customer SCD geography | FK plus frozen crosswalk; point-in-time join | Home-geography performance | CT-GEO-001, CT-TMP-001 | SPEC-02, 03, 09 |
| BIZ-02 | Segment, industry and size are distinct | `segment_code`, stable `industry_code`, SCD `company_size_band` | Independent/correlated generation without identity collapse | Customer profiles, cohorts | Distribution and distinctness tests | SPEC-02, 03, 05, 10 |
| BIZ-03 | Every order identifies customer/date/channel/status/geography | `orders` fields and FKs | Status/date validation | Order intake and fulfilled sales | CT-FIN-002, DATA-T08 | SPEC-02, 03 |
| BIZ-04 | Every line supports financial reconciliation | Quantity, unit snapshots, posted sale/VAT fields | SPEC-04 line posting formulas | Finance marts and KPIs | CT-FIN-001, CT-FIN-008 | SPEC-03, 04, 09 |
| BIZ-05 | Product catalogue and supplier relationships are analysable | Product/category fields; effective supplier bridge | Stable category; interval supplier joins | Product/supplier views | CT-SUP-001 | SPEC-02, 03, 11 |
| BIZ-06 | Product families differ materially | Category parameters; ordinary observed transactions | Seeded heterogeneous distributions | Product profitability, XYZ, returns | Distribution tolerance tests; scenario QA | SPEC-05, 06, 11 |
| BIZ-07 | Salespeople have plausible market, tenure and ownership | Salespeople, account assignments, nullable order credit | Half-open employment/ownership joins | Balanced salesperson scorecard | CT-TMP-004, CT-TMP-005 | SPEC-02–04, 11 |
| BIZ-08 | Channel mix and economics differ | `channel_code`, order and finance fields | Conditional but nondeterministic generator | Channel performance | Distribution tests; finance reconciliation | SPEC-05, 09, 11 |
| BIZ-09 | Returns reference valid fulfilled lines and valid time | Request/decision dates, quantities, status, return postings | Accepted-only cap and decision-date recognition | Refunds, return rate, quality cost | CT-FIN-003–007 | SPEC-03, 04, 07 |
| BIZ-10 | Targets roll up without double counting | Target leaf fields, allocation/country/metric | Sum leaves once; actuals at matching grain | Target attainment | CT-TGT-001–003 | SPEC-03, 04, 09, 11 |
| BIZ-11 | Support retention, profitability, concentration and execution | Conformed sale/return/customer/product/rep facts | Versioned SQL marts and methods | SPEC-10–14 outputs | CT-ANA-001–004 plus module fixtures | SPEC-09–14 |
| BIZ-12 | Analytics independent of generator truth | Explicit public allowlists; private registry only | Reject private columns at raw→silver boundary | All consumer analytics | CT-LEAK-001 | SPEC-05–09, 16 |
| BIZ-13 | Behaviour is non-uniform | Configured market/segment/category/channel effects | Seeded stochastic generation with tolerance reports | Meaningful diagnostic variation | Distribution-summary acceptance test | SPEC-05, 06 |
| BIZ-14 | Names and entities are fictional | Generated identity fields and manifest | No external client/person source ingestion | Public disclosure and data docs | Release provenance review | SPEC-02, 05, 16 |
| DATA-01 | Customer attributes preserve history | Customer SCD2 versions | Half-open, nonoverlap, complete coverage; role-date join | Migration, historical segment/home views | CT-TMP-001–003 | SPEC-03, 09, 10 |
| DATA-02 | Ownership and sales credit remain distinct | Ownership bridge; `credited_salesperson_id` | Independent joins; never impute credit | Owner portfolio vs credited scorecard | CT-TMP-005 | SPEC-03, 04, 09, 11 |
| DATA-03 | Supplier relationship history is safe | Product-supplier interval fields | One effective primary; alternatives separate; no sale-source inference | Sourcing reference analysis | CT-SUP-001 | SPEC-03, 09 |
| DATA-04 | Geographic roles remain separate | Customer-home SCD, ship-to city, rep primary geography | Role-playing dimensions, one role per reported total | Geography page role selector | CT-GEO-002 | SPEC-03, 09, 15 |
| FIN-01 | Revenue is fulfilment-based and cancellations post zero | Fulfilment/cancellation dates and posted line fields | Recognise on fulfilment; zero otherwise | FIN-K01, FIN-K03 | CT-FIN-001–002 | SPEC-04 |
| FIN-02 | Discounts and historical prices/costs are immutable | List price, discount rate, realised price, unit cost | Line cent posting; no current-catalogue rewrite | Discount %, GP, bridges | CT-FIN-001 | SPEC-03, 04, 11 |
| FIN-03 | Only accepted returns affect finance | Return lifecycle and posting fields | Decision-date accepted posting | FIN-K02–05, FIN-K08 | CT-FIN-003–007 | SPEC-04 |
| FIN-04 | VAT is explicit and excluded from KPIs | Treatment/rate and posted VAT fields | Explicit synthetic rate; original-rate reversal | Invoice/VAT reconciliation only | CT-FIN-008 | SPEC-03, 04 |
| FIN-05 | Freight is separate from gross profit | Order/return freight fields | Pro-rata line allocation; contribution after freight | FIN-K15 | CT-FIN-009 | SPEC-04, 09 |
| FIN-06 | KPI denominators are explicit | Posted measures and eligibility flags | Metric catalogue FIN-K01–K15 | Executive/product/customer/sales marts | CT-TGT-003, CT-ANA-004 | SPEC-04, 10–15 |
| GEN-01 | Generation is deterministic and reproducible | Seed, component IDs, config/hash manifest | Stable RNG streams, sorting, Decimal serialization | Rebuild manifest | CT-REP-001 | SPEC-05, 08, 16 |
| DQ-01 | Defects do not alter truth and cannot disappear | Raw lineage, DQ decision log, quarantine | Detect independently; count conservation | Data Quality page | Per-defect fixtures; G06 | SPEC-07, 08, 15, 16 |
| WH-01 | Warehouse preserves grain and avoids multiplication | Fact/dimension keys; bridges | SCD/interval joins and posting union | All marts | CT-TMP-001–003; CT-SUP-001 | SPEC-09 |
| CUST-01 | Lifecycle is observed, not hidden | Fulfilled orders through cutoff | 12-month active/lapse/reactivation rules | Customer lifecycle/RFM | Customer fixtures; CT-LEAK-001 | SPEC-10 |
| PROD-01 | Product and commercial bridges reconcile | Fulfilled units/prices, return postings | Matched PVM plus explicit new/exit/return/residual rows | Revenue/PVM bridge | CT-ANA-001 | SPEC-11 |
| STAT-01 | Statistical claims state uncertainty and avoid causality | Study catalogue and time-valid features | Cluster-aware inference, diagnostics, BH where exploratory | Statistical studies | Known/null-effect and leakage fixtures | SPEC-12 |
| FCST-01 | Forecasts beat or contextualise baselines without leakage | Complete monthly net revenue | Expanding-window 1/3/6 horizon evaluation | Forecast/error/interval tables | CT-FCST-001 | SPEC-13 |
| INS-01 | Opportunities are explainable, conservative and nonadditive | Evidence IDs, impact range, overlap IDs | Fixed score weights and evidence thresholds | Ranked opportunity cards | Golden rules and overlap tests | SPEC-14 |
| APP-01 | Application is read-only and reconciled | Versioned marts/build ID/filter context | Shared query/filter semantics; no metric reimplementation | Eleven Streamlit pages | Page contract/smoke tests; G10 | SPEC-15, 16 |
| REL-01 | Release requires all gates, not document presence | Test reports and manifests | G01–G12 blocking gate | Release report | CT-DOC-001 plus future runtime suite | SPEC-16 |

## Coverage statement

All BIZ-01 through BIZ-14 requirements and each material cross-spec contract have a field/interface, transformation owner, consumer and verification mechanism. This is design traceability; rows become implementation evidence only when the named automated checks exist and are executed under SPEC-16.
