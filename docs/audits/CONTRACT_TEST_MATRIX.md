# Contract Test Matrix

**Audit date:** 2026-10-11  
**Status:** Test definitions complete; no implementation tests executed  
**Purpose:** Freeze inputs, expected outputs and failure conditions before implementation. Paths below are planned automation locations, not existing test evidence.

## Conventions

All financial fixtures use EUR decimal arithmetic and round-half-even at each posted line/event amount. Dates use half-open effective intervals. Unless stated otherwise, the reporting cutoff is 2026-09-30, VAT treatment is `sim_standard` at 0.200000, and customer-home country is the finance geography role.

## Financial and target reconciliation

| Test ID | Requirement | Input fixture | Expected numeric output | Failure condition | Owner | Planned automation |
|---|---|---|---|---|---|---|
| CT-FIN-001 | Fulfilment posting and cent rounding | Fulfilled line: q=3, list=10.0050, discount=0.100000, cost=6.3333, VAT=0.200000 | list=30.02; discount=3.00; net sales=27.02; COGS=19.00; GP=8.02; VAT=5.40; invoice=32.42 | Any different cent, float tolerance used, or posting date not fulfilment date | SPEC-04 §§2–3 | `tests/contracts/test_financial_postings.py` |
| CT-FIN-002 | Cancelled/placed orders have no recognised money | Same line snapshots as CT-FIN-001 on one `placed` and one `cancelled` order | Each has net sales=0.00, COGS=0.00, VAT=0.00; order-intake count=1 each; cancellation count=1 only | Any recognised amount or cancelled row counted fulfilled | SPEC-03 §6.1; SPEC-04 §4 | `tests/contracts/test_order_status.py` |
| CT-FIN-003 | Accepted return reverses only accepted economics | CT-FIN-001 line; accepted qty=1; refund unit=9.0045; disposition=restockable; original VAT rate=.20 | refund=9.00; recovered COGS=6.33; VAT reversal=1.80; resulting net revenue=18.02; net COGS=12.67; net GP=5.35 | Request quantity used, current cost/rate used, or signs stored negative in return fact | SPEC-04 §5 | `tests/contracts/test_returns.py` |
| CT-FIN-004 | Written-off return recovers no COGS | Same as CT-FIN-003 but disposition=written_off | refund=9.00; recovered COGS=0.00; VAT reversal=1.80; resulting net GP=-0.98 | Any recovered COGS or refund omitted | SPEC-04 §5 | `tests/contracts/test_returns.py` |
| CT-FIN-005 | Requested and rejected events do not post or consume cap | Sold qty=3; requested qty=3/requested; requested qty=2/rejected; accepted qty=3 later | First two events all postings=0.00; final accepted event allowed; cumulative accepted=3 | Nonaccepted event posts money or prevents final acceptance | SPEC-03 §6.3; SPEC-04 §5 | `tests/contracts/test_returns.py` |
| CT-FIN-006 | Cumulative accepted cap | Sold qty=3; accepted events qty=1 then qty=2 then qty=1, ordered by decision date/ID | First two valid; cumulative=3; third event quarantined | Cumulative accepted=4 reaches silver or a request is counted in cap | SPEC-07 DQ12 | `tests/contracts/test_return_caps.py` |
| CT-FIN-007 | Cutoff and cross-period recognition | Sale fulfilled 2026-09-30 for 100.00/COGS 60.00; accepted return decision 2026-10-02 for refund 25.00/recovery 15.00 | At 2026-09-30: revenue=100, COGS=60, GP=40. At 2026-10-02 extended view: revenue=75, COGS=45, GP=30. Default dashboard remains first result | Return projected backward or extension changes default cutoff | SPEC-04 §§5,9 | `tests/contracts/test_cutoff.py` |
| CT-FIN-008 | VAT treatment is explicit, not inferred | Two otherwise identical fulfilled lines net=10.00: one `sim_standard`, one `sim_zero`; cross-border shipping on both | VAT=2.00 and 0.00; combined revenue=20.00; combined VAT=2.00 | Shipping/home geography changes treatment or VAT enters revenue | SPEC-04 §6 | `tests/contracts/test_vat.py` |
| CT-FIN-009 | Freight allocation preserves order cents | Two line net sales 27.02 and 12.98; outbound freight=5.00 | Allocations=3.38 and 1.62; sum=5.00 | Sum differs or 5.00 duplicated to each line | SPEC-04 §6 | `tests/contracts/test_freight.py` |
| CT-TGT-001 | Target leaf aggregation with unallocated country bucket | LT salesperson=100, LT unallocated=20, LV salesperson=80 | LT=120; LV=80; company=200; leaf count=3 | Unallocated added again, country/company fact row stored, or company≠200 | SPEC-03 §6.4; SPEC-04 §7 | `tests/contracts/test_targets.py` |
| CT-TGT-002 | Actual and target attainment at each grain | Actuals: LT credited=90, LT uncredited=25, LV credited=70; targets from CT-TGT-001 | LT actual=115, attainment=0.958333; LV=.875000; company actual=185, attainment=.925000; LT salesperson=.900000 | Uncredited excluded from country/company, assigned to a rep, or compared as unallocated-bucket attainment | SPEC-04 §7 | `tests/contracts/test_targets.py` |
| CT-TGT-003 | Zero target denominator | Actual=25.00, target=0.00 | attainment=NULL and reason=`zero_target` | Infinity, zero or 100% returned | SPEC-04 FIN-K11 | `tests/contracts/test_kpi_denominators.py` |

## Temporal, geographic and relationship integrity

| Test ID | Requirement | Input fixture | Expected output | Failure condition | Owner | Planned automation |
|---|---|---|---|---|---|---|
| CT-TMP-001 | SCD2 half-open boundary | Customer versions: A `[2025-01-01,2025-07-01)`, B `[2025-07-01,NULL)`; events 2025-06-30 and 2025-07-01 | First joins A exactly once; second joins B exactly once | Zero/two joins or inclusive end used | SPEC-03 §5.1a; SPEC-09 | `tests/contracts/test_temporal_joins.py` |
| CT-TMP-002 | Finance SCD date differs from order-intake date | Order 2025-06-30, fulfilled 2025-07-01 using CT-TMP-001 | `customer_sk_order=A`; `customer_sk_fulfilment=B`; finance home attributes=B | One current/latest key reused for both | SPEC-04 §9; SPEC-09 | `tests/contracts/test_temporal_joins.py` |
| CT-TMP-003 | Return uses decision-date customer version | Accepted return requested 2025-06-30, decided 2025-07-01 | Return posting joins version B | Request/order/current date used instead | SPEC-04 §§5,9 | `tests/contracts/test_temporal_joins.py` |
| CT-TMP-004 | Employment end is exclusive | Employment `[2025-01-01,2025-07-01)`; fulfilled orders on 06-30 and 07-01 | Credit valid on 06-30; invalid on 07-01 | Termination date treated eligible | SPEC-03 §§5.2,6.1 | `tests/contracts/test_sales_credit.py` |
| CT-TMP-005 | Ownership does not impute credit | Customer owner SP-01 effective; web fulfilled order has NULL credited salesperson | Owner=SP-01 in ownership view; credited revenue=uncredited; total unchanged | Credit silently set to SP-01 | SPEC-03 §5.3; SPEC-04 §4 | `tests/contracts/test_sales_credit.py` |
| CT-GEO-001 | Frozen country-region-city crosswalk | All 14 SPEC-03 city rows plus mutation `EE-NARVA → EE-37` | 14 canonical rows pass; mutation fails | Wrong parent accepted or free-text city added | SPEC-03 §§4.1–4.3 | `tests/contracts/test_geography.py` |
| CT-GEO-002 | Geographic roles remain independent | Customer home LT-Vilnius; ship-to LV-Riga; credited rep primary EE | Home, destination and territory outputs each retain their own country; each view total=one sale, never combined total=three | Roles overwritten or summed together | SPEC-02 §5; SPEC-15 | `tests/contracts/test_geography_roles.py` |
| CT-SUP-001 | Exactly one effective primary supplier | Product P: primary S1 `[2024-01-01,2025-01-01)`, S2 `[2025-01-01,NULL)`, alternative S3 open | One primary on each tested date; alternative preserved; sale supplier remains unknown | Gap/overlap in primary or sale attributed to S3 without provenance | SPEC-03 §5.5 | `tests/contracts/test_supplier_history.py` |

## Analytics, leakage and reproducibility

| Test ID | Requirement | Input fixture | Expected numeric/output result | Failure condition | Owner | Planned automation |
|---|---|---|---|---|---|---|
| CT-ANA-001 | PVM and return bridge reconciliation | A: q0=10,p0=5,q1=12,p1=6; B: q0=20,p0=2,q1=15,p1=2.5 | Prior matched=90.00; current matched=109.50; pure volume=-9.00; mix=9.00; price=19.50; matched change=19.50; residual=0.00 | Returns embedded in price/volume or bridge does not reconcile | SPEC-11 | `tests/contracts/test_pvm.py` |
| CT-ANA-002 | Basket denominator is orders | Orders: {A,B}, {A}, {B}, {C}; duplicate A line in first order | support(A,B)=.25; confidence(A→B)=.50; confidence(B→A)=.50; lift=1.00 | Line rows used as baskets or duplicate SKU counted twice | SPEC-11 | `tests/contracts/test_baskets.py` |
| CT-ANA-003 | Cohort maturity/right censoring | Cohort size=10; only 8 observable at month 12; 4 of those 8 retained | mature denominator=8; retention=.500000; two censored excluded | Denominator=10 or future data used | SPEC-10; SPEC-12 | `tests/contracts/test_cohorts.py` |
| CT-ANA-004 | Null financial denominator | Net revenue=0.00, GP=-5.00 | GP%=NULL with reason=`nonpositive_revenue` | Divide-by-zero, infinity or 0% | SPEC-04 FIN-K06 | `tests/contracts/test_kpi_denominators.py` |
| CT-FCST-001 | Time-aware forecast evaluation | Monthly series with origin 2026-03, horizon=6 and actuals only through 2026-07 | Origin excluded from h=6 evaluation; no random split | Partial future window scored or future preprocessing leakage | SPEC-13 | `tests/contracts/test_forecast_windows.py` |
| CT-LEAK-001 | No private simulation labels | Inject columns `story_id`, `latent_churn_state`, `defect_id` into raw source | Schema whitelist rejects all three; zero such columns in silver/warehouse/features | Any column survives or drives an insight | SPEC-05–09, SPEC-16 | `tests/contracts/test_private_label_leakage.py` |
| CT-REP-001 | Deterministic canonical output | Run same seed/config twice; then change master seed | Same-run canonical content hashes equal; changed seed changes stochastic payload; reference taxonomy hash unchanged | Hash drift without documented environment variance or taxonomy changes with seed | SPEC-05; SPEC-16 | `tests/contracts/test_reproducibility.py` |
| CT-DOC-001 | Markdown and references structurally complete | All 16 specs, register, decisions and audit matrices | Balanced fences; tables have consistent column counts; all relative links/`SPEC-nn` targets resolve | Any structural error or missing spec | SPEC-16 G01 | `tests/docs/test_specifications.py` |

## Execution status

No test in this matrix was executed during the documentation remediation. The static verification commands actually run are recorded in the final audit report and specification register. A future implementation report must record command, environment, timestamp and result per automated test; the presence of this matrix is not a passing runtime result.
