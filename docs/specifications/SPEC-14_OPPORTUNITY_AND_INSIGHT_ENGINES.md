# SPEC-14 — Opportunity and Insight Engines

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose and evidence boundary
Produce explainable management-level, deterministic recommendations from ordinary analytical facts. They are hypotheses and quantified opportunity *estimates*, not guaranteed sales, predicted transactions or fabricated client outcomes.

## Opportunity catalogue
| Code | Candidate | Evidence | Conservative estimate |
|---|---|---|---|
| O01 | Reactivate lapsed customers | past frequency/GP, recency vs peer cadence | scenario range from historical peer repeat contribution; discount for uncertainty |
| O02 | Cross-sell category | pair lift with support and account coverage | peer attach rate × eligible accounts × historical contribution; cap |
| O03 | Margin recovery | comparable items with abnormal discount or cost squeeze | eligible volume × plausible recoverable cents, with uptake sensitivity |
| O04 | Category penetration | similar customers purchase missing family | segment-conditioned uptake range |
| O05 | Growth accounts | credible positive trajectory and capacity | peer growth distribution, not extrapolation guarantee |
| O06 | Returns intervention | persistently high defect-related returns | avoided refund/cost range given plausible improvement |

## Ranking and deterministic rules
- Score each candidate with transparent components (0–100 each): `impact` 40%, `confidence` 30%, `actionability` 20%, `urgency` 10%; weights fixed in approved config, tie-break by code/entity stable ID. Confidence accounts for count, tenure, sensitivity and measurement quality; opportunities below minimum evidence are suppressed or tagged investigative.
- Output `opportunity_id`, `as_of_date`, `business_entity`, `problem_or_potential`, `evidence_metric_ids`, `baseline`, `estimated_impact_low/base/high`, `assumptions`, `score`, `limitation`, `recommended_next_step`, `owner_role`, `method_version`. Impact examples exclude invented certainty and avoid sums across overlapping opportunities without deduplication.
- Insight rule family: margin decline despite revenue growth; return quality deterioration; concentration threshold crossed; cohort retention decay; excessive discounts after adjustment; regional market vs channel mix; forecast deviation with uncertainty. Every rule defines lookback, denominator, minimum support, severity, independent evidence and exact trigger.
- Avoid echoing planted story IDs or using hidden generators as rule features. Interpret mechanisms as possible explanations, not proven facts.

## Validation
- Golden-fixture tests for every rule, stable ordering and explanation text, near-threshold cases, no-data fallback and suppressed unsupported candidates.
- Reconcile opportunity ranking with unit contribution margin; calculate overlap IDs and flag if several proposals rely on the same revenue base.
- Business owners can trace each claim to SQL mart, source measure, date, assumptions and confidence; no 'AI says' unsupported prose.

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
