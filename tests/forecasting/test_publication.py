"""Miniature source, output, lineage, and atomic-publication checks."""

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from baltic_sales_intelligence.customer.builder import _publish_directory, _validate_manifest
from baltic_sales_intelligence.forecasting.builder import (
    _create_output_tables,
    _objects,
    _persist_anomalies,
    _persist_forecasting,
    _validate_monthly,
    _validate_output,
)
from baltic_sales_intelligence.forecasting.contracts import ForecastContractError
from baltic_sales_intelligence.forecasting.core import add_months
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES


def _mini_monthly() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA gold")
    con.execute("CREATE SCHEMA warehouse")
    con.execute(
        "CREATE TABLE gold.mart_commercial_month(reporting_month DATE,"
        "home_country_code VARCHAR,net_revenue_eur DECIMAL(18,2),"
        "net_gross_profit_eur DECIMAL(18,2),posting_count BIGINT)"
    )
    con.execute(
        "CREATE TABLE gold.mart_company_month(reporting_month DATE,"
        "net_revenue_eur DECIMAL(18,2),net_gross_profit_eur DECIMAL(18,2),"
        "posting_count BIGINT,is_complete_period BOOLEAN,as_of_date DATE)"
    )
    con.execute(
        "CREATE TABLE warehouse.fact_financial_posting_historical("
        "posting_date DATE,home_country_code VARCHAR,net_revenue_eur DECIMAL(18,2),"
        "net_gross_profit_eur DECIMAL(18,2))"
    )
    for index in range(57):
        month = add_months(date(2022, 1, 1), index)
        for country in ("LT", "LV", "EE"):
            con.execute(
                "INSERT INTO gold.mart_commercial_month VALUES (?,?,?,?,?)",
                [month, country, Decimal("10.00"), Decimal("3.00"), 1],
            )
            con.execute(
                "INSERT INTO warehouse.fact_financial_posting_historical VALUES (?,?,?,?)",
                [month, country, Decimal("10.00"), Decimal("3.00")],
            )
        con.execute(
            "INSERT INTO gold.mart_company_month VALUES (?,?,?,?,?,?)",
            [month, Decimal("30.00"), Decimal("9.00"), 3, True, date(2026, 9, 30)],
        )
    con.execute(
        "CREATE TABLE build_parameters AS SELECT DATE '2022-01-01' "
        "period_start_month,DATE '2026-09-01' as_of_month,"
        "DATE '2026-09-30' as_of_date"
    )
    sql = Path("sql/analysis/forecasting_monthly.sql").read_text()
    con.execute(sql)
    return con


def test_canonical_monthly_finance_and_missing_month() -> None:
    con = _mini_monthly()
    try:
        result = _validate_monthly(con, date(2026, 9, 30))
        assert result["row_count"] == 228
        assert result["company_net_revenue_eur"] == "1710.00"
        assert result["missing_country_months"] == 0
        con.execute(
            "UPDATE sales_monthly SET net_revenue_eur=11 "
            "WHERE scope_code='LT' AND reporting_month=DATE '2022-01-01'"
        )
        with pytest.raises(ForecastContractError, match="reconcile"):
            _validate_monthly(con, date(2026, 9, 30))
    finally:
        con.close()


def test_missing_country_month_is_explicit_and_reconciled() -> None:
    con = _mini_monthly()
    try:
        # Remove one source month and its posting, then regenerate the monthly table.
        con.execute(
            "DELETE FROM gold.mart_commercial_month WHERE "
            "home_country_code='LV' AND reporting_month=DATE '2022-01-01'"
        )
        con.execute(
            "DELETE FROM warehouse.fact_financial_posting_historical WHERE "
            "home_country_code='LV' AND posting_date=DATE '2022-01-01'"
        )
        con.execute(
            "UPDATE gold.mart_company_month SET net_revenue_eur=20,"
            "net_gross_profit_eur=6,posting_count=2 "
            "WHERE reporting_month=DATE '2022-01-01'"
        )
        con.execute("DROP TABLE sales_monthly")
        con.execute(Path("sql/analysis/forecasting_monthly.sql").read_text())
        result = _validate_monthly(con, date(2026, 9, 30))
        assert result["missing_country_months"] == 1
        assert con.execute(
            "SELECT net_revenue_eur,source_month_present FROM sales_monthly "
            "WHERE scope_code='LV' AND reporting_month=DATE '2022-01-01'"
        ).fetchone() == (Decimal("0.00"), False)
    finally:
        con.close()


def test_output_policy_and_private_columns() -> None:
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE sales_monthly(reporting_month DATE,scope_code VARCHAR)")
        _create_output_tables(con)
        _persist_anomalies(con, [], date(2026, 9, 30))
        statuses = dict(con.execute("SELECT metric,status FROM anomaly_metric_status").fetchall())
        assert statuses["mature_cohort_return_rate"] == "not_estimable"
        assert con.execute("SELECT count(*) FROM commercial_anomaly").fetchone()[0] == 0
        with pytest.raises(ForecastContractError, match="future forecast requires"):
            _validate_output(con, date(2026, 9, 30))
        assert all(item["row_count"] >= 0 for item in _objects(con, date(2026, 9, 30)).values())
        con.execute(
            f'ALTER TABLE sales_monthly ADD COLUMN "{next(iter(PRIVATE_LABEL_NAMES))}" VARCHAR'
        )
        with pytest.raises(ForecastContractError, match="private control"):
            _objects(con, date(2026, 9, 30))
    finally:
        con.close()


def test_bad_source_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import baltic_sales_intelligence.customer.builder as customer_builder

    source = tmp_path / "product"
    source.mkdir()
    database = source / "product_intelligence.duckdb"
    database.write_bytes(b"actual")
    manifest = {
        "schema_id": "bsi.product_intelligence_publication",
        "schema_version": 1,
        "stage": "product",
        "publication_status": "passed",
        "build_config_hash": "hash",
        "source_batch_id": "batch",
        "database": {"path": database.name, "sha256": hashlib.sha256(b"different").hexdigest()},
    }
    (source / "product_intelligence_manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(
        customer_builder.ProjectConfiguration, "deterministic_hash", lambda self: "hash"
    )
    with pytest.raises(ValueError, match="checksum mismatch"):
        _validate_manifest(
            customer_builder.ProjectConfiguration.model_construct(),
            source,
            "product_intelligence_manifest.json",
            "product",
            "bsi.product_intelligence_publication",
            database.name,
        )


def test_forecast_records_fit_declared_output_schema() -> None:
    con = duckdb.connect()
    try:
        _create_output_tables(con)
        origin = date(2025, 1, 1)
        _persist_forecasting(
            con,
            [
                dict(
                    scope="LT",
                    origin_month=origin,
                    target_month=date(2025, 2, 1),
                    horizon=1,
                    model="selected",
                    source_model="seasonal_naive",
                    training_months=37,
                    actual_eur=Decimal(11),
                    predicted_eur=Decimal(10),
                    error_eur=Decimal(1),
                    interval_status="insufficient_history",
                    calibration_n=0,
                )
            ],
            [
                dict(
                    scope="LT",
                    origin_month=origin,
                    model="seasonal_naive",
                    training_months=37,
                    status="passed",
                )
            ],
            [
                dict(
                    scope="LT",
                    origin_month=origin,
                    horizon=1,
                    selected_model="seasonal_naive",
                    reason="baseline_preferred",
                    paired_origins=0,
                    baseline_wape=None,
                    selected_wape=None,
                )
            ],
            [
                dict(
                    scope="LT",
                    horizon=1,
                    model="selected",
                    n=1,
                    origin_first=origin,
                    origin_last=origin,
                    wape=1 / 11,
                    mae_eur=1.0,
                    rmse_eur=1.0,
                    mean_error_eur=1.0,
                    smape=2 / 21,
                    interval_n=0,
                    coverage80=None,
                    coverage95=None,
                    mean_width80_eur=None,
                    mean_width95_eur=None,
                )
            ],
            [
                dict(
                    scope="LT",
                    origin_month=date(2026, 9, 1),
                    target_month=date(2026, 10, 1),
                    horizon=1,
                    source_model="seasonal_naive",
                    training_months=57,
                    predicted_eur=Decimal(10),
                    interval_status="insufficient_history",
                    calibration_n=0,
                )
            ],
        )
        for table in (
            "walkforward_prediction",
            "model_fit_diagnostic",
            "model_selection",
            "model_evaluation",
            "future_forecast",
        ):
            assert con.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
    finally:
        con.close()


def test_future_output_coherence_and_nested_bounds() -> None:
    con = duckdb.connect()
    try:
        _create_output_tables(con)
        _persist_anomalies(con, [], date(2026, 9, 30))
        for horizon in (1, 3, 6):
            for scope, amount in (("LT", 10), ("LV", 20), ("EE", 30), ("COMPANY", 60)):
                con.execute(
                    "INSERT INTO future_forecast VALUES (" + ",".join("?" * 15) + ")",
                    [
                        scope,
                        date(2026, 9, 1),
                        add_months(date(2026, 9, 1), horizon),
                        horizon,
                        "bottom_up_selected_countries" if scope == "COMPANY" else "seasonal_naive",
                        57,
                        Decimal(amount),
                        "calibrated",
                        10,
                        Decimal(amount - 2),
                        Decimal(amount + 2),
                        Decimal(amount - 4),
                        Decimal(amount + 4),
                        Decimal(60) if scope == "COMPANY" else None,
                        Decimal(0) if scope == "COMPANY" else None,
                    ],
                )
        assert _validate_output(con, date(2026, 9, 30))["status"] == "passed"
        con.execute(
            "UPDATE future_forecast SET interval95_low_eur=interval80_low_eur+1 "
            "WHERE scope_code='LT' AND horizon_months=1"
        )
        with pytest.raises(ForecastContractError, match="nesting"):
            _validate_output(con, date(2026, 9, 30))
    finally:
        con.close()


def test_atomic_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import baltic_sales_intelligence.customer.builder as customer_builder

    output = tmp_path / "output"
    staged = tmp_path / "staged"
    output.mkdir()
    staged.mkdir()
    (output / "old.txt").write_text("old")
    (staged / "new.txt").write_text("new")
    real_replace = customer_builder.os.replace

    def fail_second(source: Path, target: Path) -> None:
        if source == staged:
            raise OSError("simulated publication failure")
        real_replace(source, target)

    monkeypatch.setattr(customer_builder.os, "replace", fail_second)
    with pytest.raises(OSError, match="simulated"):
        _publish_directory(staged, output, True)
    assert (output / "old.txt").read_text() == "old"
    assert not (output / "new.txt").exists()
