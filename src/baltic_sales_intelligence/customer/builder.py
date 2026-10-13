"""Build and atomically publish Milestone 9 customer analytics."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import tempfile
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from baltic_sales_intelligence import __version__
from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES

DATABASE_NAME = "customer_analytics.duckdb"
MANIFEST_NAME = "customer_analytics_manifest.json"

DATASET_CONTRACTS: dict[str, dict[str, Any]] = {
    "mart_customer_value": {
        "grain": "one stable customer acquired by the analysis cutoff",
        "key": ["customer_id"],
        "method_version": "customer-value-v1",
        "units": "EUR monetary values; fulfilled orders and postings are counts",
        "source": "warehouse.dim_customer, warehouse.fact_order_line, gold.mart_customer_month",
        "definition": "Realised cumulative value through the cutoff; not predictive CLV.",
    },
    "mart_rfm": {
        "grain": "one stable customer acquired by the analysis cutoff",
        "key": ["customer_id"],
        "method_version": "rfm-v1",
        "units": "recency in days; frequency in distinct orders; monetary value in EUR",
        "source": "warehouse.dim_customer, warehouse.fact_order_line, gold.mart_customer_month",
        "definition": (
            "Trailing-12-month active buyers receive deterministic ntile(5) scores; "
            "1 is lowest and 5 highest, recency is ordered worst-to-best, customer_id "
            "breaks boundary ties, and no fixed numeric thresholds are imposed."
        ),
    },
    "mart_cohort_retention": {
        "grain": "one first-purchase cohort month, cohort home country and cohort age",
        "key": ["cohort_month", "cohort_home_country_code", "cohort_age_month"],
        "method_version": "buying-cohort-v1",
        "units": "customers are counts; retention_rate is a unitless proportion",
        "source": "warehouse.dim_customer and warehouse.fact_order_line",
        "definition": (
            "First-fulfilled-order cohorts use the original cohort size as denominator; "
            "months incomplete at cutoff are right-censored."
        ),
    },
    "mart_customer_concentration": {
        "grain": "one company or customer-home posting-country scope at the cutoff",
        "key": ["scope_type", "scope_key"],
        "method_version": "customer-concentration-v1",
        "units": "EUR balances; shares and HHI are unitless proportions",
        "source": "gold.mart_customer_month",
        "definition": (
            "Top shares and HHI include positive stable-customer net-revenue balances "
            "only; nonpositive balances are counted separately."
        ),
    },
    "mart_customer_migration": {
        "grain": "one stable customer SCD2 transition effective by the cutoff",
        "key": ["customer_id", "to_customer_version_id"],
        "method_version": "customer-migration-v1",
        "units": "EUR monetary values; fulfilled orders are counts; exposure is days",
        "source": "warehouse.dim_customer and gold.mart_customer_month",
        "definition": (
            "Descriptive SCD2 version totals with exposure boundaries; not a causal or "
            "duration-normalised behavioural-change estimate."
        ),
    },
}

WAREHOUSE_REPORTING_DEFAULT_VIEW = "fact_financial_posting_historical"
GOLD_REPORTING_DEFAULT_BOUNDARY = "historical_only"

WAREHOUSE_COLUMNS = {
    "dim_customer": {
        "customer_sk": "BIGINT",
        "customer_version_id": "VARCHAR",
        "customer_id": "VARCHAR",
        "customer_name": "VARCHAR",
        "acquisition_date": "DATE",
        "account_status": "VARCHAR",
        "industry_code": "VARCHAR",
        "industry_name": "VARCHAR",
        "credit_tier": "VARCHAR",
        "valid_from": "DATE",
        "valid_to": "DATE",
        "segment_code": "VARCHAR",
        "segment_name": "VARCHAR",
        "company_size_band": "VARCHAR",
        "home_city_id": "VARCHAR",
        "home_region_id": "VARCHAR",
        "home_country_code": "VARCHAR",
    },
    "fact_order_line": {
        "order_line_id": "VARCHAR",
        "order_id": "VARCHAR",
        "customer_id": "VARCHAR",
        "customer_sk_fulfilment": "BIGINT",
        "fulfilled_date": "DATE",
        "order_status": "VARCHAR",
    },
}

UPSTREAM_OBJECT_CONTRACTS = {
    "warehouse": {
        "dim_customer": {
            "grain": "one effective SCD2 customer version",
            "key": ["customer_sk"],
        },
        "fact_order_line": {
            "grain": "one operational order line",
            "key": ["order_line_id"],
        },
    },
    "Gold": {
        "mart_customer_month": {
            "grain": "one reporting month and posting-date customer SCD version",
            "key": ["reporting_month", "customer_sk"],
        }
    },
}

GOLD_COLUMNS = {
    "mart_customer_month": {
        "reporting_month": "DATE",
        "customer_sk": "BIGINT",
        "customer_id": "VARCHAR",
        "home_country_code": "VARCHAR",
        "posting_count": "BIGINT",
        "fulfilled_sale_posting_count": "BIGINT",
        "accepted_return_posting_count": "BIGINT",
        "fulfilled_order_count": "BIGINT",
        "fulfilled_sales_eur": "DECIMAL(18,2)",
        "accepted_refunds_eur": "DECIMAL(18,2)",
        "net_revenue_eur": "DECIMAL(18,2)",
        "net_cogs_eur": "DECIMAL(18,2)",
        "net_gross_profit_eur": "DECIMAL(18,2)",
        "outbound_freight_eur": "DECIMAL(18,2)",
        "returns_freight_eur": "DECIMAL(18,2)",
        "contribution_after_freight_eur": "DECIMAL(18,2)",
        "source_batch_id": "VARCHAR",
        "source_warehouse_manifest_sha256": "VARCHAR",
    }
}


class CustomerAnalyticsError(ValueError):
    """Raised when an input or customer-analytics acceptance gate fails."""


@dataclass(frozen=True)
class CustomerAnalyticsBuildResult:
    """Metadata returned by published and dry-run customer builds."""

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


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    if not path.is_file():
        raise CustomerAnalyticsError(f"{label} manifest is missing: {path}")
    content = path.read_bytes()
    try:
        manifest = json.loads(content)
    except json.JSONDecodeError as exc:
        raise CustomerAnalyticsError(f"{label} manifest is not valid JSON: {exc}") from exc
    return manifest, hashlib.sha256(content).hexdigest()


def _validate_manifest(
    configuration: ProjectConfiguration,
    directory: Path,
    manifest_name: str,
    stage: str,
    schema_id: str,
    database_name: str,
) -> tuple[dict[str, Any], str, Path]:
    manifest, manifest_hash = _read_json(directory / manifest_name, stage)
    if (
        manifest.get("schema_id") != schema_id
        or manifest.get("schema_version") != 1
        or manifest.get("stage") != stage
        or manifest.get("publication_status") != "passed"
    ):
        raise CustomerAnalyticsError(f"{stage} publication contract is incompatible")
    if manifest.get("build_config_hash") != configuration.deterministic_hash():
        raise CustomerAnalyticsError(f"{stage} configuration hash is incompatible")
    batch_id = manifest.get("source_batch_id")
    if not isinstance(batch_id, str) or not batch_id:
        raise CustomerAnalyticsError(f"{stage} source batch identity is missing")
    database = manifest.get("database")
    if not isinstance(database, dict) or database.get("path") != database_name:
        raise CustomerAnalyticsError(f"{stage} database contract is incompatible")
    database_path = directory / database_name
    if not database_path.is_file():
        raise CustomerAnalyticsError(f"{stage} database is missing: {database_path}")
    if (
        not isinstance(database.get("sha256"), str)
        or _file_hash(database_path) != database["sha256"]
    ):
        raise CustomerAnalyticsError(f"{stage} database checksum mismatch")
    return manifest, manifest_hash, database_path


def _validate_manifest_contracts(
    configuration: ProjectConfiguration,
    warehouse_directory: Path,
    gold_directory: Path,
) -> tuple[dict[str, Any], str, Path, dict[str, Any], str, Path]:
    warehouse, warehouse_hash, warehouse_database = _validate_manifest(
        configuration,
        warehouse_directory,
        "warehouse_manifest.json",
        "warehouse",
        "bsi.warehouse_publication",
        "baltic_sales.duckdb",
    )
    gold, gold_hash, gold_database = _validate_manifest(
        configuration,
        gold_directory,
        "gold_manifest.json",
        "analytics",
        "bsi.gold_publication",
        "baltic_sales_gold.duckdb",
    )
    if gold.get("source_batch_id") != warehouse.get("source_batch_id"):
        raise CustomerAnalyticsError("warehouse and Gold source batches do not match")
    if gold.get("source_warehouse_manifest_sha256") != warehouse_hash:
        raise CustomerAnalyticsError("Gold does not reference the supplied warehouse manifest")
    if gold.get("source_warehouse_database_sha256") != warehouse["database"]["sha256"]:
        raise CustomerAnalyticsError("Gold does not reference the supplied warehouse database")
    if gold.get("source_silver_manifest_sha256") != warehouse.get("source_silver_manifest_sha256"):
        raise CustomerAnalyticsError("warehouse and Gold Silver lineage do not match")
    reporting = configuration.application.reporting
    warehouse_window = {
        "period_start": str(reporting.period_start),
        "period_end": str(reporting.period_end),
        "operational_extension_end": str(reporting.extension_end),
        "default_as_of_date": str(reporting.default_as_of_date),
        "default_view": WAREHOUSE_REPORTING_DEFAULT_VIEW,
    }
    gold_window = {
        "period_start": str(reporting.period_start),
        "period_end": str(reporting.period_end),
        "default_as_of_date": str(reporting.default_as_of_date),
        "operational_extension_end": str(reporting.extension_end),
        "default_boundary": GOLD_REPORTING_DEFAULT_BOUNDARY,
    }
    if warehouse.get("reporting_window") != warehouse_window:
        raise CustomerAnalyticsError("warehouse reporting-window contract is incompatible")
    if gold.get("reporting_window") != gold_window:
        raise CustomerAnalyticsError("Gold reporting-window contract is incompatible")
    silver_hash = warehouse.get("source_silver_manifest_sha256")
    if not isinstance(silver_hash, str) or not silver_hash:
        raise CustomerAnalyticsError("warehouse Silver lineage is missing")
    return warehouse, warehouse_hash, warehouse_database, gold, gold_hash, gold_database


def _inspect_database(
    path: Path,
    manifest: dict[str, Any],
    columns: dict[str, dict[str, str]],
    label: str,
) -> None:
    objects = manifest.get("objects")
    if not isinstance(objects, dict):
        raise CustomerAnalyticsError(f"{label} object inventory is missing")
    try:
        connection = duckdb.connect(str(path), read_only=True)
        try:
            connection.execute("PRAGMA database_size").fetchall()
            inventory = {
                row[0]: row[1]
                for row in connection.execute(
                    """
                    SELECT table_name, table_type FROM information_schema.tables
                    WHERE table_schema='main' AND table_catalog=current_database()
                    """
                ).fetchall()
            }
            for name, expected_columns in columns.items():
                metadata = objects.get(name)
                if not isinstance(metadata, dict):
                    raise CustomerAnalyticsError(f"required {label} object is missing: {name}")
                contract = UPSTREAM_OBJECT_CONTRACTS[label][name]
                if (
                    metadata.get("object_type") != "table"
                    or metadata.get("grain") != contract["grain"]
                    or metadata.get("key") != contract["key"]
                    or inventory.get(name) != "BASE TABLE"
                ):
                    raise CustomerAnalyticsError(f"{label} object contract mismatch: {name}")
                actual_columns = {
                    row[1]: row[2]
                    for row in connection.execute(f"PRAGMA table_info('{name}')").fetchall()
                }
                for column, expected_type in expected_columns.items():
                    if actual_columns.get(column) != expected_type:
                        raise CustomerAnalyticsError(
                            f"{label} column contract mismatch: {name}.{column}"
                        )
                actual_count = connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                if actual_count != metadata.get("row_count"):
                    raise CustomerAnalyticsError(f"{label} row-count mismatch: {name}")
                key = contract["key"]
                duplicate_count = connection.execute(
                    "SELECT count(*) FROM ("
                    f'SELECT {", ".join(key)} FROM "{name}" '
                    f"GROUP BY {', '.join(key)} HAVING count(*) > 1)"
                ).fetchone()[0]
                null_count = connection.execute(
                    f'SELECT count(*) FROM "{name}" WHERE '
                    + " OR ".join(f'"{column}" IS NULL' for column in key)
                ).fetchone()[0]
                if duplicate_count or null_count:
                    raise CustomerAnalyticsError(f"{label} key contract mismatch: {name}")
            private_columns = {
                row[0]
                for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='main'"
                ).fetchall()
            } & PRIVATE_LABEL_NAMES
            if private_columns:
                raise CustomerAnalyticsError(
                    f"private metadata leaked into {label}: {sorted(private_columns)}"
                )
        finally:
            connection.close()
    except duckdb.Error as exc:
        raise CustomerAnalyticsError(f"{label} integrity check failed: {exc}") from exc


def _validate_gold_lineage(path: Path, source_batch_id: str, warehouse_manifest_hash: str) -> None:
    try:
        connection = duckdb.connect(str(path), read_only=True)
        try:
            lineages = connection.execute(
                """
                SELECT DISTINCT source_batch_id, source_warehouse_manifest_sha256
                FROM mart_customer_month
                """
            ).fetchall()
        finally:
            connection.close()
    except duckdb.Error as exc:
        raise CustomerAnalyticsError(f"Gold lineage check failed: {exc}") from exc
    if lineages != [(source_batch_id, warehouse_manifest_hash)]:
        raise CustomerAnalyticsError("Gold row lineage does not match its input manifests")


def _require_zero(
    connection: duckdb.DuckDBPyConnection, checks: dict[str, str]
) -> dict[str, dict[str, Any]]:
    results = {}
    for name, sql in checks.items():
        violations = int(connection.execute(sql).fetchone()[0])
        results[name] = {
            "status": "passed" if violations == 0 else "failed",
            "violations": violations,
        }
        if violations:
            raise CustomerAnalyticsError(
                f"customer analytics acceptance check failed: {name} ({violations})"
            )
    return results


def _validate_outputs(
    connection: duckdb.DuckDBPyConnection,
    configuration: ProjectConfiguration,
) -> dict[str, Any]:
    inventory = {
        row[0]: row[1]
        for row in connection.execute(
            """
            SELECT table_name, table_type FROM information_schema.tables
            WHERE table_schema='main' AND table_catalog=current_database()
            """
        ).fetchall()
    }
    if set(inventory) != set(DATASET_CONTRACTS) or any(
        value != "BASE TABLE" for value in inventory.values()
    ):
        raise CustomerAnalyticsError(f"customer dataset inventory mismatch: {sorted(inventory)}")
    row_counts = {
        name: int(connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0])
        for name in DATASET_CONTRACTS
    }
    checks = {}
    for name, contract in DATASET_CONTRACTS.items():
        key = contract["key"]
        checks[f"{name}_key_unique"] = (
            "SELECT count(*) FROM ("
            f'SELECT {", ".join(key)} FROM "{name}" GROUP BY {", ".join(key)} '
            "HAVING count(*) > 1)"
        )
        checks[f"{name}_key_not_null"] = f'SELECT count(*) FROM "{name}" WHERE ' + " OR ".join(
            f"{column} IS NULL" for column in key
        )
    as_of = configuration.application.reporting.default_as_of_date
    period_start = configuration.application.reporting.period_start
    checks.update(
        {
            "stable_customer_population": """
                SELECT abs((SELECT count(*) FROM mart_customer_value)
                    - (SELECT count(*) FROM customer_identity))
            """,
            "rfm_customer_population": """
                SELECT abs((SELECT count(*) FROM mart_rfm)
                    - (SELECT count(*) FROM customer_identity))
            """,
            "customer_value_post_cutoff": f"""
                SELECT count(*) FROM mart_customer_value
                WHERE first_fulfilled_date > DATE '{as_of}'
                   OR last_fulfilled_date > DATE '{as_of}'
            """,
            "customer_value_tenure": f"""
                SELECT count(*) FROM mart_customer_value
                WHERE (first_fulfilled_date IS NULL AND buying_tenure_days_at_cutoff IS NOT NULL)
                   OR (first_fulfilled_date IS NOT NULL AND buying_tenure_days_at_cutoff
                        != date_diff('day', first_fulfilled_date, DATE '{as_of}') + 1)
            """,
            "rfm_recency": f"""
                SELECT count(*) FROM mart_rfm
                WHERE last_fulfilled_date IS NOT NULL
                  AND recency_days != date_diff('day', last_fulfilled_date, DATE '{as_of}')
            """,
            "rfm_score_eligibility": """
                SELECT count(*) FROM mart_rfm
                WHERE (is_active_buyer AND (recency_score IS NULL
                        OR frequency_score IS NULL OR monetary_score IS NULL))
                   OR (NOT is_active_buyer AND (recency_score IS NOT NULL
                        OR frequency_score IS NOT NULL OR monetary_score IS NOT NULL))
            """,
            "cohort_age_zero_retention": """
                SELECT count(*) FROM mart_cohort_retention
                WHERE cohort_age_month = 0 AND is_observation_mature
                  AND retained_customer_count != cohort_customer_count
            """,
            "cohort_censoring": """
                SELECT count(*) FROM mart_cohort_retention
                WHERE (NOT is_observation_mature AND (retention_rate IS NOT NULL
                        OR retained_customer_count IS NOT NULL
                        OR eligible_customer_count != 0
                        OR not_estimable_reason != 'right_censored'))
                   OR (is_observation_mature AND (retention_rate IS NULL
                        OR retained_customer_count IS NULL
                        OR eligible_customer_count != cohort_customer_count
                        OR not_estimable_reason IS NOT NULL))
            """,
            "concentration_denominator": """
                SELECT count(*) FROM mart_customer_concentration
                WHERE (positive_net_revenue_denominator_eur <= 0 AND (
                        top_1_share IS NOT NULL OR top_5_share IS NOT NULL
                        OR top_10_share IS NOT NULL OR hhi IS NOT NULL
                        OR not_estimable_reason IS NULL))
                   OR (positive_net_revenue_denominator_eur > 0 AND (
                        top_1_share IS NULL OR top_5_share IS NULL
                        OR top_10_share IS NULL OR hhi IS NULL
                        OR not_estimable_reason IS NOT NULL))
            """,
            "migration_transition_cutoff": f"""
                SELECT count(*) FROM mart_customer_migration
                WHERE transition_date > DATE '{as_of}'
            """,
            "migration_exposure_boundaries": """
                SELECT count(*) FROM mart_customer_migration
                WHERE before_exposure_days <= 0 OR after_exposure_days <= 0
                   OR before_observation_end_date >= after_observation_start_date
                   OR after_observation_end_date != as_of_date
                   OR comparison_status != 'raw_version_totals_not_duration_normalised'
            """,
            "gold_reporting_window": f"""
                SELECT count(*) FROM gold.mart_customer_month
                WHERE reporting_month < date_trunc('month', DATE '{period_start}')
                   OR reporting_month > date_trunc('month', DATE '{as_of}')
            """,
            "first_purchase_attribution": """
                SELECT count(*)
                FROM mart_customer_value AS value
                LEFT JOIN (
                    SELECT customer_id, min(fulfilled_date) AS first_fulfilled_date
                    FROM fulfilled_orders GROUP BY customer_id
                ) AS expected USING (customer_id)
                WHERE value.first_fulfilled_date IS DISTINCT FROM expected.first_fulfilled_date
            """,
            "customer_financial_reconciliation": """
                SELECT count(*) FROM (
                    SELECT
                        coalesce(output.customer_id, source.customer_id) AS customer_id
                    FROM mart_customer_value AS output
                    FULL OUTER JOIN (
                        SELECT
                            customer_id,
                            sum(fulfilled_sales_eur)::DECIMAL(18, 2) AS fulfilled_sales_eur,
                            sum(accepted_refunds_eur)::DECIMAL(18, 2) AS accepted_refunds_eur,
                            sum(net_revenue_eur)::DECIMAL(18, 2) AS net_revenue_eur,
                            sum(net_cogs_eur)::DECIMAL(18, 2) AS net_cogs_eur,
                            sum(net_gross_profit_eur)::DECIMAL(18, 2) AS net_gross_profit_eur,
                            sum(outbound_freight_eur)::DECIMAL(18, 2) AS outbound_freight_eur,
                            sum(returns_freight_eur)::DECIMAL(18, 2) AS returns_freight_eur,
                            sum(contribution_after_freight_eur)::DECIMAL(18, 2)
                                AS contribution_after_freight_eur
                        FROM gold.mart_customer_month GROUP BY customer_id
                    ) AS source USING (customer_id)
                    WHERE output.customer_id IS NULL
                       OR output.fulfilled_sales_eur
                            != coalesce(source.fulfilled_sales_eur, 0)
                       OR output.accepted_refunds_eur
                            != coalesce(source.accepted_refunds_eur, 0)
                       OR output.net_revenue_eur != coalesce(source.net_revenue_eur, 0)
                       OR output.net_cogs_eur != coalesce(source.net_cogs_eur, 0)
                       OR output.historical_value_net_gross_profit_eur
                            != coalesce(source.net_gross_profit_eur, 0)
                       OR output.outbound_freight_eur
                            != coalesce(source.outbound_freight_eur, 0)
                       OR output.returns_freight_eur
                            != coalesce(source.returns_freight_eur, 0)
                       OR output.contribution_after_freight_eur
                            != coalesce(source.contribution_after_freight_eur, 0)
                )
            """,
        }
    )
    semantic_results = _require_zero(connection, checks)

    source_fields = (
        "fulfilled_sales_eur",
        "accepted_refunds_eur",
        "net_revenue_eur",
        "net_cogs_eur",
        "net_gross_profit_eur",
        "outbound_freight_eur",
        "returns_freight_eur",
        "contribution_after_freight_eur",
    )
    output_fields = (
        "fulfilled_sales_eur",
        "accepted_refunds_eur",
        "net_revenue_eur",
        "net_cogs_eur",
        "historical_value_net_gross_profit_eur",
        "outbound_freight_eur",
        "returns_freight_eur",
        "contribution_after_freight_eur",
    )
    source_totals = connection.execute(
        "SELECT "
        + ", ".join(f"sum({field})" for field in source_fields)
        + " FROM gold.mart_customer_month"
    ).fetchone()
    output_totals = connection.execute(
        "SELECT "
        + ", ".join(f"sum({field})" for field in output_fields)
        + " FROM mart_customer_value"
    ).fetchone()
    if source_totals != output_totals:
        raise CustomerAnalyticsError("customer historical value does not reconcile to Gold")

    private_columns = {
        row[0]
        for row in connection.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='main' AND table_catalog=current_database()
            """
        ).fetchall()
    } & PRIVATE_LABEL_NAMES
    if private_columns:
        raise CustomerAnalyticsError(
            f"private metadata leaked into customer analytics: {sorted(private_columns)}"
        )
    return {
        "grain_and_semantics": semantic_results,
        "financial_reconciliation": {
            "status": "passed",
            "totals": {
                field: str(value if value is not None else Decimal("0.00"))
                for field, value in zip(source_fields, source_totals, strict=True)
            },
        },
        "private_metadata": {"status": "passed", "forbidden_columns": []},
        "row_counts": row_counts,
    }


def _sql_path() -> Path:
    return Path(__file__).parents[3] / "sql" / "analysis" / "customer_analytics.sql"


def _prepare_database(
    configuration: ProjectConfiguration,
    warehouse_path: Path,
    gold_path: Path,
    warehouse_manifest: dict[str, Any],
    warehouse_manifest_hash: str,
    gold_manifest: dict[str, Any],
    gold_manifest_hash: str,
    staged_output: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    staged_output.mkdir()
    database_path = staged_output / DATABASE_NAME
    sql_path = _sql_path()
    if not sql_path.is_file():
        raise CustomerAnalyticsError(f"customer SQL model is missing: {sql_path}")
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("PRAGMA threads=1")
        warehouse_sql = str(warehouse_path.resolve()).replace("'", "''")
        gold_sql = str(gold_path.resolve()).replace("'", "''")
        connection.execute(f"ATTACH '{warehouse_sql}' AS warehouse (READ_ONLY)")
        connection.execute(f"ATTACH '{gold_sql}' AS gold (READ_ONLY)")
        reporting = configuration.application.reporting
        lookback_start = reporting.default_as_of_date.replace(
            year=reporting.default_as_of_date.year - 1
        ) + timedelta(days=1)
        connection.execute(
            """
            CREATE TEMP TABLE build_lineage AS SELECT
                ?::DATE AS as_of_date,
                date_trunc('month', ?::DATE)::DATE AS as_of_month,
                ?::DATE AS observation_start_date,
                ?::DATE AS lookback_start_date,
                date_trunc('month', ?::DATE)::DATE AS lookback_start_month,
                ?::VARCHAR AS source_batch_id,
                ?::VARCHAR AS source_warehouse_manifest_sha256,
                ?::VARCHAR AS source_gold_manifest_sha256
            """,
            [
                reporting.default_as_of_date,
                reporting.default_as_of_date,
                reporting.period_start,
                lookback_start,
                lookback_start,
                warehouse_manifest["source_batch_id"],
                warehouse_manifest_hash,
                gold_manifest_hash,
            ],
        )
        connection.execute(sql_path.read_text(encoding="utf-8"))
        validations = {
            "input_validation": {
                "status": "passed",
                "source_batch_id": warehouse_manifest["source_batch_id"],
                "warehouse_manifest_sha256": warehouse_manifest_hash,
                "gold_manifest_sha256": gold_manifest_hash,
            },
            **_validate_outputs(connection, configuration),
        }
        objects = {}
        for name, contract in DATASET_CONTRACTS.items():
            objects[name] = {
                "schema_id": f"bsi.customer.{name}",
                "schema_version": 1,
                "object_type": "table",
                **contract,
                "observation_boundary": (
                    "2022-01-01 through 2026-09-30 inclusive; output-specific "
                    "observation_start_date is stored on every row"
                ),
                "currency": "EUR for monetary measures; counts and scores are unitless",
                "row_count": validations["row_counts"][name],
                "columns": [
                    {"name": row[1], "type": row[2], "nullable": not row[3]}
                    for row in connection.execute(f"PRAGMA table_info('{name}')").fetchall()
                ],
            }
        connection.execute("DETACH gold")
        connection.execute("DETACH warehouse")
        connection.execute("CHECKPOINT")
    except (duckdb.Error, OSError) as exc:
        raise CustomerAnalyticsError(f"customer analytics build failed: {exc}") from exc
    finally:
        connection.close()

    manifest = {
        "schema_id": "bsi.customer_analytics_publication",
        "schema_version": 1,
        "stage": "customer",
        "pipeline_version": __version__,
        "publication_status": "passed",
        "source_batch_id": warehouse_manifest["source_batch_id"],
        "source_warehouse_manifest_sha256": warehouse_manifest_hash,
        "source_warehouse_database_sha256": warehouse_manifest["database"]["sha256"],
        "source_gold_manifest_sha256": gold_manifest_hash,
        "source_gold_database_sha256": gold_manifest["database"]["sha256"],
        "source_silver_manifest_sha256": warehouse_manifest["source_silver_manifest_sha256"],
        "build_config_hash": configuration.deterministic_hash(),
        "stage_duration_seconds": 0.0,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {"duckdb": importlib.metadata.version("duckdb")},
        },
        "database": {"path": DATABASE_NAME, "sha256": _file_hash(database_path)},
        "objects": objects,
        "analysis_window": {
            "period_start": str(configuration.application.reporting.period_start),
            "as_of_date": str(configuration.application.reporting.default_as_of_date),
            "rfm_lookback_start": str(lookback_start),
            "post_cutoff_excluded": True,
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
        raise CustomerAnalyticsError(
            f"customer analytics output already exists; use --force: {output}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = output.with_name(f".{output.name}.previous-{os.getpid()}")
    if backup.exists():
        raise CustomerAnalyticsError(f"publication backup path already exists: {backup}")
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
    if backup.exists():
        shutil.rmtree(backup) if backup.is_dir() else backup.unlink()


def build_customer_analytics(
    configuration: ProjectConfiguration,
    warehouse_directory: Path,
    gold_directory: Path,
    output_directory: Path,
    *,
    publish: bool = True,
    force: bool = False,
) -> CustomerAnalyticsBuildResult:
    """Validate upstream publications, build customer marts, and publish atomically."""

    warehouse_directory = Path(warehouse_directory)
    gold_directory = Path(gold_directory)
    output_directory = Path(output_directory)
    (
        warehouse_manifest,
        warehouse_manifest_hash,
        warehouse_path,
        gold_manifest,
        gold_manifest_hash,
        gold_path,
    ) = _validate_manifest_contracts(configuration, warehouse_directory, gold_directory)
    _inspect_database(warehouse_path, warehouse_manifest, WAREHOUSE_COLUMNS, "warehouse")
    _inspect_database(gold_path, gold_manifest, GOLD_COLUMNS, "Gold")
    _validate_gold_lineage(
        gold_path, warehouse_manifest["source_batch_id"], warehouse_manifest_hash
    )
    if publish and _has_material_output(output_directory) and not force:
        raise CustomerAnalyticsError(
            f"customer analytics output already exists; use --force: {output_directory}"
        )
    output_directory.parent.mkdir(parents=True, exist_ok=True)
    temporary_parent = output_directory.parent if publish else None
    with tempfile.TemporaryDirectory(
        prefix=f".{output_directory.name}.staging-", dir=temporary_parent
    ) as temporary:
        staged = Path(temporary) / output_directory.name
        manifest, validations = _prepare_database(
            configuration,
            warehouse_path,
            gold_path,
            warehouse_manifest,
            warehouse_manifest_hash,
            gold_manifest,
            gold_manifest_hash,
            staged,
        )
        if not publish:
            return CustomerAnalyticsBuildResult(manifest=manifest, validations=validations)
        _publish_directory(staged, output_directory, force)
    return CustomerAnalyticsBuildResult(
        manifest=manifest,
        validations=validations,
        output_directory=output_directory,
        database_path=output_directory / DATABASE_NAME,
    )
