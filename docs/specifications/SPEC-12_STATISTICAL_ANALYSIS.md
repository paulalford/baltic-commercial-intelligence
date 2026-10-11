# SPEC-12 — Statistical Analysis

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose and evidence standards
Use statistics to test business hypotheses, estimate uncertainty and identify robust **associations**, not unsupported causal effects. Every output states population, sample size, independence limitations, period, controls, confidence interval and alternative explanations.

## Mandatory study catalogue
1. Do high-discount accounts actually have lower realised margin *after controlling for category, segment, market, tenure and period*?
2. Did EE margin deterioration exceed comparable market variation, and how much is explained descriptively by price/mix/cost?
3. Is the worsening return rate in one category beyond sampling noise when measured on matured fulfilled-order cohorts?
4. Are retention differences among segments robust after standardising customer tenure and purchasing frequency?
5. Is cross-sell association strong enough to merit commercial investigation once category popularity is accounted for?

## Methods, assumptions, and outputs
- Descriptives: distribution quantiles, robust location/dispersion, segment-standardised rates, missingness counts, n and uncertainty.
- Inference: bootstrap percentile/BCa 95% interval with seeded resampling for skewed revenue/margin and cluster-by-customer sampling; Wilson binomial intervals for proportions when independent Bernoulli assumptions reasonable; aggregate/cluster if repeated within customer.
- Hypothesis: predeclared null/alternative, unit of analysis, effect size (difference in euro GP, percentage-point return rate, risk ratio, standardised mean difference), interval and p-value; avoid p-value-only decisions. Report multiple-testing policy such as Benjamini-Hochberg when exploring many segments.
- Regression: for gross profit per line use interpretable GLM/OLS with robust/cluster SE and residual diagnostics, with possible product/category/time fixed effects; for return probability use binomial/logistic with class balance, confidence intervals and calibration; select target/features as-of event time, not future outcomes.
- Avoid leakage from hidden scenario labels, post-fulfilment returns into predictors trained at order date, contemporaneous target values predicting realised outputs, and duplicate line/customer folds.
- Diagnose collinearity, heteroskedasticity, nonlinearity, influential points and sparse strata; check coefficient stability by time and country, out-of-sample performance where predictive.
- Claims must be explicitly descriptive, associative, predictive or causal. **No causal claims** absent an identified design/assumption (randomised intervention, credible natural experiment, etc.).

## Reproducibility and acceptance
- Analysis catalogue includes study ID, question, data grain, exclusions, predeclared contrast, method, assumptions, sample count, metric unit, estimates, CI, sensitivity result, limitations and management interpretation.
- Fixed resampling seed and time/cluster-aware splits; tests on synthetic known-effect and null-effect fixtures, missingness and small sample edge cases.
- Output can say `insufficient_evidence`, `not_estimable`, or `association_only`; no spurious precision for tiny samples.

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
