"""Focused Milestone 9 customer-analytics acceptance tests."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from shutil import copytree

import duckdb
import pytest

from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.customer import CustomerAnalyticsError, build_customer_analytics
from baltic_sales_intelligence.customer.builder import DATASET_CONTRACTS
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES
from baltic_sales_intelligence.pipeline.cli import ExitCode, main
from test_support.warehouse_fixtures import CONFIG_DIRECTORY


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_contracts_lineage_grains_and_read_only_inputs(
    customer_publication, customer_inputs
) -> None:
    upstream = [
        customer_inputs.warehouse / "baltic_sales.duckdb",
        customer_inputs.warehouse / "warehouse_manifest.json",
        customer_inputs.gold / "baltic_sales_gold.duckdb",
        customer_inputs.gold / "gold_manifest.json",
    ]
    before = {path: _hash(path) for path in upstream}
    manifest = customer_publication.manifest
    assert manifest["schema_id"] == "bsi.customer_analytics_publication"
    assert manifest["stage"] == "customer"
    assert manifest["publication_status"] == "passed"
    assert manifest["analysis_window"] == {
        "period_start": "2022-01-01",
        "as_of_date": "2026-09-30",
        "rfm_lookback_start": "2025-10-01",
        "post_cutoff_excluded": True,
    }
    assert set(manifest["objects"]) == set(DATASET_CONTRACTS)
    assert all(
        item["grain"]
        and item["key"]
        and item["currency"]
        and item["units"]
        and item["definition"]
        and item["observation_boundary"]
        and item["source"]
        for item in manifest["objects"].values()
    )
    assert {path: _hash(path) for path in upstream} == before

    connection = duckdb.connect(str(customer_publication.database_path), read_only=True)
    try:
        inventory = {
            row[0]
            for row in connection.execute(
                """
                SELECT table_name FROM information_schema.tables
                WHERE table_schema='main' AND table_catalog=current_database()
                """
            ).fetchall()
        }
        assert inventory == set(DATASET_CONTRACTS)
        columns = {
            row[0]
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='main' AND table_catalog=current_database()
                """
            ).fetchall()
        }
        assert not columns & PRIVATE_LABEL_NAMES
        lineage_count = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT DISTINCT source_batch_id, source_warehouse_manifest_sha256,
                                source_gold_manifest_sha256, as_of_date
                FROM mart_customer_value
            )
            """
        ).fetchone()[0]
        assert lineage_count == 1
    finally:
        connection.close()


def test_realised_customer_value_scd_returns_and_exact_reconciliation(
    customer_publication,
) -> None:
    connection = duckdb.connect(str(customer_publication.database_path), read_only=True)
    try:
        c1 = connection.execute(
            """
            SELECT customer_version_id_as_of, segment_code_as_of, home_country_code_as_of,
                   first_fulfilled_date, last_fulfilled_date,
                   lifetime_fulfilled_order_count, fulfilled_sales_eur,
                   accepted_refunds_eur, net_revenue_eur, net_cogs_eur,
                   historical_value_net_gross_profit_eur, outbound_freight_eur,
                   returns_freight_eur, contribution_after_freight_eur,
                   value_status, value_basis
            FROM mart_customer_value WHERE customer_id='C1'
            """
        ).fetchone()
        assert c1[:6] == (
            "CV-C1-B",
            "S2",
            "LV",
            date(2024, 1, 15),
            date(2026, 9, 15),
            3,
        )
        assert tuple(str(value) for value in c1[6:14]) == (
            "180.00",
            "90.00",
            "90.00",
            "70.00",
            "20.00",
            "4.00",
            "5.00",
            "11.00",
        )
        assert c1[14:] == ("observed_historical_value", "observed_historical_not_predictive")
        no_purchase = connection.execute(
            """
            SELECT customer_id, first_fulfilled_date, net_revenue_eur, value_status
            FROM mart_customer_value WHERE customer_id IN ('C3', 'C7') ORDER BY customer_id
            """
        ).fetchall()
        assert no_purchase == [
            ("C3", None, Decimal("0.00"), "no_fulfilled_purchase"),
            ("C7", None, Decimal("0.00"), "no_fulfilled_purchase"),
        ]
        totals = connection.execute(
            """
            SELECT sum(fulfilled_sales_eur), sum(accepted_refunds_eur),
                   sum(net_revenue_eur), sum(net_cogs_eur),
                   sum(historical_value_net_gross_profit_eur),
                   sum(outbound_freight_eur), sum(returns_freight_eur),
                   sum(contribution_after_freight_eur)
            FROM mart_customer_value
            """
        ).fetchone()
        assert tuple(map(str, totals)) == (
            "470.00",
            "140.00",
            "330.00",
            "219.00",
            "111.00",
            "10.00",
            "7.00",
            "94.00",
        )
    finally:
        connection.close()


def test_rfm_window_boundaries_ties_negative_value_and_reactivation(
    customer_publication,
) -> None:
    connection = duckdb.connect(str(customer_publication.database_path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT customer_id, recency_days, frequency_orders_trailing_12m,
                   monetary_net_revenue_trailing_12m_eur, rfm_code, score_status,
                   is_active_buyer, is_new_buyer_in_lookback, is_lapsed_buyer,
                   is_reactivated_in_lookback
            FROM mart_rfm ORDER BY customer_id
            """
        ).fetchall()
        by_customer = {row[0]: row[1:] for row in rows}
        assert by_customer["C1"] == (
            15,
            1,
            Decimal("30.00"),
            "512",
            "scored_active_buyer",
            True,
            False,
            False,
            True,
        )
        assert by_customer["C4"][:4] == (121, 2, Decimal("80.00"), "454")
        assert by_customer["C5"][:4] == (182, 1, Decimal("80.00"), "225")
        assert by_customer["C2"][3:8] == (
            None,
            "not_estimable_no_purchase_in_lookback",
            False,
            False,
            True,
        )
        assert by_customer["C3"][3:8] == (
            None,
            "not_estimable_never_purchased",
            False,
            False,
            False,
        )
        assert by_customer["C7"][0] is None
        assert by_customer["C7"][3] is None
        assert connection.execute(
            "SELECT DISTINCT score_population_count FROM mart_rfm WHERE is_active_buyer"
        ).fetchall() == [(5,)]
    finally:
        connection.close()


def test_cohort_denominators_and_right_censoring(customer_publication) -> None:
    connection = duckdb.connect(str(customer_publication.database_path), read_only=True)
    try:
        april = connection.execute(
            """
            SELECT cohort_age_month, observation_month, cohort_customer_count,
                   eligible_customer_count, retained_customer_count, retention_rate,
                   is_observation_mature, not_estimable_reason
            FROM mart_cohort_retention
            WHERE cohort_month=DATE '2026-04-01' AND cohort_home_country_code='EE'
              AND cohort_age_month IN (0, 5, 6)
            ORDER BY cohort_age_month
            """
        ).fetchall()
        assert april == [
            (0, date(2026, 4, 1), 2, 2, 2, Decimal("1.000000"), True, None),
            (5, date(2026, 9, 1), 2, 2, 0, Decimal("0.000000"), True, None),
            (6, date(2026, 10, 1), 2, 0, None, None, False, "right_censored"),
        ]
        assert connection.execute(
            """
            SELECT retained_customer_count, retention_rate
            FROM mart_cohort_retention
            WHERE cohort_month=DATE '2024-01-01' AND cohort_home_country_code='LT'
              AND cohort_age_month=32
            """
        ).fetchone() == (1, Decimal("1.000000"))
    finally:
        connection.close()


def test_concentration_and_scd_migration_are_descriptive(customer_publication) -> None:
    connection = duckdb.connect(str(customer_publication.database_path), read_only=True)
    try:
        company = connection.execute(
            """
            SELECT eligible_customer_count, positive_balance_customer_count,
                   nonpositive_balance_customer_count, positive_net_revenue_denominator_eur,
                   top_1_positive_net_revenue_eur, top_5_positive_net_revenue_eur,
                   top_1_share, top_5_share, hhi
            FROM mart_customer_concentration
            WHERE scope_type='company'
            """
        ).fetchone()
        assert company[:6] == (
            8,
            5,
            3,
            Decimal("340.00"),
            Decimal("90.00"),
            Decimal("340.00"),
        )
        assert company[6:] == (
            Decimal("0.264706"),
            Decimal("1.000000"),
            Decimal("0.216263"),
        )
        lv = connection.execute(
            """
            SELECT positive_net_revenue_denominator_eur, top_1_share, hhi,
                   not_estimable_reason
            FROM mart_customer_concentration
            WHERE scope_type='country' AND scope_key='LV'
            """
        ).fetchone()
        assert lv == (
            Decimal("0.00"),
            None,
            None,
            "nonpositive_positive_balance_denominator",
        )
        migration = connection.execute(
            """
            SELECT from_customer_version_id, to_customer_version_id, transition_date,
                   segment_changed, company_size_changed, home_country_changed,
                   before_fulfilled_order_count, after_fulfilled_order_count,
                   before_net_revenue_eur, after_net_revenue_eur,
                   before_net_gross_profit_eur, after_net_gross_profit_eur, value_basis
            FROM mart_customer_migration WHERE customer_id='C1'
            """
        ).fetchone()
        assert migration == (
            "CV-C1-A",
            "CV-C1-B",
            date(2025, 7, 1),
            True,
            True,
            True,
            1,
            2,
            Decimal("100.00"),
            Decimal("-10.00"),
            Decimal("40.00"),
            Decimal("-20.00"),
            "descriptive_version_value_no_causal_inference",
        )
    finally:
        connection.close()


def test_deterministic_rerun_and_dry_run_does_not_publish(
    tmp_path: Path,
    customer_configuration: ProjectConfiguration,
    customer_inputs,
    customer_publication,
) -> None:
    second_output = tmp_path / "second"
    second = build_customer_analytics(
        customer_configuration,
        customer_inputs.warehouse,
        customer_inputs.gold,
        second_output,
    )
    assert second.manifest == customer_publication.manifest
    assert _hash(second.database_path) == _hash(customer_publication.database_path)
    dry_output = tmp_path / "dry"
    dry = build_customer_analytics(
        customer_configuration,
        customer_inputs.warehouse,
        customer_inputs.gold,
        dry_output,
        publish=False,
    )
    assert dry.manifest == customer_publication.manifest
    assert not dry_output.exists()


def test_corrupt_input_rejected_and_previous_publication_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    customer_configuration: ProjectConfiguration,
    customer_inputs,
) -> None:
    corrupt_gold = tmp_path / "corrupt-gold"
    copytree(customer_inputs.gold, corrupt_gold)
    manifest_path = corrupt_gold / "gold_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["database"]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CustomerAnalyticsError, match="checksum mismatch"):
        build_customer_analytics(
            customer_configuration,
            customer_inputs.warehouse,
            corrupt_gold,
            tmp_path / "rejected",
        )

    future_gold = tmp_path / "future-gold"
    copytree(customer_inputs.gold, future_gold)
    manifest_path = future_gold / "gold_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    database_path = future_gold / "baltic_sales_gold.duckdb"
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute(
            """
            INSERT INTO mart_customer_month VALUES (
                DATE '2026-10-01', 2, 'C1', 'LV', 1, 0, 1, 0,
                0.00, 999.00, -999.00, -1.00, -998.00, 0.00, 1.00, -999.00,
                ?, ?
            )
            """,
            [manifest["source_batch_id"], manifest["source_warehouse_manifest_sha256"]],
        )
        connection.execute("CHECKPOINT")
    finally:
        connection.close()
    manifest["database"]["sha256"] = _hash(database_path)
    manifest["objects"]["mart_customer_month"]["row_count"] += 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CustomerAnalyticsError, match="gold_reporting_window"):
        build_customer_analytics(
            customer_configuration,
            customer_inputs.warehouse,
            future_gold,
            tmp_path / "future-rejected",
        )

    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "preserved.txt"
    marker.write_text("previous publication", encoding="utf-8")
    real_replace = os.replace

    def fail_new_publication(source, destination):
        if ".staging-" in str(source) and Path(destination) == existing:
            raise OSError("simulated atomic swap failure")
        real_replace(source, destination)

    monkeypatch.setattr(
        "baltic_sales_intelligence.customer.builder.os.replace", fail_new_publication
    )
    with pytest.raises(OSError, match="simulated atomic swap failure"):
        build_customer_analytics(
            customer_configuration,
            customer_inputs.warehouse,
            customer_inputs.gold,
            existing,
            force=True,
        )
    assert marker.read_text(encoding="utf-8") == "previous publication"


def test_customer_cli_dry_run_and_publication(
    tmp_path: Path,
    customer_inputs,
) -> None:
    config = CONFIG_DIRECTORY
    output = tmp_path / "cli-customer"
    arguments = [
        "--config",
        str(config),
        "--stage",
        "customer",
        "--input",
        str(customer_inputs.warehouse.parent),
        "--output",
        str(output),
    ]
    assert main([*arguments, "--dry-run"]) == ExitCode.OK
    assert not output.exists()
    assert main(arguments) == ExitCode.OK
    assert (output / "customer_analytics.duckdb").is_file()
    assert (output / "customer_analytics_manifest.json").is_file()
