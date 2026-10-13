-- Milestone 10. All finance uses posted cents in the canonical historical ledger.
CREATE TABLE mart_product_month_country AS
SELECT p.product_id, p.sku, p.category_id, p.category_name,
       g.reporting_month, g.home_country_code, 'customer_home_at_posting'::VARCHAR AS country_role,
       sum(g.fulfilled_sales_eur)::DECIMAL(18,2) AS fulfilled_sales_eur,
       sum(g.accepted_refunds_eur)::DECIMAL(18,2) AS accepted_refunds_eur,
       sum(g.net_revenue_eur)::DECIMAL(18,2) AS net_revenue_eur,
       sum(g.fulfilled_cogs_eur)::DECIMAL(18,2) AS fulfilled_cogs_eur,
       sum(g.recovered_cogs_eur)::DECIMAL(18,2) AS recovered_cogs_eur,
       sum(g.net_cogs_eur)::DECIMAL(18,2) AS net_cogs_eur,
       sum(g.net_gross_profit_eur)::DECIMAL(18,2) AS net_gross_profit_eur,
       sum(g.outbound_freight_eur)::DECIMAL(18,2) AS outbound_freight_eur,
       sum(g.returns_freight_eur)::DECIMAL(18,2) AS returns_freight_eur,
       sum(g.contribution_after_freight_eur)::DECIMAL(18,2) AS contribution_after_freight_eur,
       CASE WHEN sum(g.net_revenue_eur)>0 THEN
         round(sum(g.net_gross_profit_eur)::DECIMAL(38,8)/sum(g.net_revenue_eur)::DECIMAL(38,8),6)::DECIMAL(18,6)
       END AS gross_margin_rate,
       l.as_of_date, l.source_batch_id, l.source_warehouse_manifest_sha256, l.source_gold_manifest_sha256
FROM gold.mart_commercial_month g JOIN warehouse.dim_product p USING(product_id)
CROSS JOIN build_lineage l
WHERE g.reporting_month BETWEEN l.period_start AND l.as_of_month
GROUP BY ALL ORDER BY g.reporting_month, g.home_country_code, p.product_id;

CREATE TABLE mart_product_profitability AS
WITH finance AS (
 SELECT product_id, sum(fulfilled_sales_eur)::DECIMAL(18,2) fulfilled_sales_eur,
        sum(accepted_refunds_eur)::DECIMAL(18,2) accepted_refunds_eur,
        sum(net_revenue_eur)::DECIMAL(18,2) net_revenue_eur,
        sum(fulfilled_cogs_eur)::DECIMAL(18,2) fulfilled_cogs_eur,
        sum(recovered_cogs_eur)::DECIMAL(18,2) recovered_cogs_eur,
        sum(net_cogs_eur)::DECIMAL(18,2) net_cogs_eur,
        sum(net_gross_profit_eur)::DECIMAL(18,2) net_gross_profit_eur,
        sum(outbound_freight_eur)::DECIMAL(18,2) outbound_freight_eur,
        sum(returns_freight_eur)::DECIMAL(18,2) returns_freight_eur,
        sum(contribution_after_freight_eur)::DECIMAL(18,2) contribution_after_freight_eur
 FROM warehouse.fact_financial_posting_historical GROUP BY product_id
), primary_supplier AS (
 SELECT b.product_id, b.supplier_id, s.supplier_name
 FROM warehouse.bridge_product_supplier b JOIN warehouse.dim_supplier s USING(supplier_id)
 CROSS JOIN build_lineage l
 WHERE b.relationship_type='primary' AND b.valid_from<=l.as_of_date
   AND (b.valid_to IS NULL OR b.valid_to>l.as_of_date)
), units AS (
 SELECT product_id, sum(quantity_ordered)::BIGINT fulfilled_units
 FROM warehouse.fact_order_line, build_lineage l
 WHERE order_status='fulfilled' AND fulfilled_date BETWEEN l.period_start AND l.as_of_date
 GROUP BY product_id
), accepted AS (
 SELECT product_id, sum(accepted_quantity)::BIGINT accepted_return_units,
        count(*)::BIGINT accepted_return_events
 FROM warehouse.fact_return_event, build_lineage l
 WHERE return_status='accepted' AND return_decision_date BETWEEN l.period_start AND l.as_of_date
 GROUP BY product_id
)
SELECT p.product_id,p.sku,p.product_name,p.subcategory_id,p.subcategory_name,p.category_id,p.category_name,
       p.launch_date,p.discontinued_date,p.lifecycle_status,
       s.supplier_id AS approved_primary_supplier_id_as_of,
       s.supplier_name AS approved_primary_supplier_name_as_of,
       'unknown_transaction_supplier'::VARCHAR AS actual_sale_supplier_status,
       coalesce(u.fulfilled_units,0)::BIGINT fulfilled_units,
       coalesce(a.accepted_return_units,0)::BIGINT accepted_return_units,
       coalesce(a.accepted_return_events,0)::BIGINT accepted_return_events,
       coalesce(f.fulfilled_sales_eur,0)::DECIMAL(18,2) fulfilled_sales_eur,
       coalesce(f.accepted_refunds_eur,0)::DECIMAL(18,2) accepted_refunds_eur,
       coalesce(f.net_revenue_eur,0)::DECIMAL(18,2) net_revenue_eur,
       coalesce(f.fulfilled_cogs_eur,0)::DECIMAL(18,2) fulfilled_cogs_eur,
       coalesce(f.recovered_cogs_eur,0)::DECIMAL(18,2) recovered_cogs_eur,
       coalesce(f.net_cogs_eur,0)::DECIMAL(18,2) net_cogs_eur,
       coalesce(f.net_gross_profit_eur,0)::DECIMAL(18,2) net_gross_profit_eur,
       coalesce(f.outbound_freight_eur,0)::DECIMAL(18,2) outbound_freight_eur,
       coalesce(f.returns_freight_eur,0)::DECIMAL(18,2) returns_freight_eur,
       coalesce(f.contribution_after_freight_eur,0)::DECIMAL(18,2) contribution_after_freight_eur,
       CASE WHEN f.net_revenue_eur>0 THEN
          round(f.net_gross_profit_eur::DECIMAL(38,8)/f.net_revenue_eur::DECIMAL(38,8),6)::DECIMAL(18,6)
       END AS gross_margin_rate,
       CASE WHEN coalesce(f.net_revenue_eur,0)<=0 THEN 'nonpositive_net_revenue' END AS margin_reason,
       NULL::DECIMAL(18,6) AS mature_return_unit_rate,
       'maturity_duration_not_approved'::VARCHAR AS return_rate_reason,
       l.period_start AS observation_start_date,l.as_of_date,l.source_batch_id,
       l.source_warehouse_manifest_sha256,l.source_gold_manifest_sha256
FROM warehouse.dim_product p CROSS JOIN build_lineage l
LEFT JOIN finance f USING(product_id) LEFT JOIN units u USING(product_id)
LEFT JOIN accepted a USING(product_id) LEFT JOIN primary_supplier s USING(product_id)
WHERE p.launch_date<=l.as_of_date
ORDER BY p.product_id;

CREATE TABLE mart_product_abc_xyz AS
WITH trailing_finance AS (
 SELECT p.product_id,
        coalesce(sum(g.net_gross_profit_eur),0)::DECIMAL(18,2) trailing_gp_eur
 FROM warehouse.dim_product p CROSS JOIN build_lineage l
 LEFT JOIN gold.mart_commercial_month g ON g.product_id=p.product_id
      AND g.reporting_month BETWEEN l.trailing_start_month AND l.as_of_month
 WHERE p.launch_date<=l.as_of_date
 GROUP BY p.product_id
), positive AS (
 SELECT *, sum(trailing_gp_eur) FILTER(WHERE trailing_gp_eur>0) OVER()::DECIMAL(18,2) positive_total_eur
 FROM trailing_finance
), groups AS (
 SELECT trailing_gp_eur, count(*)::BIGINT tied_products,
        sum(trailing_gp_eur)::DECIMAL(18,2) tie_gp_eur
 FROM positive GROUP BY trailing_gp_eur
), ranked_groups AS (
 SELECT *, coalesce(sum(tie_gp_eur) FILTER(WHERE trailing_gp_eur>0) OVER(
        ORDER BY trailing_gp_eur DESC ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),0)::DECIMAL(18,2) preceding_gp_eur
 FROM groups
), ranked AS (
 SELECT p.*,g.tie_gp_eur,g.preceding_gp_eur,
        row_number() OVER(ORDER BY p.trailing_gp_eur DESC,p.product_id) abc_rank
 FROM positive p JOIN ranked_groups g USING(trailing_gp_eur)
), abc AS (
 SELECT product_id,trailing_gp_eur,positive_total_eur,abc_rank,
        CASE WHEN trailing_gp_eur>0 AND positive_total_eur>0 THEN
          round(trailing_gp_eur::DECIMAL(38,8)/positive_total_eur,8)::DECIMAL(18,8) END abc_share,
        CASE WHEN trailing_gp_eur>0 AND positive_total_eur>0 THEN
          round(preceding_gp_eur::DECIMAL(38,8)/positive_total_eur,8)::DECIMAL(18,8) END abc_cumulative_before,
        CASE WHEN trailing_gp_eur>0 AND positive_total_eur>0 THEN
          round((preceding_gp_eur+tie_gp_eur)::DECIMAL(38,8)/positive_total_eur,8)::DECIMAL(18,8) END abc_cumulative_after_tie,
        CASE WHEN trailing_gp_eur<=0 OR positive_total_eur IS NULL THEN 'nonpositive'
             WHEN preceding_gp_eur::DECIMAL(38,8)/positive_total_eur<l.abc_a THEN 'A'
             WHEN preceding_gp_eur::DECIMAL(38,8)/positive_total_eur<l.abc_b THEN 'B'
             ELSE 'C' END abc_class
 FROM ranked CROSS JOIN build_lineage l
), months AS (
 SELECT p.product_id, m.month_start_date,
        coalesce(sum(s.quantity_ordered),0)::BIGINT units
 FROM warehouse.dim_product p CROSS JOIN build_lineage l
 JOIN warehouse.dim_date m ON m.full_date=m.month_start_date
   AND m.month_start_date BETWEEN l.trailing_start_month AND l.as_of_month
   AND p.launch_date<=m.month_start_date
   AND (p.discontinued_date IS NULL OR p.discontinued_date>last_day(m.month_start_date))
 LEFT JOIN warehouse.fact_order_line s ON s.product_id=p.product_id
      AND s.order_status='fulfilled' AND s.fulfilled_date>=m.month_start_date
      AND s.fulfilled_date<=last_day(m.month_start_date)
 GROUP BY p.product_id,m.month_start_date
), demand AS (
 SELECT product_id,count(*)::BIGINT eligible_months,
        sum(units)::BIGINT trailing_units,
        avg(units)::DECIMAL(18,6) mean_monthly_units,
        stddev_pop(units)::DECIMAL(18,6) stddev_monthly_units
 FROM months GROUP BY product_id
), xyz AS (
 SELECT p.product_id, coalesce(d.eligible_months,0)::BIGINT eligible_months,
        coalesce(d.trailing_units,0)::BIGINT trailing_units,
        d.mean_monthly_units,d.stddev_monthly_units,
        CASE WHEN d.mean_monthly_units>0 THEN
          round(d.stddev_monthly_units::DECIMAL(38,8)/d.mean_monthly_units,6)::DECIMAL(18,6) END cv,
        CASE WHEN coalesce(d.eligible_months,0)<l.xyz_min_months THEN 'insufficient_history'
             WHEN coalesce(d.mean_monthly_units,0)=0 THEN 'zero_mean'
             WHEN d.stddev_monthly_units/d.mean_monthly_units<=l.xyz_x THEN 'X'
             WHEN d.stddev_monthly_units/d.mean_monthly_units<=l.xyz_y THEN 'Y'
             ELSE 'Z' END xyz_class
 FROM warehouse.dim_product p CROSS JOIN build_lineage l
 LEFT JOIN demand d USING(product_id) WHERE p.launch_date<=l.as_of_date
)
SELECT a.product_id,p.sku,p.category_id,p.category_name,a.trailing_gp_eur,a.positive_total_eur,
       a.abc_rank,a.abc_share,a.abc_cumulative_before,a.abc_cumulative_after_tie,a.abc_class,
       x.eligible_months,x.trailing_units,x.mean_monthly_units,x.stddev_monthly_units,x.cv,x.xyz_class,
       CASE WHEN a.abc_class IN ('A','B','C') AND x.xyz_class IN ('X','Y','Z')
            THEN a.abc_class||x.xyz_class END portfolio_class,
       CASE WHEN a.abc_class='nonpositive' THEN 'nonpositive_gp'
            WHEN x.xyz_class IN ('insufficient_history','zero_mean') THEN x.xyz_class END not_estimated_reason,
       l.trailing_start_month AS observation_start_month,l.as_of_date,l.source_batch_id,
       l.source_warehouse_manifest_sha256,l.source_gold_manifest_sha256
FROM abc a JOIN xyz x USING(product_id) JOIN warehouse.dim_product p USING(product_id)
CROSS JOIN build_lineage l ORDER BY a.product_id;

CREATE TABLE mart_basket_rules AS
WITH baskets AS (
 SELECT DISTINCT s.order_id,p.subcategory_id
 FROM warehouse.fact_order_line s JOIN warehouse.dim_product p USING(product_id)
 CROSS JOIN build_lineage l
 WHERE s.order_status='fulfilled' AND s.fulfilled_date BETWEEN l.period_start AND l.as_of_date
), population AS (SELECT count(DISTINCT order_id)::BIGINT n FROM baskets),
items AS (SELECT subcategory_id,count(*)::BIGINT n FROM baskets GROUP BY subcategory_id),
pairs AS (
 SELECT a.subcategory_id item_a,b.subcategory_id item_b,count(*)::BIGINT joint_n
 FROM baskets a JOIN baskets b ON a.order_id=b.order_id AND a.subcategory_id<b.subcategory_id
 GROUP BY 1,2
)
SELECT q.item_a AS subcategory_a_id,q.item_b AS subcategory_b_id,
       n.n AS basket_count, ia.n AS item_a_baskets,ib.n AS item_b_baskets,q.joint_n AS joint_baskets,
       round(ia.n::DECIMAL(38,8)/n.n,8)::DECIMAL(18,8) item_a_support,
       round(ib.n::DECIMAL(38,8)/n.n,8)::DECIMAL(18,8) item_b_support,
       round(q.joint_n::DECIMAL(38,8)/n.n,8)::DECIMAL(18,8) pair_support,
       round(q.joint_n::DECIMAL(38,8)/ia.n,8)::DECIMAL(18,8) confidence_a_to_b,
       round(q.joint_n::DECIMAL(38,8)/ib.n,8)::DECIMAL(18,8) confidence_b_to_a,
       round((q.joint_n::DECIMAL(38,8)*n.n)/(ia.n::DECIMAL(38,8)*ib.n),8)::DECIMAL(18,8) lift,
       l.period_start observation_start_date,l.as_of_date,l.source_batch_id,
       l.source_warehouse_manifest_sha256,l.source_gold_manifest_sha256
FROM pairs q JOIN items ia ON ia.subcategory_id=q.item_a
JOIN items ib ON ib.subcategory_id=q.item_b CROSS JOIN population n CROSS JOIN build_lineage l
WHERE q.joint_n>=l.min_joint_baskets AND q.joint_n::DECIMAL(38,8)/n.n>=l.min_pair_support
ORDER BY q.item_a,q.item_b;

CREATE TABLE mart_basket_context AS
WITH basket_items AS (
 SELECT DISTINCT s.order_id,c.home_country_code,c.segment_code,p.subcategory_id
 FROM warehouse.fact_order_line s
 JOIN warehouse.dim_product p USING(product_id)
 JOIN warehouse.dim_customer c ON c.customer_sk=s.customer_sk_fulfilment
 CROSS JOIN build_lineage l
 WHERE s.order_status='fulfilled' AND s.fulfilled_date BETWEEN l.period_start AND l.as_of_date
), population AS (
 SELECT home_country_code,segment_code,count(DISTINCT order_id)::BIGINT basket_count
 FROM basket_items GROUP BY 1,2
), items AS (
 SELECT home_country_code,segment_code,subcategory_id,count(*)::BIGINT item_baskets
 FROM basket_items GROUP BY 1,2,3
), pairs AS (
 SELECT a.home_country_code,a.segment_code,a.subcategory_id item_a,
        b.subcategory_id item_b,count(*)::BIGINT joint_baskets
 FROM basket_items a JOIN basket_items b
 ON a.order_id=b.order_id AND a.home_country_code=b.home_country_code
 AND a.segment_code=b.segment_code AND a.subcategory_id<b.subcategory_id
 GROUP BY 1,2,3,4
)
SELECT q.home_country_code,q.segment_code,q.item_a AS subcategory_a_id,
       q.item_b AS subcategory_b_id,n.basket_count,ia.item_baskets AS item_a_baskets,
       ib.item_baskets AS item_b_baskets,q.joint_baskets,
       round(q.joint_baskets::DECIMAL(38,8)/n.basket_count,8)::DECIMAL(18,8) pair_support,
       round(q.joint_baskets::DECIMAL(38,8)/ia.item_baskets,8)::DECIMAL(18,8) confidence_a_to_b,
       round(q.joint_baskets::DECIMAL(38,8)/ib.item_baskets,8)::DECIMAL(18,8) confidence_b_to_a,
       round(q.joint_baskets::DECIMAL(38,8)*n.basket_count/
             (ia.item_baskets*ib.item_baskets),8)::DECIMAL(18,8) lift,
       CASE WHEN q.joint_baskets>=l.min_joint_baskets AND
                 q.joint_baskets::DECIMAL(38,8)/n.basket_count>=l.min_pair_support
            THEN 'threshold_met' ELSE 'sparse_context' END AS context_status,
       'customer_home_at_fulfilment'::VARCHAR AS country_role,
       l.period_start observation_start_date,l.as_of_date,l.source_batch_id,
       l.source_warehouse_manifest_sha256,l.source_gold_manifest_sha256
FROM pairs q JOIN mart_basket_rules r ON r.subcategory_a_id=q.item_a
 AND r.subcategory_b_id=q.item_b
JOIN population n ON n.home_country_code=q.home_country_code AND n.segment_code=q.segment_code
JOIN items ia ON ia.home_country_code=q.home_country_code AND ia.segment_code=q.segment_code
 AND ia.subcategory_id=q.item_a
JOIN items ib ON ib.home_country_code=q.home_country_code AND ib.segment_code=q.segment_code
 AND ib.subcategory_id=q.item_b
CROSS JOIN build_lineage l
ORDER BY q.home_country_code,q.segment_code,q.item_a,q.item_b;

CREATE TABLE mart_product_return_event AS
SELECT r.return_id,r.order_line_id,r.order_id,r.product_id,p.sku,p.category_id,p.subcategory_id,
       s.fulfilled_date AS original_fulfilled_date,s.quantity_ordered AS original_fulfilled_units,
       s.posted_net_sales_eur AS original_fulfilled_sales_eur,
       s.posted_cogs_eur AS original_fulfilled_cogs_eur,
       r.return_requested_date,
       CASE WHEN r.return_decision_date<=l.as_of_date THEN r.return_decision_date END AS return_decision_date,
       r.requested_quantity,
       CASE WHEN r.return_decision_date<=l.as_of_date THEN r.accepted_quantity END AS accepted_quantity,
       CASE WHEN r.return_decision_date>l.as_of_date THEN 'requested' ELSE r.return_status END AS return_status,r.return_reason_code,
       CASE WHEN r.return_decision_date<=l.as_of_date THEN r.restocking_outcome_code END AS restocking_outcome_code,
       CASE WHEN r.return_reason_code IN ('defective','quality_issue') THEN 'product_quality'
            WHEN r.return_reason_code IN ('wrong_item','damaged_in_transit') THEN 'fulfilment_or_transit'
            ELSE 'buyer_or_expectation' END AS reason_group,
       CASE WHEN r.return_decision_date<=l.as_of_date THEN r.posted_refund_ex_vat_eur ELSE 0::DECIMAL(18,2) END AS posted_refund_ex_vat_eur,
       CASE WHEN r.return_decision_date<=l.as_of_date THEN r.posted_recovered_cogs_eur ELSE 0::DECIMAL(18,2) END AS posted_recovered_cogs_eur,
       l.as_of_date,l.source_batch_id,l.source_warehouse_manifest_sha256,l.source_gold_manifest_sha256
FROM warehouse.fact_return_event r
JOIN warehouse.fact_order_line s USING(order_line_id)
JOIN warehouse.dim_product p ON p.product_id=r.product_id CROSS JOIN build_lineage l
WHERE r.return_requested_date BETWEEN l.period_start AND l.as_of_date
ORDER BY r.return_id;

CREATE TABLE mart_product_return_quality AS
SELECT p.product_id,p.sku,r.return_status,r.return_reason_code,r.reason_group,r.restocking_outcome_code,
       count(*)::BIGINT return_event_count,
       sum(r.requested_quantity)::BIGINT requested_units,
       coalesce(sum(r.accepted_quantity),0)::BIGINT accepted_units,
       sum(r.posted_refund_ex_vat_eur)::DECIMAL(18,2) posted_refund_eur,
       l.period_start observation_start_date,l.as_of_date,l.source_batch_id,
       l.source_warehouse_manifest_sha256,l.source_gold_manifest_sha256
FROM mart_product_return_event r JOIN warehouse.dim_product p USING(product_id)
CROSS JOIN build_lineage l GROUP BY ALL
ORDER BY p.product_id,r.return_status,r.return_reason_code,r.restocking_outcome_code;
