-- Milestone 9 descriptive customer analytics.
-- `warehouse` and `gold` are attached read-only upstream publications.

CREATE TEMP TABLE customer_identity AS
SELECT
    customer.customer_id,
    customer.customer_name,
    customer.acquisition_date,
    customer.account_status,
    customer.industry_code,
    customer.industry_name,
    customer.credit_tier,
    customer.customer_sk AS customer_sk_as_of,
    customer.customer_version_id AS customer_version_id_as_of,
    customer.segment_code AS segment_code_as_of,
    customer.segment_name AS segment_name_as_of,
    customer.company_size_band AS company_size_band_as_of,
    customer.home_city_id AS home_city_id_as_of,
    customer.home_region_id AS home_region_id_as_of,
    customer.home_country_code AS home_country_code_as_of
FROM warehouse.dim_customer AS customer
CROSS JOIN build_lineage AS lineage
WHERE customer.acquisition_date <= lineage.as_of_date
  AND lineage.as_of_date >= customer.valid_from
  AND (customer.valid_to IS NULL OR lineage.as_of_date < customer.valid_to);

CREATE TEMP TABLE fulfilled_orders AS
SELECT
    line.order_id,
    line.customer_id,
    min(line.fulfilled_date) AS fulfilled_date,
    min(line.customer_sk_fulfilment) AS customer_sk_fulfilment
FROM warehouse.fact_order_line AS line
CROSS JOIN build_lineage AS lineage
WHERE line.order_status = 'fulfilled'
  AND line.fulfilled_date >= lineage.observation_start_date
  AND line.fulfilled_date <= lineage.as_of_date
GROUP BY line.order_id, line.customer_id;

CREATE TEMP TABLE customer_financial AS
SELECT
    month.customer_id,
    sum(month.posting_count)::BIGINT AS posting_count,
    sum(month.fulfilled_sale_posting_count)::BIGINT AS fulfilled_sale_posting_count,
    sum(month.accepted_return_posting_count)::BIGINT AS accepted_return_posting_count,
    sum(month.fulfilled_order_count)::BIGINT AS fulfilled_order_count,
    sum(month.fulfilled_sales_eur)::DECIMAL(18, 2) AS fulfilled_sales_eur,
    sum(month.accepted_refunds_eur)::DECIMAL(18, 2) AS accepted_refunds_eur,
    sum(month.net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
    sum(month.net_cogs_eur)::DECIMAL(18, 2) AS net_cogs_eur,
    sum(month.net_gross_profit_eur)::DECIMAL(18, 2) AS net_gross_profit_eur,
    sum(month.outbound_freight_eur)::DECIMAL(18, 2) AS outbound_freight_eur,
    sum(month.returns_freight_eur)::DECIMAL(18, 2) AS returns_freight_eur,
    sum(month.contribution_after_freight_eur)::DECIMAL(18, 2)
        AS contribution_after_freight_eur
FROM gold.mart_customer_month AS month
CROSS JOIN build_lineage AS lineage
WHERE month.reporting_month <= date_trunc('month', lineage.as_of_date)
  AND month.reporting_month >= date_trunc('month', lineage.observation_start_date)
GROUP BY month.customer_id;

CREATE TABLE mart_customer_value AS
WITH purchase_summary AS (
    SELECT
        customer_id,
        min(fulfilled_date) AS first_fulfilled_date,
        max(fulfilled_date) AS last_fulfilled_date,
        count(DISTINCT order_id)::BIGINT AS lifetime_fulfilled_order_count
    FROM fulfilled_orders
    GROUP BY customer_id
)
SELECT
    customer.customer_id,
    customer.customer_name,
    customer.acquisition_date,
    purchase.first_fulfilled_date,
    date_trunc('month', purchase.first_fulfilled_date)::DATE AS first_purchase_cohort_month,
    purchase.last_fulfilled_date,
    CASE
        WHEN purchase.first_fulfilled_date IS NULL THEN NULL
        ELSE date_diff('day', purchase.first_fulfilled_date, lineage.as_of_date) + 1
    END::BIGINT AS buying_tenure_days_at_cutoff,
    coalesce(purchase.lifetime_fulfilled_order_count, 0)::BIGINT
        AS lifetime_fulfilled_order_count,
    coalesce(finance.posting_count, 0)::BIGINT AS posting_count,
    coalesce(finance.accepted_return_posting_count, 0)::BIGINT
        AS accepted_return_posting_count,
    coalesce(finance.fulfilled_sales_eur, 0)::DECIMAL(18, 2) AS fulfilled_sales_eur,
    coalesce(finance.accepted_refunds_eur, 0)::DECIMAL(18, 2) AS accepted_refunds_eur,
    coalesce(finance.net_revenue_eur, 0)::DECIMAL(18, 2) AS net_revenue_eur,
    coalesce(finance.net_cogs_eur, 0)::DECIMAL(18, 2) AS net_cogs_eur,
    coalesce(finance.net_gross_profit_eur, 0)::DECIMAL(18, 2)
        AS historical_value_net_gross_profit_eur,
    coalesce(finance.outbound_freight_eur, 0)::DECIMAL(18, 2) AS outbound_freight_eur,
    coalesce(finance.returns_freight_eur, 0)::DECIMAL(18, 2) AS returns_freight_eur,
    coalesce(finance.contribution_after_freight_eur, 0)::DECIMAL(18, 2)
        AS contribution_after_freight_eur,
    CASE
        WHEN purchase.first_fulfilled_date IS NULL THEN 'no_fulfilled_purchase'
        ELSE 'observed_historical_value'
    END::VARCHAR AS value_status,
    customer.customer_version_id_as_of,
    customer.segment_code_as_of,
    customer.segment_name_as_of,
    customer.company_size_band_as_of,
    customer.home_city_id_as_of,
    customer.home_region_id_as_of,
    customer.home_country_code_as_of,
    1::BIGINT AS eligible_customer_count,
    lineage.as_of_date,
    lineage.observation_start_date,
    'customer_home_as_of'::VARCHAR AS report_country_role,
    'EUR'::VARCHAR AS currency_code,
    'observed_historical_not_predictive'::VARCHAR AS value_basis,
    'customer-value-v1'::VARCHAR AS method_version,
    lineage.source_batch_id,
    lineage.source_warehouse_manifest_sha256,
    lineage.source_gold_manifest_sha256
FROM customer_identity AS customer
CROSS JOIN build_lineage AS lineage
LEFT JOIN purchase_summary AS purchase USING (customer_id)
LEFT JOIN customer_financial AS finance USING (customer_id)
ORDER BY customer.customer_id;

CREATE TABLE mart_rfm AS
WITH purchase_summary AS (
    SELECT
        customer_id,
        min(fulfilled_date) AS first_fulfilled_date,
        max(fulfilled_date) AS last_fulfilled_date,
        count(DISTINCT order_id) FILTER (
            WHERE fulfilled_date >= (
                SELECT lookback_start_date FROM build_lineage
            )
        )::BIGINT AS frequency_orders_trailing_12m
    FROM fulfilled_orders
    GROUP BY customer_id
), sequenced AS (
    SELECT
        customer_id,
        fulfilled_date,
        lag(fulfilled_date) OVER (
            PARTITION BY customer_id ORDER BY fulfilled_date, order_id
        ) AS previous_fulfilled_date
    FROM fulfilled_orders
), reactivation AS (
    SELECT
        customer_id,
        bool_or(
            fulfilled_date >= (SELECT lookback_start_date FROM build_lineage)
            AND previous_fulfilled_date IS NOT NULL
            AND fulfilled_date >= previous_fulfilled_date + INTERVAL 12 MONTH
        ) AS is_reactivated_in_lookback
    FROM sequenced
    GROUP BY customer_id
), monetary AS (
    SELECT
        customer_id,
        sum(net_revenue_eur)::DECIMAL(18, 2) AS monetary_net_revenue_trailing_12m_eur
    FROM gold.mart_customer_month
    WHERE reporting_month >= (SELECT lookback_start_month FROM build_lineage)
      AND reporting_month <= (SELECT as_of_month FROM build_lineage)
    GROUP BY customer_id
), base AS (
    SELECT
        customer.*,
        purchase.first_fulfilled_date,
        purchase.last_fulfilled_date,
        CASE
            WHEN purchase.last_fulfilled_date IS NULL THEN NULL
            ELSE date_diff('day', purchase.last_fulfilled_date, lineage.as_of_date)
        END::BIGINT AS recency_days,
        coalesce(purchase.frequency_orders_trailing_12m, 0)::BIGINT
            AS frequency_orders_trailing_12m,
        coalesce(monetary.monetary_net_revenue_trailing_12m_eur, 0)::DECIMAL(18, 2)
            AS monetary_net_revenue_trailing_12m_eur,
        (coalesce(purchase.frequency_orders_trailing_12m, 0) > 0)
            AS is_active_buyer,
        coalesce((
            purchase.first_fulfilled_date >= lineage.lookback_start_date
            AND purchase.first_fulfilled_date <= lineage.as_of_date
        ), false) AS is_new_buyer_in_lookback,
        (
            purchase.first_fulfilled_date IS NOT NULL
            AND coalesce(purchase.frequency_orders_trailing_12m, 0) = 0
        ) AS is_lapsed_buyer,
        coalesce(reactivation.is_reactivated_in_lookback, false)
            AS is_reactivated_in_lookback
    FROM customer_identity AS customer
    CROSS JOIN build_lineage AS lineage
    LEFT JOIN purchase_summary AS purchase USING (customer_id)
    LEFT JOIN monetary USING (customer_id)
    LEFT JOIN reactivation USING (customer_id)
), active_scores AS (
    SELECT
        customer_id,
        ntile(5) OVER (ORDER BY recency_days DESC, customer_id)::SMALLINT AS recency_score,
        ntile(5) OVER (
            ORDER BY frequency_orders_trailing_12m, customer_id
        )::SMALLINT AS frequency_score,
        ntile(5) OVER (
            ORDER BY monetary_net_revenue_trailing_12m_eur, customer_id
        )::SMALLINT AS monetary_score,
        count(*) OVER ()::BIGINT AS score_population_count
    FROM base
    WHERE is_active_buyer
)
SELECT
    base.customer_id,
    base.customer_name,
    base.acquisition_date,
    base.first_fulfilled_date,
    base.last_fulfilled_date,
    base.recency_days,
    base.frequency_orders_trailing_12m,
    base.monetary_net_revenue_trailing_12m_eur,
    scores.recency_score,
    scores.frequency_score,
    scores.monetary_score,
    CASE
        WHEN scores.recency_score IS NULL THEN NULL
        ELSE concat(scores.recency_score, scores.frequency_score, scores.monetary_score)
    END::VARCHAR AS rfm_code,
    CASE
        WHEN base.is_active_buyer THEN 'scored_active_buyer'
        WHEN base.first_fulfilled_date IS NULL THEN 'not_estimable_never_purchased'
        ELSE 'not_estimable_no_purchase_in_lookback'
    END::VARCHAR AS score_status,
    coalesce(scores.score_population_count, 0)::BIGINT AS score_population_count,
    base.is_active_buyer,
    base.is_new_buyer_in_lookback,
    base.is_lapsed_buyer,
    base.is_reactivated_in_lookback,
    base.customer_version_id_as_of,
    base.segment_code_as_of,
    base.company_size_band_as_of,
    base.home_country_code_as_of,
    1::BIGINT AS eligible_customer_count,
    lineage.as_of_date,
    lineage.lookback_start_date AS observation_start_date,
    'customer_home_as_of'::VARCHAR AS report_country_role,
    'EUR'::VARCHAR AS currency_code,
    'posting_date_net_revenue'::VARCHAR AS monetary_basis,
    'ntile_5_customer_id_tiebreak'::VARCHAR AS scoring_method,
    'customer_id_ascending'::VARCHAR AS tie_breaker,
    'rfm-v1'::VARCHAR AS method_version,
    lineage.source_batch_id,
    lineage.source_warehouse_manifest_sha256,
    lineage.source_gold_manifest_sha256
FROM base
CROSS JOIN build_lineage AS lineage
LEFT JOIN active_scores AS scores USING (customer_id)
ORDER BY base.customer_id;

CREATE TABLE mart_cohort_retention AS
WITH ranked_first AS (
    SELECT
        orders.*,
        row_number() OVER (
            PARTITION BY customer_id ORDER BY fulfilled_date, order_id
        ) AS purchase_number
    FROM fulfilled_orders AS orders
), cohort_customer AS (
    SELECT
        first.customer_id,
        date_trunc('month', first.fulfilled_date)::DATE AS cohort_month,
        customer.home_country_code AS cohort_home_country_code
    FROM ranked_first AS first
    JOIN warehouse.dim_customer AS customer
      ON customer.customer_sk = first.customer_sk_fulfilment
    WHERE first.purchase_number = 1
), cohort_size AS (
    SELECT
        cohort_month,
        cohort_home_country_code,
        count(*)::BIGINT AS cohort_customer_count
    FROM cohort_customer
    GROUP BY cohort_month, cohort_home_country_code
), max_age AS (
    SELECT max(date_diff('month', cohort_month, lineage.as_of_month))::BIGINT AS value
    FROM cohort_size CROSS JOIN build_lineage AS lineage
), cohort_grid AS (
    SELECT
        cohort.*,
        age.cohort_age_month,
        (cohort.cohort_month + age.cohort_age_month * INTERVAL 1 MONTH)::DATE
            AS observation_month
    FROM cohort_size AS cohort
    CROSS JOIN max_age
    CROSS JOIN range(0, max_age.value + 1) AS age(cohort_age_month)
), retained AS (
    SELECT
        cohort.cohort_month,
        cohort.cohort_home_country_code,
        date_diff(
            'month', cohort.cohort_month, date_trunc('month', orders.fulfilled_date)
        )::BIGINT AS cohort_age_month,
        count(DISTINCT cohort.customer_id)::BIGINT AS retained_customer_count
    FROM cohort_customer AS cohort
    JOIN fulfilled_orders AS orders USING (customer_id)
    GROUP BY cohort.cohort_month, cohort.cohort_home_country_code, cohort_age_month
)
SELECT
    grid.cohort_month,
    grid.cohort_home_country_code,
    grid.cohort_age_month,
    grid.observation_month,
    grid.cohort_customer_count,
    CASE
        WHEN last_day(grid.observation_month) <= lineage.as_of_date
        THEN grid.cohort_customer_count ELSE 0
    END::BIGINT AS eligible_customer_count,
    CASE
        WHEN last_day(grid.observation_month) <= lineage.as_of_date
        THEN coalesce(retained.retained_customer_count, 0) ELSE NULL
    END::BIGINT AS retained_customer_count,
    CASE
        WHEN last_day(grid.observation_month) <= lineage.as_of_date
        THEN round(
            coalesce(retained.retained_customer_count, 0)::DECIMAL(38, 8)
                / grid.cohort_customer_count::DECIMAL(38, 8),
            6
        )::DECIMAL(18, 6)
        ELSE NULL
    END AS retention_rate,
    (last_day(grid.observation_month) <= lineage.as_of_date) AS is_observation_mature,
    CASE
        WHEN last_day(grid.observation_month) > lineage.as_of_date THEN 'right_censored'
        ELSE NULL
    END::VARCHAR AS not_estimable_reason,
    lineage.as_of_date,
    grid.cohort_month AS observation_start_date,
    'customer_home_at_first_fulfilled_purchase'::VARCHAR AS report_country_role,
    'fulfilled_order_month'::VARCHAR AS activity_basis,
    'buying-cohort-v1'::VARCHAR AS method_version,
    lineage.source_batch_id,
    lineage.source_warehouse_manifest_sha256,
    lineage.source_gold_manifest_sha256
FROM cohort_grid AS grid
CROSS JOIN build_lineage AS lineage
LEFT JOIN retained USING (
    cohort_month, cohort_home_country_code, cohort_age_month
)
ORDER BY grid.cohort_month, grid.cohort_home_country_code, grid.cohort_age_month;

CREATE TABLE mart_customer_concentration AS
WITH country_balance AS (
    SELECT
        month.home_country_code AS scope_key,
        month.customer_id,
        sum(month.net_revenue_eur)::DECIMAL(18, 2) AS customer_net_revenue_eur
    FROM gold.mart_customer_month AS month
    GROUP BY month.home_country_code, month.customer_id
), company_balance AS (
    SELECT
        'COMPANY'::VARCHAR AS scope_key,
        customer.customer_id,
        coalesce(sum(month.net_revenue_eur), 0)::DECIMAL(18, 2)
            AS customer_net_revenue_eur
    FROM customer_identity AS customer
    LEFT JOIN gold.mart_customer_month AS month USING (customer_id)
    GROUP BY customer.customer_id
), balances AS (
    SELECT 'company'::VARCHAR AS scope_type, * FROM company_balance
    UNION ALL
    SELECT 'country'::VARCHAR AS scope_type, * FROM country_balance
), ranked AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY scope_type, scope_key
            ORDER BY customer_net_revenue_eur DESC, customer_id
        ) AS positive_rank
    FROM balances
    WHERE customer_net_revenue_eur > 0
), summary AS (
    SELECT
        balance.scope_type,
        balance.scope_key,
        count(*)::BIGINT AS eligible_customer_count,
        count(*) FILTER (WHERE balance.customer_net_revenue_eur > 0)::BIGINT
            AS positive_balance_customer_count,
        count(*) FILTER (WHERE balance.customer_net_revenue_eur <= 0)::BIGINT
            AS nonpositive_balance_customer_count,
        sum(balance.customer_net_revenue_eur) FILTER (
            WHERE balance.customer_net_revenue_eur > 0
        )::DECIMAL(18, 2) AS positive_net_revenue_denominator_eur
    FROM balances AS balance
    GROUP BY balance.scope_type, balance.scope_key
), metrics AS (
    SELECT
        summary.*,
        sum(ranked.customer_net_revenue_eur) FILTER (
            WHERE ranked.positive_rank <= 1
        )::DECIMAL(18, 2) AS top_1_positive_net_revenue_eur,
        sum(ranked.customer_net_revenue_eur) FILTER (
            WHERE ranked.positive_rank <= 5
        )::DECIMAL(18, 2) AS top_5_positive_net_revenue_eur,
        sum(ranked.customer_net_revenue_eur) FILTER (
            WHERE ranked.positive_rank <= 10
        )::DECIMAL(18, 2) AS top_10_positive_net_revenue_eur,
        sum(
            power(
                ranked.customer_net_revenue_eur::DECIMAL(38, 8)
                    / summary.positive_net_revenue_denominator_eur::DECIMAL(38, 8),
                2
            )
        ) AS hhi_value
    FROM summary
    LEFT JOIN ranked USING (scope_type, scope_key)
    GROUP BY ALL
)
SELECT
    metrics.scope_type,
    metrics.scope_key,
    CASE WHEN metrics.scope_type = 'country' THEN metrics.scope_key ELSE NULL END::VARCHAR
        AS home_country_code,
    metrics.eligible_customer_count,
    metrics.positive_balance_customer_count,
    metrics.nonpositive_balance_customer_count,
    coalesce(metrics.positive_net_revenue_denominator_eur, 0)::DECIMAL(18, 2)
        AS positive_net_revenue_denominator_eur,
    coalesce(metrics.top_1_positive_net_revenue_eur, 0)::DECIMAL(18, 2)
        AS top_1_positive_net_revenue_eur,
    coalesce(metrics.top_5_positive_net_revenue_eur, 0)::DECIMAL(18, 2)
        AS top_5_positive_net_revenue_eur,
    coalesce(metrics.top_10_positive_net_revenue_eur, 0)::DECIMAL(18, 2)
        AS top_10_positive_net_revenue_eur,
    CASE
        WHEN metrics.positive_net_revenue_denominator_eur > 0 THEN round(
            metrics.top_1_positive_net_revenue_eur::DECIMAL(38, 8)
                / metrics.positive_net_revenue_denominator_eur::DECIMAL(38, 8), 6
        )::DECIMAL(18, 6) ELSE NULL
    END AS top_1_share,
    CASE
        WHEN metrics.positive_net_revenue_denominator_eur > 0 THEN round(
            metrics.top_5_positive_net_revenue_eur::DECIMAL(38, 8)
                / metrics.positive_net_revenue_denominator_eur::DECIMAL(38, 8), 6
        )::DECIMAL(18, 6) ELSE NULL
    END AS top_5_share,
    CASE
        WHEN metrics.positive_net_revenue_denominator_eur > 0 THEN round(
            metrics.top_10_positive_net_revenue_eur::DECIMAL(38, 8)
                / metrics.positive_net_revenue_denominator_eur::DECIMAL(38, 8), 6
        )::DECIMAL(18, 6) ELSE NULL
    END AS top_10_share,
    CASE
        WHEN metrics.positive_net_revenue_denominator_eur > 0
        THEN round(metrics.hhi_value, 6)::DECIMAL(18, 6) ELSE NULL
    END AS hhi,
    CASE
        WHEN coalesce(metrics.positive_net_revenue_denominator_eur, 0) <= 0
        THEN 'nonpositive_positive_balance_denominator' ELSE NULL
    END::VARCHAR AS not_estimable_reason,
    lineage.as_of_date,
    lineage.observation_start_date,
    CASE WHEN metrics.scope_type = 'country' THEN 'customer_home_at_posting'
         ELSE 'company' END::VARCHAR AS report_country_role,
    'EUR'::VARCHAR AS currency_code,
    'positive_customer_net_revenue_balances'::VARCHAR AS concentration_basis,
    'customer-concentration-v1'::VARCHAR AS method_version,
    lineage.source_batch_id,
    lineage.source_warehouse_manifest_sha256,
    lineage.source_gold_manifest_sha256
FROM metrics
CROSS JOIN build_lineage AS lineage
ORDER BY metrics.scope_type, metrics.scope_key;

CREATE TABLE mart_customer_migration AS
WITH versions AS (
    SELECT
        customer.*,
        lead(customer_sk) OVER (
            PARTITION BY customer_id ORDER BY valid_from, customer_sk
        ) AS next_customer_sk,
        lead(customer_version_id) OVER (
            PARTITION BY customer_id ORDER BY valid_from, customer_sk
        ) AS next_customer_version_id,
        lead(valid_from) OVER (
            PARTITION BY customer_id ORDER BY valid_from, customer_sk
        ) AS transition_date,
        lead(segment_code) OVER (
            PARTITION BY customer_id ORDER BY valid_from, customer_sk
        ) AS next_segment_code,
        lead(company_size_band) OVER (
            PARTITION BY customer_id ORDER BY valid_from, customer_sk
        ) AS next_company_size_band,
        lead(home_country_code) OVER (
            PARTITION BY customer_id ORDER BY valid_from, customer_sk
        ) AS next_home_country_code
    FROM warehouse.dim_customer AS customer
), version_value AS (
    SELECT
        customer_sk,
        sum(fulfilled_order_count)::BIGINT AS fulfilled_order_count,
        sum(net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
        sum(net_gross_profit_eur)::DECIMAL(18, 2) AS net_gross_profit_eur
    FROM gold.mart_customer_month
    GROUP BY customer_sk
)
SELECT
    versions.customer_id,
    versions.customer_name,
    versions.customer_version_id AS from_customer_version_id,
    versions.next_customer_version_id AS to_customer_version_id,
    versions.transition_date,
    versions.segment_code AS from_segment_code,
    versions.next_segment_code AS to_segment_code,
    versions.company_size_band AS from_company_size_band,
    versions.next_company_size_band AS to_company_size_band,
    versions.home_country_code AS from_home_country_code,
    versions.next_home_country_code AS to_home_country_code,
    versions.valid_from AS before_observation_start_date,
    (versions.transition_date - INTERVAL 1 DAY)::DATE AS before_observation_end_date,
    date_diff('day', versions.valid_from, versions.transition_date)::BIGINT
        AS before_exposure_days,
    versions.transition_date AS after_observation_start_date,
    lineage.as_of_date AS after_observation_end_date,
    (date_diff('day', versions.transition_date, lineage.as_of_date) + 1)::BIGINT
        AS after_exposure_days,
    (versions.segment_code IS DISTINCT FROM versions.next_segment_code)
        AS segment_changed,
    (versions.company_size_band IS DISTINCT FROM versions.next_company_size_band)
        AS company_size_changed,
    (versions.home_country_code IS DISTINCT FROM versions.next_home_country_code)
        AS home_country_changed,
    coalesce(before.fulfilled_order_count, 0)::BIGINT AS before_fulfilled_order_count,
    coalesce(after.fulfilled_order_count, 0)::BIGINT AS after_fulfilled_order_count,
    coalesce(before.net_revenue_eur, 0)::DECIMAL(18, 2) AS before_net_revenue_eur,
    coalesce(after.net_revenue_eur, 0)::DECIMAL(18, 2) AS after_net_revenue_eur,
    coalesce(before.net_gross_profit_eur, 0)::DECIMAL(18, 2)
        AS before_net_gross_profit_eur,
    coalesce(after.net_gross_profit_eur, 0)::DECIMAL(18, 2)
        AS after_net_gross_profit_eur,
    1::BIGINT AS eligible_customer_count,
    lineage.as_of_date,
    versions.valid_from AS observation_start_date,
    'scd_customer_home_version'::VARCHAR AS report_country_role,
    'EUR'::VARCHAR AS currency_code,
    'descriptive_version_value_no_causal_inference'::VARCHAR AS value_basis,
    'raw_version_totals_not_duration_normalised'::VARCHAR AS comparison_status,
    'customer-migration-v1'::VARCHAR AS method_version,
    lineage.source_batch_id,
    lineage.source_warehouse_manifest_sha256,
    lineage.source_gold_manifest_sha256
FROM versions
CROSS JOIN build_lineage AS lineage
LEFT JOIN version_value AS before ON before.customer_sk = versions.customer_sk
LEFT JOIN version_value AS after ON after.customer_sk = versions.next_customer_sk
WHERE versions.next_customer_sk IS NOT NULL
  AND versions.transition_date <= lineage.as_of_date
ORDER BY versions.customer_id, versions.transition_date, versions.next_customer_version_id;
