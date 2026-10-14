# Forecasting and anomaly publication (Milestone 12)

This stage implements SPEC-13 over the existing passed Warehouse, Gold and Product
publications. It reads source databases without write access and publishes one
`data/forecasting/forecasting_anomalies.duckdb` with a checksummed manifest. It does
not rebuild any upstream stage.

## Actuals and lineage

`sales_monthly` contains January 2022 through September 2026 (57 months) for LT,
LV, EE and COMPANY. Revenue and gross profit are posted EUR from Gold, assigned to
customer home country at posting. Every country/month exists; absent source months
are explicit zeroes with `source_month_present=false`. The stage verifies every
month's country sum against company Gold and independently against Warehouse
financial postings. It rejects post-cutoff, incomplete, duplicate, unapproved
geography, mismatched source lineage, manifest checksum and source schema. No
private S14 scenario label or defect registry is a feature or output field.

The return-rate metric is recorded as `not_estimable`: no mature-cohort duration
and eligible-unit denominator have been approved. Private S14 detection quality
is `pending_private_qa`; this implementation does not assert private-scenario
success.

## Forecast design

These numeric choices are implementation choices, not new business policy.

- Candidate forecasts: lag-12 seasonal naive, level ETS (at 24 months), additive
  trend plus additive 12-month season ETS (at 36), and fixed SARIMA
  `(0,1,1)(0,1,1,12)` (at 48). The statistical set is deliberately small. There
  is no search over SARIMA orders or multiplicative ETS. Failed fits, convergence
  warnings and unavailable histories have diagnostic rows. A failed preferred fit
  at the final origin falls back explicitly to the baseline and records why.
- The baseline uses the same month in the last observed year for each of the
  next 1 through 6 months. With fewer than 12 training months it uses the mean
  of the last three observations, fixed for all forecast steps. Published
  backtests begin at 24 months, so this fallback is a documented early-window
  rule rather than a backtest candidate.
- Expanding origins run monthly from the 24th observed month. Every model is
  refitted at every eligible origin. Evaluated horizons are 1, 3 and 6 months,
  only when the corresponding target actual exists. Model selection for an
  origin uses only candidate errors whose target is already observed then.
  A more complex candidate needs at least eight paired origins and at least a
  5% relative WAPE reduction versus the baseline. The simplest qualifying
  candidate wins; raw errors from different eligibility windows are not ranked
  against each other. Otherwise the baseline wins.
- `model_evaluation` reports origin count and period, WAPE, MAE, RMSE, mean
  signed error, sMAPE, interval coverage and mean interval width. WAPE is NULL
  when total absolute actual is zero; a zero/zero sMAPE term is zero. Overlapping
  horizons are retained as forecasts from distinct origins, without an
  independence claim.
- 80% and 95% bounds use the higher empirical quantile of absolute errors
  from prequential selected forecasts whose target month is at or before the
  new forecast origin. Eight such errors are required; otherwise the interval
  status is `insufficient_history` and bounds stay NULL. The 95% radius is at
  least the 80% radius. Bounds are symmetric and may be negative, since net
  revenue can be negative; actuals and estimates are never silently truncated.
- Country point forecasts are selected separately. The published COMPANY point
  forecast is the exact sum of the three country points. An independent company
  model is retained only as a comparator with a coherence delta. Company bounds
  use the historical joint bottom-up error, calibrated at the company level.
  They are not a sum of marginal country bounds.

## Prediction Interval Calibration — Observed Limitations

The nominal interval levels are 80% and 95% targets, not guarantees of achieved
coverage. Calibration uses historically available residuals under the
chronological, prequential model-selection policy described above. An independent
audit reconstructed all 1,500 backtest rows, all 460 selected/reconciled rows,
and all 12 future intervals without finding a calculation or temporal-leakage
defect.

| Scope and horizon | Nominal 80% coverage | Nominal 95% coverage |
| --- | ---: | ---: |
| Latvia, 1 month | 52.0% | 88.0% |
| Latvia, 3 months | 38.1% | 61.9% |
| Latvia, 6 months | 40.0% | 66.7% |
| COMPANY reconciled, 3 months | 57.1% | — |

Latvia's later calibrated forecasts have increasing errors and predominantly
positive residuals, indicating underprediction. This does not establish a
specific underlying commercial cause. Calibration starts with eight historical
residuals; small samples, changes in the selected forecasting policy, overlapping
forecast horizons, and nonstationary error distributions limit the reliability
of nominal coverage. Early ETS forecasts can legitimately use residuals from
the preceding baseline policy. Consumers must inspect the published
`model_evaluation` coverage fields and should not treat nominal intervals as
validated probability guarantees.

## Anomaly design

The implementation uses the median of prior same-calendar-month observations
as the seasonal expectation, followed by a robust score against earlier
seasonal residuals. Two prior same-season observations and 12 earlier residuals
are required. A zero median absolute deviation is reported as `zero_mad`; it
does not produce an infinite score or an anomaly. Scores use the scaled MAD
constant 1.4826, with absolute threshold 3.5. A revenue deviation also needs
at least EUR 1,000 exposure. A margin deviation needs at least three percentage
points and EUR 300 exposure (`absolute margin deviation × current revenue`).
Margin on nonpositive revenue is `invalid_denominator`.

Consecutive same-sign qualifying months form a persistent shift; others are
isolated spikes. The severity choices are `info`, `investigate`, `high`.
`investigate` requires a score of at least 5 with EUR 5,000 exposure, or three
consecutive qualifying months. `high` requires a score of at least 7, EUR 20,000
exposure, and at least 24 prior residuals. These are conservative implementation
thresholds, not an approved business action rule. Investigation text asks for
posting and mix review and makes no causal or misconduct claim.

## Independent execution

From the repository root in the configured virtual environment:

```bash
.venv/bin/python -m baltic_sales_intelligence.pipeline --config config --stage forecasting --dry-run
.venv/bin/python -m baltic_sales_intelligence.pipeline --config config --stage forecasting
```

Use `--force` only to replace an existing forecasting publication. Dry-run
computes and validates in a temporary directory but does not publish. The
publisher stages the database and manifest together, then atomically swaps the
directory, restoring the previous publication if the swap fails. The published
manifest lists source manifest/database hashes, batch, schemas, row counts,
methods, runtime versions and validations actually executed.

Read-only independent checks:

```sql
SELECT reporting_month,
       sum(net_revenue_eur) FILTER (WHERE scope_code <> 'COMPANY') AS countries,
       max(net_revenue_eur) FILTER (WHERE scope_code = 'COMPANY') AS company
FROM sales_monthly GROUP BY 1 ORDER BY 1;

SELECT horizon_months,
       sum(predicted_eur) FILTER (WHERE scope_code <> 'COMPANY') AS countries,
       max(predicted_eur) FILTER (WHERE scope_code = 'COMPANY') AS company
FROM future_forecast GROUP BY 1 ORDER BY 1;

SELECT scope_code,horizon_months,model,n_origins,wape,mae_eur,rmse_eur,
       coverage80,coverage95,mean_width80_eur,mean_width95_eur
FROM model_evaluation ORDER BY 1,2,3;

SELECT metric,status,reason FROM anomaly_metric_status ORDER BY metric;
SELECT severity,count(*) FROM commercial_anomaly GROUP BY 1 ORDER BY 1;
```

The fixed SARIMA fits dominate compute time; this stage has not been run at
production scale inside Codex. A full independent run must inspect convergence
diagnostics, interval coverage by scope and horizon, anomaly counts, and private
S14 QA before treating detection performance as established.
