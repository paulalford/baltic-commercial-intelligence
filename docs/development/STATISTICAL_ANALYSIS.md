# Milestone 11 statistical analysis

This stage publishes precomputed, observational statistical results from the four governed historical publications. It uses source batch `8e33878d4c2719cc` and the configured `2026-09-30` cutoff. The stage does not estimate causal effects or use operational-extension rows. No Milestone 11 implementation plan was found; the user request and SPEC-12 govern this implementation.

## Inputs and output

Run with `python -m baltic_sales_intelligence.pipeline --config config --stage statistics --dry-run` and then omit `--dry-run` to publish. Use `--force` only to atomically replace an existing statistical publication. `--input` may point to a data root with `database/`, `gold/`, `customer/`, and `product/`; `--output` may override `data/statistics/`.

The stage validates each manifest and database SHA-256; expected schema, keys, rows, source batch and lineage; reporting windows; and the full-population five-measure finance totals across Warehouse, Gold, Customer and Product. It attaches all four databases read-only. Temporary narrow samples are created in the new database, checked for duplicate grains and cutoff leakage, then omitted from the final publication. A failed contract or estimate leaves the prior publication intact. An inferentially insufficient result is published with an explicit status.

`data/statistics/statistical_analysis.duckdb` contains `study_catalogue`, `study_estimate`, `study_diagnostic`, `study_sensitivity`, `estonia_gp_bridge`, and `basket_pair_result`. The adjacent `statistical_analysis_manifest.json` records object schemas and row counts, database SHA-256, all four input manifest and database hashes, method seed, resampling settings, package versions, study statuses and validation results. The stage uses deterministic sorting and seed `12026`. `study_estimate` contains typed numeric estimates, intervals and p/q values; unavailable values are SQL NULL. Monetary bridge values are `DECIMAL(18,2)`.

## Prespecified studies

| Study | Population and unit | Estimator and contrast | Main limitation |
| --- | --- | --- | --- |
| STAT-01 | Positive-net-revenue customer-month-category-segment-posting-market cells, 2022-01..2026-09 | Revenue-weighted OLS of realised gross margin on **prior** cumulative account discount, with category, segment, market, month and tenure controls; customer-clustered SE. Effect is margin percentage points for a 10 percentage point higher prior discount. | No causal discount effect; customers lacking prior list value and nonpositive-net cells are excluded. Current-cell discount is descriptive only. |
| STAT-02 | Posted market-month finance; EE fulfilled SKU-period finance; prior 2024-10..2025-09 and current 2025-10..2026-09 | EE gross-margin percentage-point change minus pooled LT/LV change, with paired circular 3-month block bootstrap across years and markets. Separate exact-cent matched-SKU price/mix/cost GP bridge includes new/exited products, return/geography timing and rounding/unallocated residual. | Two annual periods and 12 paired month positions limit inference. Bridge components describe an identity, not causes. |
| STAT-03 | Original fulfilled sale lines and accepted return decision events | **Not estimable** in production. Fixture-only helper counts eligible fulfilled units and capped partial accepted return units through the cutoff under an explicitly supplied maturity duration. | No approved maturity duration; no production rate, CI or p-value is published. Wilson intervals would require defensible independent Bernoulli units, which are not assumed. |
| STAT-04 | First-purchase customer × annual landmark with complete next 12-month horizon | Customer-clustered binomial GLM of any next-12-month fulfilled order, adjusting for prior-12-month order frequency, tenure, home market and landmark year. Pooled-covariate standardised segment rates and percentage-point contrasts; covariance-draw 95% intervals and BH within contrasts. | Incomplete outcome horizons are censored, sparse segments are withheld, and unmeasured segment selection remains. Only prior-window features enter the model. |
| STAT-05 | Distinct subcategory membership per fulfilled order; globally eligible pairs from Product rules | Joint/marginal probabilities, support, directional confidence, lift versus independent-popularity expectation; customer-cluster bootstrap 95% intervals and log-lift p-values, BH across eligible pairs. | Pair eligibility and sparse customer clusters limit generalisation. Lift does not measure sales caused by a cross-sell intervention. |

All five catalogue rows contain the business question, hypotheses, population, grain, periods, source datasets, metric, method, assumptions, limitations, evidence classification, status, sample and cluster counts where available, missingness where applicable, and management interpretation. Diagnostics and sensitivity outputs include collinearity rank, condition number, heteroskedasticity check, residual/influence summaries, largest-customer removal, retention predicted-probability range, bootstrap replicate counts and bridge reconciliation as applicable. Each study can instead report `insufficient_evidence` or `not_estimable` with a reason; no numeric conclusion is manufactured.

## Independent publication checks

Run after publication from the repository root:

```bash
sha256sum data/statistics/statistical_analysis.duckdb
python - <<'PY'
import json, pathlib, hashlib, duckdb
p = pathlib.Path('data/statistics')
m = json.loads((p/'statistical_analysis_manifest.json').read_text())
with (p/m['database']['path']).open('rb') as f:
    assert hashlib.file_digest(f, 'sha256').hexdigest() == m['database']['sha256']
with duckdb.connect(str(p/m['database']['path']), read_only=True) as c:
    print(c.execute('SELECT study_id,status,n_observations,n_clusters,management_interpretation FROM study_catalogue ORDER BY 1').fetchall())
    print(c.execute('SELECT estimate_id,estimate,ci_low,ci_high,p_value,q_value,status FROM study_estimate ORDER BY 1').fetchall())
    print(c.execute('SELECT component,amount_eur FROM estonia_gp_bridge ORDER BY 1').fetchall())
    print(c.execute("SELECT (SELECT sum(amount_eur) FROM estonia_gp_bridge) = (SELECT amount_eur FROM study_sensitivity WHERE study_id='STAT-02' AND check_name='bridge_reconciliation')").fetchone())
    print(c.execute("SELECT estimate,ci_low,ci_high FROM study_estimate WHERE study_id='STAT-03'").fetchall())
PY
```

The EE bridge sum should equal the `STAT-02` `bridge_reconciliation` sensitivity amount; its residual is explicit. Verify `STAT-03` has `not_estimable` and NULL numeric values. Run full regression tests and full-scale processing outside this implementation session.
