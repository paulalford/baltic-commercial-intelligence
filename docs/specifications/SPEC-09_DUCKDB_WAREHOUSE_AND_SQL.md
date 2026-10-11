# SPEC-09 — DuckDB Warehouse and SQL

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose
Demonstrate advanced, inspectable SQL and dimensional modelling while retaining source-lineage, historical semantics, and cent-level finance reconciliation.

## Warehouse and grains
| Table | Grain / key | Essential join discipline |
|---|---|---|
| `dim_date` | one date; `date_key` YYYYMMDD | calendar and ISO year distinct |
| `dim_customer` | one effective SCD2 version; surrogate `customer_sk` | business key customer_id, `[from,to)` nonoverlap |
| `dim_product` | one stable product | stable category/subcategory; sourcing bridge separate |
| `dim_salesperson` | one representative; surrogate optional | hire/termination eligibility |
| `dim_geography` | one city with region and country | role-playing home vs shipping |
| `dim_channel` | one approved channel | same values across all marts |
| `bridge_customer_owner` | one effective ownership interval | interval joins never row multiplication |
| `bridge_product_supplier` | one relationship interval | primary vs approved alternate; no source inference |
| `fact_order_line` | one operational order line | preserve placed/cancelled but recognised fields zero until fulfilled |
| `fact_return_event` | one requested/accepted/rejected return event | accepted quantity and decision-date postings only for accepted events; never cross-join for revenue sums |
| `fact_target_allocation` | one month, KPI, salesperson/unallocated country bucket | country/company totals derived only |

## Fact design details
- `fact_order_line` carries operational order/line IDs, customer business ID, `customer_sk_order` resolved at order date and `customer_sk_fulfilment` resolved at fulfilment date, channel, nullable credited rep, fixed shipping city, all milestone date keys, quantity, historical unit snapshots and the named SPEC-03 posted sale/VAT fields. Nonfulfilled recognised postings are zero. Finance geography uses the fulfilment SCD key; order-intake analysis uses the order key.
- Reporting recognised amounts are keyed to `fulfilled_date`; order-intake facts use `order_date` and are labelled distinctly. `fact_return_event` carries request/decision dates, requested/accepted quantities, status, disposition and named posted refund/recovered-COGS/VAT-reversal fields. It resolves `customer_sk_return` at accepted decision date for home-geography reporting. A canonical posting view `UNION ALL`s fulfilled sale and accepted return postings, with negative signs only in that view.
- Date join as-of query must restrict recognised posting dates ≤ cutoff; historical SCD2 resolved with `event_date >= valid_from AND (valid_to IS NULL OR event_date < valid_to)` and verify exactly one match for eligible customers.
- Denormalised lineage columns include source record ID, stage manifest, batch version, not private generator annotations.

## SQL engineering demonstration
- At least one tested `LAG/LEAD` cohort migration query, windowed rolling 12-month performance, `FILTER`, conditional aggregations, `GROUPING SETS` where correct, quantile/percentile, CTEs and joins with explicit grain controls; explain query plan for expensive stages.
- Use materialized marts `sales_daily`, `sales_monthly`, `customer_metrics`, `product_metrics`, `salesperson_metrics`, `regional_metrics`, `return_analysis`; publish exact metric IDs from SPEC-04, with filter columns, refreshed timestamp and schema version.
- Define period comparability flags for partial Q3 2026 vs prior full-year; avoid naive YoY mismatches. Store root tables in DuckDB, export gold Parquet for read-only consumption when useful.

## Warehouse tests
- No duplicate PKs, orphan FKs, overlapping SCD2 or ownership/supplier intervals; exactly one joined customer version per eligible event.
- Finance ledger reconciliation for every posting date/country/line exactly to posted cents, `credited + uncredited = total`, aggregate target leaves once, and enforce accepted-return cap. Inventory quantity and valuation are out of scope, so no inventory-balance claim is made.
- Fact order-line cardinality matches silver order lines, except explicitly documented rejection; returns never multiply base fact when joined.
- Published marts match independent SQL fixture expectations for returns across months and mixed home/ship-to countries.

## Acceptance
New DuckDB file built reproducibly from silver; SQL folder contains nontrivial readable SQL rather than all business logic hidden in Python; tests run without Streamlit.

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
