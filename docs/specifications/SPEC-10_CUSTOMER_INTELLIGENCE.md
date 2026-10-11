# SPEC-10 — Customer Intelligence

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose, business questions and scope
Identify who grows, stays, defects, contributes profit, concentrates risk and offers credible reactivation or cross-sell potential. Outputs are descriptive or calibrated estimators, **not** representations of real customer outcomes.

## Definitions and methods
- **Acquisition cohort:** first fulfilled order month for buying-customer cohorts; separately report CRM acquisition_date for onboarding cohorts. Do not conflate these populations. Cohort age t counts months elapsed since first fulfilled month; retention denominator is customers in cohort with sufficient observation maturity. Exclude censored months from mature-month comparisons.
- **Active buyer:** customer with ≥1 fulfilled order in trailing 12 months as of cutoff. **New buyer:** first fulfilled order within current reporting period. **Lapsed:** formerly buying, no purchase trailing 12 months; **declining:** trailing 6-month order frequency materially below preceding 6 (define minimum history, e.g. 2 prior purchases); **reactivated:** purchase after preceding ≥12 months without fulfilled purchases. Status is inferred, never an imported churn label. Administrative account state shown independently.
- **RFM:** recency days since last fulfilled date at cutoff, frequency distinct fulfilled orders in trailing 12 months, monetary sum net revenue in trailing 12 months (clearly labelled posting-date vs original order cohort). Quantile score 1–5 for each using deterministic tie-break handling; new customers and negative monetary need explicit treatment; publish score/threshold metadata.
- **Customer concentration:** top-1/5/10 share and HHI use positive customer net-revenue balances only, with the sum of those positive balances as denominator; customers with nonpositive balances are reported separately. Geography uses customer-home country at each sale/return posting date. If the positive-balance denominator is zero, return undefined rather than pseudo-shares.
- **Migration:** SCD2 as-of home market/segment/size transitions; measure counts and value pre/post without inferring causality. Account ownership transfer cannot rewrite prior credited salesperson.
- **Historical CLV:** realised cumulative net gross profit to date, by customer since first sale, net of accepted returns observed by cutoff. Label `historical value`, not predictive lifetime value. Compare cohorts by tenure/exposure; predictive CLV is optional, never claimed without validation.
- **Attrition risk:** transparent score using recency vs customer historic cadence, frequency trajectory, amount trend, tenure, seasonality and return burden; calculated from pre-cutoff observed data only. Report score, top drivers, baseline/validation approach. A binary outcome for validation must be determined using a strictly future evaluation interval unavailable to features.

## SQL/Python interfaces
`mart_customer_month`, `mart_cohort_retention`, `mart_rfm`, `mart_customer_concentration`, `mart_customer_migration`, `mart_customer_risk`, `mart_customer_value`; each includes `as_of_date`, report country role, eligible-customer count and method version.

## Edge cases
New customers have inadequate tenure; very infrequent legitimate purchasing cycles; no purchases; returns posted after sale; zero/negative cumulative GP; moved regions; ties in quantiles; customers only acquired after reporting cutoff; right-censored churn labels.

## Acceptance
- Independent fixtures prove tenure-aware retention, lapsed/reactivated state, non-leaking RFM, SCD2 migration and concentration denominators.
- No claims of causally proven churn drivers; risk calibration/precision reported on time-separated holdout if predictive.
- Management interpretation highlights exposure limits and asks for follow-up investigation, not certain future outcomes.

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
