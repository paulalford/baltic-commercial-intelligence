"""Read governed marts at the approved cutoff; no private scenario inputs."""

# ruff: noqa: E501  # Keep SQL expressions intact for source-measure traceability.

from __future__ import annotations

from datetime import date
from typing import Any

import duckdb


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None):
    result = con.execute(sql, params or [])
    names = [column[0] for column in result.description]
    return [dict(zip(names, row, strict=True)) for row in result.fetchall()]


def load_facts(con: duckdb.DuckDBPyConnection, cutoff: date) -> dict[str, list[dict]]:
    """Extract only published measures, with explicit date filters on event sources."""
    recent_ordinal = cutoff.year * 12 + cutoff.month - 1 - 11
    recent_start = date(recent_ordinal // 12, recent_ordinal % 12 + 1, 1)
    prior_start = date(recent_start.year - 1, recent_start.month, 1)
    quarter_start = date(cutoff.year, ((cutoff.month - 1) // 3) * 3 + 1, 1)
    previous_quarter = date(
        quarter_start.year - (quarter_start.month == 1), ((quarter_start.month - 4) % 12) + 1, 1
    )
    facts: dict[str, list[dict]] = {}
    facts["customers"] = _rows(
        con,
        """SELECT v.customer_id,v.customer_name,v.segment_code_as_of segment,
      v.home_country_code_as_of country,v.first_fulfilled_date,v.last_fulfilled_date,
      v.lifetime_fulfilled_order_count lifetime_orders,
      v.historical_value_net_gross_profit_eur lifetime_gp,
      v.buying_tenure_days_at_cutoff tenure_days,
      r.is_lapsed_buyer,r.is_active_buyer,r.recency_days,
      coalesce(sum(CASE WHEN g.reporting_month>=? THEN g.net_gross_profit_eur ELSE 0 END),0)::DECIMAL(18,2) recent_gp,
      coalesce(sum(CASE WHEN g.reporting_month>=? AND g.reporting_month<? THEN g.net_gross_profit_eur ELSE 0 END),0)::DECIMAL(18,2) prior_gp,
      coalesce(sum(CASE WHEN g.reporting_month>=? THEN g.net_revenue_eur ELSE 0 END),0)::DECIMAL(18,2) recent_revenue,
      coalesce(sum(CASE WHEN g.reporting_month>=? AND g.reporting_month<? THEN g.net_revenue_eur ELSE 0 END),0)::DECIMAL(18,2) prior_revenue
      FROM customer.mart_customer_value v JOIN customer.mart_rfm r USING(customer_id)
      LEFT JOIN gold.mart_commercial_month g ON g.customer_id=v.customer_id AND g.reporting_month<=?
      GROUP BY ALL ORDER BY v.customer_id""",
        [recent_start, prior_start, recent_start, recent_start, prior_start, recent_start, cutoff],
    )
    facts["customer_subcategories"] = _rows(
        con,
        """SELECT customer_id,subcategory_id,category_id,
      count(DISTINCT reporting_month)::BIGINT active_months,
      sum(fulfilled_sales_eur)::DECIMAL(18,2) sales_eur,
      sum(net_gross_profit_eur)::DECIMAL(18,2) gp_eur
      FROM gold.mart_commercial_month
      WHERE reporting_month BETWEEN ? AND ? AND customer_id IS NOT NULL
      GROUP BY 1,2,3 HAVING sum(fulfilled_sale_posting_count)>0
      ORDER BY 1,2""",
        [recent_start, cutoff],
    )
    facts["basket_pairs"] = _rows(
        con,
        """SELECT subcategory_a_id,subcategory_b_id,basket_count,
      item_a_baskets,item_b_baskets,joint_baskets,pair_support,lift,q_value,status
      FROM statistics.basket_pair_result ORDER BY 1,2""",
    )
    facts["basket_context"] = _rows(
        con,
        """SELECT home_country_code country,segment_code segment,
      subcategory_a_id,subcategory_b_id,basket_count,joint_baskets,
      confidence_a_to_b,confidence_b_to_a,
      context_status FROM product.mart_basket_context ORDER BY 1,2,3,4""",
    )
    facts["products"] = _rows(
        con,
        """SELECT l.product_id,p.category_id,p.subcategory_id,
      sum(CASE WHEN l.fulfilled_date>=? THEN l.quantity_ordered ELSE 0 END)::BIGINT recent_units,
      sum(CASE WHEN l.fulfilled_date>=? THEN l.posted_list_value_eur ELSE 0 END)::DECIMAL(18,2) recent_list,
      sum(CASE WHEN l.fulfilled_date>=? THEN l.posted_discount_eur ELSE 0 END)::DECIMAL(18,2) recent_discount,
      sum(CASE WHEN l.fulfilled_date>=? THEN l.posted_gross_profit_eur ELSE 0 END)::DECIMAL(18,2) recent_gp,
      sum(CASE WHEN l.fulfilled_date>=? AND l.fulfilled_date<? THEN l.quantity_ordered ELSE 0 END)::BIGINT prior_units,
      sum(CASE WHEN l.fulfilled_date>=? AND l.fulfilled_date<? THEN l.posted_list_value_eur ELSE 0 END)::DECIMAL(18,2) prior_list,
      sum(CASE WHEN l.fulfilled_date>=? AND l.fulfilled_date<? THEN l.posted_discount_eur ELSE 0 END)::DECIMAL(18,2) prior_discount,
      sum(CASE WHEN l.fulfilled_date>=? AND l.fulfilled_date<? THEN l.posted_gross_profit_eur ELSE 0 END)::DECIMAL(18,2) prior_gp
      FROM warehouse.fact_order_line l JOIN warehouse.dim_product p USING(product_id)
      WHERE l.order_status='fulfilled' AND l.fulfilled_date<=?
      GROUP BY 1,2,3 ORDER BY 1""",
        [recent_start] * 4 + [prior_start, recent_start] * 4 + [cutoff],
    )
    facts["returns"] = _rows(
        con,
        """SELECT e.product_id,p.category_id,e.return_decision_date,
      e.accepted_quantity,e.posted_refund_ex_vat_eur,e.posted_recovered_cogs_eur,
      e.reason_group,e.return_id
      FROM product.mart_product_return_event e
      JOIN warehouse.dim_product p USING(product_id)
      WHERE e.return_status='accepted' AND e.return_decision_date BETWEEN ? AND ?
      ORDER BY e.product_id,e.return_decision_date,e.return_id""",
        [prior_start, cutoff],
    )
    facts["cohorts"] = _rows(
        con,
        """SELECT cohort_month,cohort_home_country_code country,
      cohort_age_month,cohort_customer_count,eligible_customer_count,retained_customer_count,
      retention_rate,is_observation_mature
      FROM customer.mart_cohort_retention
      WHERE cohort_age_month=6 AND observation_month<=? ORDER BY 1,2""",
        [cutoff],
    )
    facts["monthly"] = _rows(
        con,
        """SELECT reporting_month,scope_code AS "scope",net_revenue_eur revenue,
      net_gross_profit_eur gp,posting_count FROM forecasting.sales_monthly
      WHERE reporting_month<=? ORDER BY 1,2""",
        [cutoff],
    )
    facts["customer_month"] = _rows(
        con,
        """SELECT reporting_month,customer_id,home_country_code country,
      sum(net_revenue_eur)::DECIMAL(18,2) revenue
      FROM gold.mart_commercial_month WHERE reporting_month BETWEEN ? AND ?
      GROUP BY 1,2,3 ORDER BY 1,2,3""",
        [prior_start, cutoff],
    )
    facts["channel_month"] = _rows(
        con,
        """SELECT reporting_month,home_country_code country,
      channel_code,sum(net_revenue_eur)::DECIMAL(18,2) revenue
      FROM gold.mart_commercial_month WHERE reporting_month BETWEEN ? AND ?
      GROUP BY 1,2,3 ORDER BY 1,2,3""",
        [prior_start, cutoff],
    )
    facts["forecast"] = _rows(
        con,
        """SELECT w.scope_code AS "scope",w.origin_month,w.target_month,
      w.actual_eur,w.predicted_eur,w.error_eur,w.interval_status,w.calibration_n,
      w.interval95_low_eur,w.interval95_high_eur,e.interval_n,e.coverage95
      FROM forecasting.walkforward_prediction w
      JOIN forecasting.model_evaluation e ON e.scope_code=w.scope_code
       AND e.horizon_months=w.horizon_months AND e.model=w.model
      WHERE w.model IN ('selected','reconciled') AND w.horizon_months=1
       AND w.target_month=? AND (w.scope_code<>'COMPANY' OR w.model='reconciled')
      ORDER BY w.scope_code""",
        [date(cutoff.year, cutoff.month, 1)],
    )
    facts["windows"] = [
        {
            "recent_start": recent_start,
            "prior_start": prior_start,
            "quarter_start": quarter_start,
            "previous_quarter": previous_quarter,
        }
    ]
    return facts
