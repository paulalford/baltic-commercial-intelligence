"""Shared miniature Silver and warehouse fixtures for focused analytical tests."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from baltic_sales_intelligence.config import ProjectConfiguration, load_configuration
from baltic_sales_intelligence.generation.reference_data import PUBLIC_COLUMNS
from baltic_sales_intelligence.quality.contracts import arrow_schema
from baltic_sales_intelligence.warehouse import WarehouseBuildResult, build_warehouse

CONFIG_DIRECTORY = Path(__file__).parents[1] / "config"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(frame: pd.DataFrame, path: Path, dataset: str) -> None:
    schema = arrow_schema(dataset)
    arrays = []
    for field in schema:
        values = [None if value is None or pd.isna(value) else value for value in frame[field.name]]
        arrays.append(pa.array(values, type=field.type))
    pq.write_table(pa.Table.from_arrays(arrays, schema=schema), path)


def _date_dimension() -> pd.DataFrame:
    rows = []
    for timestamp in pd.date_range("2022-01-01", "2026-12-31"):
        value = timestamp.date()
        iso = value.isocalendar()
        rows.append(
            (
                int(value.strftime("%Y%m%d")),
                value,
                value.year,
                (value.month - 1) // 3 + 1,
                value.month,
                value.replace(day=1),
                iso.week,
                iso.year,
                iso.weekday,
                value.day,
                value.timetuple().tm_yday,
                value.weekday() >= 5,
                value <= date(2026, 9, 30),
            )
        )
    return pd.DataFrame(rows, columns=PUBLIC_COLUMNS["date_dimension"])


def _frames() -> dict[str, pd.DataFrame]:
    money_zero = Decimal("0.00")
    frames = {
        "countries": pd.DataFrame(
            [("LT", "Lithuania", "EUR"), ("LV", "Latvia", "EUR"), ("EE", "Estonia", "EUR")],
            columns=PUBLIC_COLUMNS["countries"],
        ),
        "regions": pd.DataFrame(
            [("LT-01", "LT", "Vilnius", "fixture"), ("LV-01", "LV", "Riga", "fixture")],
            columns=PUBLIC_COLUMNS["regions"],
        ),
        "cities": pd.DataFrame(
            [
                ("LT-VILNIUS", "LT-01", "LT", "Vilnius"),
                ("LV-RIGA", "LV-01", "LV", "Riga"),
            ],
            columns=PUBLIC_COLUMNS["cities"],
        ),
        "customer_segments": pd.DataFrame(
            [("SMALL_BUSINESS", "Small Business"), ("MID_MARKET", "Mid-Market")],
            columns=PUBLIC_COLUMNS["customer_segments"],
        ),
        "industries": pd.DataFrame(
            [("MANUFACTURING", "Manufacturing")], columns=PUBLIC_COLUMNS["industries"]
        ),
        "product_categories": pd.DataFrame(
            [("CAT-01", "Safety Equipment")], columns=PUBLIC_COLUMNS["product_categories"]
        ),
        "product_subcategories": pd.DataFrame(
            [("SUB-01", "CAT-01", "Gloves")],
            columns=PUBLIC_COLUMNS["product_subcategories"],
        ),
        "suppliers": pd.DataFrame(
            [("SUP-01", "Fixture Supplier", "LT")], columns=PUBLIC_COLUMNS["suppliers"]
        ),
        "sales_channels": pd.DataFrame(
            [("WEB", "Web")], columns=PUBLIC_COLUMNS["sales_channels"]
        ),
        "customers": pd.DataFrame(
            [
                (
                    "CUST-01",
                    "Fixture Customer",
                    date(2022, 1, 1),
                    "active",
                    "MANUFACTURING",
                    "tier_1",
                    "WEB",
                )
            ],
            columns=PUBLIC_COLUMNS["customers"],
        ),
        "customer_attribute_history": pd.DataFrame(
            [
                (
                    "CVER-01-A",
                    "CUST-01",
                    date(2022, 1, 1),
                    date(2025, 7, 1),
                    "SMALL_BUSINESS",
                    "small",
                    "LT-VILNIUS",
                    "LT-01",
                    "LT",
                    False,
                ),
                (
                    "CVER-01-B",
                    "CUST-01",
                    date(2025, 7, 1),
                    None,
                    "MID_MARKET",
                    "medium",
                    "LV-RIGA",
                    "LV-01",
                    "LV",
                    True,
                ),
            ],
            columns=PUBLIC_COLUMNS["customer_attribute_history"],
        ),
        "salespeople": pd.DataFrame(
            [
                ("SP-CREDIT", "Credited Rep", "LT", "LT-01", date(2022, 1, 1), None, None),
                ("SP-OWNER", "Account Owner", "LV", "LV-01", date(2022, 1, 1), None, None),
            ],
            columns=PUBLIC_COLUMNS["salespeople"],
        ),
        "customer_account_assignments": pd.DataFrame(
            [("ASSIGN-01", "CUST-01", "SP-OWNER", date(2022, 1, 1), None)],
            columns=PUBLIC_COLUMNS["customer_account_assignments"],
        ),
        "products": pd.DataFrame(
            [
                (
                    "PROD-01",
                    "SKU-01",
                    "Fixture Gloves",
                    "SUB-01",
                    date(2022, 1, 1),
                    None,
                    "active",
                    Decimal("25.0000"),
                    Decimal("15.0000"),
                )
            ],
            columns=PUBLIC_COLUMNS["products"],
        ),
        "product_supplier_relationships": pd.DataFrame(
            [("PSR-01", "PROD-01", "SUP-01", "primary", date(2022, 1, 1), None)],
            columns=PUBLIC_COLUMNS["product_supplier_relationships"],
        ),
        "date_dimension": _date_dimension(),
        "orders": pd.DataFrame(
            [
                (
                    "ORD-01",
                    "CUST-01",
                    date(2025, 6, 30),
                    date(2025, 7, 1),
                    None,
                    "WEB",
                    "fulfilled",
                    "LV-RIGA",
                    "SP-CREDIT",
                    "net_30",
                    Decimal("5.00"),
                ),
                (
                    "ORD-02",
                    "CUST-01",
                    date(2026, 9, 29),
                    None,
                    None,
                    "WEB",
                    "placed",
                    "LT-VILNIUS",
                    None,
                    "net_30",
                    money_zero,
                ),
                (
                    "ORD-03",
                    "CUST-01",
                    date(2026, 9, 29),
                    None,
                    date(2026, 9, 30),
                    "WEB",
                    "cancelled",
                    "LT-VILNIUS",
                    None,
                    "net_30",
                    money_zero,
                ),
                (
                    "ORD-04",
                    "CUST-01",
                    date(2026, 10, 1),
                    date(2026, 10, 2),
                    None,
                    "WEB",
                    "fulfilled",
                    "LV-RIGA",
                    "SP-CREDIT",
                    "net_30",
                    Decimal("2.00"),
                ),
            ],
            columns=PUBLIC_COLUMNS["orders"],
        ),
        "order_lines": pd.DataFrame(
            [
                (
                    "OL-01",
                    "ORD-01",
                    1,
                    "PROD-01",
                    3,
                    Decimal("10.0050"),
                    Decimal("0.100000"),
                    Decimal("9.0045"),
                    Decimal("6.3333"),
                    Decimal("30.02"),
                    Decimal("3.00"),
                    Decimal("27.02"),
                    Decimal("19.00"),
                    "LT",
                    "sim_standard",
                    Decimal("0.200000"),
                    Decimal("27.02"),
                    Decimal("5.40"),
                    Decimal("32.42"),
                ),
                (
                    "OL-02",
                    "ORD-01",
                    2,
                    "PROD-01",
                    1,
                    Decimal("12.9800"),
                    Decimal("0.000000"),
                    Decimal("12.9800"),
                    Decimal("5.0000"),
                    Decimal("12.98"),
                    money_zero,
                    Decimal("12.98"),
                    Decimal("5.00"),
                    "LT",
                    "sim_zero",
                    Decimal("0.000000"),
                    Decimal("12.98"),
                    money_zero,
                    Decimal("12.98"),
                ),
                (
                    "OL-03",
                    "ORD-02",
                    1,
                    "PROD-01",
                    1,
                    Decimal("10.0000"),
                    Decimal("0.000000"),
                    Decimal("10.0000"),
                    Decimal("5.0000"),
                    money_zero,
                    money_zero,
                    money_zero,
                    money_zero,
                    "LT",
                    "sim_standard",
                    Decimal("0.200000"),
                    money_zero,
                    money_zero,
                    money_zero,
                ),
                (
                    "OL-04",
                    "ORD-03",
                    1,
                    "PROD-01",
                    1,
                    Decimal("10.0000"),
                    Decimal("0.000000"),
                    Decimal("10.0000"),
                    Decimal("5.0000"),
                    money_zero,
                    money_zero,
                    money_zero,
                    money_zero,
                    "LT",
                    "sim_standard",
                    Decimal("0.200000"),
                    money_zero,
                    money_zero,
                    money_zero,
                ),
                (
                    "OL-05",
                    "ORD-04",
                    1,
                    "PROD-01",
                    4,
                    Decimal("25.0000"),
                    Decimal("0.000000"),
                    Decimal("25.0000"),
                    Decimal("15.0000"),
                    Decimal("100.00"),
                    money_zero,
                    Decimal("100.00"),
                    Decimal("60.00"),
                    "LV",
                    "sim_standard",
                    Decimal("0.200000"),
                    Decimal("100.00"),
                    Decimal("20.00"),
                    Decimal("120.00"),
                ),
            ],
            columns=PUBLIC_COLUMNS["order_lines"],
        ),
        "returns": pd.DataFrame(
            [
                (
                    "RET-01",
                    "OL-01",
                    date(2025, 7, 1),
                    date(2025, 7, 2),
                    1,
                    1,
                    "accepted",
                    "defective",
                    "restockable",
                    Decimal("9.0045"),
                    Decimal("9.00"),
                    Decimal("6.33"),
                    Decimal("1.80"),
                    Decimal("1.00"),
                ),
                (
                    "RET-02",
                    "OL-01",
                    date(2025, 7, 3),
                    None,
                    1,
                    None,
                    "requested",
                    "not_as_expected",
                    None,
                    None,
                    money_zero,
                    money_zero,
                    money_zero,
                    money_zero,
                ),
                (
                    "RET-03",
                    "OL-01",
                    date(2025, 7, 3),
                    date(2025, 7, 4),
                    1,
                    None,
                    "rejected",
                    "not_as_expected",
                    None,
                    None,
                    money_zero,
                    money_zero,
                    money_zero,
                    money_zero,
                ),
                (
                    "RET-04",
                    "OL-05",
                    date(2026, 10, 2),
                    date(2026, 10, 3),
                    1,
                    1,
                    "accepted",
                    "defective",
                    "restockable",
                    Decimal("25.0000"),
                    Decimal("25.00"),
                    Decimal("15.00"),
                    Decimal("5.00"),
                    Decimal("0.50"),
                ),
            ],
            columns=PUBLIC_COLUMNS["returns"],
        ),
        "sales_targets": pd.DataFrame(
            [
                (
                    "TGT-01",
                    date(2025, 7, 1),
                    "net_revenue_eur",
                    "salesperson",
                    "SP-CREDIT",
                    "LT",
                    Decimal("100.00"),
                ),
                (
                    "TGT-02",
                    date(2025, 7, 1),
                    "net_revenue_eur",
                    "unallocated_country",
                    None,
                    "LT",
                    Decimal("20.00"),
                ),
                (
                    "TGT-03",
                    date(2025, 7, 1),
                    "net_revenue_eur",
                    "salesperson",
                    "SP-OWNER",
                    "LV",
                    Decimal("80.00"),
                ),
                (
                    "TGT-04",
                    date(2025, 7, 1),
                    "gross_profit_eur",
                    "salesperson",
                    "SP-CREDIT",
                    "LT",
                    Decimal("50.00"),
                ),
            ],
            columns=PUBLIC_COLUMNS["sales_targets"],
        ),
    }
    assert set(frames) == set(PUBLIC_COLUMNS)
    return frames


@pytest.fixture(scope="session")
def warehouse_configuration() -> ProjectConfiguration:
    return load_configuration(CONFIG_DIRECTORY)


@pytest.fixture(scope="session")
def miniature_silver(tmp_path_factory, warehouse_configuration: ProjectConfiguration) -> Path:
    output = tmp_path_factory.mktemp("warehouse-silver")
    datasets = {}
    for name, frame in _frames().items():
        path = output / f"{name}.parquet"
        _write(frame, path, name)
        datasets[name] = {
            "schema_id": f"bsi.silver.{name}",
            "schema_version": 1,
            "path": path.name,
            "row_count": len(frame),
            "columns": list(PUBLIC_COLUMNS[name]),
            "sha256": _hash(path),
        }
    manifest = {
        "schema_id": "bsi.silver_publication",
        "schema_version": 1,
        "stage": "validate",
        "quality_status": "passed",
        "source_batch_id": "fixture-batch",
        "config_hash": warehouse_configuration.deterministic_hash(),
        "datasets": datasets,
        "artifacts": {},
    }
    (output / "silver_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return output


@pytest.fixture(scope="session")
def warehouse_publication(
    tmp_path_factory,
    warehouse_configuration: ProjectConfiguration,
    miniature_silver: Path,
) -> WarehouseBuildResult:
    output = tmp_path_factory.mktemp("warehouse-output") / "database"
    return build_warehouse(warehouse_configuration, miniature_silver, output)
