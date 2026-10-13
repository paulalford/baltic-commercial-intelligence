-- Milestone 7 transaction facts and canonical signed posting views.

CREATE TABLE fact_order_line AS
WITH line_base AS (
    SELECT
        line.*,
        orders.customer_id,
        orders.order_date,
        orders.fulfilled_date,
        orders.cancelled_date,
        orders.channel_code,
        orders.order_status,
        orders.ship_to_city_id,
        orders.credited_salesperson_id,
        orders.payment_terms_code,
        orders.outbound_freight_eur,
        row_number() OVER (
            PARTITION BY line.order_id ORDER BY line.line_number, line.order_line_id
        ) AS freight_line_number,
        count(*) OVER (PARTITION BY line.order_id) AS freight_line_count,
        sum(line.posted_net_sales_eur) OVER (PARTITION BY line.order_id) AS order_net_sales_eur,
        sum(line.quantity_ordered) OVER (PARTITION BY line.order_id) AS order_quantity
    FROM silver_order_lines AS line
    JOIN silver_orders AS orders USING (order_id)
), preliminary_freight AS (
    SELECT
        *,
        CASE
            WHEN order_status != 'fulfilled' THEN 0::DECIMAL(18, 2)
            WHEN freight_line_number = freight_line_count THEN 0::DECIMAL(18, 2)
            WHEN order_net_sales_eur != 0 THEN round_even(
                outbound_freight_eur::DECIMAL(38, 8)
                    * posted_net_sales_eur::DECIMAL(38, 8)
                    / order_net_sales_eur::DECIMAL(38, 8),
                2
            )::DECIMAL(18, 2)
            ELSE round_even(
                outbound_freight_eur::DECIMAL(38, 8)
                    * quantity_ordered::DECIMAL(38, 8)
                    / order_quantity::DECIMAL(38, 8),
                2
            )::DECIMAL(18, 2)
        END AS preliminary_outbound_freight_eur
    FROM line_base
), allocated_freight AS (
    SELECT
        *,
        CASE
            WHEN order_status != 'fulfilled' THEN 0::DECIMAL(18, 2)
            WHEN freight_line_number = freight_line_count THEN (
                outbound_freight_eur
                - sum(preliminary_outbound_freight_eur) OVER (PARTITION BY order_id)
            )::DECIMAL(18, 2)
            ELSE preliminary_outbound_freight_eur
        END AS allocated_outbound_freight_eur
    FROM preliminary_freight
)
SELECT
    line.order_line_id,
    lineage.source_batch_id,
    lineage.source_silver_manifest_sha256,
    line.order_id,
    line.line_number,
    line.customer_id,
    customer_order.customer_sk AS customer_sk_order,
    customer_fulfilment.customer_sk AS customer_sk_fulfilment,
    order_date.date_key AS order_date_key,
    fulfilled_date.date_key AS fulfilled_date_key,
    cancelled_date.date_key AS cancelled_date_key,
    line.order_date,
    line.fulfilled_date,
    line.cancelled_date,
    line.order_status,
    line.product_id,
    line.channel_code,
    line.ship_to_city_id,
    line.credited_salesperson_id,
    line.payment_terms_code,
    line.quantity_ordered,
    line.list_unit_price_eur,
    line.discount_rate,
    line.realised_unit_price_eur,
    line.unit_cost_eur,
    line.posted_list_value_eur,
    line.posted_discount_eur,
    line.posted_net_sales_eur,
    line.posted_cogs_eur,
    (line.posted_net_sales_eur - line.posted_cogs_eur)::DECIMAL(18, 2)
        AS posted_gross_profit_eur,
    line.tax_jurisdiction_code,
    line.vat_treatment_code,
    line.vat_rate_fraction,
    line.posted_taxable_base_eur,
    line.posted_vat_amount_eur,
    line.posted_invoice_total_eur,
    line.allocated_outbound_freight_eur,
    (
        line.posted_net_sales_eur
        - line.posted_cogs_eur
        - line.allocated_outbound_freight_eur
    )::DECIMAL(18, 2) AS sale_contribution_after_freight_eur
FROM allocated_freight AS line
CROSS JOIN build_lineage AS lineage
JOIN dim_date AS order_date ON order_date.full_date = line.order_date
LEFT JOIN dim_date AS fulfilled_date ON fulfilled_date.full_date = line.fulfilled_date
LEFT JOIN dim_date AS cancelled_date ON cancelled_date.full_date = line.cancelled_date
JOIN dim_customer AS customer_order
    ON customer_order.customer_id = line.customer_id
    AND line.order_date >= customer_order.valid_from
    AND (customer_order.valid_to IS NULL OR line.order_date < customer_order.valid_to)
LEFT JOIN dim_customer AS customer_fulfilment
    ON line.order_status = 'fulfilled'
    AND customer_fulfilment.customer_id = line.customer_id
    AND line.fulfilled_date >= customer_fulfilment.valid_from
    AND (
        customer_fulfilment.valid_to IS NULL
        OR line.fulfilled_date < customer_fulfilment.valid_to
    )
ORDER BY line.order_line_id;

CREATE TABLE fact_return_event AS
SELECT
    returns.return_id,
    lineage.source_batch_id,
    lineage.source_silver_manifest_sha256,
    returns.order_line_id,
    sale.order_id,
    sale.customer_id,
    CASE
        WHEN returns.return_status = 'accepted' THEN customer_return.customer_sk
        ELSE NULL
    END AS customer_sk_return,
    sale.product_id,
    sale.channel_code,
    sale.ship_to_city_id,
    sale.credited_salesperson_id,
    requested_date.date_key AS return_requested_date_key,
    decision_date.date_key AS return_decision_date_key,
    returns.return_requested_date,
    returns.return_decision_date,
    returns.requested_quantity,
    returns.accepted_quantity,
    returns.return_status,
    returns.return_reason_code,
    returns.restocking_outcome_code,
    returns.refund_unit_ex_vat_eur,
    returns.posted_refund_ex_vat_eur,
    returns.posted_recovered_cogs_eur,
    returns.posted_vat_reversal_eur,
    returns.returns_freight_eur,
    (
        -returns.posted_refund_ex_vat_eur + returns.posted_recovered_cogs_eur
    )::DECIMAL(18, 2) AS return_gross_profit_effect_eur,
    (
        -returns.posted_refund_ex_vat_eur
        + returns.posted_recovered_cogs_eur
        - returns.returns_freight_eur
    )::DECIMAL(18, 2) AS return_contribution_after_freight_effect_eur
FROM silver_returns AS returns
CROSS JOIN build_lineage AS lineage
JOIN fact_order_line AS sale USING (order_line_id)
JOIN dim_date AS requested_date ON requested_date.full_date = returns.return_requested_date
LEFT JOIN dim_date AS decision_date ON decision_date.full_date = returns.return_decision_date
LEFT JOIN dim_customer AS customer_return
    ON returns.return_status = 'accepted'
    AND customer_return.customer_id = sale.customer_id
    AND returns.return_decision_date >= customer_return.valid_from
    AND (
        customer_return.valid_to IS NULL
        OR returns.return_decision_date < customer_return.valid_to
    )
ORDER BY returns.return_id;

CREATE TABLE fact_target_allocation AS
SELECT
    target.target_id,
    lineage.source_batch_id,
    lineage.source_silver_manifest_sha256,
    target.target_month,
    date.date_key AS target_month_date_key,
    target.metric_code,
    target.allocation_type,
    target.salesperson_id,
    target.country_code,
    target.target_value
FROM silver_sales_targets AS target
CROSS JOIN build_lineage AS lineage
JOIN dim_date AS date ON date.full_date = target.target_month
ORDER BY target.target_month, target.metric_code, target.target_id;

CREATE VIEW fact_financial_posting AS
SELECT
    'SALE:' || sale.order_line_id AS posting_id,
    'fulfilled_sale' AS posting_type,
    sale.fulfilled_date AS posting_date,
    sale.fulfilled_date_key AS posting_date_key,
    sale.order_id,
    sale.order_line_id,
    NULL::VARCHAR AS return_id,
    sale.customer_id,
    sale.customer_sk_fulfilment AS customer_sk,
    customer.home_city_id,
    customer.home_country_code,
    sale.product_id,
    sale.channel_code,
    sale.ship_to_city_id,
    sale.credited_salesperson_id,
    sale.posted_list_value_eur,
    sale.posted_discount_eur,
    sale.posted_net_sales_eur AS fulfilled_sales_eur,
    0::DECIMAL(18, 2) AS accepted_refunds_eur,
    sale.posted_net_sales_eur AS net_revenue_eur,
    sale.posted_cogs_eur AS fulfilled_cogs_eur,
    0::DECIMAL(18, 2) AS recovered_cogs_eur,
    sale.posted_cogs_eur AS net_cogs_eur,
    sale.posted_gross_profit_eur AS net_gross_profit_eur,
    sale.posted_vat_amount_eur AS net_vat_eur,
    sale.allocated_outbound_freight_eur AS outbound_freight_eur,
    0::DECIMAL(18, 2) AS returns_freight_eur,
    sale.sale_contribution_after_freight_eur AS contribution_after_freight_eur
FROM fact_order_line AS sale
JOIN dim_customer AS customer ON customer.customer_sk = sale.customer_sk_fulfilment
WHERE sale.order_status = 'fulfilled'

UNION ALL

SELECT
    'RETURN:' || returns.return_id AS posting_id,
    'accepted_return' AS posting_type,
    returns.return_decision_date AS posting_date,
    returns.return_decision_date_key AS posting_date_key,
    returns.order_id,
    returns.order_line_id,
    returns.return_id,
    returns.customer_id,
    returns.customer_sk_return AS customer_sk,
    customer.home_city_id,
    customer.home_country_code,
    returns.product_id,
    returns.channel_code,
    returns.ship_to_city_id,
    returns.credited_salesperson_id,
    0::DECIMAL(18, 2) AS posted_list_value_eur,
    0::DECIMAL(18, 2) AS posted_discount_eur,
    0::DECIMAL(18, 2) AS fulfilled_sales_eur,
    returns.posted_refund_ex_vat_eur AS accepted_refunds_eur,
    -returns.posted_refund_ex_vat_eur AS net_revenue_eur,
    0::DECIMAL(18, 2) AS fulfilled_cogs_eur,
    returns.posted_recovered_cogs_eur AS recovered_cogs_eur,
    -returns.posted_recovered_cogs_eur AS net_cogs_eur,
    returns.return_gross_profit_effect_eur AS net_gross_profit_eur,
    -returns.posted_vat_reversal_eur AS net_vat_eur,
    0::DECIMAL(18, 2) AS outbound_freight_eur,
    returns.returns_freight_eur,
    returns.return_contribution_after_freight_effect_eur AS contribution_after_freight_eur
FROM fact_return_event AS returns
JOIN dim_customer AS customer ON customer.customer_sk = returns.customer_sk_return
WHERE returns.return_status = 'accepted';

CREATE VIEW fact_financial_posting_historical AS
SELECT posting.*
FROM fact_financial_posting AS posting
JOIN dim_date AS date ON date.date_key = posting.posting_date_key
WHERE date.is_within_reporting_window;
