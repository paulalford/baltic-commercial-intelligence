"""Focused Milestone 8 Gold analytical-layer acceptance tests."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from shutil import copytree

import duckdb
import pytest

from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES
from baltic_sales_intelligence.gold import GoldError, build_gold
from baltic_sales_intelligence.gold.builder import DATABASE_NAME, MANIFEST_NAME, MART_CONTRACTS


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_gold_schema_grains_lineage_and_no_fanout(gold_publication) -> None:
    manifest = gold_publication.manifest
    assert manifest["schema_id"] == "bsi.gold_publication"
    assert manifest["schema_version"] == 1
    assert manifest["stage"] == "analytics"
    assert manifest["publication_status"] == "passed"
    assert set(manifest["objects"]) == set(MART_CONTRACTS)
    assert all(
        item["schema_id"] == f"bsi.gold.{name}"
        and item["schema_version"] == 1
        and item["grain"]
        and item["key"]
        and item["measures"]
        and item["dimensional_compatibility"]
        for name, item in manifest["objects"].items()
    )

    connection = duckdb.connect(str(gold_publication.database_path), read_only=True)
    try:
        inventory = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'main' AND table_catalog = current_database()
                """
            ).fetchall()
        }
        assert inventory == set(MART_CONTRACTS)
        source_count = connection.execute(
            "SELECT sum(posting_count) FROM mart_commercial_month"
        ).fetchone()[0]
        assert source_count == 4
        assert (
            connection.execute("SELECT sum(posting_count) FROM mart_customer_month").fetchone()[0]
            == source_count
        )
        types = {
            row[1]: row[2]
            for row in connection.execute("PRAGMA table_info('mart_commercial_month')").fetchall()
        }
        assert types["net_revenue_eur"] == "DECIMAL(18,2)"
        assert types["gross_margin_rate"] == "DECIMAL(18,6)"
        lineage = connection.execute(
            """
            SELECT DISTINCT source_batch_id, source_warehouse_manifest_sha256, as_of_date
            FROM mart_customer_month
            """
        ).fetchone()
        assert lineage == (
            manifest["source_batch_id"],
            manifest["source_warehouse_manifest_sha256"],
            date(2026, 9, 30),
        )
        columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = 'main' AND table_catalog = current_database()
                """
            ).fetchall()
        }
        assert not columns & PRIVATE_LABEL_NAMES
    finally:
        connection.close()


def test_exact_finance_scd_credit_cutoff_and_negative_values(gold_publication) -> None:
    connection = duckdb.connect(str(gold_publication.database_path), read_only=True)
    try:
        customer_months = connection.execute(
            """
            SELECT reporting_month, customer_version_id, fulfilled_sales_eur,
                   accepted_refunds_eur, net_revenue_eur, net_cogs_eur,
                   net_gross_profit_eur, outbound_freight_eur, returns_freight_eur,
                   contribution_after_freight_eur, gross_margin_rate,
                   gross_margin_reason
            FROM mart_customer_month
            ORDER BY reporting_month
            """
        ).fetchall()
        assert [
            (
                row[0],
                row[1],
                *(str(value) if value is not None else None for value in row[2:]),
            )
            for row in customer_months
        ] == [
            (
                date(2024, 7, 1),
                "CVER-01-A",
                "20.00",
                "0.00",
                "20.00",
                "10.00",
                "10.00",
                "0.00",
                "0.00",
                "10.00",
                "0.500000",
                None,
            ),
            (
                date(2025, 7, 1),
                "CVER-01-B",
                "40.00",
                "0.00",
                "40.00",
                "24.00",
                "16.00",
                "5.00",
                "0.00",
                "11.00",
                "0.400000",
                None,
            ),
            (
                date(2025, 8, 1),
                "CVER-01-B",
                "0.00",
                "9.00",
                "-9.00",
                "-6.33",
                "-2.67",
                "0.00",
                "1.00",
                "-3.67",
                None,
                "nonpositive_revenue",
            ),
        ]
        totals = connection.execute(
            """
            SELECT sum(fulfilled_sales_eur), sum(accepted_refunds_eur),
                   sum(net_revenue_eur), sum(net_cogs_eur),
                   sum(net_gross_profit_eur), sum(outbound_freight_eur),
                   sum(returns_freight_eur), sum(contribution_after_freight_eur)
            FROM mart_company_month
            """
        ).fetchone()
        assert tuple(map(str, totals)) == (
            "60.00",
            "9.00",
            "51.00",
            "27.67",
            "23.33",
            "5.00",
            "1.00",
            "17.33",
        )
        assert (
            connection.execute(
                """
            SELECT fulfilled_order_count FROM mart_customer_month
            WHERE reporting_month = DATE '2025-07-01'
            """
            ).fetchone()[0]
            == 1
        )
        assert (
            connection.execute(
                """
                SELECT count(*) FROM mart_commercial_month
                WHERE reporting_month > DATE '2026-09-01'
                """
            ).fetchone()[0]
            == 0
        )
        credited = connection.execute(
            """
            SELECT DISTINCT credited_salesperson_id
            FROM mart_commercial_month
            """
        ).fetchall()
        assert credited == [("SP-CREDIT",)]
    finally:
        connection.close()


def test_targets_complete_periods_and_safe_ratios(gold_publication) -> None:
    connection = duckdb.connect(str(gold_publication.database_path), read_only=True)
    try:
        july = connection.execute(
            """
            SELECT metric_code, scope_type, scope_key, target_value, actual_value,
                   uncredited_actual_value, attainment_rate, attainment_reason
            FROM mart_target_achievement_month
            WHERE target_month = DATE '2025-07-01'
            ORDER BY metric_code, scope_type, scope_key
            """
        ).fetchall()
        assert [
            (metric, scope, key, *(str(value) if value is not None else None for value in values))
            for metric, scope, key, *values in july
        ] == [
            ("gross_profit_eur", "company", "COMPANY", "50.00", "16.00", "0.00", "0.320000", None),
            (
                "gross_profit_eur",
                "country",
                "COUNTRY:LT",
                "50.00",
                "0.00",
                "-16.00",
                "0.000000",
                None,
            ),
            (
                "gross_profit_eur",
                "country",
                "COUNTRY:LV",
                "0.00",
                "16.00",
                "16.00",
                None,
                "zero_target",
            ),
            (
                "gross_profit_eur",
                "salesperson",
                "SALESPERSON:SP-CREDIT",
                "50.00",
                "16.00",
                None,
                "0.320000",
                None,
            ),
            (
                "gross_profit_eur",
                "salesperson",
                "SALESPERSON:SP-OWNER",
                "0.00",
                "0.00",
                None,
                None,
                "zero_target",
            ),
            ("net_revenue_eur", "company", "COMPANY", "200.00", "40.00", "0.00", "0.200000", None),
            (
                "net_revenue_eur",
                "country",
                "COUNTRY:LT",
                "120.00",
                "0.00",
                "-40.00",
                "0.000000",
                None,
            ),
            (
                "net_revenue_eur",
                "country",
                "COUNTRY:LV",
                "80.00",
                "40.00",
                "40.00",
                "0.500000",
                None,
            ),
            (
                "net_revenue_eur",
                "salesperson",
                "SALESPERSON:SP-CREDIT",
                "100.00",
                "40.00",
                None,
                "0.400000",
                None,
            ),
            (
                "net_revenue_eur",
                "salesperson",
                "SALESPERSON:SP-OWNER",
                "80.00",
                "0.00",
                None,
                "0.000000",
                None,
            ),
        ]
        company_span = connection.execute(
            """
            SELECT count(*), min(reporting_month), max(reporting_month),
                   bool_and(is_complete_period)
            FROM mart_company_month
            """
        ).fetchone()
        assert company_span == (57, date(2022, 1, 1), date(2026, 9, 1), True)
        assert (
            connection.execute(
                """
            SELECT count(*) FROM mart_target_achievement_month
            WHERE target_month > DATE '2026-09-30'
            """
            ).fetchone()[0]
            == 0
        )
        comparable = connection.execute(
            """
            SELECT prior_year_net_revenue_eur, revenue_yoy_rate, revenue_yoy_reason
            FROM mart_company_month WHERE reporting_month = DATE '2025-07-01'
            """
        ).fetchone()
        assert (str(comparable[0]), str(comparable[1]), comparable[2]) == (
            "20.00",
            "1.000000",
            None,
        )
        no_denominator = connection.execute(
            """
            SELECT gross_margin_rate, gross_margin_reason, revenue_yoy_rate,
                   revenue_yoy_reason
            FROM mart_company_month WHERE reporting_month = DATE '2022-01-01'
            """
        ).fetchone()
        assert no_denominator == (
            None,
            "nonpositive_revenue",
            None,
            "no_prior_comparable_period",
        )
    finally:
        connection.close()


def test_deterministic_dry_run_and_upstream_preservation(
    tmp_path: Path,
    gold_configuration: ProjectConfiguration,
    gold_warehouse,
    gold_publication,
) -> None:
    source_database = gold_warehouse.database_path
    source_manifest = gold_warehouse.output_directory / "warehouse_manifest.json"
    before = (_hash(source_database), _hash(source_manifest))
    rerun = build_gold(gold_configuration, gold_warehouse.output_directory, tmp_path / "rerun")
    assert rerun.manifest == gold_publication.manifest
    dry_output = tmp_path / "dry"
    dry_run = build_gold(
        gold_configuration,
        gold_warehouse.output_directory,
        dry_output,
        publish=False,
    )
    assert dry_run.manifest == gold_publication.manifest
    assert dry_run.database_path is None
    assert not dry_output.exists()
    assert (_hash(source_database), _hash(source_manifest)) == before


@pytest.mark.parametrize(
    "failure", ["missing_manifest", "missing_database", "tampered_database", "wrong_version"]
)
def test_invalid_warehouse_fails_closed(
    failure: str,
    tmp_path: Path,
    gold_configuration: ProjectConfiguration,
    gold_warehouse,
) -> None:
    source = tmp_path / "database"
    copytree(gold_warehouse.output_directory, source)
    if failure == "missing_manifest":
        (source / "warehouse_manifest.json").unlink()
    elif failure == "missing_database":
        (source / "baltic_sales.duckdb").unlink()
    elif failure == "wrong_version":
        manifest_path = source / "warehouse_manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["schema_version"] = 2
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        with (source / "baltic_sales.duckdb").open("ab") as handle:
            handle.write(b"tampered")
    output = tmp_path / "gold"
    with pytest.raises(GoldError):
        build_gold(gold_configuration, source, output)
    assert not output.exists()


def test_failed_force_build_preserves_previous_gold(
    tmp_path: Path,
    gold_configuration: ProjectConfiguration,
    gold_warehouse,
) -> None:
    output = tmp_path / "gold"
    build_gold(gold_configuration, gold_warehouse.output_directory, output)
    before = (_hash(output / DATABASE_NAME), _hash(output / MANIFEST_NAME))

    invalid_source = tmp_path / "invalid-database"
    copytree(gold_warehouse.output_directory, invalid_source)
    manifest_path = invalid_source / "warehouse_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["publication_status"] = "failed"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(GoldError):
        build_gold(gold_configuration, invalid_source, output, force=True)
    assert (_hash(output / DATABASE_NAME), _hash(output / MANIFEST_NAME)) == before
