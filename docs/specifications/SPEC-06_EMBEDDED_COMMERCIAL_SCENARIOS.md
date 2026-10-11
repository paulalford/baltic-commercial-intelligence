# SPEC-06 — Embedded Commercial Scenarios

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose and epistemic boundaries
Design fifteen evidence-generating phenomena in synthetic truth to challenge downstream analysts. A story is **not** automatically an analytical finding. Private control parameters and scenario labels are accessible only to generation validation, never analytic models, marts or Streamlit.

## Story catalogue
| ID | Intended commercial phenomenon | Observable evidence | Confounding / analysis required |
|---|---|---|---|
| S01 | Large LT account gradually ceases buying | recency, frequency, net-revenue trend | seasonality and normal buying cycles |
| S02 | EE revenue grows while margin falls | net revenue and gross profit by time | price/mix versus cost and discount |
| S03 | One territory discounts excessively | posted discount rate by comparable mix | customer/segment mix |
| S04 | High-revenue salesperson low profit | sales and GP by credited rep | account size and returns |
| S05 | Modest-revenue rep has high retention and margin | repeated buying and GP | portfolio tenure / selection |
| S06 | Category sells more but returns rise | accepted returned units/eligible cohorts | right-censoring and exposure |
| S07 | LV acquisition strengthens | genuine first-acquired clients and first purchases | sector and time variation |
| S08 | Category-specific seasonality | category-month patterns over years | growth and promotions |
| S09 | Cross-sell affinity between two categories | co-occurrence and lift | popularity and account segments |
| S10 | Market customer concentration | top 5/10 share, HHI | enterprise market footprint |
| S11 | Price hike boosts unit GP, reduces volume | comparable product price, units, costs | demand change and other confounders |
| S12 | Overlooked segment has strong lifetime GP | historical cohort net GP | time at risk and exposure |
| S13 | Category revenue healthy but GP declines from cost | historical cost vs selling price | mix and discount confounding |
| S14 | Temporary demand spike | robust residual anomaly | holiday/promo/seasonality |
| S15 | Surface growth masking weak commercial health | bridge, cohorts or mix changes | competing explanations |

## Mechanism contract
- Embed smooth, stochastic (not deterministic row label) changes as time-varying purchase intensity, price/cost trajectory, refund propensity, basket association and segment acquisition-rate modifiers.
- Limit intervention amplitude to commercially plausible levels, keep overlap among stories possible but not perfect, and quantify baseline/background variation. Specify numeric mechanism parameters in a private **version-controlled generator config**; seeds make the simulation reproducible, not trivially solvable.
- Scenarios must be independently auditable via known exposure and private truth tests, but analytics must use ordinary customers, products, orders, costs, returns and dates only. Private truth files live outside data destined for reporting.
- A false first impression must be genuinely possible: S02 or S15 can grow in revenue while profitability or retention deteriorates.

## Truth registry (restricted to generator QA)
Fields `story_id`, `mechanism`, `target_entity_private_id`, `start_date`, `end_date`, `target_effect`, `baseline_comparator`, `generation_seed_component`, `validation_tolerance`, `observed_effect`. Do not copy any of these into customer/product public data or mart output. Entity IDs alone must not encode targets.

## Independent scenario checks
- Scenario-specific held-out SQL/Python validation measures the intended pattern from **observable columns**, uses thresholds set prior to generation and checks that unrelated groups retain enough overlapping variation.
- For S14, check detector flags true spike period and report false positives. For S09, measure category-pair lift with minimum count support, not just joint frequency. For S06, compare return cohorts mature enough for a fair comparison.
- Prohibit rule `if story_id == ... then emit insight`. Analytical engines must calculate differences, uncertainty and alternative explanations.
- Tests assert no private-control columns in downstream source whitelists, query lineage or feature tables.

## Acceptance
All 15 scenarios have an independently measurable test with sufficient exposure; no scenario is hard-coded into dashboard conclusions. Tests have predeclared tolerance and produce pass/fail plus observed effect, not a hand-written declaration of success.

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
