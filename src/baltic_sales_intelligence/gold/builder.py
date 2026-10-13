"""Build and atomically publish the Milestone 8 Gold analytical layer."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from baltic_sales_intelligence import __version__
from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES

DATABASE_NAME = "baltic_sales_gold.duckdb"
MANIFEST_NAME = "gold_manifest.json"

MONEY_FIELDS = (
    "posted_list_value_eur",
    "posted_discount_eur",
    "fulfilled_sales_eur",
    "accepted_refunds_eur",
    "net_revenue_eur",
    "fulfilled_cogs_eur",
    "recovered_cogs_eur",
    "net_cogs_eur",
    "net_gross_profit_eur",
    "net_vat_eur",
    "outbound_freight_eur",
    "returns_freight_eur",
    "contribution_after_freight_eur",
)
POSTING_COUNT_FIELDS = (
    "posting_count",
    "fulfilled_sale_posting_count",
    "accepted_return_posting_count",
    "fulfilled_order_count",
)

MART_CONTRACTS: dict[str, dict[str, Any]] = {
    "mart_commercial_month": {
        "grain": (
            "one reporting month, posting-date customer SCD version, product, channel "
            "and credited/uncredited salesperson combination"
        ),
        "key": [
            "reporting_month",
            "customer_sk",
            "product_id",
            "channel_code",
            "credited_salesperson_key",
        ],
        "measures": [
            *POSTING_COUNT_FIELDS,
            *MONEY_FIELDS,
            "gross_margin_rate",
            "discount_rate",
        ],
        "dimensional_compatibility": (
            "customer-home geography and customer attributes are posting-date SCD values; "
            "product, channel and credited salesperson are conformed dimensions; distinct "
            "fulfilled-order count is non-additive across those dimensions"
        ),
    },
    "mart_customer_month": {
        "grain": "one reporting month and posting-date customer SCD version",
        "key": ["reporting_month", "customer_sk"],
        "measures": [
            "eligible_customer_count",
            *POSTING_COUNT_FIELDS,
            *MONEY_FIELDS,
            "gross_margin_rate",
        ],
        "dimensional_compatibility": (
            "customer attributes and home geography belong to the posting-date SCD version"
        ),
    },
    "mart_company_month": {
        "grain": "one complete historical reporting month at company scope",
        "key": ["reporting_month"],
        "measures": [
            "posting_customer_version_count",
            *POSTING_COUNT_FIELDS,
            *MONEY_FIELDS,
            "gross_margin_rate",
            "revenue_yoy_rate",
        ],
        "dimensional_compatibility": "company roll-up only; do not sum with lower-grain marts",
    },
    "mart_target_achievement_month": {
        "grain": "one target month, metric, and company/country/salesperson reporting scope",
        "key": ["target_month", "metric_code", "scope_type", "scope_key"],
        "measures": [
            "target_value",
            "actual_value",
            "uncredited_actual_value",
            "attainment_rate",
        ],
        "dimensional_compatibility": (
            "company and country actuals use customer-home posting geography; salesperson "
            "actuals use credited salesperson independently of ownership; scope types are "
            "alternative roll-ups and must never be summed together"
        ),
    },
}

REQUIRED_WAREHOUSE_COLUMNS: dict[str, dict[str, str]] = {
    "fact_financial_posting_historical": {
        "posting_id": "VARCHAR",
        "posting_type": "VARCHAR",
        "posting_date": "DATE",
        "posting_date_key": "BIGINT",
        "order_id": "VARCHAR",
        "customer_sk": "BIGINT",
        "home_city_id": "VARCHAR",
        "home_country_code": "VARCHAR",
        "product_id": "VARCHAR",
        "channel_code": "VARCHAR",
        "credited_salesperson_id": "VARCHAR",
        **{field: "DECIMAL(18,2)" for field in MONEY_FIELDS},
    },
    "fact_target_allocation": {
        "target_month": "DATE",
        "metric_code": "VARCHAR",
        "allocation_type": "VARCHAR",
        "salesperson_id": "VARCHAR",
        "country_code": "VARCHAR",
        "target_value": "DECIMAL(18,2)",
        "source_batch_id": "VARCHAR",
        "source_silver_manifest_sha256": "VARCHAR",
    },
    "dim_date": {
        "date_key": "BIGINT",
        "full_date": "DATE",
        "calendar_year": "BIGINT",
        "calendar_quarter": "BIGINT",
        "calendar_month": "BIGINT",
        "month_start_date": "DATE",
        "is_within_reporting_window": "BOOLEAN",
    },
    "dim_customer": {
        "customer_sk": "BIGINT",
        "customer_version_id": "VARCHAR",
        "customer_id": "VARCHAR",
        "customer_name": "VARCHAR",
        "segment_code": "VARCHAR",
        "segment_name": "VARCHAR",
        "company_size_band": "VARCHAR",
        "industry_code": "VARCHAR",
        "industry_name": "VARCHAR",
        "home_city_id": "VARCHAR",
        "home_region_id": "VARCHAR",
        "home_country_code": "VARCHAR",
    },
    "dim_geography": {
        "city_id": "VARCHAR",
        "city_name": "VARCHAR",
        "region_id": "VARCHAR",
        "region_name": "VARCHAR",
        "country_code": "VARCHAR",
        "country_name": "VARCHAR",
    },
    "dim_channel": {"channel_code": "VARCHAR", "channel_name": "VARCHAR"},
    "dim_product": {
        "product_id": "VARCHAR",
        "sku": "VARCHAR",
        "product_name": "VARCHAR",
        "subcategory_id": "VARCHAR",
        "subcategory_name": "VARCHAR",
        "category_id": "VARCHAR",
        "category_name": "VARCHAR",
    },
    "dim_salesperson": {
        "salesperson_id": "VARCHAR",
        "salesperson_name": "VARCHAR",
    },
}


class GoldError(ValueError):
    """Raised when warehouse input or a Gold acceptance gate fails."""


@dataclass(frozen=True)
class GoldBuildResult:
    """Gold build metadata returned for published and dry-run builds."""

    manifest: dict[str, Any]
    validations: dict[str, Any]
    output_directory: Path | None = None
    database_path: Path | None = None


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_warehouse_manifest(
    configuration: ProjectConfiguration, source: Path
) -> tuple[dict[str, Any], str, Path]:
    manifest_path = source / "warehouse_manifest.json"
    if not manifest_path.is_file():
        raise GoldError(f"warehouse manifest is missing: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise GoldError(f"warehouse manifest is not valid JSON: {exc}") from exc
    if (
        manifest.get("schema_id") != "bsi.warehouse_publication"
        or manifest.get("schema_version") != 1
        or manifest.get("stage") != "warehouse"
    ):
        raise GoldError("warehouse manifest schema version or stage is incompatible")
    if manifest.get("publication_status") != "passed":
        raise GoldError("warehouse publication does not have publication_status='passed'")
    if manifest.get("build_config_hash") != configuration.deterministic_hash():
        raise GoldError("warehouse publication configuration hash is incompatible")
    source_batch_id = manifest.get("source_batch_id")
    if not isinstance(source_batch_id, str) or not source_batch_id:
        raise GoldError("warehouse source batch identity is missing")

    database = manifest.get("database")
    if not isinstance(database, dict) or database.get("path") != "baltic_sales.duckdb":
        raise GoldError("warehouse database contract is incompatible")
    database_path = source / "baltic_sales.duckdb"
    if not database_path.is_file():
        raise GoldError(f"warehouse database is missing: {database_path}")
    expected_hash = database.get("sha256")
    if not isinstance(expected_hash, str) or _file_hash(database_path) != expected_hash:
        raise GoldError("warehouse database checksum mismatch")

    objects = manifest.get("objects")
    if not isinstance(objects, dict):
        raise GoldError("warehouse object inventory is missing")
    for name in REQUIRED_WAREHOUSE_COLUMNS:
        metadata = objects.get(name)
        if not isinstance(metadata, dict):
            raise GoldError(f"required warehouse object is absent from manifest: {name}")
        expected_type = "view" if name == "fact_financial_posting_historical" else "table"
        if metadata.get("object_type") != expected_type:
            raise GoldError(f"warehouse object type is incompatible: {name}")

    reporting = manifest.get("reporting_window")
    expected_reporting = configuration.application.reporting
    expected_window = {
        "period_start": str(expected_reporting.period_start),
        "period_end": str(expected_reporting.period_end),
        "operational_extension_end": str(expected_reporting.extension_end),
        "default_as_of_date": str(expected_reporting.default_as_of_date),
        "default_view": "fact_financial_posting_historical",
    }
    if reporting != expected_window:
        raise GoldError("warehouse reporting-window contract is incompatible")
    return manifest, hashlib.sha256(manifest_bytes).hexdigest(), database_path


def _inspect_warehouse(database_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    try:
        connection = duckdb.connect(str(database_path), read_only=True)
        try:
            connection.execute("PRAGMA database_size").fetchall()
            inventory = {
                row[0]: row[1]
                for row in connection.execute(
                    """
                    SELECT table_name, table_type
                    FROM information_schema.tables
                    WHERE table_schema = 'main'
                    """
                ).fetchall()
            }
            for name, columns in REQUIRED_WAREHOUSE_COLUMNS.items():
                if name not in inventory:
                    raise GoldError(f"required warehouse object is missing: {name}")
                actual_columns = {
                    row[1]: row[2]
                    for row in connection.execute(f"PRAGMA table_info('{name}')").fetchall()
                }
                for column, expected_type in columns.items():
                    if actual_columns.get(column) != expected_type:
                        raise GoldError(f"warehouse column contract mismatch: {name}.{column}")
                actual_count = int(
                    connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                )
                if actual_count != manifest["objects"][name]["row_count"]:
                    raise GoldError(f"warehouse row-count mismatch: {name}")

            lineage = connection.execute(
                """
                SELECT DISTINCT source_batch_id, source_silver_manifest_sha256
                FROM fact_target_allocation
                """
            ).fetchall()
            if lineage != [
                (
                    manifest["source_batch_id"],
                    manifest["source_silver_manifest_sha256"],
                )
            ]:
                raise GoldError("warehouse source lineage is incompatible")
            private_columns = {
                row[0]
                for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='main'"
                ).fetchall()
            } & PRIVATE_LABEL_NAMES
            if private_columns:
                raise GoldError(
                    f"private metadata leaked into warehouse: {sorted(private_columns)}"
                )
        finally:
            connection.close()
    except duckdb.Error as exc:
        raise GoldError(f"warehouse database integrity check failed: {exc}") from exc
    return {
        "status": "passed",
        "required_objects": sorted(REQUIRED_WAREHOUSE_COLUMNS),
        "source_batch_id": manifest["source_batch_id"],
    }


def _sql_path() -> Path:
    return Path(__file__).parents[3] / "sql" / "marts" / "gold_commercial_marts.sql"


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> Any:
    return connection.execute(sql).fetchone()[0]


def _decimal_text(value: Decimal | None) -> str:
    return str(value if value is not None else Decimal("0.00"))


def _require_zero(
    connection: duckdb.DuckDBPyConnection, checks: dict[str, str]
) -> dict[str, dict[str, Any]]:
    results = {}
    for name, sql in checks.items():
        violations = int(_scalar(connection, sql))
        results[name] = {
            "status": "passed" if violations == 0 else "failed",
            "violations": violations,
        }
        if violations:
            raise GoldError(f"Gold acceptance check failed: {name} ({violations})")
    return results


def _validate_gold(
    connection: duckdb.DuckDBPyConnection,
    configuration: ProjectConfiguration,
) -> dict[str, Any]:
    inventory = {
        row[0]: row[1]
        for row in connection.execute(
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_catalog = current_database()
            """
        ).fetchall()
    }
    if set(inventory) != set(MART_CONTRACTS) or any(
        object_type != "BASE TABLE" for object_type in inventory.values()
    ):
        raise GoldError(
            "Gold object inventory mismatch: "
            f"expected {sorted(MART_CONTRACTS)}, found {sorted(inventory)}"
        )

    row_counts = {
        table: int(_scalar(connection, f'SELECT count(*) FROM "{table}"'))
        for table in MART_CONTRACTS
    }
    grain_checks = {
        f"{table}_grain_unique": (
            "SELECT count(*) FROM ("
            f'SELECT {", ".join(contract["key"])} FROM "{table}" '
            f"GROUP BY {', '.join(contract['key'])} HAVING count(*) > 1)"
        )
        for table, contract in MART_CONTRACTS.items()
    }
    grain_checks.update(
        {
            f"{table}_key_not_null": (
                f'SELECT count(*) FROM "{table}" WHERE '
                + " OR ".join(f"{column} IS NULL" for column in contract["key"])
            )
            for table, contract in MART_CONTRACTS.items()
        }
    )
    grain_checks.update(
        {
            "commercial_dimensions_resolve_once": """
                SELECT count(*) FROM mart_commercial_month
                WHERE customer_sk IS NULL OR product_id IS NULL OR channel_code IS NULL
                   OR home_city_id IS NULL OR home_country_code IS NULL
            """,
            "customer_dimensions_resolve_once": """
                SELECT count(*) FROM mart_customer_month
                WHERE customer_sk IS NULL OR home_city_id IS NULL OR home_country_code IS NULL
            """,
            "historical_cutoff": f"""
                SELECT count(*) FROM mart_commercial_month
                WHERE reporting_month > DATE '{configuration.application.reporting.period_end}'
            """,
            "gross_profit_formula": """
                SELECT count(*) FROM mart_commercial_month
                WHERE net_gross_profit_eur
                    IS DISTINCT FROM net_revenue_eur - net_cogs_eur
            """,
            "contribution_formula": """
                SELECT count(*) FROM mart_commercial_month
                WHERE contribution_after_freight_eur
                    IS DISTINCT FROM
                        net_gross_profit_eur - outbound_freight_eur - returns_freight_eur
            """,
            "gross_margin_denominator": """
                SELECT count(*) FROM mart_commercial_month
                WHERE (net_revenue_eur <= 0 AND (
                        gross_margin_rate IS NOT NULL
                        OR gross_margin_reason IS DISTINCT FROM 'nonpositive_revenue'
                    ))
                   OR (net_revenue_eur > 0 AND (
                        gross_margin_rate IS NULL OR gross_margin_reason IS NOT NULL
                    ))
            """,
            "target_denominator": """
                SELECT count(*) FROM mart_target_achievement_month
                WHERE (target_value = 0 AND (
                        attainment_rate IS NOT NULL
                        OR attainment_reason IS DISTINCT FROM 'zero_target'
                    ))
                   OR (target_value != 0 AND (
                        attainment_rate IS NULL OR attainment_reason IS NOT NULL
                    ))
            """,
            "target_historical_cutoff": f"""
                SELECT count(*) FROM mart_target_achievement_month
                WHERE target_month > DATE '{configuration.application.reporting.period_end}'
            """,
            "target_company_plan_reconciliation": f"""
                WITH expected AS (
                    SELECT target_month, metric_code,
                           sum(target_value)::DECIMAL(18, 2) AS target_value
                    FROM warehouse.fact_target_allocation
                    WHERE target_month
                        <= DATE '{configuration.application.reporting.default_as_of_date}'
                    GROUP BY target_month, metric_code
                ), actual AS (
                    SELECT target_month, metric_code, target_value
                    FROM mart_target_achievement_month WHERE scope_type = 'company'
                )
                SELECT count(*) FROM expected FULL OUTER JOIN actual USING (
                    target_month, metric_code
                )
                WHERE expected.target_value IS DISTINCT FROM actual.target_value
            """,
            "target_country_plan_reconciliation": f"""
                WITH expected AS (
                    SELECT target_month, metric_code, country_code,
                           sum(target_value)::DECIMAL(18, 2) AS target_value
                    FROM warehouse.fact_target_allocation
                    WHERE target_month
                        <= DATE '{configuration.application.reporting.default_as_of_date}'
                    GROUP BY target_month, metric_code, country_code
                ), actual AS (
                    SELECT target_month, metric_code, country_code, target_value
                    FROM mart_target_achievement_month WHERE scope_type = 'country'
                )
                SELECT count(*) FROM expected FULL OUTER JOIN actual USING (
                    target_month, metric_code, country_code
                )
                WHERE expected.target_value IS DISTINCT FROM actual.target_value
            """,
            "target_company_actual_reconciliation": """
                WITH expected AS (
                    SELECT date.month_start_date AS target_month,
                           sum(posting.net_revenue_eur)::DECIMAL(18, 2)
                               AS net_revenue_eur,
                           sum(posting.net_gross_profit_eur)::DECIMAL(18, 2)
                               AS gross_profit_eur
                    FROM warehouse.fact_financial_posting_historical AS posting
                    JOIN warehouse.dim_date AS date
                      ON date.date_key = posting.posting_date_key
                    GROUP BY date.month_start_date
                )
                SELECT count(*) FROM mart_target_achievement_month AS actual
                LEFT JOIN expected USING (target_month)
                WHERE actual.scope_type = 'company'
                  AND actual.actual_value IS DISTINCT FROM CASE actual.metric_code
                      WHEN 'net_revenue_eur' THEN coalesce(expected.net_revenue_eur, 0)
                      WHEN 'gross_profit_eur' THEN coalesce(expected.gross_profit_eur, 0)
                  END::DECIMAL(18, 2)
            """,
            "target_country_actual_reconciliation": """
                WITH expected AS (
                    SELECT date.month_start_date AS target_month,
                           posting.home_country_code AS country_code,
                           sum(posting.net_revenue_eur)::DECIMAL(18, 2)
                               AS net_revenue_eur,
                           sum(posting.net_gross_profit_eur)::DECIMAL(18, 2)
                               AS gross_profit_eur
                    FROM warehouse.fact_financial_posting_historical AS posting
                    JOIN warehouse.dim_date AS date
                      ON date.date_key = posting.posting_date_key
                    GROUP BY date.month_start_date, posting.home_country_code
                )
                SELECT count(*) FROM mart_target_achievement_month AS actual
                LEFT JOIN expected USING (target_month, country_code)
                WHERE actual.scope_type = 'country'
                  AND actual.actual_value IS DISTINCT FROM CASE actual.metric_code
                      WHEN 'net_revenue_eur' THEN coalesce(expected.net_revenue_eur, 0)
                      WHEN 'gross_profit_eur' THEN coalesce(expected.gross_profit_eur, 0)
                  END::DECIMAL(18, 2)
            """,
            "target_salesperson_actual_reconciliation": """
                WITH expected AS (
                    SELECT date.month_start_date AS target_month,
                           posting.credited_salesperson_id AS salesperson_id,
                           sum(posting.net_revenue_eur)::DECIMAL(18, 2)
                               AS net_revenue_eur,
                           sum(posting.net_gross_profit_eur)::DECIMAL(18, 2)
                               AS gross_profit_eur
                    FROM warehouse.fact_financial_posting_historical AS posting
                    JOIN warehouse.dim_date AS date
                      ON date.date_key = posting.posting_date_key
                    WHERE posting.credited_salesperson_id IS NOT NULL
                    GROUP BY date.month_start_date, posting.credited_salesperson_id
                )
                SELECT count(*) FROM mart_target_achievement_month AS actual
                LEFT JOIN expected USING (target_month, salesperson_id)
                WHERE actual.scope_type = 'salesperson'
                  AND actual.actual_value IS DISTINCT FROM CASE actual.metric_code
                      WHEN 'net_revenue_eur' THEN coalesce(expected.net_revenue_eur, 0)
                      WHEN 'gross_profit_eur' THEN coalesce(expected.gross_profit_eur, 0)
                  END::DECIMAL(18, 2)
            """,
        }
    )
    grain_results = _require_zero(connection, grain_checks)

    source_totals = connection.execute(
        "SELECT count(*), "
        + ", ".join(f"sum({field})" for field in MONEY_FIELDS)
        + " FROM warehouse.fact_financial_posting_historical"
    ).fetchone()
    reconciliation: dict[str, Any] = {}
    for table in (
        "mart_commercial_month",
        "mart_customer_month",
        "mart_company_month",
    ):
        totals = connection.execute(
            "SELECT sum(posting_count), "
            + ", ".join(f"sum({field})" for field in MONEY_FIELDS)
            + f' FROM "{table}"'
        ).fetchone()
        if totals != source_totals:
            raise GoldError(f"Gold financial reconciliation failed: {table}")
        reconciliation[table] = {
            "status": "passed",
            "posting_count": int(totals[0]),
            "totals": {
                field: _decimal_text(value)
                for field, value in zip(MONEY_FIELDS, totals[1:], strict=True)
            },
        }

    dimension_reconciliation = {}
    for dimension, source_column, gold_column in (
        ("country", "posting.home_country_code", "home_country_code"),
        ("customer", "posting.customer_sk", "customer_sk"),
        ("product", "posting.product_id", "product_id"),
        ("channel", "posting.channel_code", "channel_code"),
        (
            "credited_salesperson",
            "posting.credited_salesperson_id",
            "credited_salesperson_id",
        ),
    ):
        source_rows = connection.execute(
            "SELECT date.month_start_date, "
            f"coalesce(cast({source_column} AS VARCHAR), '__NULL__'), count(*), "
            + ", ".join(f"sum(posting.{field})" for field in MONEY_FIELDS)
            + " FROM warehouse.fact_financial_posting_historical AS posting "
            "JOIN warehouse.dim_date AS date ON date.date_key = posting.posting_date_key "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        gold_rows = connection.execute(
            "SELECT reporting_month, "
            f"coalesce(cast({gold_column} AS VARCHAR), '__NULL__'), sum(posting_count), "
            + ", ".join(f"sum({field})" for field in MONEY_FIELDS)
            + " FROM mart_commercial_month GROUP BY 1, 2 ORDER BY 1, 2"
        ).fetchall()
        if source_rows != gold_rows:
            raise GoldError(f"Gold {dimension} financial reconciliation failed")
        dimension_reconciliation[dimension] = {
            "status": "passed",
            "group_count": len(gold_rows),
        }

    source_target_totals = connection.execute(
        f"""
        SELECT metric_code, sum(target_value)
        FROM warehouse.fact_target_allocation
        WHERE target_month <= DATE '{configuration.application.reporting.default_as_of_date}'
        GROUP BY metric_code ORDER BY metric_code
        """
    ).fetchall()
    gold_target_totals = connection.execute(
        """
        SELECT metric_code, sum(target_value)
        FROM mart_target_achievement_month
        WHERE scope_type = 'company'
        GROUP BY metric_code ORDER BY metric_code
        """
    ).fetchall()
    if source_target_totals != gold_target_totals:
        raise GoldError("Gold company targets do not reconcile to warehouse allocation leaves")
    country_target_totals = connection.execute(
        """
        SELECT metric_code, sum(target_value)
        FROM mart_target_achievement_month
        WHERE scope_type = 'country'
        GROUP BY metric_code ORDER BY metric_code
        """
    ).fetchall()
    if source_target_totals != country_target_totals:
        raise GoldError("Gold country targets do not reconcile to warehouse allocation leaves")

    company_min, company_max, company_count = connection.execute(
        "SELECT min(reporting_month), max(reporting_month), count(*) FROM mart_company_month"
    ).fetchone()
    reporting = configuration.application.reporting
    expected_months = (
        (reporting.period_end.year - reporting.period_start.year) * 12
        + reporting.period_end.month
        - reporting.period_start.month
        + 1
    )
    if (
        company_min != reporting.period_start.replace(day=1)
        or company_max != reporting.period_end.replace(day=1)
        or company_count != expected_months
    ):
        raise GoldError("company-month spine does not cover every complete reporting month")

    private_columns = {
        row[0]
        for row in connection.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = 'main' AND table_catalog = current_database()
            """
        ).fetchall()
    } & PRIVATE_LABEL_NAMES
    if private_columns:
        raise GoldError(f"private metadata leaked into Gold: {sorted(private_columns)}")

    return {
        "grain_and_semantics": grain_results,
        "financial_reconciliation": reconciliation,
        "dimension_financial_reconciliation": dimension_reconciliation,
        "target_reconciliation": {
            "status": "passed",
            "metric_totals": {
                metric: _decimal_text(value) for metric, value in source_target_totals
            },
        },
        "reporting_window": {
            "status": "passed",
            "first_month": str(company_min),
            "last_month": str(company_max),
            "complete_month_count": company_count,
        },
        "private_metadata": {"status": "passed", "forbidden_columns": []},
        "row_counts": row_counts,
    }


def _object_metadata(
    connection: duckdb.DuckDBPyConnection,
    validations: dict[str, Any],
) -> dict[str, Any]:
    result = {}
    for name, contract in MART_CONTRACTS.items():
        columns = [
            {"name": row[1], "type": row[2], "nullable": not row[3]}
            for row in connection.execute(f"PRAGMA table_info('{name}')").fetchall()
        ]
        result[name] = {
            "schema_id": f"bsi.gold.{name}",
            "schema_version": 1,
            "object_type": "table",
            **contract,
            "date_basis": "posting_date",
            "reporting_boundary": "2022-01-01 through 2026-09-30 inclusive",
            "currency": "EUR for monetary measures; rates are unitless",
            "source": "warehouse.fact_financial_posting_historical"
            if name != "mart_target_achievement_month"
            else "warehouse.fact_target_allocation and fact_financial_posting_historical",
            "row_count": validations["row_counts"][name],
            "columns": columns,
        }
    return result


def _prepare_gold_database(
    configuration: ProjectConfiguration,
    warehouse_path: Path,
    source_manifest: dict[str, Any],
    source_manifest_hash: str,
    input_validation: dict[str, Any],
    staged_output: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    staged_output.mkdir()
    database_path = staged_output / DATABASE_NAME
    sql_path = _sql_path()
    if not sql_path.is_file():
        raise GoldError(f"Gold SQL model is missing: {sql_path}")
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("PRAGMA threads=1")
        escaped_path = str(warehouse_path.resolve()).replace("'", "''")
        connection.execute(f"ATTACH '{escaped_path}' AS warehouse (READ_ONLY)")
        connection.execute(
            """
            CREATE TEMP TABLE build_lineage AS
            SELECT ?::DATE AS as_of_date,
                   ?::VARCHAR AS source_batch_id,
                   ?::VARCHAR AS source_warehouse_manifest_sha256
            """,
            [
                configuration.application.reporting.default_as_of_date,
                source_manifest["source_batch_id"],
                source_manifest_hash,
            ],
        )
        connection.execute(sql_path.read_text(encoding="utf-8"))
        validations = {
            "input_validation": input_validation,
            **_validate_gold(connection, configuration),
        }
        objects = _object_metadata(connection, validations)
        connection.execute("DETACH warehouse")
        connection.execute("CHECKPOINT")
    except (duckdb.Error, OSError) as exc:
        raise GoldError(f"Gold build failed: {exc}") from exc
    finally:
        connection.close()

    database_hash = _file_hash(database_path)
    reporting = configuration.application.reporting
    manifest = {
        "schema_id": "bsi.gold_publication",
        "schema_version": 1,
        "stage": "analytics",
        "pipeline_version": __version__,
        "publication_status": "passed",
        "source_warehouse_manifest_sha256": source_manifest_hash,
        "source_warehouse_database_sha256": source_manifest["database"]["sha256"],
        "source_silver_manifest_sha256": source_manifest["source_silver_manifest_sha256"],
        "source_batch_id": source_manifest["source_batch_id"],
        "build_config_hash": configuration.deterministic_hash(),
        "stage_duration_seconds": 0.0,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {"duckdb": importlib.metadata.version("duckdb")},
        },
        "database": {"path": DATABASE_NAME, "sha256": database_hash},
        "objects": objects,
        "reporting_window": {
            "period_start": str(reporting.period_start),
            "period_end": str(reporting.period_end),
            "default_as_of_date": str(reporting.default_as_of_date),
            "operational_extension_end": str(reporting.extension_end),
            "default_boundary": "historical_only",
        },
        "validations": validations,
    }
    (staged_output / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest, validations


def _has_material_output(path: Path) -> bool:
    return path.exists() and (
        not path.is_dir() or any(item.name != ".gitkeep" for item in path.iterdir())
    )


def _publish_directory(staged: Path, output: Path, force: bool) -> None:
    if _has_material_output(output) and not force:
        raise GoldError(f"Gold output already exists; use --force: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = output.with_name(f".{output.name}.previous-{os.getpid()}")
    if backup.exists():
        raise GoldError(f"Gold publication backup path already exists: {backup}")
    replaced = False
    try:
        if output.exists():
            os.replace(output, backup)
            replaced = True
        os.replace(staged, output)
    except Exception:
        if output.exists():
            shutil.rmtree(output) if output.is_dir() else output.unlink()
        if replaced and backup.exists():
            os.replace(backup, output)
        raise
    else:
        if backup.exists():
            shutil.rmtree(backup) if backup.is_dir() else backup.unlink()


def build_gold(
    configuration: ProjectConfiguration,
    source: Path,
    output: Path,
    *,
    publish: bool = True,
    force: bool = False,
) -> GoldBuildResult:
    """Validate a published warehouse, build Gold marts, and publish atomically."""

    source = Path(source)
    output = Path(output)
    source_manifest, source_manifest_hash, warehouse_path = _read_warehouse_manifest(
        configuration, source
    )
    input_validation = _inspect_warehouse(warehouse_path, source_manifest)
    if publish and _has_material_output(output) and not force:
        raise GoldError(f"Gold output already exists; use --force: {output}")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_parent = output.parent if publish else None
    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.staging-", dir=temporary_parent
    ) as temporary:
        staged = Path(temporary) / output.name
        manifest, validations = _prepare_gold_database(
            configuration,
            warehouse_path,
            source_manifest,
            source_manifest_hash,
            input_validation,
            staged,
        )
        if not publish:
            return GoldBuildResult(manifest=manifest, validations=validations)
        _publish_directory(staged, output, force)

    return GoldBuildResult(
        manifest=manifest,
        validations=validations,
        output_directory=output,
        database_path=output / DATABASE_NAME,
    )
