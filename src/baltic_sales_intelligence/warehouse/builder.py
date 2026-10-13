"""Build and atomically publish the Milestone 7 DuckDB analytical warehouse."""

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
import pyarrow as pa
import pyarrow.parquet as pq

from baltic_sales_intelligence import __version__
from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES, PUBLIC_COLUMNS
from baltic_sales_intelligence.quality.contracts import arrow_schema

DATABASE_NAME = "baltic_sales.duckdb"
MANIFEST_NAME = "warehouse_manifest.json"

TABLE_CONTRACTS: dict[str, dict[str, Any]] = {
    "dim_date": {"grain": "one calendar date", "key": ["date_key"]},
    "dim_geography": {"grain": "one city with region and country", "key": ["city_id"]},
    "dim_channel": {"grain": "one approved sales channel", "key": ["channel_code"]},
    "dim_customer": {
        "grain": "one effective SCD2 customer version",
        "key": ["customer_sk"],
        "business_key": ["customer_version_id"],
    },
    "dim_product": {"grain": "one stable catalogue product", "key": ["product_id"]},
    "dim_salesperson": {"grain": "one salesperson", "key": ["salesperson_id"]},
    "dim_supplier": {"grain": "one approved supplier", "key": ["supplier_id"]},
    "bridge_customer_owner": {
        "grain": "one effective customer ownership interval",
        "key": ["assignment_id"],
    },
    "bridge_product_supplier": {
        "grain": "one effective product-supplier relationship",
        "key": ["product_supplier_relationship_id"],
    },
    "fact_order_line": {
        "grain": "one operational order line",
        "key": ["order_line_id"],
    },
    "fact_return_event": {"grain": "one return event", "key": ["return_id"]},
    "fact_target_allocation": {
        "grain": "one month, metric and leaf allocation",
        "key": ["target_id"],
    },
}

VIEW_CONTRACTS: dict[str, dict[str, Any]] = {
    "fact_financial_posting": {
        "grain": "one fulfilled-sale or accepted-return posting",
        "key": ["posting_id"],
    },
    "fact_financial_posting_historical": {
        "grain": "one posting inside the approved historical reporting window",
        "key": ["posting_id"],
    },
}


class WarehouseError(ValueError):
    """Raised when Silver input or a warehouse acceptance gate fails."""


@dataclass(frozen=True)
class WarehouseBuildResult:
    """Warehouse build metadata returned for published and dry-run builds."""

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


def _read_silver(
    configuration: ProjectConfiguration, source: Path
) -> tuple[dict[str, pa.Table], dict[str, Any], str]:
    manifest_path = source / "silver_manifest.json"
    if not manifest_path.is_file():
        raise WarehouseError(f"Silver manifest is missing: {manifest_path}")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise WarehouseError(f"Silver manifest is not valid JSON: {exc}") from exc
    if (
        manifest.get("schema_id") != "bsi.silver_publication"
        or manifest.get("schema_version") != 1
        or manifest.get("stage") != "validate"
    ):
        raise WarehouseError("Silver manifest schema version or stage is incompatible")
    if manifest.get("quality_status") != "passed":
        raise WarehouseError("Silver publication does not have quality_status='passed'")
    if manifest.get("config_hash") != configuration.deterministic_hash():
        raise WarehouseError("Silver publication configuration hash is incompatible")
    source_batch_id = manifest.get("source_batch_id")
    if not isinstance(source_batch_id, str) or not source_batch_id:
        raise WarehouseError("Silver source batch identity is missing")
    datasets = manifest.get("datasets")
    if not isinstance(datasets, dict) or set(datasets) != set(PUBLIC_COLUMNS):
        raise WarehouseError("Silver dataset inventory does not match the public contract")

    tables: dict[str, pa.Table] = {}
    for name, columns in PUBLIC_COLUMNS.items():
        metadata = datasets[name]
        if not isinstance(metadata, dict):
            raise WarehouseError(f"Silver dataset metadata is invalid: {name}")
        if (
            metadata.get("schema_id") != f"bsi.silver.{name}"
            or metadata.get("schema_version") != 1
            or metadata.get("path") != f"{name}.parquet"
            or metadata.get("columns") != list(columns)
        ):
            raise WarehouseError(f"Silver dataset contract is incompatible: {name}")
        path = source / f"{name}.parquet"
        if not path.is_file():
            raise WarehouseError(f"Silver dataset is missing: {path}")
        expected_hash = metadata.get("sha256")
        if not isinstance(expected_hash, str) or _file_hash(path) != expected_hash:
            raise WarehouseError(f"Silver dataset checksum mismatch: {name}")
        table = pq.read_table(path)
        if not table.schema.equals(arrow_schema(name), check_metadata=False):
            raise WarehouseError(f"Silver dataset schema mismatch: {name}")
        if table.num_rows != metadata.get("row_count"):
            raise WarehouseError(f"Silver dataset row-count mismatch: {name}")
        if set(table.column_names) & PRIVATE_LABEL_NAMES:
            raise WarehouseError(f"private metadata is forbidden in Silver dataset: {name}")
        tables[name] = table
    return tables, manifest, hashlib.sha256(manifest_bytes).hexdigest()


def _sql_path(*parts: str) -> Path:
    return Path(__file__).parents[3].joinpath("sql", *parts)


def _execute_models(connection: duckdb.DuckDBPyConnection) -> None:
    for relative in (
        ("dimensions", "warehouse_dimensions.sql"),
        ("facts", "warehouse_facts.sql"),
    ):
        path = _sql_path(*relative)
        if not path.is_file():
            raise WarehouseError(f"warehouse SQL model is missing: {path}")
        connection.execute(path.read_text(encoding="utf-8"))


def _scalar(connection: duckdb.DuckDBPyConnection, sql: str) -> Any:
    return connection.execute(sql).fetchone()[0]


def _require_zero(
    connection: duckdb.DuckDBPyConnection,
    checks: dict[str, str],
) -> dict[str, dict[str, Any]]:
    results = {}
    for name, sql in checks.items():
        violations = int(_scalar(connection, sql))
        results[name] = {
            "status": "passed" if violations == 0 else "failed",
            "violations": violations,
        }
        if violations:
            raise WarehouseError(f"warehouse acceptance check failed: {name} ({violations})")
    return results


def _decimal_text(value: Decimal | None) -> str:
    return str(value if value is not None else Decimal("0.00"))


def _validate_warehouse(
    connection: duckdb.DuckDBPyConnection,
    configuration: ProjectConfiguration,
    source_manifest: dict[str, Any],
) -> dict[str, Any]:
    inventory = {
        row[0]: row[1]
        for row in connection.execute(
            """
            SELECT table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_name NOT LIKE 'silver_%'
              AND table_name != 'build_lineage'
            """
        ).fetchall()
    }
    expected_inventory = set(TABLE_CONTRACTS) | set(VIEW_CONTRACTS)
    if set(inventory) != expected_inventory:
        raise WarehouseError(
            "warehouse object inventory mismatch: "
            f"expected {sorted(expected_inventory)}, found {sorted(inventory)}"
        )

    row_conservation_sources = {
        "dim_date": "date_dimension",
        "dim_geography": "cities",
        "dim_channel": "sales_channels",
        "dim_customer": "customer_attribute_history",
        "dim_product": "products",
        "dim_salesperson": "salespeople",
        "dim_supplier": "suppliers",
        "bridge_customer_owner": "customer_account_assignments",
        "bridge_product_supplier": "product_supplier_relationships",
        "fact_order_line": "order_lines",
        "fact_return_event": "returns",
        "fact_target_allocation": "sales_targets",
    }
    row_counts: dict[str, int] = {}
    for table, source_name in row_conservation_sources.items():
        actual = int(_scalar(connection, f'SELECT count(*) FROM "{table}"'))
        expected = int(source_manifest["datasets"][source_name]["row_count"])
        if actual != expected:
            raise WarehouseError(
                f"warehouse row conservation failed: {table} expected {expected}, found {actual}"
            )
        row_counts[table] = actual
    for view in VIEW_CONTRACTS:
        row_counts[view] = int(_scalar(connection, f'SELECT count(*) FROM "{view}"'))

    uniqueness_checks = {
        f"{table}_key_unique": (
            "SELECT count(*) FROM ("
            f'SELECT {", ".join(contract["key"])} FROM "{table}" '
            f'GROUP BY {", ".join(contract["key"])} HAVING count(*) > 1)'
        )
        for table, contract in (TABLE_CONTRACTS | VIEW_CONTRACTS).items()
    }
    uniqueness_checks["dim_customer_version_unique"] = """
        SELECT count(*) FROM (
            SELECT customer_version_id FROM dim_customer
            GROUP BY customer_version_id HAVING count(*) > 1
        )
    """
    key_results = _require_zero(connection, uniqueness_checks)

    relationship_checks = {
        "fact_order_line_product_fk": """
            SELECT count(*) FROM fact_order_line fact
            LEFT JOIN dim_product dim USING (product_id)
            WHERE dim.product_id IS NULL
        """,
        "fact_order_line_channel_fk": """
            SELECT count(*) FROM fact_order_line fact
            LEFT JOIN dim_channel dim USING (channel_code)
            WHERE dim.channel_code IS NULL
        """,
        "fact_order_line_ship_geography_fk": """
            SELECT count(*) FROM fact_order_line fact
            LEFT JOIN dim_geography dim ON dim.city_id = fact.ship_to_city_id
            WHERE dim.city_id IS NULL
        """,
        "fact_order_line_credited_salesperson_fk": """
            SELECT count(*) FROM fact_order_line fact
            LEFT JOIN dim_salesperson dim
              ON dim.salesperson_id = fact.credited_salesperson_id
            WHERE fact.credited_salesperson_id IS NOT NULL
              AND dim.salesperson_id IS NULL
        """,
        "fact_order_line_customer_order_fk": """
            SELECT count(*) FROM fact_order_line WHERE customer_sk_order IS NULL
        """,
        "fact_order_line_customer_fulfilment_fk": """
            SELECT count(*) FROM fact_order_line
            WHERE (order_status = 'fulfilled') != (customer_sk_fulfilment IS NOT NULL)
        """,
        "fact_return_line_fk": """
            SELECT count(*) FROM fact_return_event fact
            LEFT JOIN fact_order_line sale USING (order_line_id)
            WHERE sale.order_line_id IS NULL
        """,
        "fact_return_customer_fk": """
            SELECT count(*) FROM fact_return_event
            WHERE (return_status = 'accepted') != (customer_sk_return IS NOT NULL)
        """,
        "fact_target_country_fk": """
            SELECT count(*) FROM fact_target_allocation target
            LEFT JOIN silver_countries country USING (country_code)
            WHERE country.country_code IS NULL
        """,
        "fact_target_salesperson_fk": """
            SELECT count(*) FROM fact_target_allocation target
            LEFT JOIN dim_salesperson salesperson USING (salesperson_id)
            WHERE (target.allocation_type = 'salesperson')
                  != (salesperson.salesperson_id IS NOT NULL)
        """,
        "dim_customer_home_geography_fk": """
            SELECT count(*) FROM dim_customer customer
            LEFT JOIN dim_geography geography
              ON geography.city_id = customer.home_city_id
             AND geography.region_id = customer.home_region_id
             AND geography.country_code = customer.home_country_code
            WHERE geography.city_id IS NULL
        """,
        "bridge_owner_fk": """
            SELECT count(*) FROM bridge_customer_owner bridge
            LEFT JOIN silver_customers customer USING (customer_id)
            LEFT JOIN dim_salesperson salesperson USING (salesperson_id)
            WHERE customer.customer_id IS NULL OR salesperson.salesperson_id IS NULL
        """,
        "bridge_supplier_fk": """
            SELECT count(*) FROM bridge_product_supplier bridge
            LEFT JOIN dim_product product USING (product_id)
            LEFT JOIN dim_supplier supplier USING (supplier_id)
            WHERE product.product_id IS NULL OR supplier.supplier_id IS NULL
        """,
    }
    relationship_results = _require_zero(connection, relationship_checks)

    temporal_checks = {
        "customer_scd_nonoverlap": """
            SELECT count(*) FROM dim_customer left_version
            JOIN dim_customer right_version
              ON left_version.customer_id = right_version.customer_id
             AND left_version.customer_sk < right_version.customer_sk
             AND left_version.valid_from < coalesce(right_version.valid_to, DATE '9999-12-31')
             AND right_version.valid_from < coalesce(left_version.valid_to, DATE '9999-12-31')
        """,
        "customer_owner_nonoverlap": """
            SELECT count(*) FROM bridge_customer_owner left_owner
            JOIN bridge_customer_owner right_owner
              ON left_owner.customer_id = right_owner.customer_id
             AND left_owner.assignment_id < right_owner.assignment_id
             AND left_owner.valid_from < coalesce(right_owner.valid_to, DATE '9999-12-31')
             AND right_owner.valid_from < coalesce(left_owner.valid_to, DATE '9999-12-31')
        """,
        "product_supplier_pair_nonoverlap": """
            SELECT count(*) FROM bridge_product_supplier left_supplier
            JOIN bridge_product_supplier right_supplier
              ON left_supplier.product_id = right_supplier.product_id
             AND left_supplier.supplier_id = right_supplier.supplier_id
             AND left_supplier.product_supplier_relationship_id
                 < right_supplier.product_supplier_relationship_id
             AND left_supplier.valid_from < coalesce(right_supplier.valid_to, DATE '9999-12-31')
             AND right_supplier.valid_from < coalesce(left_supplier.valid_to, DATE '9999-12-31')
        """,
        "primary_supplier_nonoverlap": """
            SELECT count(*) FROM bridge_product_supplier left_supplier
            JOIN bridge_product_supplier right_supplier
              ON left_supplier.product_id = right_supplier.product_id
             AND left_supplier.relationship_type = 'primary'
             AND right_supplier.relationship_type = 'primary'
             AND left_supplier.product_supplier_relationship_id
                 < right_supplier.product_supplier_relationship_id
             AND left_supplier.valid_from < coalesce(right_supplier.valid_to, DATE '9999-12-31')
             AND right_supplier.valid_from < coalesce(left_supplier.valid_to, DATE '9999-12-31')
        """,
        "target_leaf_grain_unique": """
            SELECT count(*) FROM (
                SELECT
                    target_month,
                    metric_code,
                    allocation_type,
                    coalesce(salesperson_id, country_code) AS allocation_key
                FROM fact_target_allocation
                GROUP BY ALL HAVING count(*) > 1
            )
        """,
        "credited_salesperson_event_eligibility": """
            SELECT count(*) FROM fact_order_line fact
            JOIN dim_salesperson salesperson
              ON salesperson.salesperson_id = fact.credited_salesperson_id
            WHERE fact.credited_salesperson_id IS NOT NULL
              AND (
                  CASE
                      WHEN fact.order_status = 'fulfilled' THEN fact.fulfilled_date
                      ELSE fact.order_date
                  END < salesperson.hire_date
                  OR (
                      salesperson.termination_date IS NOT NULL
                      AND CASE
                          WHEN fact.order_status = 'fulfilled' THEN fact.fulfilled_date
                          ELSE fact.order_date
                      END >= salesperson.termination_date
                  )
              )
        """,
        "product_event_eligibility": """
            SELECT count(*) FROM fact_order_line fact
            JOIN dim_product product USING (product_id)
            WHERE fact.order_date < product.launch_date
               OR (
                   product.discontinued_date IS NOT NULL
                   AND fact.order_date >= product.discontinued_date
               )
               OR (
                   fact.fulfilled_date IS NOT NULL
                   AND (
                       fact.fulfilled_date < product.launch_date
                       OR (
                           product.discontinued_date IS NOT NULL
                           AND fact.fulfilled_date >= product.discontinued_date
                       )
                   )
               )
        """,
        "target_employment_eligibility": """
            SELECT count(*) FROM fact_target_allocation target
            JOIN dim_salesperson salesperson USING (salesperson_id)
            WHERE target.allocation_type = 'salesperson'
              AND NOT (
                  salesperson.hire_date
                      < target.target_month + INTERVAL 1 MONTH
                  AND (
                      salesperson.termination_date IS NULL
                      OR salesperson.termination_date > target.target_month
                  )
              )
        """,
    }
    temporal_results = _require_zero(connection, temporal_checks)

    posting_checks = {
        "nonfulfilled_sales_do_not_post": """
            SELECT count(*) FROM fact_order_line
            WHERE order_status != 'fulfilled'
              AND (
                  posted_list_value_eur != 0
                  OR posted_discount_eur != 0
                  OR posted_net_sales_eur != 0
                  OR posted_cogs_eur != 0
                  OR posted_taxable_base_eur != 0
                  OR posted_vat_amount_eur != 0
                  OR posted_invoice_total_eur != 0
                  OR allocated_outbound_freight_eur != 0
              )
        """,
        "nonaccepted_returns_do_not_post": """
            SELECT count(*) FROM fact_return_event
            WHERE return_status != 'accepted'
              AND (
                  posted_refund_ex_vat_eur != 0
                  OR posted_recovered_cogs_eur != 0
                  OR posted_vat_reversal_eur != 0
                  OR returns_freight_eur != 0
              )
        """,
        "accepted_return_recognition_date": """
            SELECT count(*) FROM fact_return_event
            WHERE return_status = 'accepted'
              AND (return_decision_date IS NULL OR return_decision_date_key IS NULL)
        """,
        "sale_derived_amounts_reconcile": """
            SELECT count(*) FROM fact_order_line
            WHERE posted_gross_profit_eur != posted_net_sales_eur - posted_cogs_eur
               OR posted_invoice_total_eur != posted_taxable_base_eur + posted_vat_amount_eur
        """,
        "outbound_freight_reconciles_by_order": """
            SELECT count(*) FROM (
                SELECT fact.order_id
                FROM fact_order_line fact
                JOIN silver_orders orders USING (order_id)
                GROUP BY fact.order_id, orders.outbound_freight_eur
                HAVING sum(fact.allocated_outbound_freight_eur) != orders.outbound_freight_eur
            )
        """,
        "canonical_posting_cardinality": """
            SELECT abs(
                (SELECT count(*) FROM fact_financial_posting)
                - (
                    (SELECT count(*) FROM fact_order_line WHERE order_status = 'fulfilled')
                    + (SELECT count(*) FROM fact_return_event WHERE return_status = 'accepted')
                )
            )
        """,
    }
    posting_results = _require_zero(connection, posting_checks)

    start = configuration.application.reporting.period_start
    end = configuration.application.reporting.period_end
    extension = configuration.application.reporting.extension_end
    date_min, date_max, date_count, reporting_count = connection.execute(
        """
        SELECT min(full_date), max(full_date), count(*),
               count(*) FILTER (WHERE is_within_reporting_window)
        FROM dim_date
        """
    ).fetchone()
    expected_date_count = (extension - start).days + 1
    expected_reporting_count = (end - start).days + 1
    if (
        date_min != start
        or date_max != extension
        or date_count != expected_date_count
        or reporting_count != expected_reporting_count
    ):
        raise WarehouseError(
            "date dimension does not cover the approved reporting and extension window"
        )

    financial_fields = (
        "posted_list_value_eur",
        "posted_discount_eur",
        "posted_net_sales_eur",
        "posted_cogs_eur",
        "posted_taxable_base_eur",
        "posted_vat_amount_eur",
        "posted_invoice_total_eur",
    )
    source_sales = connection.execute(
        "SELECT " + ", ".join(f"sum({field})" for field in financial_fields)
        + " FROM silver_order_lines"
    ).fetchone()
    warehouse_sales = connection.execute(
        "SELECT " + ", ".join(f"sum({field})" for field in financial_fields)
        + " FROM fact_order_line"
    ).fetchone()
    if source_sales != warehouse_sales:
        raise WarehouseError("sale financial postings do not reconcile to Silver")

    return_fields = (
        "posted_refund_ex_vat_eur",
        "posted_recovered_cogs_eur",
        "posted_vat_reversal_eur",
        "returns_freight_eur",
    )
    source_returns = connection.execute(
        "SELECT " + ", ".join(f"sum({field})" for field in return_fields)
        + " FROM silver_returns"
    ).fetchone()
    warehouse_returns = connection.execute(
        "SELECT " + ", ".join(f"sum({field})" for field in return_fields)
        + " FROM fact_return_event"
    ).fetchone()
    if source_returns != warehouse_returns:
        raise WarehouseError("return financial postings do not reconcile to Silver")

    source_freight = _scalar(connection, "SELECT sum(outbound_freight_eur) FROM silver_orders")
    warehouse_freight = _scalar(
        connection, "SELECT sum(allocated_outbound_freight_eur) FROM fact_order_line"
    )
    if source_freight != warehouse_freight:
        raise WarehouseError("allocated outbound freight does not reconcile to order freight")

    target_source = connection.execute(
        """
        SELECT metric_code, sum(target_value)
        FROM silver_sales_targets GROUP BY metric_code ORDER BY metric_code
        """
    ).fetchall()
    target_warehouse = connection.execute(
        """
        SELECT metric_code, sum(target_value)
        FROM fact_target_allocation GROUP BY metric_code ORDER BY metric_code
        """
    ).fetchall()
    if target_source != target_warehouse:
        raise WarehouseError("target allocations do not reconcile to Silver")

    ledger_totals = connection.execute(
        """
        SELECT
            sum(fulfilled_sales_eur),
            sum(accepted_refunds_eur),
            sum(net_revenue_eur),
            sum(net_cogs_eur),
            sum(net_gross_profit_eur),
            sum(outbound_freight_eur),
            sum(returns_freight_eur),
            sum(contribution_after_freight_eur)
        FROM fact_financial_posting
        """
    ).fetchone()
    historical_totals = connection.execute(
        """
        SELECT sum(net_revenue_eur), sum(net_cogs_eur), sum(net_gross_profit_eur)
        FROM fact_financial_posting_historical
        """
    ).fetchone()
    if ledger_totals[2] - ledger_totals[3] != ledger_totals[4]:
        raise WarehouseError("canonical posting ledger gross profit does not reconcile")
    if ledger_totals[4] - ledger_totals[5] - ledger_totals[6] != ledger_totals[7]:
        raise WarehouseError("canonical posting ledger freight contribution does not reconcile")

    private_columns = {
        row[0]
        for row in connection.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'main'"
        ).fetchall()
    } & PRIVATE_LABEL_NAMES
    if private_columns:
        raise WarehouseError(f"private metadata leaked into warehouse: {sorted(private_columns)}")

    return {
        "row_conservation": {"status": "passed", "row_counts": row_counts},
        "key_validation": key_results,
        "relationship_validation": relationship_results,
        "temporal_validation": temporal_results,
        "posting_validation": posting_results,
        "financial_reconciliation": {
            "status": "passed",
            "sale_posting_totals": {
                field: _decimal_text(value)
                for field, value in zip(financial_fields, warehouse_sales, strict=True)
            },
            "return_posting_totals": {
                field: _decimal_text(value)
                for field, value in zip(return_fields, warehouse_returns, strict=True)
            },
            "outbound_freight_eur": _decimal_text(warehouse_freight),
            "extended_ledger": {
                name: _decimal_text(value)
                for name, value in zip(
                    (
                        "fulfilled_sales_eur",
                        "accepted_refunds_eur",
                        "net_revenue_eur",
                        "net_cogs_eur",
                        "net_gross_profit_eur",
                        "outbound_freight_eur",
                        "returns_freight_eur",
                        "contribution_after_freight_eur",
                    ),
                    ledger_totals,
                    strict=True,
                )
            },
            "historical_ledger": {
                name: _decimal_text(value)
                for name, value in zip(
                    ("net_revenue_eur", "net_cogs_eur", "net_gross_profit_eur"),
                    historical_totals,
                    strict=True,
                )
            },
        },
        "target_reconciliation": {
            "status": "passed",
            "metric_totals": {metric: _decimal_text(value) for metric, value in target_warehouse},
        },
        "private_metadata": {"status": "passed", "forbidden_columns": []},
    }


def _prepare_database(
    configuration: ProjectConfiguration,
    source: Path,
    staged_output: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tables, source_manifest, source_manifest_hash = _read_silver(configuration, source)
    staged_output.mkdir()
    database_path = staged_output / DATABASE_NAME
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute("PRAGMA threads=1")
        for name, table in tables.items():
            connection.register(f"silver_{name}", table)
        connection.execute(
            """
            CREATE TEMP TABLE build_lineage AS
            SELECT ?::VARCHAR AS source_batch_id,
                   ?::VARCHAR AS source_silver_manifest_sha256
            """,
            [source_manifest["source_batch_id"], source_manifest_hash],
        )
        _execute_models(connection)
        validations = _validate_warehouse(connection, configuration, source_manifest)
        connection.execute("CHECKPOINT")
    except (duckdb.Error, OSError) as exc:
        raise WarehouseError(f"warehouse build failed: {exc}") from exc
    finally:
        connection.close()

    database_hash = _file_hash(database_path)
    objects = {
        name: {
            **contract,
            "object_type": "table",
            "row_count": validations["row_conservation"]["row_counts"][name],
        }
        for name, contract in TABLE_CONTRACTS.items()
    }
    objects.update(
        {
            name: {
                **contract,
                "object_type": "view",
                "row_count": validations["row_conservation"]["row_counts"][name],
            }
            for name, contract in VIEW_CONTRACTS.items()
        }
    )
    reporting = configuration.application.reporting
    manifest = {
        "schema_id": "bsi.warehouse_publication",
        "schema_version": 1,
        "stage": "warehouse",
        "pipeline_version": __version__,
        "publication_status": "passed",
        "source_silver_manifest_sha256": source_manifest_hash,
        "source_batch_id": source_manifest["source_batch_id"],
        "build_config_hash": configuration.deterministic_hash(),
        "stage_duration_seconds": 0.0,
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "packages": {
                package: importlib.metadata.version(package)
                for package in ("duckdb", "pyarrow")
            },
        },
        "database": {"path": DATABASE_NAME, "sha256": database_hash},
        "objects": objects,
        "reporting_window": {
            "period_start": str(reporting.period_start),
            "period_end": str(reporting.period_end),
            "operational_extension_end": str(reporting.extension_end),
            "default_as_of_date": str(reporting.default_as_of_date),
            "default_view": "fact_financial_posting_historical",
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
        raise WarehouseError(f"warehouse output already exists; use --force: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    backup = output.with_name(f".{output.name}.previous-{os.getpid()}")
    if backup.exists():
        raise WarehouseError(f"warehouse publication backup path already exists: {backup}")
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


def build_warehouse(
    configuration: ProjectConfiguration,
    silver_directory: str | Path,
    output_directory: str | Path,
    *,
    publish: bool = True,
    force: bool = False,
) -> WarehouseBuildResult:
    """Validate Silver, build all warehouse objects, and publish only after all gates pass."""

    source = Path(silver_directory)
    output = Path(output_directory)
    if publish and _has_material_output(output) and not force:
        raise WarehouseError(f"warehouse output already exists; use --force: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".warehouse-", dir=output.parent) as temporary:
        staged = Path(temporary) / "database"
        manifest, validations = _prepare_database(configuration, source, staged)
        if publish:
            _publish_directory(staged, output, force)
    return WarehouseBuildResult(
        manifest=manifest,
        validations=validations,
        output_directory=output if publish else None,
        database_path=(output / DATABASE_NAME) if publish else None,
    )
