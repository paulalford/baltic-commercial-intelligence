"""Focused Milestone 7 warehouse acceptance tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from shutil import copytree

import duckdb
import pytest

from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES
from baltic_sales_intelligence.warehouse import WarehouseError, build_warehouse
from baltic_sales_intelligence.warehouse.builder import TABLE_CONTRACTS, VIEW_CONTRACTS


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_schema_grains_and_historical_attribution(warehouse_publication) -> None:
    manifest = warehouse_publication.manifest
    assert manifest["schema_id"] == "bsi.warehouse_publication"
    assert manifest["publication_status"] == "passed"
    assert set(manifest["objects"]) == set(TABLE_CONTRACTS) | set(VIEW_CONTRACTS)
    assert all(item["grain"] and item["key"] for item in manifest["objects"].values())
    assert all(
        validation["status"] == "passed"
        for group in (
            "key_validation",
            "relationship_validation",
            "temporal_validation",
            "posting_validation",
        )
        for validation in manifest["validations"][group].values()
    )

    connection = duckdb.connect(str(warehouse_publication.database_path), read_only=True)
    try:
        order_line_types = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info('fact_order_line')").fetchall()
        }
        assert order_line_types["order_date"] == "DATE"
        assert order_line_types["quantity_ordered"] == "BIGINT"
        assert order_line_types["list_unit_price_eur"] == "DECIMAL(18,4)"
        assert order_line_types["discount_rate"] == "DECIMAL(9,6)"
        assert order_line_types["posted_net_sales_eur"] == "DECIMAL(18,2)"
        assert order_line_types["allocated_outbound_freight_eur"] == "DECIMAL(18,2)"
        lineage = connection.execute(
            """
            SELECT DISTINCT source_batch_id, source_silver_manifest_sha256
            FROM fact_order_line
            """
        ).fetchone()
        assert lineage == (
            warehouse_publication.manifest["source_batch_id"],
            warehouse_publication.manifest["source_silver_manifest_sha256"],
        )
        order_keys = connection.execute(
            """
            SELECT
                customer_order.customer_version_id,
                customer_fulfilment.customer_version_id,
                sale.credited_salesperson_id
            FROM fact_order_line sale
            JOIN dim_customer customer_order
              ON customer_order.customer_sk = sale.customer_sk_order
            JOIN dim_customer customer_fulfilment
              ON customer_fulfilment.customer_sk = sale.customer_sk_fulfilment
            WHERE sale.order_line_id = 'OL-01'
            """
        ).fetchone()
        assert order_keys == ("CVER-01-A", "CVER-01-B", "SP-CREDIT")
        owner = connection.execute(
            """
            SELECT salesperson_id FROM bridge_customer_owner
            WHERE customer_id = 'CUST-01'
              AND DATE '2025-07-01' >= valid_from
              AND (valid_to IS NULL OR DATE '2025-07-01' < valid_to)
            """
        ).fetchone()[0]
        assert owner == "SP-OWNER"
        assert owner != order_keys[2]

        return_version = connection.execute(
            """
            SELECT customer.customer_version_id
            FROM fact_return_event event
            JOIN dim_customer customer ON customer.customer_sk = event.customer_sk_return
            WHERE event.return_id = 'RET-01'
            """
        ).fetchone()[0]
        assert return_version == "CVER-01-B"
        assert connection.execute(
            "SELECT relationship_type FROM bridge_product_supplier"
        ).fetchall() == [("primary",)]
    finally:
        connection.close()


def test_exact_finance_cutoff_freight_returns_and_targets(warehouse_publication) -> None:
    connection = duckdb.connect(str(warehouse_publication.database_path), read_only=True)
    try:
        sale = connection.execute(
            """
            SELECT
                posted_list_value_eur,
                posted_discount_eur,
                posted_net_sales_eur,
                posted_cogs_eur,
                posted_gross_profit_eur,
                posted_vat_amount_eur,
                posted_invoice_total_eur
            FROM fact_order_line WHERE order_line_id = 'OL-01'
            """
        ).fetchone()
        assert tuple(map(str, sale)) == ("30.02", "3.00", "27.02", "19.00", "8.02", "5.40", "32.42")
        freight = connection.execute(
            """
            SELECT order_line_id, allocated_outbound_freight_eur
            FROM fact_order_line WHERE order_id = 'ORD-01' ORDER BY line_number
            """
        ).fetchall()
        assert [(line_id, str(value)) for line_id, value in freight] == [
            ("OL-01", "3.38"),
            ("OL-02", "1.62"),
        ]
        nonfulfilled = connection.execute(
            """
            SELECT order_status, posted_net_sales_eur, posted_cogs_eur, posted_vat_amount_eur
            FROM fact_order_line WHERE order_status != 'fulfilled' ORDER BY order_status
            """
        ).fetchall()
        assert all(str(value) == "0.00" for row in nonfulfilled for value in row[1:])

        returns = connection.execute(
            """
            SELECT
                return_status,
                posted_refund_ex_vat_eur,
                posted_recovered_cogs_eur,
                posted_vat_reversal_eur
            FROM fact_return_event ORDER BY return_id
            """
        ).fetchall()
        assert tuple(map(str, returns[0][1:])) == ("9.00", "6.33", "1.80")
        assert all(str(value) == "0.00" for row in returns[1:3] for value in row[1:])

        historical = connection.execute(
            """
            SELECT
                count(*), sum(net_revenue_eur), sum(net_cogs_eur),
                sum(net_gross_profit_eur), sum(contribution_after_freight_eur)
            FROM fact_financial_posting_historical
            """
        ).fetchone()
        extended = connection.execute(
            """
            SELECT count(*), sum(net_revenue_eur), sum(net_cogs_eur), sum(net_gross_profit_eur)
            FROM fact_financial_posting
            """
        ).fetchone()
        assert (historical[0], *map(str, historical[1:])) == (3, "31.00", "17.67", "13.33", "7.33")
        assert (extended[0], *map(str, extended[1:])) == (5, "106.00", "62.67", "43.33")
        assert connection.execute(
            "SELECT count(*) FROM fact_financial_posting WHERE posting_date > DATE '2026-09-30'"
        ).fetchone()[0] == 2
        joined_sales = connection.execute(
            """
            SELECT count(*), sum(sale.posted_net_sales_eur)
            FROM fact_order_line sale
            JOIN dim_product product USING (product_id)
            JOIN dim_channel channel USING (channel_code)
            JOIN dim_geography geography ON geography.city_id = sale.ship_to_city_id
            JOIN dim_customer customer ON customer.customer_sk = sale.customer_sk_order
            """
        ).fetchone()
        assert (joined_sales[0], str(joined_sales[1])) == (5, "140.00")
        targets = connection.execute(
            """
            SELECT metric_code, sum(target_value), count(*)
            FROM fact_target_allocation GROUP BY metric_code ORDER BY metric_code
            """
        ).fetchall()
        assert [(metric, str(value), count) for metric, value, count in targets] == [
            ("gross_profit_eur", "50.00", 1),
            ("net_revenue_eur", "200.00", 3),
        ]
    finally:
        connection.close()


def test_deterministic_build_and_no_private_metadata(
    tmp_path: Path,
    warehouse_configuration: ProjectConfiguration,
    miniature_silver: Path,
    warehouse_publication,
) -> None:
    rerun = build_warehouse(
        warehouse_configuration,
        miniature_silver,
        tmp_path / "rerun",
    )
    assert rerun.manifest == warehouse_publication.manifest
    connection = duckdb.connect(str(rerun.database_path), read_only=True)
    try:
        columns = {
            row[0]
            for row in connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'main'"
            ).fetchall()
        }
        assert not columns & PRIVATE_LABEL_NAMES
        assert connection.execute(
            """
            SELECT count(*) FROM fact_order_line sale
            LEFT JOIN fact_return_event event USING (order_line_id)
            """
        ).fetchone()[0] == 7
        assert connection.execute("SELECT count(*) FROM fact_order_line").fetchone()[0] == 5
    finally:
        connection.close()


def test_dry_run_validates_without_publication(
    tmp_path: Path,
    warehouse_configuration: ProjectConfiguration,
    miniature_silver: Path,
) -> None:
    output = tmp_path / "database"
    result = build_warehouse(
        warehouse_configuration,
        miniature_silver,
        output,
        publish=False,
    )
    assert result.manifest["publication_status"] == "passed"
    assert result.database_path is None
    assert not output.exists()


@pytest.mark.parametrize(
    "failure",
    ["missing_manifest", "missing_dataset", "tampered_dataset", "incompatible_manifest"],
)
def test_invalid_silver_fails_closed(
    failure: str,
    tmp_path: Path,
    warehouse_configuration: ProjectConfiguration,
    miniature_silver: Path,
) -> None:
    source = tmp_path / "silver"
    copytree(miniature_silver, source)
    if failure == "missing_manifest":
        (source / "silver_manifest.json").unlink()
    elif failure == "missing_dataset":
        (source / "products.parquet").unlink()
    elif failure == "incompatible_manifest":
        manifest_path = source / "silver_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["schema_version"] = 2
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        with (source / "products.parquet").open("ab") as handle:
            handle.write(b"tampered")
    output = tmp_path / "database"
    with pytest.raises(WarehouseError):
        build_warehouse(warehouse_configuration, source, output)
    assert not output.exists()


def test_failed_force_build_preserves_previous_publication(
    tmp_path: Path,
    warehouse_configuration: ProjectConfiguration,
    miniature_silver: Path,
) -> None:
    output = tmp_path / "database"
    build_warehouse(warehouse_configuration, miniature_silver, output)
    database_hash = _hash(output / "baltic_sales.duckdb")
    manifest_hash = _hash(output / "warehouse_manifest.json")

    invalid_source = tmp_path / "invalid-silver"
    copytree(miniature_silver, invalid_source)
    manifest_path = invalid_source / "silver_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["quality_status"] = "failed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(WarehouseError):
        build_warehouse(warehouse_configuration, invalid_source, output, force=True)
    assert _hash(output / "baltic_sales.duckdb") == database_hash
    assert _hash(output / "warehouse_manifest.json") == manifest_hash
