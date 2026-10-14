"""Tiny SQL source fixture for cutoff, prior features and order grain."""

from __future__ import annotations

import re
from pathlib import Path

import duckdb
import pytest

from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES
from baltic_sales_intelligence.statistics.builder import _check_samples
from baltic_sales_intelligence.statistics.contracts import StatisticalContractError


def test_sql_samples_exclude_future_and_deduplicate_order_grain() -> None:
    con = duckdb.connect()
    for schema in ("warehouse", "gold", "customer"):
        con.execute(f"CREATE SCHEMA {schema}")
    con.execute("""CREATE TABLE warehouse.dim_customer AS SELECT
        1::BIGINT customer_sk,'C1'::VARCHAR customer_id,DATE '2023-01-01' acquisition_date,
        'S1'::VARCHAR segment_code,'EE'::VARCHAR home_country_code,
        DATE '2022-01-01' valid_from,NULL::DATE valid_to""")
    con.execute("""CREATE TABLE warehouse.dim_product AS SELECT * FROM (VALUES
        ('P1','A'),('P2','B')) t(product_id,subcategory_id)""")
    con.execute("""CREATE TABLE warehouse.fact_order_line AS SELECT * FROM (VALUES
        ('O1','C1',1,'P1',DATE '2024-09-20','fulfilled',1,10.00,5.00),
        ('O2','C1',1,'P1',DATE '2025-12-01','fulfilled',1,10.00,5.00),
        ('O2','C1',1,'P2',DATE '2026-01-01','fulfilled',1,10.00,5.00),
        ('O3','C1',1,'P2',DATE '2026-10-01','fulfilled',1,10.00,5.00)
        ) t(order_id,customer_id,customer_sk_fulfilment,product_id,fulfilled_date,
            order_status,quantity_ordered,posted_net_sales_eur,posted_cogs_eur)""")
    con.execute("""CREATE TABLE gold.mart_commercial_month AS SELECT * FROM (VALUES
        ('C1',DATE '2025-08-01','CAT','S1','EE',100.00,10.00,90.00,30.00,1,90.00),
        ('C1',DATE '2025-09-01','CAT','S1','EE',100.00,20.00,80.00,20.00,1,80.00),
        ('C1',DATE '2026-10-01','CAT','S1','EE',100.00,99.00,1.00,-5.00,1,1.00)
        ) t(customer_id,reporting_month,category_id,segment_code,home_country_code,
            posted_list_value_eur,posted_discount_eur,net_revenue_eur,net_gross_profit_eur,
            customer_sk,fulfilled_sales_eur)""")
    con.execute("""CREATE TABLE customer.mart_customer_value AS SELECT
        'C1'::VARCHAR customer_id,DATE '2024-09-01' first_purchase_cohort_month""")
    con.execute("""CREATE TEMP TABLE build_parameters AS SELECT
        DATE '2022-01-01' period_start,DATE '2026-09-30' as_of_date,
        DATE '2026-09-01' as_of_month,DATE '2024-10-01' prior_start_month,
        DATE '2025-10-01' current_start_month""")
    sql = Path("sql/analysis/statistical_samples.sql").read_text()
    assert all(
        re.search(rf"\b{re.escape(name)}\b", sql, flags=re.IGNORECASE) is None
        for name in PRIVATE_LABEL_NAMES
    )
    con.execute(sql)
    assert _check_samples(con)["sample_join_coverage"]["status"] == "passed"
    discount = con.execute("""SELECT prior_account_discount_rate,current_cell_discount_rate
        FROM stat_discount_candidates WHERE reporting_month=DATE '2025-09-01'""").fetchone()
    assert discount == (0.1, 0.2)
    assert (
        con.execute(
            "SELECT count(*) FROM stat_discount_candidates WHERE reporting_month>DATE '2026-09-30'"
        ).fetchone()[0]
        == 0
    )
    assert (
        con.execute("SELECT count(*) FROM stat_basket_membership WHERE order_id='O3'").fetchone()[0]
        == 0
    )
    rows = con.execute("""SELECT landmark_month,prior_12m_orders,retained_next_12m,
        outcome_mature FROM stat_retention_candidates ORDER BY landmark_month""").fetchall()
    assert rows[0] == (rows[0][0], 1, 1, True)
    assert rows[1][1:] == (1, None, False)
    assert (
        con.execute("SELECT count(*) FROM stat_basket_membership WHERE order_id='O2'").fetchone()[0]
        == 2
    )
    con.execute("""INSERT INTO warehouse.fact_order_line VALUES
        ('O4','C1',1,'UNKNOWN',DATE '2026-09-01','fulfilled',1,10.00,5.00)""")
    with pytest.raises(StatisticalContractError, match="orphan products"):
        _check_samples(con)
