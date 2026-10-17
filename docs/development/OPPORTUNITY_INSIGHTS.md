# Governed opportunity and management insights (Milestone 13)

This stage reads approved, synthetic Warehouse, Gold, Customer, Product,
Statistics and Forecasting publications without write access. It publishes a
separate `data/insights/opportunity_insights.duckdb` and SHA-256 manifest. The
reporting cutoff is 2026-09-30; no private scenario labels or post-cutoff events
are analytical inputs. These are hypotheses and illustrative 12-month EUR net
gross-profit scenarios before freight, not confirmed savings, transactions or
forecast probabilities.

## Grain and publication contract

- `opportunity`: one stable `opportunity_id` (family plus customer, product or
  customer/category key), selected from eligible candidates. `status` is
  `estimable` or `investigative`. Investigative rows have NULL financial bounds,
  financial unit and formula. Each row carries its historical observation start
  and end dates. `baseline` and `assumptions` are sorted JSON;
  `evidence_metric_ids` and `overlap_ids` are typed string arrays. Financial
  bounds are posted EUR cents and always low ≤ base ≤ high.
- `opportunity_overlap`: one pair of estimable opportunities and shared business
  base. `must_not_sum=true` on related opportunities prevents naïve aggregation.
  A shared customer, customer/category or product base is treated as overlap;
  category-level overlap also applies to product proposals. This includes
  proposals within the same family. No portfolio total is published without a
  deduplication design.
- `opportunity_suppression`: counts by reason, including insufficient support
  and the configured per-family management-list limit. Truncation affects the
  list, not historical source totals.
- `management_insight`: one stable rule/scope/as-of evaluation, including
  `triggered`, `not_triggered` and `insufficient_evidence` states. Numerator and
  denominator are NULL when support is insufficient. Severity is populated only
  for triggered rules. Independent evidence is sorted JSON.

The stage validates source manifest and database hashes, batch IDs, reporting
windows, declared grains, keys, row counts, required types, lineage and private
label exclusions. It reconciles company monthly net revenue and net gross profit
across Warehouse postings, Gold commercial mart and Forecasting actuals. The
output is built in staging, validated, checkpointed and atomically swapped on
publication. The manifest carries a file SHA-256 for integrity and a canonical,
key-ordered row-content SHA-256 per table for deterministic replay checks;
DuckDB file bytes can differ between equivalent builds. Dry-run executes the
same validation without publishing.

## Opportunity methods

All scenarios use historical contribution, not revenue as a proxy for gross
profit. Scenario multipliers and caps are implementation assumptions in
[`config/opportunities.yaml`](../../config/opportunities.yaml), not approved
commercial policies.

| Code | Evidence and eligible population | Estimation basis and limitation |
| --- | --- | --- |
| O01 | Lapsed buyers with repeat fulfilled orders, recency at least the configured multiple of own and active same-country/segment peer purchase cadence | Minimum of annualised realised lifetime GP and peer median trailing-year GP, times response scenarios; capped at a fraction of annualised GP. Dormant time and peer mismatch can lower relevance. |
| O02 | Active A-buyers without B, subcategory pairs with at least 30 joint baskets and 1% order-basket support, supported lift and adjusted association q-value, plus local segment basket context and at least the configured minimum comparable B-buying peer accounts | Median positive trailing-year B-subcategory net GP of active B buyers in the candidate's current country and segment × observed local directional A-to-B basket confidence × conservative realization factor; capped at a configured fraction of the account's trailing-year net GP. Basket association does not establish need or causation. |
| O03 | Same SKU with enough units in both comparable years and a larger discount or lower unit sale GP | Recent units times capped recoverable GP per unit times uptake scenarios; capped by prior same-SKU GP and recent list value. Cost, discount and demand mechanisms remain unproven. |
| O04 | Active account missing a category purchased by distinct active peers in its country/segment | Peer median category GP times observed peer penetration and uptake scenarios; capped by account trailing-year GP. Peer mix is descriptive. |
| O05 | Active account with positive comparable-year GP and revenue trajectory and supported positive-growth peers | Minimum of account GP gain and peer median gain times scenarios; capped by recent GP. No growth extrapolation is guaranteed. |
| O06 | Product with sustained defect-reason accepted returns in each of two complete quarters, above an observed category-peer accepted-return share | Positive observed refund minus recovered COGS (GP reversal) times improvement scenarios; capped at the observed reversal. No other-product category accepted events means peer share is unavailable and status is investigative with NULL impact; an observed zero share is valid. This is an event-period accepted-return measure, **not** a mature fulfilled-unit return rate. |

For O02, local directional confidence is joint fulfilled order baskets containing
A and B divided by local fulfilled order baskets containing A. Reversing A and B
uses the separately published B-to-A confidence. The financial peer cohort
contains distinct active B-buying accounts in the candidate's current country
and segment with positive trailing-year B-subcategory net GP; its support count
is the number of those accounts, and its median is over their annual B GP.
The low/base/high realization factors (2%/5%/10%) multiply observed confidence
and peer median GP for one eligible account. Each result is capped at 25% of
that account's trailing-year net GP. These factors are scenario assumptions,
not observed attachment rates. Local basket geography and segment use customer
attributes at fulfilment; the peer cohort uses current attributes at cutoff.

An unsupported candidate is either omitted with a suppression reason or retained
as `investigative` with no positive financial impact. Source records with zero or
negative contribution cannot create a positive estimate. Revenue and list value
are used only for eligibility, discounts and explicit caps. Customer and product
estimates share historical bases; they must not be added without resolving the
published overlap relationships.

## Score

Each component is 0–100. `score = 0.40 impact + 0.30 confidence + 0.20
actionability + 0.10 urgency`. Impact is the candidate base estimate relative
to the largest estimable base in the run; a NULL estimate scores zero impact.
Confidence combines capped evidence count (35%), observation length (25%),
measurement-quality factor (25%) and low/high sensitivity ratio (15%). The
quality factors and urgency/actionability values are versioned implementation
assumptions. Results sort by descending score, then opportunity code and stable
entity ID. The fixed weights are the SPEC-14 approved contract.

## Management rules

The configured thresholds and support minimums below are **implementation
assumptions requiring business approval**. All 12-month comparisons use two
complete, non-overlapping years ending 2026-09-30 unless stated otherwise.
Triggers identify patterns for review; they do not assign causation.

| Rule | Exact trigger and support | Independent evidence and limitation |
| --- | --- | --- |
| R01 | Country or company: recent net revenue / prior net revenue − 1 ≥ 3%, while prior GP/revenue − recent GP/revenue ≥ 3 percentage points; 12 months and ≥20 postings in each year, positive revenue denominators. | GP totals and posting counts; product mix, price, costs and return timing may differ. |
| R02 | Company: defect-related accepted events / all accepted events rises by ≥5 points from prior to recent decision year, with positive observed GP reversal; ≥20 accepted events per year. | Refund less recovered COGS on defect events; this is not a mature-cohort return rate and classification mix may change. |
| R03 | Country or company: top-five positive-customer net-revenue share crosses from below 50% to at least 50%; ≥20 positive-balance customers and positive denominators each year. | Positive-balance HHI and customer count; nonpositive customer balances are excluded. |
| R04 | Company: weighted age-six retained/eligible cohort rate of the preceding six mature cohort months minus the latest six mature cohort months ≥10 points; all 12 cohort months must be consecutive, with ≥3 mature months and ≥30 eligible customers in each group. | Cohort counts and maturity flags; vintage composition may differ. |
| R05 | Company: prior-list-weighted same-SKU discount-rate increase ≥3 points, with at least one matched SKU showing lower sale GP per unit; ≥20 matched SKUs with positive list values and units in both years. | Matched SKU counts and GP/unit; adjustment controls SKU mix, not customer or channel mix. |
| R06 | Customer-home country: comparable net revenue declines ≥10% and the largest absolute channel revenue-share change is ≥10 points; ≥2 channels, positive country revenues and ≥20 postings per year. | Channel-specific net revenue; channel movement does not explain market decline. |
| R07 | Latest complete month: actual falls outside that origin's historical 95% interval; ≥8 calibration errors and ≥12 evaluated calibrated intervals. | Point error, interval bounds, calibration count and empirical coverage. Severity is only `high` if observed 95% coverage is ≥90%; otherwise `investigate`. Latvia's known undercoverage means nominal 95% is not a validated probability guarantee. |

Each row records its observation window, numerator, denominator, threshold,
trigger condition, support, severity, evidence, interpretation, follow-up and
alternative explanation. The source Forecasting publication retains its 1-, 3-
and 6-month intervals; R07 uses the most recent 1-month selected/reconciled
historical forecast only. No return-cohort maturity duration is inferred.

## Local execution and limitations

From the repository root:

```bash
.venv/bin/python -m baltic_sales_intelligence.pipeline --config config --stage insights --dry-run
.venv/bin/python -m baltic_sales_intelligence.pipeline --config config --stage insights
```

Use `--force` only to replace an existing insight publication. The separate
policy file avoids changing the shared build-config hash recorded by upstream
publications. Any policy change changes this stage's policy hash and warrants
review of its candidate and rule outputs.

The reported amounts are sensitivity scenarios based on historical synthetic
patterns. Price elasticity, account capacity, stock availability, avoidable
return fractions and causal effects are not observed. Small or selected peer
samples, changing customer mix and event timing can weaken estimates. Published
forecast intervals have poor empirical coverage in some scopes, especially
Latvia; consumers must inspect the source `model_evaluation` coverage fields.
