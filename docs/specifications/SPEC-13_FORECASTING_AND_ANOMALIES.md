# SPEC-13 — Forecasting and Anomalies

**Status:** Design-approved recommendation; documentation consistency gate passed  
**Version:** 1.0 · 2026-10-11  
**Scope:** Fictional Baltic Trade Supplies UAB; synthetic data only

## Purpose and horizon
Forecast monthly company and market **net revenue** with transparent baselines, intervals and walk-forward evaluation; identify unusual commercial behaviour with explainable evidence. Business truth must not leak from generator scenarios.

## Forecasting contract
- Input `sales_monthly` full closed months only; from 2022-01 through 2026-09 gives 57 months. Forecast at horizons 1, 3 and 6 complete months, evaluating only origins with full forward actual windows. Exclude partial and after-cutoff months.
- Baselines: seasonal naive `ŷ_t = y_(t−12)` when 12 history months, trailing-3 mean for early fit; statistical candidates ETS/Holt-Winters with multiplicative components only when positive and appropriate, and seasonal SARIMA where enough data. Prefer simpler if holdout no better.
- Walk-forward expanding window, no random train/test split. Minimum 24 training months, evaluation origins by month and model/refit policy declared. Fit transformations and parameter selection inside training folds. Publish WAPE (undefined if total actual zero), MAE, RMSE, bias, sMAPE with caveats and optional MASE; compare to naive, report origin-horizon error distribution.
- Interval: calibrated residual/bootstrapped 80% and 95% forecast intervals using errors from historical rolling origins; evaluate empirical coverage and width. Wider intervals at longer horizons unless evidence to contrary. Handle negative model forecasts by documented truncation at 0 for positive revenue case, but net revenue can be negative in principle; disclose policy instead of hiding.
- Country/company coherence: dashboard forecasts use bottom-up reconciled country forecasts so they sum to company. Evaluation may show independent company estimates with a disclosed coherence delta. Historical actuals use customer-home country at each sale/return posting date; target unallocated buckets are planning facts and never enter forecast actuals.

## Anomaly methodology
- Detect robust seasonal residual anomalies on daily/weekly or monthly net revenue, return rate and margin; candidates: STL decomposition + robust MAD z-score, exponentially weighted control limits and explicit comparison to last-year equivalent period.
- Minimum valid seasonal history and exposure thresholds; for return-rate anomaly require mature eligible cohorts and binomial uncertainty; for sales spike require at least 2–3 full seasonal cycles where feasible. No hidden scenario IDs as features.
- Severity (`info`, `investigate`, `high`) from magnitude, persistence, value at risk and evidence quality, not generator truth. Emit date, metric, baseline, deviation, uncertainty, affected entities and recommended investigation; never claim fraud without evidence.

## Validation and acceptance
- Walk-forward origin/horizon table demonstrates baseline comparison with reproducible metrics; interval coverage stated alongside width.
- Known private spike S14 is detectable through independently calculated residuals; report false-positive rate on non-scenario periods, including seasonal holidays.
- Do not call `forecast`, `risk`, or `anomaly` result certain; attach method version, observation count and data cutoff.

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
