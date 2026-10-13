"""Independent miniature upstream publications for customer-analytics tests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from baltic_sales_intelligence.config import ProjectConfiguration, load_configuration
from baltic_sales_intelligence.customer import (
    CustomerAnalyticsBuildResult,
    build_customer_analytics,
)
from test_support.warehouse_fixtures import CONFIG_DIRECTORY

BATCH_ID = "customer-fixture-batch"
SILVER_HASH = "a" * 64


@dataclass(frozen=True)
class CustomerInputs:
    warehouse: Path
    gold: Path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(path: Path, manifest: dict) -> str:
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _hash(path)


def _reporting_windows(configuration: ProjectConfiguration) -> tuple[dict, dict]:
    reporting = configuration.application.reporting
    common = {
        "period_start": str(reporting.period_start),
        "period_end": str(reporting.period_end),
        "operational_extension_end": str(reporting.extension_end),
        "default_as_of_date": str(reporting.default_as_of_date),
    }
    return (
        {**common, "default_view": "fact_financial_posting_historical"},
        {**common, "default_boundary": "historical_only"},
    )


def _build_warehouse(path: Path) -> dict[str, int]:
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE dim_customer (
                customer_sk BIGINT, customer_version_id VARCHAR, customer_id VARCHAR,
                customer_name VARCHAR, acquisition_date DATE, account_status VARCHAR,
                industry_code VARCHAR, industry_name VARCHAR, credit_tier VARCHAR,
                valid_from DATE, valid_to DATE, segment_code VARCHAR, segment_name VARCHAR,
                company_size_band VARCHAR, home_city_id VARCHAR, home_region_id VARCHAR,
                home_country_code VARCHAR
            )
            """
        )
        customers = [
            (
                1,
                "CV-C1-A",
                "C1",
                "Customer 1",
                date(2022, 1, 1),
                "active",
                "I1",
                "Trade",
                "A",
                date(2022, 1, 1),
                date(2025, 7, 1),
                "S1",
                "Core",
                "SMALL",
                "LT-VNO",
                "LT-VNO",
                "LT",
            ),
            (
                2,
                "CV-C1-B",
                "C1",
                "Customer 1",
                date(2022, 1, 1),
                "active",
                "I1",
                "Trade",
                "A",
                date(2025, 7, 1),
                None,
                "S2",
                "Growth",
                "MID",
                "LV-RIX",
                "LV-RIX",
                "LV",
            ),
            (
                3,
                "CV-C2",
                "C2",
                "Customer 2",
                date(2022, 3, 1),
                "active",
                "I1",
                "Trade",
                "B",
                date(2022, 3, 1),
                None,
                "S1",
                "Core",
                "SMALL",
                "LT-VNO",
                "LT-VNO",
                "LT",
            ),
            (
                4,
                "CV-C3",
                "C3",
                "Customer 3",
                date(2023, 1, 1),
                "active",
                "I2",
                "Services",
                "B",
                date(2023, 1, 1),
                None,
                "S1",
                "Core",
                "SMALL",
                "EE-TLL",
                "EE-TLL",
                "EE",
            ),
            (
                5,
                "CV-C4",
                "C4",
                "Customer 4",
                date(2024, 1, 1),
                "active",
                "I2",
                "Services",
                "A",
                date(2024, 1, 1),
                None,
                "S2",
                "Growth",
                "MID",
                "EE-TLL",
                "EE-TLL",
                "EE",
            ),
            (
                6,
                "CV-C5",
                "C5",
                "Customer 5",
                date(2025, 12, 1),
                "active",
                "I2",
                "Services",
                "A",
                date(2025, 12, 1),
                None,
                "S2",
                "Growth",
                "MID",
                "EE-TLL",
                "EE-TLL",
                "EE",
            ),
            (
                7,
                "CV-C6",
                "C6",
                "Customer 6",
                date(2024, 5, 1),
                "active",
                "I1",
                "Trade",
                "C",
                date(2024, 5, 1),
                None,
                "S1",
                "Core",
                "SMALL",
                "LV-RIX",
                "LV-RIX",
                "LV",
            ),
            (
                8,
                "CV-C7",
                "C7",
                "Customer 7",
                date(2026, 1, 1),
                "active",
                "I1",
                "Trade",
                "B",
                date(2026, 1, 1),
                None,
                "S1",
                "Core",
                "SMALL",
                "LT-VNO",
                "LT-VNO",
                "LT",
            ),
            (
                9,
                "CV-C8",
                "C8",
                "Customer 8",
                date(2025, 12, 1),
                "active",
                "I2",
                "Services",
                "A",
                date(2025, 12, 1),
                None,
                "S2",
                "Growth",
                "MID",
                "EE-TLL",
                "EE-TLL",
                "EE",
            ),
        ]
        connection.executemany(
            "INSERT INTO dim_customer VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            customers,
        )
        connection.execute(
            """
            CREATE TABLE fact_order_line (
                order_line_id VARCHAR, order_id VARCHAR, customer_id VARCHAR,
                customer_sk_fulfilment BIGINT,
                fulfilled_date DATE, order_status VARCHAR
            )
            """
        )
        orders = [
            ("OL-C1-1A", "O-C1-1", "C1", 1, date(2024, 1, 15), "fulfilled"),
            ("OL-C1-1B", "O-C1-1", "C1", 1, date(2024, 1, 15), "fulfilled"),
            ("OL-C1-2", "O-C1-2", "C1", 2, date(2025, 7, 1), "fulfilled"),
            ("OL-C1-3", "O-C1-3", "C1", 2, date(2026, 9, 15), "fulfilled"),
            ("OL-C2-1", "O-C2-1", "C2", 3, date(2024, 12, 1), "fulfilled"),
            ("OL-C4-1", "O-C4-1", "C4", 5, date(2025, 10, 1), "fulfilled"),
            ("OL-C4-2", "O-C4-2", "C4", 5, date(2026, 6, 1), "fulfilled"),
            ("OL-C5-1", "O-C5-1", "C5", 6, date(2026, 4, 1), "fulfilled"),
            ("OL-C6-1", "O-C6-1", "C6", 7, date(2025, 11, 1), "fulfilled"),
            ("OL-C7-F", "O-C7-FUTURE", "C7", 8, date(2026, 10, 1), "fulfilled"),
            ("OL-C8-1", "O-C8-1", "C8", 9, date(2026, 4, 15), "fulfilled"),
        ]
        connection.executemany("INSERT INTO fact_order_line VALUES (?, ?, ?, ?, ?, ?)", orders)
        connection.execute("CHECKPOINT")
        return {"dim_customer": len(customers), "fact_order_line": len(orders)}
    finally:
        connection.close()


def _build_gold(path: Path, warehouse_manifest_hash: str) -> int:
    connection = duckdb.connect(str(path))
    try:
        connection.execute(
            """
            CREATE TABLE mart_customer_month (
                reporting_month DATE, customer_sk BIGINT, customer_id VARCHAR,
                home_country_code VARCHAR, posting_count BIGINT,
                fulfilled_sale_posting_count BIGINT,
                accepted_return_posting_count BIGINT, fulfilled_order_count BIGINT,
                fulfilled_sales_eur DECIMAL(18,2), accepted_refunds_eur DECIMAL(18,2),
                net_revenue_eur DECIMAL(18,2), net_cogs_eur DECIMAL(18,2),
                net_gross_profit_eur DECIMAL(18,2), outbound_freight_eur DECIMAL(18,2),
                returns_freight_eur DECIMAL(18,2),
                contribution_after_freight_eur DECIMAL(18,2),
                source_batch_id VARCHAR, source_warehouse_manifest_sha256 VARCHAR
            )
            """
        )
        d = Decimal
        rows = [
            (
                date(2024, 1, 1),
                1,
                "C1",
                "LT",
                1,
                1,
                0,
                1,
                d("100"),
                d("0"),
                d("100"),
                d("60"),
                d("40"),
                d("2"),
                d("0"),
                d("38"),
            ),
            (
                date(2025, 7, 1),
                2,
                "C1",
                "LV",
                1,
                1,
                0,
                1,
                d("50"),
                d("0"),
                d("50"),
                d("30"),
                d("20"),
                d("1"),
                d("0"),
                d("19"),
            ),
            (
                date(2025, 8, 1),
                2,
                "C1",
                "LV",
                1,
                0,
                1,
                0,
                d("0"),
                d("90"),
                d("-90"),
                d("-40"),
                d("-50"),
                d("0"),
                d("5"),
                d("-55"),
            ),
            (
                date(2026, 9, 1),
                2,
                "C1",
                "LV",
                1,
                1,
                0,
                1,
                d("30"),
                d("0"),
                d("30"),
                d("20"),
                d("10"),
                d("1"),
                d("0"),
                d("9"),
            ),
            (
                date(2024, 12, 1),
                3,
                "C2",
                "LT",
                1,
                1,
                0,
                1,
                d("40"),
                d("0"),
                d("40"),
                d("25"),
                d("15"),
                d("1"),
                d("0"),
                d("14"),
            ),
            (
                date(2025, 10, 1),
                5,
                "C4",
                "EE",
                1,
                1,
                0,
                1,
                d("40"),
                d("0"),
                d("40"),
                d("25"),
                d("15"),
                d("1"),
                d("0"),
                d("14"),
            ),
            (
                date(2026, 6, 1),
                5,
                "C4",
                "EE",
                1,
                1,
                0,
                1,
                d("40"),
                d("0"),
                d("40"),
                d("25"),
                d("15"),
                d("1"),
                d("0"),
                d("14"),
            ),
            (
                date(2026, 4, 1),
                6,
                "C5",
                "EE",
                1,
                1,
                0,
                1,
                d("80"),
                d("0"),
                d("80"),
                d("50"),
                d("30"),
                d("1"),
                d("0"),
                d("29"),
            ),
            (
                date(2025, 11, 1),
                7,
                "C6",
                "LV",
                2,
                1,
                1,
                1,
                d("40"),
                d("50"),
                d("-10"),
                d("-6"),
                d("-4"),
                d("1"),
                d("2"),
                d("-7"),
            ),
            (
                date(2026, 4, 1),
                9,
                "C8",
                "EE",
                1,
                1,
                0,
                1,
                d("50"),
                d("0"),
                d("50"),
                d("30"),
                d("20"),
                d("1"),
                d("0"),
                d("19"),
            ),
        ]
        rows = [(*row, BATCH_ID, warehouse_manifest_hash) for row in rows]
        connection.executemany(
            "INSERT INTO mart_customer_month VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.execute("CHECKPOINT")
        return len(rows)
    finally:
        connection.close()


@pytest.fixture(scope="session")
def customer_configuration() -> ProjectConfiguration:
    return load_configuration(CONFIG_DIRECTORY)


@pytest.fixture(scope="session")
def customer_inputs(
    tmp_path_factory, customer_configuration: ProjectConfiguration
) -> CustomerInputs:
    root = tmp_path_factory.mktemp("customer-inputs")
    warehouse = root / "database"
    gold = root / "gold"
    warehouse.mkdir()
    gold.mkdir()
    warehouse_database = warehouse / "baltic_sales.duckdb"
    counts = _build_warehouse(warehouse_database)
    warehouse_window, gold_window = _reporting_windows(customer_configuration)
    warehouse_manifest = {
        "schema_id": "bsi.warehouse_publication",
        "schema_version": 1,
        "stage": "warehouse",
        "publication_status": "passed",
        "source_batch_id": BATCH_ID,
        "source_silver_manifest_sha256": SILVER_HASH,
        "build_config_hash": customer_configuration.deterministic_hash(),
        "database": {"path": warehouse_database.name, "sha256": _hash(warehouse_database)},
        "objects": {
            "dim_customer": {
                "object_type": "table",
                "grain": "one effective SCD2 customer version",
                "key": ["customer_sk"],
                "row_count": counts["dim_customer"],
            },
            "fact_order_line": {
                "object_type": "table",
                "grain": "one operational order line",
                "key": ["order_line_id"],
                "row_count": counts["fact_order_line"],
            },
        },
        "reporting_window": warehouse_window,
    }
    warehouse_manifest_hash = _write_manifest(
        warehouse / "warehouse_manifest.json", warehouse_manifest
    )
    gold_database = gold / "baltic_sales_gold.duckdb"
    gold_count = _build_gold(gold_database, warehouse_manifest_hash)
    gold_manifest = {
        "schema_id": "bsi.gold_publication",
        "schema_version": 1,
        "stage": "analytics",
        "publication_status": "passed",
        "source_batch_id": BATCH_ID,
        "source_warehouse_manifest_sha256": warehouse_manifest_hash,
        "source_warehouse_database_sha256": warehouse_manifest["database"]["sha256"],
        "source_silver_manifest_sha256": SILVER_HASH,
        "build_config_hash": customer_configuration.deterministic_hash(),
        "database": {"path": gold_database.name, "sha256": _hash(gold_database)},
        "objects": {
            "mart_customer_month": {
                "object_type": "table",
                "grain": "one reporting month and posting-date customer SCD version",
                "key": ["reporting_month", "customer_sk"],
                "row_count": gold_count,
            }
        },
        "reporting_window": gold_window,
    }
    _write_manifest(gold / "gold_manifest.json", gold_manifest)
    return CustomerInputs(warehouse=warehouse, gold=gold)


@pytest.fixture(scope="session")
def customer_publication(
    tmp_path_factory,
    customer_configuration: ProjectConfiguration,
    customer_inputs: CustomerInputs,
) -> CustomerAnalyticsBuildResult:
    output = tmp_path_factory.mktemp("customer-output") / "customer"
    return build_customer_analytics(
        customer_configuration, customer_inputs.warehouse, customer_inputs.gold, output
    )
