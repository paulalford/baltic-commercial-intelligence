"""Read-only upstream publication, schema, lineage, and finance contracts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.customer.builder import (
    _validate_manifest,
    _validate_manifest_contracts,
)
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES

BATCH = "8e33878d4c2719cc"
REQUIRED: dict[str, dict[str, tuple[list[str], list[str]]]] = {
    "warehouse": {
        "dim_customer": (
            ["customer_sk"],
            [
                "customer_id",
                "valid_from",
                "valid_to",
                "acquisition_date",
                "segment_code",
                "home_country_code",
            ],
        ),
        "dim_product": (["product_id"], ["subcategory_id"]),
        "fact_order_line": (
            ["order_line_id"],
            [
                "order_id",
                "customer_id",
                "customer_sk_fulfilment",
                "product_id",
                "fulfilled_date",
                "order_status",
                "quantity_ordered",
                "posted_net_sales_eur",
                "posted_cogs_eur",
                "source_batch_id",
            ],
        ),
        "fact_return_event": (
            ["return_id"],
            [
                "order_line_id",
                "return_status",
                "return_decision_date",
                "accepted_quantity",
                "posted_refund_ex_vat_eur",
                "source_batch_id",
            ],
        ),
        "fact_financial_posting_historical": (
            ["posting_id"],
            ["posting_date", "net_revenue_eur", "net_cogs_eur", "net_gross_profit_eur"],
        ),
    },
    "gold": {
        "mart_commercial_month": (
            [
                "reporting_month",
                "customer_sk",
                "product_id",
                "channel_code",
                "credited_salesperson_key",
            ],
            [
                "customer_id",
                "segment_code",
                "home_country_code",
                "category_id",
                "posted_list_value_eur",
                "posted_discount_eur",
                "fulfilled_sales_eur",
                "net_revenue_eur",
                "net_gross_profit_eur",
                "source_batch_id",
                "source_warehouse_manifest_sha256",
            ],
        ),
        "mart_company_month": (
            ["reporting_month"],
            [
                "fulfilled_sales_eur",
                "accepted_refunds_eur",
                "net_revenue_eur",
                "net_cogs_eur",
                "net_gross_profit_eur",
                "source_batch_id",
                "source_warehouse_manifest_sha256",
            ],
        ),
    },
    "customer": {
        "mart_customer_value": (
            ["customer_id"],
            [
                "first_purchase_cohort_month",
                "first_fulfilled_date",
                "fulfilled_sales_eur",
                "accepted_refunds_eur",
                "net_revenue_eur",
                "net_cogs_eur",
                "historical_value_net_gross_profit_eur",
                "source_batch_id",
                "source_warehouse_manifest_sha256",
                "source_gold_manifest_sha256",
            ],
        ),
    },
    "product": {
        "mart_basket_rules": (
            ["subcategory_a_id", "subcategory_b_id"],
            [
                "basket_count",
                "item_a_baskets",
                "item_b_baskets",
                "joint_baskets",
                "pair_support",
                "lift",
                "source_batch_id",
                "source_warehouse_manifest_sha256",
                "source_gold_manifest_sha256",
            ],
        ),
        "mart_product_profitability": (
            ["product_id"],
            [
                "fulfilled_sales_eur",
                "accepted_refunds_eur",
                "net_revenue_eur",
                "net_cogs_eur",
                "net_gross_profit_eur",
                "source_batch_id",
                "source_warehouse_manifest_sha256",
                "source_gold_manifest_sha256",
            ],
        ),
    },
}
EXPECTED_TYPES = {
    "customer_sk": "BIGINT",
    "fulfilled_date": "DATE",
    "reporting_month": "DATE",
    "quantity_ordered": "BIGINT",
    "basket_count": "BIGINT",
    "joint_baskets": "BIGINT",
    "posted_net_sales_eur": "DECIMAL(18,2)",
    "posted_cogs_eur": "DECIMAL(18,2)",
    "posted_list_value_eur": "DECIMAL(18,2)",
    "posted_discount_eur": "DECIMAL(18,2)",
    "fulfilled_sales_eur": "DECIMAL(18,2)",
    "accepted_refunds_eur": "DECIMAL(18,2)",
    "net_revenue_eur": "DECIMAL(18,2)",
    "net_cogs_eur": "DECIMAL(18,2)",
    "net_gross_profit_eur": "DECIMAL(18,2)",
}


class StatisticalContractError(ValueError):
    """An upstream integrity or analysis-sample acceptance gate failed."""


@dataclass(frozen=True)
class InputPublication:
    manifest: dict[str, Any]
    manifest_sha256: str
    database_path: Path


def _inspect(source: InputPublication, label: str) -> dict[str, Any]:
    required = REQUIRED[label]
    with duckdb.connect(str(source.database_path), read_only=True) as con:
        inventory = dict(
            con.execute(
                "SELECT table_name,table_type FROM information_schema.tables "
                "WHERE table_schema='main' AND table_catalog=current_database()"
            ).fetchall()
        )
        counts = {}
        for name, (key, required_columns) in required.items():
            metadata = source.manifest.get("objects", {}).get(name)
            if (
                not isinstance(metadata, dict)
                or metadata.get("key") != key
                or not metadata.get("grain")
            ):
                raise StatisticalContractError(f"{label}.{name} object key/grain mismatch")
            view = name == "fact_financial_posting_historical"
            if inventory.get(name) != ("VIEW" if view else "BASE TABLE") or metadata.get(
                "object_type"
            ) != ("view" if view else "table"):
                raise StatisticalContractError(f"{label}.{name} object type mismatch")
            columns = {
                row[1]: row[2] for row in con.execute(f"PRAGMA table_info('{name}')").fetchall()
            }
            if not set(key + required_columns) <= columns.keys():
                raise StatisticalContractError(f"{label}.{name} required schema mismatch")
            for column, expected in EXPECTED_TYPES.items():
                if column in key + required_columns and columns[column] != expected:
                    raise StatisticalContractError(f"{label}.{name}.{column} type mismatch")
            count = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            if count != metadata.get("row_count"):
                raise StatisticalContractError(f"{label}.{name} row count mismatch")
            group = ",".join(f'"{x}"' for x in key)
            duplicate = con.execute(
                f'SELECT count(*) FROM (SELECT {group} FROM "{name}" '
                f"GROUP BY {group} HAVING count(*)>1)"
            ).fetchone()[0]
            nulls = con.execute(
                f'SELECT count(*) FROM "{name}" WHERE ' + " OR ".join(f'"{x}" IS NULL' for x in key)
            ).fetchone()[0]
            if duplicate or nulls:
                raise StatisticalContractError(f"{label}.{name} key violation")
            counts[name] = count
        leaked = {
            row[0]
            for row in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='main'"
            ).fetchall()
        } & PRIVATE_LABEL_NAMES
        if leaked:
            raise StatisticalContractError(f"{label} private control columns found")
    return {
        "status": "passed",
        "violations": 0,
        "objects_checked": len(required),
        "row_counts": counts,
    }


def validate_inputs(
    configuration: ProjectConfiguration,
    warehouse_dir: Path,
    gold_dir: Path,
    customer_dir: Path,
    product_dir: Path,
) -> tuple[dict[str, InputPublication], dict[str, Any]]:
    """Validate four immutable upstream releases and their shared financial lineage."""
    wh, wh_hash, wh_db, gold, gold_hash, gold_db = _validate_manifest_contracts(
        configuration, warehouse_dir, gold_dir
    )
    customer, customer_hash, customer_db = _validate_manifest(
        configuration,
        customer_dir,
        "customer_analytics_manifest.json",
        "customer",
        "bsi.customer_analytics_publication",
        "customer_analytics.duckdb",
    )
    product, product_hash, product_db = _validate_manifest(
        configuration,
        product_dir,
        "product_intelligence_manifest.json",
        "product",
        "bsi.product_intelligence_publication",
        "product_intelligence.duckdb",
    )
    inputs = {
        "warehouse": InputPublication(wh, wh_hash, wh_db),
        "gold": InputPublication(gold, gold_hash, gold_db),
        "customer": InputPublication(customer, customer_hash, customer_db),
        "product": InputPublication(product, product_hash, product_db),
    }
    for label in ("warehouse", "gold", "customer", "product"):
        item = inputs[label]
        if item.manifest.get("source_batch_id") != BATCH:
            raise StatisticalContractError(f"{label} source batch mismatch")
    for label in ("customer", "product"):
        item = inputs[label].manifest
        if (
            item.get("source_warehouse_manifest_sha256") != wh_hash
            or item.get("source_warehouse_database_sha256") != wh["database"]["sha256"]
            or item.get("source_gold_manifest_sha256") != gold_hash
            or item.get("source_gold_database_sha256") != gold["database"]["sha256"]
            or item.get("source_silver_manifest_sha256") != wh["source_silver_manifest_sha256"]
        ):
            raise StatisticalContractError(f"{label} manifest lineage mismatch")
        window = item.get("analysis_window", {})
        report = configuration.application.reporting
        if (
            window.get("period_start") != str(report.period_start)
            or window.get("as_of_date") != str(report.default_as_of_date)
            or window.get("post_cutoff_excluded") is not True
        ):
            raise StatisticalContractError(f"{label} reporting window mismatch")
    checks = {label: _inspect(source, label) for label, source in inputs.items()}
    for label, table, cols, expected in (
        ("warehouse", "fact_order_line", "source_batch_id", (BATCH,)),
        ("warehouse", "fact_return_event", "source_batch_id", (BATCH,)),
        (
            "gold",
            "mart_commercial_month",
            "source_batch_id,source_warehouse_manifest_sha256",
            (BATCH, wh_hash),
        ),
        (
            "customer",
            "mart_customer_value",
            "source_batch_id,source_warehouse_manifest_sha256,source_gold_manifest_sha256",
            (BATCH, wh_hash, gold_hash),
        ),
        (
            "product",
            "mart_product_profitability",
            "source_batch_id,source_warehouse_manifest_sha256,source_gold_manifest_sha256",
            (BATCH, wh_hash, gold_hash),
        ),
    ):
        with duckdb.connect(str(inputs[label].database_path), read_only=True) as con:
            distinct = con.execute(f"SELECT DISTINCT {cols} FROM {table}").fetchall()
        if distinct != [expected]:
            raise StatisticalContractError(f"{label}.{table} row lineage mismatch")
    checks["row_lineage"] = {"status": "passed", "violations": 0, "tables_checked": 5}
    money = (
        "fulfilled_sales_eur",
        "accepted_refunds_eur",
        "net_revenue_eur",
        "net_cogs_eur",
        "net_gross_profit_eur",
    )
    totals = {}
    for field in money:
        values = {}
        for label, table, column in (
            ("warehouse", "fact_financial_posting_historical", field),
            ("gold", "mart_company_month", field),
            (
                "customer",
                "mart_customer_value",
                "historical_value_net_gross_profit_eur"
                if field == "net_gross_profit_eur"
                else field,
            ),
            ("product", "mart_product_profitability", field),
        ):
            with duckdb.connect(str(inputs[label].database_path), read_only=True) as con:
                value = con.execute(
                    f"SELECT coalesce(sum({column}),0)::DECIMAL(18,2) FROM {table}"
                ).fetchone()[0]
            values[label] = value
        if len(set(values.values())) != 1:
            raise StatisticalContractError(f"financial reconciliation mismatch: {field}")
        totals[field] = str(next(iter(values.values())))
    checks["financial_reconciliation"] = {"status": "passed", "violations": 0, "totals_eur": totals}
    return inputs, checks
