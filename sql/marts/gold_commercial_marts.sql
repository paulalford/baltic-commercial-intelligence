-- Milestone 8 governed Gold marts.
-- The attached read-only warehouse is named `warehouse`; all financial amounts
-- come from its canonical historical posting ledger.

CREATE TABLE mart_commercial_month AS
WITH monthly AS (
    SELECT
        date.month_start_date AS reporting_month,
        posting.customer_sk,
        posting.home_city_id,
        posting.home_country_code,
        posting.channel_code,
        posting.product_id,
        posting.credited_salesperson_id,
        coalesce(posting.credited_salesperson_id, '__UNCREDITED__')
            AS credited_salesperson_key,
        count(*)::BIGINT AS posting_count,
        count(*) FILTER (WHERE posting.posting_type = 'fulfilled_sale')::BIGINT
            AS fulfilled_sale_posting_count,
        count(*) FILTER (WHERE posting.posting_type = 'accepted_return')::BIGINT
            AS accepted_return_posting_count,
        count(DISTINCT posting.order_id)
            FILTER (WHERE posting.posting_type = 'fulfilled_sale')::BIGINT
            AS fulfilled_order_count,
        sum(posting.posted_list_value_eur)::DECIMAL(18, 2) AS posted_list_value_eur,
        sum(posting.posted_discount_eur)::DECIMAL(18, 2) AS posted_discount_eur,
        sum(posting.fulfilled_sales_eur)::DECIMAL(18, 2) AS fulfilled_sales_eur,
        sum(posting.accepted_refunds_eur)::DECIMAL(18, 2) AS accepted_refunds_eur,
        sum(posting.net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
        sum(posting.fulfilled_cogs_eur)::DECIMAL(18, 2) AS fulfilled_cogs_eur,
        sum(posting.recovered_cogs_eur)::DECIMAL(18, 2) AS recovered_cogs_eur,
        sum(posting.net_cogs_eur)::DECIMAL(18, 2) AS net_cogs_eur,
        sum(posting.net_gross_profit_eur)::DECIMAL(18, 2) AS net_gross_profit_eur,
        sum(posting.net_vat_eur)::DECIMAL(18, 2) AS net_vat_eur,
        sum(posting.outbound_freight_eur)::DECIMAL(18, 2) AS outbound_freight_eur,
        sum(posting.returns_freight_eur)::DECIMAL(18, 2) AS returns_freight_eur,
        sum(posting.contribution_after_freight_eur)::DECIMAL(18, 2)
            AS contribution_after_freight_eur
    FROM warehouse.fact_financial_posting_historical AS posting
    JOIN warehouse.dim_date AS date ON date.date_key = posting.posting_date_key
    GROUP BY ALL
)
SELECT
    monthly.reporting_month,
    date.calendar_year,
    date.calendar_quarter,
    date.calendar_month,
    monthly.customer_sk,
    customer.customer_version_id,
    customer.customer_id,
    customer.customer_name,
    customer.segment_code,
    customer.segment_name,
    customer.company_size_band,
    customer.industry_code,
    customer.industry_name,
    monthly.home_city_id,
    geography.city_name AS home_city_name,
    geography.region_id AS home_region_id,
    geography.region_name AS home_region_name,
    monthly.home_country_code,
    geography.country_name AS home_country_name,
    monthly.channel_code,
    channel.channel_name,
    monthly.product_id,
    product.sku,
    product.product_name,
    product.subcategory_id,
    product.subcategory_name,
    product.category_id,
    product.category_name,
    monthly.credited_salesperson_id,
    monthly.credited_salesperson_key,
    salesperson.salesperson_name AS credited_salesperson_name,
    monthly.posting_count,
    monthly.fulfilled_sale_posting_count,
    monthly.accepted_return_posting_count,
    monthly.fulfilled_order_count,
    monthly.posted_list_value_eur,
    monthly.posted_discount_eur,
    monthly.fulfilled_sales_eur,
    monthly.accepted_refunds_eur,
    monthly.net_revenue_eur,
    monthly.fulfilled_cogs_eur,
    monthly.recovered_cogs_eur,
    monthly.net_cogs_eur,
    monthly.net_gross_profit_eur,
    CASE
        WHEN monthly.net_revenue_eur > 0 THEN round(
            monthly.net_gross_profit_eur::DECIMAL(38, 8)
                / monthly.net_revenue_eur::DECIMAL(38, 8),
            6
        )::DECIMAL(18, 6)
        ELSE NULL
    END AS gross_margin_rate,
    CASE
        WHEN monthly.net_revenue_eur <= 0 THEN 'nonpositive_revenue'
        ELSE NULL
    END AS gross_margin_reason,
    CASE
        WHEN monthly.posted_list_value_eur != 0 THEN round(
            monthly.posted_discount_eur::DECIMAL(38, 8)
                / monthly.posted_list_value_eur::DECIMAL(38, 8),
            6
        )::DECIMAL(18, 6)
        ELSE NULL
    END AS discount_rate,
    CASE
        WHEN monthly.posted_list_value_eur = 0 THEN 'zero_list_value'
        ELSE NULL
    END AS discount_rate_reason,
    monthly.net_vat_eur,
    monthly.outbound_freight_eur,
    monthly.returns_freight_eur,
    monthly.contribution_after_freight_eur,
    lineage.as_of_date,
    'customer_home_at_posting'::VARCHAR AS report_country_role,
    'posting_date'::VARCHAR AS date_basis,
    'EUR'::VARCHAR AS currency_code,
    'observed'::VARCHAR AS value_basis,
    'gold-commercial-v1'::VARCHAR AS method_version,
    lineage.source_batch_id,
    lineage.source_warehouse_manifest_sha256
FROM monthly
CROSS JOIN build_lineage AS lineage
JOIN warehouse.dim_date AS date ON date.full_date = monthly.reporting_month
JOIN warehouse.dim_customer AS customer USING (customer_sk)
JOIN warehouse.dim_geography AS geography
    ON geography.city_id = monthly.home_city_id
   AND geography.country_code = monthly.home_country_code
JOIN warehouse.dim_channel AS channel USING (channel_code)
JOIN warehouse.dim_product AS product USING (product_id)
LEFT JOIN warehouse.dim_salesperson AS salesperson
    ON salesperson.salesperson_id = monthly.credited_salesperson_id
ORDER BY
    monthly.reporting_month,
    monthly.customer_sk,
    monthly.product_id,
    monthly.channel_code,
    monthly.credited_salesperson_key;

CREATE TABLE mart_customer_month AS
WITH monthly AS (
    SELECT
        date.month_start_date AS reporting_month,
        posting.customer_sk,
        count(*)::BIGINT AS posting_count,
        count(*) FILTER (WHERE posting.posting_type = 'fulfilled_sale')::BIGINT
            AS fulfilled_sale_posting_count,
        count(*) FILTER (WHERE posting.posting_type = 'accepted_return')::BIGINT
            AS accepted_return_posting_count,
        count(DISTINCT posting.order_id)
            FILTER (WHERE posting.posting_type = 'fulfilled_sale')::BIGINT
            AS fulfilled_order_count,
        sum(posting.posted_list_value_eur)::DECIMAL(18, 2) AS posted_list_value_eur,
        sum(posting.posted_discount_eur)::DECIMAL(18, 2) AS posted_discount_eur,
        sum(posting.fulfilled_sales_eur)::DECIMAL(18, 2) AS fulfilled_sales_eur,
        sum(posting.accepted_refunds_eur)::DECIMAL(18, 2) AS accepted_refunds_eur,
        sum(posting.net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
        sum(posting.fulfilled_cogs_eur)::DECIMAL(18, 2) AS fulfilled_cogs_eur,
        sum(posting.recovered_cogs_eur)::DECIMAL(18, 2) AS recovered_cogs_eur,
        sum(posting.net_cogs_eur)::DECIMAL(18, 2) AS net_cogs_eur,
        sum(posting.net_gross_profit_eur)::DECIMAL(18, 2) AS net_gross_profit_eur,
        sum(posting.net_vat_eur)::DECIMAL(18, 2) AS net_vat_eur,
        sum(posting.outbound_freight_eur)::DECIMAL(18, 2) AS outbound_freight_eur,
        sum(posting.returns_freight_eur)::DECIMAL(18, 2) AS returns_freight_eur,
        sum(posting.contribution_after_freight_eur)::DECIMAL(18, 2)
            AS contribution_after_freight_eur
    FROM warehouse.fact_financial_posting_historical AS posting
    JOIN warehouse.dim_date AS date ON date.date_key = posting.posting_date_key
    GROUP BY date.month_start_date, posting.customer_sk
)
SELECT
    monthly.reporting_month,
    date.calendar_year,
    date.calendar_quarter,
    date.calendar_month,
    monthly.customer_sk,
    customer.customer_version_id,
    customer.customer_id,
    customer.customer_name,
    customer.segment_code,
    customer.segment_name,
    customer.company_size_band,
    customer.industry_code,
    customer.industry_name,
    customer.home_city_id,
    geography.city_name AS home_city_name,
    customer.home_region_id,
    geography.region_name AS home_region_name,
    customer.home_country_code,
    geography.country_name AS home_country_name,
    1::BIGINT AS eligible_customer_count,
    monthly.posting_count,
    monthly.fulfilled_sale_posting_count,
    monthly.accepted_return_posting_count,
    monthly.fulfilled_order_count,
    monthly.posted_list_value_eur,
    monthly.posted_discount_eur,
    monthly.fulfilled_sales_eur,
    monthly.accepted_refunds_eur,
    monthly.net_revenue_eur,
    monthly.fulfilled_cogs_eur,
    monthly.recovered_cogs_eur,
    monthly.net_cogs_eur,
    monthly.net_gross_profit_eur,
    CASE
        WHEN monthly.net_revenue_eur > 0 THEN round(
            monthly.net_gross_profit_eur::DECIMAL(38, 8)
                / monthly.net_revenue_eur::DECIMAL(38, 8),
            6
        )::DECIMAL(18, 6)
        ELSE NULL
    END AS gross_margin_rate,
    CASE
        WHEN monthly.net_revenue_eur <= 0 THEN 'nonpositive_revenue'
        ELSE NULL
    END AS gross_margin_reason,
    monthly.net_vat_eur,
    monthly.outbound_freight_eur,
    monthly.returns_freight_eur,
    monthly.contribution_after_freight_eur,
    lineage.as_of_date,
    'customer_home_at_posting'::VARCHAR AS report_country_role,
    'posting_date'::VARCHAR AS date_basis,
    'EUR'::VARCHAR AS currency_code,
    'observed'::VARCHAR AS value_basis,
    'customer-month-v1'::VARCHAR AS method_version,
    lineage.source_batch_id,
    lineage.source_warehouse_manifest_sha256
FROM monthly
CROSS JOIN build_lineage AS lineage
JOIN warehouse.dim_date AS date ON date.full_date = monthly.reporting_month
JOIN warehouse.dim_customer AS customer USING (customer_sk)
JOIN warehouse.dim_geography AS geography
    ON geography.city_id = customer.home_city_id
   AND geography.country_code = customer.home_country_code
ORDER BY monthly.reporting_month, monthly.customer_sk;

CREATE TABLE mart_company_month AS
WITH month_spine AS (
    SELECT DISTINCT month_start_date AS reporting_month
    FROM warehouse.dim_date
    WHERE is_within_reporting_window
), actual AS (
    SELECT
        reporting_month,
        sum(posting_count)::BIGINT AS posting_count,
        sum(fulfilled_sale_posting_count)::BIGINT AS fulfilled_sale_posting_count,
        sum(accepted_return_posting_count)::BIGINT AS accepted_return_posting_count,
        sum(fulfilled_order_count)::BIGINT AS fulfilled_order_count,
        count(DISTINCT customer_sk)::BIGINT AS posting_customer_version_count,
        sum(posted_list_value_eur)::DECIMAL(18, 2) AS posted_list_value_eur,
        sum(posted_discount_eur)::DECIMAL(18, 2) AS posted_discount_eur,
        sum(fulfilled_sales_eur)::DECIMAL(18, 2) AS fulfilled_sales_eur,
        sum(accepted_refunds_eur)::DECIMAL(18, 2) AS accepted_refunds_eur,
        sum(net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
        sum(fulfilled_cogs_eur)::DECIMAL(18, 2) AS fulfilled_cogs_eur,
        sum(recovered_cogs_eur)::DECIMAL(18, 2) AS recovered_cogs_eur,
        sum(net_cogs_eur)::DECIMAL(18, 2) AS net_cogs_eur,
        sum(net_gross_profit_eur)::DECIMAL(18, 2) AS net_gross_profit_eur,
        sum(net_vat_eur)::DECIMAL(18, 2) AS net_vat_eur,
        sum(outbound_freight_eur)::DECIMAL(18, 2) AS outbound_freight_eur,
        sum(returns_freight_eur)::DECIMAL(18, 2) AS returns_freight_eur,
        sum(contribution_after_freight_eur)::DECIMAL(18, 2)
            AS contribution_after_freight_eur
    FROM mart_customer_month
    GROUP BY reporting_month
), complete AS (
    SELECT
        spine.reporting_month,
        coalesce(actual.posting_count, 0)::BIGINT AS posting_count,
        coalesce(actual.fulfilled_sale_posting_count, 0)::BIGINT
            AS fulfilled_sale_posting_count,
        coalesce(actual.accepted_return_posting_count, 0)::BIGINT
            AS accepted_return_posting_count,
        coalesce(actual.fulfilled_order_count, 0)::BIGINT AS fulfilled_order_count,
        coalesce(actual.posting_customer_version_count, 0)::BIGINT
            AS posting_customer_version_count,
        coalesce(actual.posted_list_value_eur, 0)::DECIMAL(18, 2)
            AS posted_list_value_eur,
        coalesce(actual.posted_discount_eur, 0)::DECIMAL(18, 2) AS posted_discount_eur,
        coalesce(actual.fulfilled_sales_eur, 0)::DECIMAL(18, 2) AS fulfilled_sales_eur,
        coalesce(actual.accepted_refunds_eur, 0)::DECIMAL(18, 2) AS accepted_refunds_eur,
        coalesce(actual.net_revenue_eur, 0)::DECIMAL(18, 2) AS net_revenue_eur,
        coalesce(actual.fulfilled_cogs_eur, 0)::DECIMAL(18, 2) AS fulfilled_cogs_eur,
        coalesce(actual.recovered_cogs_eur, 0)::DECIMAL(18, 2) AS recovered_cogs_eur,
        coalesce(actual.net_cogs_eur, 0)::DECIMAL(18, 2) AS net_cogs_eur,
        coalesce(actual.net_gross_profit_eur, 0)::DECIMAL(18, 2)
            AS net_gross_profit_eur,
        coalesce(actual.net_vat_eur, 0)::DECIMAL(18, 2) AS net_vat_eur,
        coalesce(actual.outbound_freight_eur, 0)::DECIMAL(18, 2) AS outbound_freight_eur,
        coalesce(actual.returns_freight_eur, 0)::DECIMAL(18, 2) AS returns_freight_eur,
        coalesce(actual.contribution_after_freight_eur, 0)::DECIMAL(18, 2)
            AS contribution_after_freight_eur
    FROM month_spine AS spine
    LEFT JOIN actual USING (reporting_month)
)
SELECT
    current.reporting_month,
    date.calendar_year,
    date.calendar_quarter,
    date.calendar_month,
    current.posting_count,
    current.fulfilled_sale_posting_count,
    current.accepted_return_posting_count,
    current.fulfilled_order_count,
    current.posting_customer_version_count,
    current.posted_list_value_eur,
    current.posted_discount_eur,
    current.fulfilled_sales_eur,
    current.accepted_refunds_eur,
    current.net_revenue_eur,
    current.fulfilled_cogs_eur,
    current.recovered_cogs_eur,
    current.net_cogs_eur,
    current.net_gross_profit_eur,
    CASE
        WHEN current.net_revenue_eur > 0 THEN round(
            current.net_gross_profit_eur::DECIMAL(38, 8)
                / current.net_revenue_eur::DECIMAL(38, 8),
            6
        )::DECIMAL(18, 6)
        ELSE NULL
    END AS gross_margin_rate,
    CASE
        WHEN current.net_revenue_eur <= 0 THEN 'nonpositive_revenue'
        ELSE NULL
    END AS gross_margin_reason,
    current.net_vat_eur,
    current.outbound_freight_eur,
    current.returns_freight_eur,
    current.contribution_after_freight_eur,
    prior.net_revenue_eur AS prior_year_net_revenue_eur,
    CASE
        WHEN prior.reporting_month IS NULL OR prior.net_revenue_eur <= 0 THEN NULL
        ELSE round(
            current.net_revenue_eur::DECIMAL(38, 8)
                / prior.net_revenue_eur::DECIMAL(38, 8) - 1,
            6
        )::DECIMAL(18, 6)
    END AS revenue_yoy_rate,
    CASE
        WHEN prior.reporting_month IS NULL THEN 'no_prior_comparable_period'
        WHEN prior.net_revenue_eur <= 0 THEN 'nonpositive_prior_revenue'
        ELSE NULL
    END AS revenue_yoy_reason,
    true AS is_complete_period,
    lineage.as_of_date,
    'company'::VARCHAR AS report_country_role,
    'posting_date'::VARCHAR AS date_basis,
    'EUR'::VARCHAR AS currency_code,
    'observed'::VARCHAR AS value_basis,
    'company-month-v1'::VARCHAR AS method_version,
    lineage.source_batch_id,
    lineage.source_warehouse_manifest_sha256
FROM complete AS current
CROSS JOIN build_lineage AS lineage
JOIN warehouse.dim_date AS date ON date.full_date = current.reporting_month
LEFT JOIN complete AS prior
    ON prior.reporting_month = current.reporting_month - INTERVAL 1 YEAR
ORDER BY current.reporting_month;

CREATE TABLE mart_target_achievement_month AS
WITH posting_actual AS (
    SELECT
        date.month_start_date AS target_month,
        posting.home_country_code,
        posting.credited_salesperson_id,
        sum(posting.net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
        sum(posting.net_gross_profit_eur)::DECIMAL(18, 2) AS gross_profit_eur
    FROM warehouse.fact_financial_posting_historical AS posting
    JOIN warehouse.dim_date AS date ON date.date_key = posting.posting_date_key
    GROUP BY ALL
), target_company AS (
    SELECT target_month, metric_code, sum(target_value)::DECIMAL(18, 2) AS target_value
    FROM warehouse.fact_target_allocation
    WHERE target_month <= (SELECT as_of_date FROM build_lineage)
    GROUP BY target_month, metric_code
), target_country AS (
    SELECT
        target_month,
        metric_code,
        country_code,
        sum(target_value)::DECIMAL(18, 2) AS target_value
    FROM warehouse.fact_target_allocation
    WHERE target_month <= (SELECT as_of_date FROM build_lineage)
    GROUP BY target_month, metric_code, country_code
), target_salesperson AS (
    SELECT
        target_month,
        metric_code,
        country_code,
        salesperson_id,
        sum(target_value)::DECIMAL(18, 2) AS target_value
    FROM warehouse.fact_target_allocation
    WHERE allocation_type = 'salesperson'
      AND target_month <= (SELECT as_of_date FROM build_lineage)
    GROUP BY target_month, metric_code, country_code, salesperson_id
), actual_company AS (
    SELECT
        target_month,
        sum(net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
        sum(gross_profit_eur)::DECIMAL(18, 2) AS gross_profit_eur
    FROM posting_actual
    GROUP BY target_month
), actual_country AS (
    SELECT
        target_month,
        home_country_code AS country_code,
        sum(net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
        sum(gross_profit_eur)::DECIMAL(18, 2) AS gross_profit_eur
    FROM posting_actual
    GROUP BY target_month, home_country_code
), actual_salesperson AS (
    SELECT
        target_month,
        credited_salesperson_id AS salesperson_id,
        sum(net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
        sum(gross_profit_eur)::DECIMAL(18, 2) AS gross_profit_eur
    FROM posting_actual
    WHERE credited_salesperson_id IS NOT NULL
    GROUP BY target_month, credited_salesperson_id
), scoped AS (
    SELECT
        target.target_month,
        target.metric_code,
        'company'::VARCHAR AS scope_type,
        'COMPANY'::VARCHAR AS scope_key,
        NULL::VARCHAR AS country_code,
        NULL::VARCHAR AS salesperson_id,
        target.target_value,
        CASE target.metric_code
            WHEN 'net_revenue_eur' THEN coalesce(actual.net_revenue_eur, 0)
            WHEN 'gross_profit_eur' THEN coalesce(actual.gross_profit_eur, 0)
        END::DECIMAL(18, 2) AS actual_value,
        CASE target.metric_code
            WHEN 'net_revenue_eur' THEN (
                coalesce(actual.net_revenue_eur, 0)
                - coalesce(credited.net_revenue_eur, 0)
            )
            WHEN 'gross_profit_eur' THEN (
                coalesce(actual.gross_profit_eur, 0)
                - coalesce(credited.gross_profit_eur, 0)
            )
        END::DECIMAL(18, 2) AS uncredited_actual_value
    FROM target_company AS target
    LEFT JOIN actual_company AS actual USING (target_month)
    LEFT JOIN (
        SELECT
            target_month,
            sum(net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
            sum(gross_profit_eur)::DECIMAL(18, 2) AS gross_profit_eur
        FROM actual_salesperson GROUP BY target_month
    ) AS credited USING (target_month)

    UNION ALL

    SELECT
        target.target_month,
        target.metric_code,
        'country'::VARCHAR AS scope_type,
        'COUNTRY:' || target.country_code AS scope_key,
        target.country_code,
        NULL::VARCHAR AS salesperson_id,
        target.target_value,
        CASE target.metric_code
            WHEN 'net_revenue_eur' THEN coalesce(actual.net_revenue_eur, 0)
            WHEN 'gross_profit_eur' THEN coalesce(actual.gross_profit_eur, 0)
        END::DECIMAL(18, 2) AS actual_value,
        CASE target.metric_code
            WHEN 'net_revenue_eur' THEN (
                coalesce(actual.net_revenue_eur, 0)
                - coalesce(credited.net_revenue_eur, 0)
            )
            WHEN 'gross_profit_eur' THEN (
                coalesce(actual.gross_profit_eur, 0)
                - coalesce(credited.gross_profit_eur, 0)
            )
        END::DECIMAL(18, 2) AS uncredited_actual_value
    FROM target_country AS target
    LEFT JOIN actual_country AS actual
        ON actual.target_month = target.target_month
       AND actual.country_code = target.country_code
    LEFT JOIN (
        SELECT
            target.target_month,
            target.country_code,
            target.metric_code,
            sum(coalesce(actual.net_revenue_eur, 0))::DECIMAL(18, 2) AS net_revenue_eur,
            sum(coalesce(actual.gross_profit_eur, 0))::DECIMAL(18, 2) AS gross_profit_eur
        FROM target_salesperson AS target
        LEFT JOIN actual_salesperson AS actual
            ON actual.target_month = target.target_month
           AND actual.salesperson_id = target.salesperson_id
        GROUP BY target.target_month, target.country_code, target.metric_code
    ) AS credited
        ON credited.target_month = target.target_month
       AND credited.country_code = target.country_code
       AND credited.metric_code = target.metric_code

    UNION ALL

    SELECT
        target.target_month,
        target.metric_code,
        'salesperson'::VARCHAR AS scope_type,
        'SALESPERSON:' || target.salesperson_id AS scope_key,
        target.country_code,
        target.salesperson_id,
        target.target_value,
        CASE target.metric_code
            WHEN 'net_revenue_eur' THEN coalesce(actual.net_revenue_eur, 0)
            WHEN 'gross_profit_eur' THEN coalesce(actual.gross_profit_eur, 0)
        END::DECIMAL(18, 2) AS actual_value,
        NULL::DECIMAL(18, 2) AS uncredited_actual_value
    FROM target_salesperson AS target
    LEFT JOIN actual_salesperson AS actual
        ON actual.target_month = target.target_month
       AND actual.salesperson_id = target.salesperson_id
)
SELECT
    scoped.target_month,
    date.calendar_year,
    date.calendar_quarter,
    date.calendar_month,
    scoped.metric_code,
    scoped.scope_type,
    scoped.scope_key,
    scoped.country_code,
    geography.country_name,
    scoped.salesperson_id,
    salesperson.salesperson_name,
    scoped.target_value,
    scoped.actual_value,
    scoped.uncredited_actual_value,
    CASE
        WHEN scoped.target_value != 0 THEN round(
            scoped.actual_value::DECIMAL(38, 8) / scoped.target_value::DECIMAL(38, 8),
            6
        )::DECIMAL(18, 6)
        ELSE NULL
    END AS attainment_rate,
    CASE WHEN scoped.target_value = 0 THEN 'zero_target' ELSE NULL END AS attainment_reason,
    true AS is_complete_period,
    lineage.as_of_date,
    CASE
        WHEN scoped.scope_type = 'country' THEN 'customer_home_at_posting'
        WHEN scoped.scope_type = 'salesperson' THEN 'credited_salesperson'
        ELSE 'company'
    END::VARCHAR AS report_country_role,
    'posting_date'::VARCHAR AS date_basis,
    'EUR'::VARCHAR AS currency_code,
    'observed_actual_vs_plan'::VARCHAR AS value_basis,
    'target-achievement-v1'::VARCHAR AS method_version,
    lineage.source_batch_id,
    lineage.source_warehouse_manifest_sha256
FROM scoped
CROSS JOIN build_lineage AS lineage
JOIN warehouse.dim_date AS date ON date.full_date = scoped.target_month
LEFT JOIN (
    SELECT DISTINCT country_code, country_name
    FROM warehouse.dim_geography
) AS geography USING (country_code)
LEFT JOIN warehouse.dim_salesperson AS salesperson USING (salesperson_id)
ORDER BY scoped.target_month, scoped.metric_code, scoped.scope_type, scoped.scope_key;
