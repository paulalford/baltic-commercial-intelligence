"""Read-only lineage, schema, and finance checks for forecasting inputs."""

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
REQUIRED = {
    "warehouse": {
        "fact_financial_posting_historical": (
            ["posting_id"],
            {
                "posting_date": "DATE",
                "home_country_code": "VARCHAR",
                "net_revenue_eur": "DECIMAL(18,2)",
                "net_gross_profit_eur": "DECIMAL(18,2)",
            },
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
            {
                "reporting_month": "DATE",
                "home_country_code": "VARCHAR",
                "net_revenue_eur": "DECIMAL(18,2)",
                "net_gross_profit_eur": "DECIMAL(18,2)",
                "posting_count": "BIGINT",
                "source_batch_id": "VARCHAR",
                "source_warehouse_manifest_sha256": "VARCHAR",
            },
        ),
        "mart_company_month": (
            ["reporting_month"],
            {
                "reporting_month": "DATE",
                "net_revenue_eur": "DECIMAL(18,2)",
                "net_gross_profit_eur": "DECIMAL(18,2)",
                "posting_count": "BIGINT",
                "is_complete_period": "BOOLEAN",
                "as_of_date": "DATE",
                "source_batch_id": "VARCHAR",
                "source_warehouse_manifest_sha256": "VARCHAR",
            },
        ),
    },
    "product": {
        "mart_product_return_event": (
            ["return_id"],
            {
                "original_fulfilled_date": "DATE",
                "accepted_quantity": "BIGINT",
                "return_status": "VARCHAR",
                "source_batch_id": "VARCHAR",
                "source_warehouse_manifest_sha256": "VARCHAR",
                "source_gold_manifest_sha256": "VARCHAR",
            },
        ),
    },
}


class ForecastContractError(ValueError):
    """Upstream or publication acceptance check failed."""


@dataclass(frozen=True)
class InputPublication:
    manifest: dict[str, Any]
    manifest_sha256: str
    database_path: Path


def _inspect(source: InputPublication, label: str) -> dict[str, Any]:
    checked = {}
    with duckdb.connect(str(source.database_path), read_only=True) as con:
        for name, (key, expected) in REQUIRED[label].items():
            metadata = source.manifest.get("objects", {}).get(name)
            view = name == "fact_financial_posting_historical"
            if (
                not isinstance(metadata, dict)
                or metadata.get("key") != key
                or not metadata.get("grain")
            ):
                raise ForecastContractError(f"{label}.{name} key/grain mismatch")
            if metadata.get("object_type") != ("view" if view else "table"):
                raise ForecastContractError(f"{label}.{name} object type mismatch")
            inventory = con.execute(
                "SELECT table_type FROM information_schema.tables "
                "WHERE table_catalog=current_database() AND table_schema='main' AND table_name=?",
                [name],
            ).fetchone()
            if inventory is None or inventory[0] != ("VIEW" if view else "BASE TABLE"):
                raise ForecastContractError(f"{label}.{name} object missing")
            columns = {
                row[1]: row[2] for row in con.execute(f"PRAGMA table_info('{name}')").fetchall()
            }
            if any(columns.get(column) != kind for column, kind in expected.items()):
                raise ForecastContractError(f"{label}.{name} schema mismatch")
            count = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            if count != metadata.get("row_count"):
                raise ForecastContractError(f"{label}.{name} row count mismatch")
            quoted = ",".join(f'"{column}"' for column in key)
            if con.execute(
                f'SELECT count(*) FROM (SELECT {quoted} FROM "{name}" '
                f"GROUP BY {quoted} HAVING count(*)>1)"
            ).fetchone()[0]:
                raise ForecastContractError(f"{label}.{name} duplicate key")
            if con.execute(
                f'SELECT count(*) FROM "{name}" WHERE '
                + " OR ".join(f'"{column}" IS NULL' for column in key)
            ).fetchone()[0]:
                raise ForecastContractError(f"{label}.{name} null key")
            checked[name] = count
        columns = {
            row[0]
            for row in con.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema='main'"
            ).fetchall()
        }
        if columns & PRIVATE_LABEL_NAMES:
            raise ForecastContractError(f"{label} private control columns detected")
    return {"status": "passed", "violations": 0, "row_counts": checked}


def validate_inputs(
    configuration: ProjectConfiguration, warehouse_dir: Path, gold_dir: Path, product_dir: Path
) -> tuple[dict[str, InputPublication], dict[str, Any]]:
    wh, wh_hash, wh_db, gold, gold_hash, gold_db = _validate_manifest_contracts(
        configuration, warehouse_dir, gold_dir
    )
    product, product_hash, product_db = _validate_manifest(
        configuration,
        product_dir,
        "product_intelligence_manifest.json",
        "product",
        "bsi.product_intelligence_publication",
        "product_intelligence.duckdb",
    )
    sources = {
        "warehouse": InputPublication(wh, wh_hash, wh_db),
        "gold": InputPublication(gold, gold_hash, gold_db),
        "product": InputPublication(product, product_hash, product_db),
    }
    report = configuration.application.reporting
    if str(report.period_start) != "2022-01-01" or str(report.default_as_of_date) != "2026-09-30":
        raise ForecastContractError("forecasting requires approved 57 complete historical months")
    for label, item in sources.items():
        if item.manifest.get("source_batch_id") != BATCH:
            raise ForecastContractError(f"{label} source batch mismatch")
        window_name = "analysis_window" if label == "product" else "reporting_window"
        window = item.manifest.get(window_name, {})
        if (
            window.get("period_start") != "2022-01-01"
            or (window.get("as_of_date") if label == "product" else window.get("period_end"))
            != "2026-09-30"
        ):
            raise ForecastContractError(f"{label} reporting window mismatch")
    if (
        product.get("source_warehouse_manifest_sha256") != wh_hash
        or product.get("source_warehouse_database_sha256") != wh["database"]["sha256"]
        or product.get("source_gold_manifest_sha256") != gold_hash
        or product.get("source_gold_database_sha256") != gold["database"]["sha256"]
        or product.get("source_silver_manifest_sha256") != wh["source_silver_manifest_sha256"]
    ):
        raise ForecastContractError("Product lineage mismatch")
    if "no approved cohort maturity duration" not in product.get("policies", {}).get(
        "return_rate", ""
    ):
        raise ForecastContractError("Product maturity policy is missing or changed")
    checks = {label: _inspect(item, label) for label, item in sources.items()}
    for label, table, columns, expected in (
        (
            "gold",
            "mart_commercial_month",
            "source_batch_id,source_warehouse_manifest_sha256",
            (BATCH, wh_hash),
        ),
        (
            "gold",
            "mart_company_month",
            "source_batch_id,source_warehouse_manifest_sha256",
            (BATCH, wh_hash),
        ),
        (
            "product",
            "mart_product_return_event",
            "source_batch_id,source_warehouse_manifest_sha256,source_gold_manifest_sha256",
            (BATCH, wh_hash, gold_hash),
        ),
    ):
        with duckdb.connect(str(sources[label].database_path), read_only=True) as con:
            distinct = con.execute(f"SELECT DISTINCT {columns} FROM {table}").fetchall()
        if distinct != [expected]:
            raise ForecastContractError(f"{label}.{table} row lineage mismatch")
    checks["row_lineage"] = {"status": "passed", "violations": 0}
    return sources, checks
