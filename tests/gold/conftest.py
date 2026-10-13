"""Independent miniature warehouse publication for Gold tests."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from baltic_sales_intelligence.config import ProjectConfiguration, load_configuration
from baltic_sales_intelligence.generation.reference_data import PUBLIC_COLUMNS
from baltic_sales_intelligence.gold import GoldBuildResult, build_gold
from baltic_sales_intelligence.warehouse import WarehouseBuildResult, build_warehouse
from test_support.warehouse_fixtures import CONFIG_DIRECTORY, _frames, _hash, _write


def _gold_frames() -> dict[str, pd.DataFrame]:
    frames = _frames()
    zero = Decimal("0.00")
    frames["returns"].loc[frames["returns"]["return_id"] == "RET-01", "return_decision_date"] = (
        date(2025, 8, 2)
    )
    second_product = pd.DataFrame(
        [
            (
                "PROD-02",
                "SKU-02",
                "Fixture Sleeves",
                "SUB-01",
                date(2022, 1, 1),
                None,
                "active",
                Decimal("15.0000"),
                Decimal("5.0000"),
            )
        ],
        columns=PUBLIC_COLUMNS["products"],
    )
    second_supplier = pd.DataFrame(
        [("PSR-02", "PROD-02", "SUP-01", "primary", date(2022, 1, 1), None)],
        columns=PUBLIC_COLUMNS["product_supplier_relationships"],
    )
    prior_order = pd.DataFrame(
        [
            (
                "ORD-GOLD-PRIOR",
                "CUST-01",
                date(2024, 6, 30),
                date(2024, 7, 1),
                None,
                "WEB",
                "fulfilled",
                "LT-VILNIUS",
                "SP-CREDIT",
                "net_30",
                zero,
            )
        ],
        columns=PUBLIC_COLUMNS["orders"],
    )
    prior_line = pd.DataFrame(
        [
            (
                "OL-GOLD-PRIOR",
                "ORD-GOLD-PRIOR",
                1,
                "PROD-01",
                2,
                Decimal("10.0000"),
                Decimal("0.000000"),
                Decimal("10.0000"),
                Decimal("5.0000"),
                Decimal("20.00"),
                zero,
                Decimal("20.00"),
                Decimal("10.00"),
                "LT",
                "sim_standard",
                Decimal("0.200000"),
                Decimal("20.00"),
                Decimal("4.00"),
                Decimal("24.00"),
            )
        ],
        columns=PUBLIC_COLUMNS["order_lines"],
    )
    frames["orders"] = pd.concat([frames["orders"], prior_order], ignore_index=True)
    frames["products"] = pd.concat([frames["products"], second_product], ignore_index=True)
    frames["product_supplier_relationships"] = pd.concat(
        [frames["product_supplier_relationships"], second_supplier], ignore_index=True
    )
    frames["order_lines"].loc[frames["order_lines"]["order_line_id"] == "OL-02", "product_id"] = (
        "PROD-02"
    )
    frames["order_lines"] = pd.concat([frames["order_lines"], prior_line], ignore_index=True)
    additional_targets = pd.DataFrame(
        [
            (
                "TGT-GOLD-ZERO",
                date(2025, 7, 1),
                "gross_profit_eur",
                "salesperson",
                "SP-OWNER",
                "LV",
                zero,
            ),
            (
                "TGT-GOLD-POST",
                date(2026, 10, 1),
                "net_revenue_eur",
                "salesperson",
                "SP-CREDIT",
                "LT",
                Decimal("100.00"),
            ),
        ],
        columns=PUBLIC_COLUMNS["sales_targets"],
    )
    frames["sales_targets"] = pd.concat(
        [frames["sales_targets"], additional_targets], ignore_index=True
    )
    return frames


@pytest.fixture(scope="session")
def gold_configuration() -> ProjectConfiguration:
    return load_configuration(CONFIG_DIRECTORY)


@pytest.fixture(scope="session")
def gold_warehouse(
    tmp_path_factory,
    gold_configuration: ProjectConfiguration,
) -> WarehouseBuildResult:
    silver = tmp_path_factory.mktemp("gold-silver")
    datasets = {}
    for name, frame in _gold_frames().items():
        path = silver / f"{name}.parquet"
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
        "source_batch_id": "gold-fixture-batch",
        "config_hash": gold_configuration.deterministic_hash(),
        "datasets": datasets,
        "artifacts": {},
    }
    (silver / "silver_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    output = tmp_path_factory.mktemp("gold-warehouse") / "database"
    return build_warehouse(gold_configuration, silver, output)


@pytest.fixture(scope="session")
def gold_publication(
    tmp_path_factory,
    gold_configuration: ProjectConfiguration,
    gold_warehouse: WarehouseBuildResult,
) -> GoldBuildResult:
    output = tmp_path_factory.mktemp("gold-output") / "gold"
    return build_gold(gold_configuration, gold_warehouse.output_directory, output)
