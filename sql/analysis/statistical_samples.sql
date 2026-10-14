-- Narrow, temporary study samples. Attached upstream databases are READ_ONLY.
CREATE TEMP TABLE stat_discount_candidates AS
WITH prior_month AS (
 SELECT customer_id,reporting_month,
        sum(posted_list_value_eur)::DECIMAL(18,2) list_eur,
        sum(posted_discount_eur)::DECIMAL(18,2) discount_eur
 FROM gold.mart_commercial_month GROUP BY 1,2
), history AS (
 SELECT customer_id,reporting_month,
        sum(list_eur) OVER(PARTITION BY customer_id ORDER BY reporting_month
          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)::DECIMAL(18,2) prior_list_eur,
        sum(discount_eur) OVER(PARTITION BY customer_id ORDER BY reporting_month
          ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING)::DECIMAL(18,2) prior_discount_eur
 FROM prior_month
), cells AS (
 SELECT g.customer_id,g.reporting_month,g.category_id,g.segment_code,
        g.home_country_code,min(c.acquisition_date) AS acquisition_date,
        sum(g.posted_list_value_eur)::DECIMAL(18,2) current_list_eur,
        sum(g.posted_discount_eur)::DECIMAL(18,2) current_discount_eur,
        sum(g.net_revenue_eur)::DECIMAL(18,2) net_revenue_eur,
        sum(g.net_gross_profit_eur)::DECIMAL(18,2) net_gross_profit_eur,
        count(*)::BIGINT source_gold_rows
 FROM gold.mart_commercial_month g
 JOIN warehouse.dim_customer c USING(customer_sk)
 CROSS JOIN build_parameters p
 WHERE g.reporting_month BETWEEN p.period_start AND p.as_of_month
 GROUP BY 1,2,3,4,5
)
SELECT x.*,h.prior_list_eur,h.prior_discount_eur,
       CASE WHEN h.prior_list_eur>0 THEN
         (h.prior_discount_eur::DOUBLE/h.prior_list_eur::DOUBLE) END AS prior_account_discount_rate,
       CASE WHEN x.current_list_eur>0 THEN
         (x.current_discount_eur::DOUBLE/x.current_list_eur::DOUBLE) END AS current_cell_discount_rate,
       CASE WHEN x.net_revenue_eur>0 THEN
         (x.net_gross_profit_eur::DOUBLE/x.net_revenue_eur::DOUBLE) END AS realised_margin_rate,
       date_diff('month',x.acquisition_date,x.reporting_month)::INTEGER tenure_months
FROM cells x LEFT JOIN history h ON h.customer_id=x.customer_id
 AND h.reporting_month=x.reporting_month
ORDER BY x.customer_id,x.reporting_month,x.category_id,x.segment_code,x.home_country_code;

CREATE TEMP TABLE stat_market_month AS
SELECT g.reporting_month,g.home_country_code,
       sum(g.fulfilled_sales_eur)::DECIMAL(18,2) fulfilled_sales_eur,
       sum(g.net_revenue_eur)::DECIMAL(18,2) net_revenue_eur,
       sum(g.net_gross_profit_eur)::DECIMAL(18,2) net_gross_profit_eur
FROM gold.mart_commercial_month g CROSS JOIN build_parameters p
WHERE g.reporting_month BETWEEN p.prior_start_month AND p.as_of_month
GROUP BY 1,2 ORDER BY 1,2;

CREATE TEMP TABLE stat_ee_sku_period AS
SELECT CASE WHEN s.fulfilled_date<p.current_start_month THEN 'prior' ELSE 'current' END period,
       s.product_id,sum(s.quantity_ordered)::BIGINT units,
       sum(s.posted_net_sales_eur)::DECIMAL(18,2) fulfilled_sales_eur,
       sum(s.posted_cogs_eur)::DECIMAL(18,2) fulfilled_cogs_eur
FROM warehouse.fact_order_line s
JOIN warehouse.dim_customer c ON c.customer_sk=s.customer_sk_fulfilment
CROSS JOIN build_parameters p
WHERE s.order_status='fulfilled' AND c.home_country_code='EE'
  AND s.fulfilled_date BETWEEN p.prior_start_month AND p.as_of_date
GROUP BY 1,2 ORDER BY 1,2;

CREATE TEMP TABLE stat_retention_candidates AS
WITH first_purchase AS (
 SELECT customer_id,first_purchase_cohort_month
 FROM customer.mart_customer_value WHERE first_purchase_cohort_month IS NOT NULL
), landmarks AS (
 SELECT f.customer_id,f.first_purchase_cohort_month,
        (f.first_purchase_cohort_month+n.n*INTERVAL '12 months')::DATE landmark_month
 FROM first_purchase f CROSS JOIN range(1,5) n(n)
), eligible AS (
 SELECT l.*,c.segment_code,c.home_country_code,
        date_diff('month',l.first_purchase_cohort_month,l.landmark_month)::INTEGER tenure_months,
        ((l.landmark_month+INTERVAL '12 months')::DATE<=p.as_of_date+1)::BOOLEAN outcome_mature
 FROM landmarks l JOIN warehouse.dim_customer c
   ON c.customer_id=l.customer_id AND c.valid_from<=l.landmark_month
   AND (c.valid_to IS NULL OR c.valid_to>l.landmark_month)
 CROSS JOIN build_parameters p WHERE l.landmark_month<=p.as_of_date
), orders AS (
 SELECT customer_id,order_id,min(fulfilled_date) AS fulfilled_date
 FROM warehouse.fact_order_line s CROSS JOIN build_parameters p
 WHERE order_status='fulfilled' AND fulfilled_date BETWEEN p.period_start AND p.as_of_date
 GROUP BY customer_id,order_id
)
SELECT e.customer_id,e.first_purchase_cohort_month,e.landmark_month,e.segment_code,
       e.home_country_code,e.tenure_months,e.outcome_mature,
       count(DISTINCT o.order_id) FILTER(WHERE o.fulfilled_date<e.landmark_month)::INTEGER
         AS prior_12m_orders,
       CASE WHEN e.outcome_mature THEN
         (count(DISTINCT o.order_id) FILTER(WHERE o.fulfilled_date>=e.landmark_month)>0)::INTEGER
       END AS retained_next_12m
FROM eligible e LEFT JOIN orders o ON o.customer_id=e.customer_id
 AND o.fulfilled_date>=e.landmark_month-INTERVAL '12 months'
 AND o.fulfilled_date<e.landmark_month+INTERVAL '12 months'
GROUP BY 1,2,3,4,5,6,7 ORDER BY e.customer_id,e.landmark_month;

CREATE TEMP TABLE stat_basket_membership AS
SELECT DISTINCT s.order_id,s.customer_id,p.subcategory_id
FROM warehouse.fact_order_line s JOIN warehouse.dim_product p USING(product_id)
CROSS JOIN build_parameters b
WHERE s.order_status='fulfilled' AND s.fulfilled_date BETWEEN b.period_start AND b.as_of_date
ORDER BY s.order_id,s.customer_id,p.subcategory_id;
