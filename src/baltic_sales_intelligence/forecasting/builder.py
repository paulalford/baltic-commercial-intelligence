"""Build and atomically publish Milestone 12 forecasting and anomaly outputs."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from baltic_sales_intelligence import __version__
from baltic_sales_intelligence.anomalies.core import METHOD_VERSION, assess_series
from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.customer.builder import _has_material_output, _publish_directory
from baltic_sales_intelligence.forecasting.contracts import (
    ForecastContractError,
    validate_inputs,
)
from baltic_sales_intelligence.forecasting.core import (
    HORIZONS,
    MIN_CALIBRATION,
    MIN_PAIRED,
    MIN_RELATIVE_WAPE_GAIN,
    MIN_TRAIN,
    MODELS,
    backtest_series,
    calibrate_backtest,
    evaluation_metrics,
    final_forecasts,
    select_prequential,
)
from baltic_sales_intelligence.generation.reference_data import PRIVATE_LABEL_NAMES

DATABASE_NAME = "forecasting_anomalies.duckdb"
MANIFEST_NAME = "forecasting_anomalies_manifest.json"
SCOPE_ORDER = ("LT", "LV", "EE", "COMPANY")


@dataclass(frozen=True)
class ForecastBuildResult:
    manifest: dict[str, Any]
    validations: dict[str, Any]
    output_directory: Path | None = None
    database_path: Path | None = None


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_monthly(con: duckdb.DuckDBPyConnection, cutoff: date) -> dict[str, Any]:
    """Check the complete calendar and exact Gold/Warehouse posted finance."""
    count, months, duplicate = con.execute(
        "SELECT count(*),count(DISTINCT reporting_month),"
        "count(*)-count(DISTINCT (reporting_month,scope_code)) FROM sales_monthly"
    ).fetchone()
    if (count, months, duplicate) != (228, 57, 0):
        raise ForecastContractError("sales_monthly must have 57 months x four unique scopes")
    invalid = con.execute(
        "SELECT count(*) FROM sales_monthly WHERE reporting_month<DATE '2022-01-01' "
        "OR reporting_month>DATE '2026-09-01' OR as_of_date<>? "
        "OR geography_role<>'customer_home_at_posting' OR date_basis<>'posting_date' "
        "OR currency_code<>'EUR' OR net_revenue_eur IS NULL OR net_gross_profit_eur IS NULL",
        [cutoff],
    ).fetchone()[0]
    if invalid:
        raise ForecastContractError("monthly actuals contain invalid period or metadata")
    source_invalid = con.execute(
        "SELECT count(*) FROM gold.mart_company_month "
        "WHERE NOT is_complete_period OR as_of_date<>? OR reporting_month>DATE '2026-09-01'",
        [cutoff],
    ).fetchone()[0]
    if source_invalid:
        raise ForecastContractError("Gold company months are incomplete or after cutoff")
    unknown = con.execute(
        "SELECT count(*) FROM gold.mart_commercial_month WHERE "
        "home_country_code IS NULL OR home_country_code NOT IN ('LT','LV','EE') "
        "OR reporting_month>DATE '2026-09-01'"
    ).fetchone()[0]
    if unknown:
        raise ForecastContractError("Gold posting geography or cutoff mismatch")
    missing_company = con.execute(
        "SELECT count(*) FROM sales_monthly WHERE scope_code='COMPANY' AND NOT source_month_present"
    ).fetchone()[0]
    if missing_company:
        raise ForecastContractError("company reporting month is missing")
    country_difference = con.execute("""SELECT count(*) FROM (
        SELECT reporting_month,
          sum(net_revenue_eur) FILTER(WHERE scope_type='country') AS countries_revenue,
          max(net_revenue_eur) FILTER(WHERE scope_type='company') AS company_revenue,
          sum(net_gross_profit_eur) FILTER(WHERE scope_type='country') AS countries_gp,
          max(net_gross_profit_eur) FILTER(WHERE scope_type='company') AS company_gp,
          sum(posting_count) FILTER(WHERE scope_type='country') AS countries_postings,
          max(posting_count) FILTER(WHERE scope_type='company') AS company_postings
        FROM sales_monthly GROUP BY reporting_month
    ) q WHERE countries_revenue<>company_revenue OR countries_gp<>company_gp
      OR countries_postings<>company_postings""").fetchone()[0]
    if country_difference:
        raise ForecastContractError("country and company monthly actuals do not reconcile")
    warehouse_difference = con.execute("""WITH posted AS (
      SELECT date_trunc('month',posting_date)::DATE reporting_month,home_country_code,
             sum(net_revenue_eur)::DECIMAL(18,2) revenue,
             sum(net_gross_profit_eur)::DECIMAL(18,2) gp,
             count(*)::BIGINT postings
      FROM warehouse.fact_financial_posting_historical
      GROUP BY 1,2
    ) SELECT count(*) FROM sales_monthly s LEFT JOIN posted w
      ON w.reporting_month=s.reporting_month AND w.home_country_code=s.scope_code
      WHERE s.scope_type='country' AND
      (s.net_revenue_eur<>coalesce(w.revenue,0) OR
       s.net_gross_profit_eur<>coalesce(w.gp,0) OR
       s.posting_count<>coalesce(w.postings,0))""").fetchone()[0]
    if warehouse_difference:
        raise ForecastContractError("Gold country months differ from Warehouse postings")
    company_difference = con.execute("""WITH posted AS (
      SELECT date_trunc('month',posting_date)::DATE reporting_month,
             sum(net_revenue_eur)::DECIMAL(18,2) revenue,
             sum(net_gross_profit_eur)::DECIMAL(18,2) gp,
             count(*)::BIGINT postings
      FROM warehouse.fact_financial_posting_historical GROUP BY 1
    ) SELECT count(*) FROM sales_monthly s LEFT JOIN posted w USING(reporting_month)
      WHERE s.scope_type='company' AND
      (s.net_revenue_eur<>coalesce(w.revenue,0) OR
       s.net_gross_profit_eur<>coalesce(w.gp,0) OR
       s.posting_count<>coalesce(w.postings,0))""").fetchone()[0]
    if company_difference:
        raise ForecastContractError("Gold company months differ from Warehouse postings")
    totals = con.execute("""SELECT sum(net_revenue_eur)::DECIMAL(18,2),
        sum(net_gross_profit_eur)::DECIMAL(18,2)
        FROM sales_monthly WHERE scope_type='company'""").fetchone()
    return {
        "status": "passed",
        "violations": 0,
        "month_count": months,
        "scope_count": 4,
        "row_count": count,
        "company_net_revenue_eur": str(totals[0]),
        "company_net_gross_profit_eur": str(totals[1]),
        "missing_country_months": con.execute(
            "SELECT count(*) FROM sales_monthly WHERE scope_type='country' "
            "AND NOT source_month_present"
        ).fetchone()[0],
    }


def _create_output_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""CREATE TABLE walkforward_prediction(
        scope_code VARCHAR,origin_month DATE,target_month DATE,horizon_months INTEGER,
        model VARCHAR,source_model VARCHAR,training_months INTEGER,
        actual_eur DECIMAL(18,2),predicted_eur DECIMAL(18,2),error_eur DECIMAL(18,2),
        interval_status VARCHAR,calibration_n INTEGER,
        interval80_low_eur DECIMAL(18,2),interval80_high_eur DECIMAL(18,2),
        interval95_low_eur DECIMAL(18,2),interval95_high_eur DECIMAL(18,2),
        coherence_delta_eur DECIMAL(18,2),
        PRIMARY KEY(scope_code,origin_month,horizon_months,model))""")
    con.execute("""CREATE TABLE model_fit_diagnostic(
        scope_code VARCHAR,origin_month DATE,model VARCHAR,training_months INTEGER,
        status VARCHAR,reason VARCHAR,
        PRIMARY KEY(scope_code,origin_month,model))""")
    con.execute("""CREATE TABLE model_selection(
        scope_code VARCHAR,origin_month DATE,horizon_months INTEGER,
        selected_model VARCHAR,reason VARCHAR,paired_origins INTEGER,
        baseline_wape DOUBLE,selected_wape DOUBLE,
        PRIMARY KEY(scope_code,origin_month,horizon_months))""")
    con.execute("""CREATE TABLE model_evaluation(
        scope_code VARCHAR,horizon_months INTEGER,model VARCHAR,n_origins INTEGER,
        origin_first DATE,origin_last DATE,wape DOUBLE,mae_eur DOUBLE,rmse_eur DOUBLE,
        mean_error_eur DOUBLE,smape DOUBLE,interval_n INTEGER,coverage80 DOUBLE,
        coverage95 DOUBLE,mean_width80_eur DOUBLE,mean_width95_eur DOUBLE,
        PRIMARY KEY(scope_code,horizon_months,model))""")
    con.execute("""CREATE TABLE future_forecast(
        scope_code VARCHAR,origin_month DATE,target_month DATE,horizon_months INTEGER,
        source_model VARCHAR,training_months INTEGER,predicted_eur DECIMAL(18,2),
        interval_status VARCHAR,calibration_n INTEGER,
        interval80_low_eur DECIMAL(18,2),interval80_high_eur DECIMAL(18,2),
        interval95_low_eur DECIMAL(18,2),interval95_high_eur DECIMAL(18,2),
        independent_company_eur DECIMAL(18,2),coherence_delta_eur DECIMAL(18,2),
        PRIMARY KEY(scope_code,horizon_months))""")
    con.execute("""CREATE TABLE anomaly_assessment(
        scope_code VARCHAR,metric VARCHAR,observation_month DATE,status VARCHAR,
        observed_eur DECIMAL(18,2),observed_rate DOUBLE,
        expected_eur DECIMAL(18,2),expected_rate DOUBLE,
        deviation_eur DECIMAL(18,2),deviation_rate DOUBLE,
        robust_score DOUBLE,exposure_eur DECIMAL(18,2),
        history_months INTEGER,seasonal_comparisons INTEGER,residual_history_count INTEGER,
        persistence_months INTEGER,severity VARCHAR,evidence_quality VARCHAR,pattern VARCHAR,
        recommended_investigation VARCHAR,method_version VARCHAR,as_of_date DATE,
        PRIMARY KEY(scope_code,metric,observation_month))""")
    con.execute("""CREATE TABLE commercial_anomaly AS
        SELECT * FROM anomaly_assessment WHERE 1=0""")
    con.execute("""CREATE TABLE anomaly_metric_status(
        metric VARCHAR PRIMARY KEY,status VARCHAR,reason VARCHAR,method_version VARCHAR,
        as_of_date DATE)""")


def _persist_forecasting(
    con: duckdb.DuckDBPyConnection,
    rows: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    evaluations: list[dict[str, Any]],
    future: list[dict[str, Any]],
) -> None:
    for row in rows:
        con.execute(
            "INSERT INTO walkforward_prediction VALUES (" + ",".join("?" * 17) + ")",
            [
                row["scope"],
                row["origin_month"],
                row["target_month"],
                row["horizon"],
                row["model"],
                row["source_model"],
                row["training_months"],
                row["actual_eur"],
                row["predicted_eur"],
                row["error_eur"],
                row.get("interval_status"),
                row.get("calibration_n"),
                row.get("interval80_low_eur"),
                row.get("interval80_high_eur"),
                row.get("interval95_low_eur"),
                row.get("interval95_high_eur"),
                row.get("coherence_delta_eur"),
            ],
        )
    for item in diagnostics:
        con.execute(
            "INSERT INTO model_fit_diagnostic VALUES (?,?,?,?,?,?)",
            [
                item["scope"],
                item["origin_month"],
                item["model"],
                item["training_months"],
                item["status"],
                item.get("reason"),
            ],
        )
    for item in decisions:
        con.execute(
            "INSERT INTO model_selection VALUES (?,?,?,?,?,?,?,?)",
            [
                item["scope"],
                item["origin_month"],
                item["horizon"],
                item["selected_model"],
                item["reason"],
                item["paired_origins"],
                item["baseline_wape"],
                item["selected_wape"],
            ],
        )
    for item in evaluations:
        con.execute(
            "INSERT INTO model_evaluation VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                item["scope"],
                item["horizon"],
                item["model"],
                item["n"],
                item["origin_first"],
                item["origin_last"],
                item["wape"],
                item["mae_eur"],
                item["rmse_eur"],
                item["mean_error_eur"],
                item["smape"],
                item["interval_n"],
                item["coverage80"],
                item["coverage95"],
                item["mean_width80_eur"],
                item["mean_width95_eur"],
            ],
        )
    for item in future:
        con.execute(
            "INSERT INTO future_forecast VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                item["scope"],
                item["origin_month"],
                item["target_month"],
                item["horizon"],
                item["source_model"],
                item["training_months"],
                item["predicted_eur"],
                item["interval_status"],
                item["calibration_n"],
                item.get("interval80_low_eur"),
                item.get("interval80_high_eur"),
                item.get("interval95_low_eur"),
                item.get("interval95_high_eur"),
                item.get("independent_company_eur"),
                item.get("coherence_delta_eur"),
            ],
        )


def _persist_anomalies(
    con: duckdb.DuckDBPyConnection, assessments: list[dict[str, Any]], cutoff: date
) -> None:
    for row in assessments:
        revenue = row["metric"] == "net_revenue_eur"
        values = [
            row["scope"],
            row["metric"],
            row["observation_month"],
            row["status"],
            row["observed_value"] if revenue else None,
            None if revenue else row["observed_value"],
            row.get("expected_value") if revenue else None,
            None if revenue else row.get("expected_value"),
            row.get("deviation") if revenue else None,
            None if revenue else row.get("deviation"),
            row.get("robust_score"),
            row.get("exposure_eur"),
            row["history_months"],
            row.get("seasonal_comparisons"),
            row.get("residual_history_count"),
            row.get("persistence_months"),
            row.get("severity"),
            row["evidence_quality"],
            row.get("pattern"),
            row.get("recommended_investigation"),
            row["method_version"],
            cutoff,
        ]
        placeholders = "(" + ",".join("?" * len(values)) + ")"
        con.execute(f"INSERT INTO anomaly_assessment VALUES {placeholders}", values)
        if row["status"] == "anomaly":
            con.execute(f"INSERT INTO commercial_anomaly VALUES {placeholders}", values)
    for metric, status, reason, method in (
        ("net_revenue_eur", "estimated", None, METHOD_VERSION),
        ("gross_margin_rate", "estimated", None, METHOD_VERSION),
        (
            "mature_cohort_return_rate",
            "not_estimable",
            "no_approved_mature_cohort_duration_or_eligible_unit_denominator",
            "policy-gated-v1",
        ),
    ):
        con.execute(
            "INSERT INTO anomaly_metric_status VALUES (?,?,?,?,?)",
            [
                metric,
                status,
                reason,
                method,
                cutoff,
            ],
        )


def _validate_output(con: duckdb.DuckDBPyConnection, cutoff: date) -> dict[str, Any]:
    if con.execute("SELECT count(*) FROM future_forecast").fetchone()[0] != 12:
        raise ForecastContractError("future forecast requires four scopes x three horizons")
    if con.execute("SELECT count(*) FROM anomaly_metric_status").fetchone()[0] != 3:
        raise ForecastContractError("three anomaly metric statuses are required")
    future_invalid = con.execute("""SELECT count(*) FROM future_forecast
      WHERE origin_month<>DATE '2026-09-01' OR target_month<>
        (origin_month + horizon_months*INTERVAL '1 month')::DATE
        OR horizon_months NOT IN (1,3,6) OR target_month<=origin_month
        OR training_months<>57 OR predicted_eur IS NULL
        OR interval_status NOT IN ('calibrated','insufficient_history')
        OR (interval_status='calibrated' AND calibration_n<8)
        OR (interval_status='insufficient_history' AND calibration_n>=8)
        OR (scope_code='COMPANY' AND source_model<>'bottom_up_selected_countries')
        OR (scope_code<>'COMPANY' AND source_model NOT IN
          ('seasonal_naive','ets_level','ets_additive','sarima_fixed'))""").fetchone()[0]
    if future_invalid:
        raise ForecastContractError("future forecast origin, horizon or interval contract violated")
    leakage = con.execute(
        """SELECT count(*) FROM walkforward_prediction
      WHERE target_month<=origin_month OR training_months<24
        OR target_month<>(origin_month + horizon_months*INTERVAL '1 month')::DATE
        OR target_month>? OR interval_status='calibrated' AND calibration_n<8""",
        [cutoff],
    ).fetchone()[0]
    if leakage:
        raise ForecastContractError("walk-forward origin, horizon or interval contract violated")
    interval_bad = con.execute("""SELECT count(*) FROM (
      SELECT interval80_low_eur l80,interval80_high_eur u80,
             interval95_low_eur l95,interval95_high_eur u95
      FROM walkforward_prediction WHERE interval_status='calibrated'
      UNION ALL
      SELECT interval80_low_eur,interval80_high_eur,
             interval95_low_eur,interval95_high_eur
      FROM future_forecast WHERE interval_status='calibrated') x
      WHERE l95>l80 OR u95<u80 OR l80>u80""").fetchone()[0]
    if interval_bad:
        raise ForecastContractError("prediction interval nesting violated")
    missing_bounds = con.execute("""SELECT count(*) FROM (
      SELECT interval_status,interval80_low_eur l80,interval80_high_eur u80,
             interval95_low_eur l95,interval95_high_eur u95 FROM walkforward_prediction
      UNION ALL SELECT interval_status,interval80_low_eur,interval80_high_eur,
             interval95_low_eur,interval95_high_eur FROM future_forecast) x
      WHERE (interval_status='calibrated' AND
             (l80 IS NULL OR u80 IS NULL OR l95 IS NULL OR u95 IS NULL))
         OR (interval_status='insufficient_history' AND
             (l80 IS NOT NULL OR u80 IS NOT NULL OR l95 IS NOT NULL OR u95 IS NOT NULL))""")
    missing_bounds = missing_bounds.fetchone()[0]
    if missing_bounds:
        raise ForecastContractError("prediction interval bounds and status disagree")
    coherence = con.execute("""SELECT count(*) FROM future_forecast c
      JOIN (SELECT horizon_months,sum(predicted_eur)::DECIMAL(18,2) countries
            FROM future_forecast WHERE scope_code<>'COMPANY' GROUP BY 1) s
      USING(horizon_months)
      WHERE c.scope_code='COMPANY' AND c.predicted_eur<>s.countries""").fetchone()[0]
    if coherence:
        raise ForecastContractError("future company forecast is not bottom-up coherent")
    backtest_coherence = con.execute("""SELECT count(*) FROM walkforward_prediction c
      JOIN (SELECT origin_month,horizon_months,sum(predicted_eur)::DECIMAL(18,2) countries
            FROM walkforward_prediction WHERE scope_code<>'COMPANY' AND model='selected'
            GROUP BY 1,2) s USING(origin_month,horizon_months)
      WHERE c.scope_code='COMPANY' AND c.model='reconciled'
        AND c.predicted_eur<>s.countries""").fetchone()[0]
    if backtest_coherence:
        raise ForecastContractError("backtest company forecast is not bottom-up coherent")
    if con.execute(
        "SELECT count(*) FROM commercial_anomaly WHERE severity NOT IN "
        "('info','investigate','high') OR observation_month>?",
        [cutoff],
    ).fetchone()[0]:
        raise ForecastContractError("anomaly severity or cutoff violation")
    if con.execute(
        "SELECT count(*) FROM commercial_anomaly WHERE metric='mature_cohort_return_rate'"
    ).fetchone()[0]:
        raise ForecastContractError("unapproved return-rate anomaly was published")
    return {
        "status": "passed",
        "violations": 0,
        "future_rows": 12,
        "nested_intervals_checked": True,
        "coherence_checked": True,
    }


def _objects(con: duckdb.DuckDBPyConnection, cutoff: date) -> dict[str, Any]:
    keys = {
        "sales_monthly": ["reporting_month", "scope_code"],
        "walkforward_prediction": ["scope_code", "origin_month", "horizon_months", "model"],
        "model_fit_diagnostic": ["scope_code", "origin_month", "model"],
        "model_selection": ["scope_code", "origin_month", "horizon_months"],
        "model_evaluation": ["scope_code", "horizon_months", "model"],
        "future_forecast": ["scope_code", "horizon_months"],
        "anomaly_assessment": ["scope_code", "metric", "observation_month"],
        "commercial_anomaly": ["scope_code", "metric", "observation_month"],
        "anomaly_metric_status": ["metric"],
    }
    objects = {}
    for table, key in keys.items():
        columns = [
            {"name": x[1], "type": x[2], "nullable": not x[3]}
            for x in con.execute(f"PRAGMA table_info('{table}')").fetchall()
        ]
        if {x["name"] for x in columns} & PRIVATE_LABEL_NAMES:
            raise ForecastContractError("private control column in forecasting publication")
        quoted = ",".join(f'"{column}"' for column in key)
        duplicates = con.execute(
            f'SELECT count(*) FROM (SELECT {quoted} FROM "{table}" '
            f"GROUP BY {quoted} HAVING count(*)>1)"
        ).fetchone()[0]
        null_keys = con.execute(
            f'SELECT count(*) FROM "{table}" WHERE '
            + " OR ".join(f'"{column}" IS NULL' for column in key)
        ).fetchone()[0]
        if duplicates or null_keys:
            raise ForecastContractError(f"{table} duplicate or null output key")
        objects[table] = {
            "schema_id": f"bsi.forecasting.{table}",
            "schema_version": 1,
            "object_type": "table",
            "grain": "+".join(key),
            "key": key,
            "row_count": con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
            "columns": columns,
            "as_of_date": str(cutoff),
            "currency": "EUR for monetary fields; ratios unitless",
        }
    return objects


def build_forecasting_anomalies(
    configuration: ProjectConfiguration,
    warehouse_directory: Path,
    gold_directory: Path,
    product_directory: Path,
    output_directory: Path,
    *,
    publish: bool = True,
    force: bool = False,
) -> ForecastBuildResult:
    """Validate sources, compute prequential results, and atomically publish."""
    sources, checks = validate_inputs(
        configuration, Path(warehouse_directory), Path(gold_directory), Path(product_directory)
    )
    output_directory = Path(output_directory)
    if publish and _has_material_output(output_directory) and not force:
        raise ForecastContractError(f"forecast output exists; use --force: {output_directory}")
    if publish:
        output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_directory.name}.staging-",
        dir=output_directory.parent if publish else None,
    ) as temporary:
        staged = Path(temporary) / output_directory.name
        staged.mkdir()
        (staged / ".gitkeep").touch()
        db = staged / DATABASE_NAME
        con = duckdb.connect(str(db))
        cutoff = configuration.application.reporting.default_as_of_date
        try:
            con.execute("PRAGMA threads=1")
            for label, source in sources.items():
                path = str(source.database_path.resolve()).replace("'", "''")
                con.execute(f"ATTACH '{path}' AS {label} (READ_ONLY)")
            if (cutoff + timedelta(days=1)).day != 1:
                raise ForecastContractError("forecast cutoff is not a complete month")
            con.execute(
                """CREATE TEMP TABLE build_parameters AS SELECT
              ?::DATE period_start_month,?::DATE as_of_month,?::DATE as_of_date""",
                [
                    configuration.application.reporting.period_start,
                    date(cutoff.year, cutoff.month, 1),
                    cutoff,
                ],
            )
            sql = (Path(__file__).parents[3] / "sql/analysis/forecasting_monthly.sql").read_text()
            con.execute(sql)
            checks["monthly_actuals"] = _validate_monthly(con, cutoff)
            source_rows = con.execute("""SELECT reporting_month,scope_code,
              net_revenue_eur,net_gross_profit_eur,gross_margin_rate
              FROM sales_monthly ORDER BY reporting_month,scope_code""").fetchall()
            months = sorted({row[0] for row in source_rows})
            series: dict[str, list[Decimal]] = {scope: [] for scope in SCOPE_ORDER}
            margins: dict[str, list[float | None]] = {scope: [] for scope in SCOPE_ORDER}
            for scope in SCOPE_ORDER:
                records = [row for row in source_rows if row[1] == scope]
                if [row[0] for row in records] != months:
                    raise ForecastContractError(f"{scope} monthly cadence mismatch")
                series[scope] = [row[2] for row in records]
                margins[scope] = [row[4] for row in records]
            candidate_rows: list[dict[str, Any]] = []
            diagnostics: list[dict[str, Any]] = []
            for scope in SCOPE_ORDER:
                predictions, fit_checks = backtest_series(months, series[scope], scope)
                candidate_rows.extend(predictions)
                diagnostics.extend(fit_checks)
            prequential, decisions = select_prequential(candidate_rows, SCOPE_ORDER)
            calibrate_backtest(prequential)
            evaluations = evaluation_metrics(candidate_rows + prequential)
            future, final_decisions, final_checks = final_forecasts(
                months, series, candidate_rows, prequential
            )
            all_predictions = sorted(
                candidate_rows + prequential,
                key=lambda r: (r["scope"], r["origin_month"], r["horizon"], r["model"]),
            )
            all_decisions = sorted(
                decisions + final_decisions,
                key=lambda r: (r["scope"], r["origin_month"], r["horizon"]),
            )
            all_diagnostics = sorted(
                diagnostics + final_checks,
                key=lambda r: (r["scope"], r["origin_month"], r["model"]),
            )
            assessments = []
            for scope in SCOPE_ORDER:
                for metric, values in (
                    ("net_revenue_eur", series[scope]),
                    ("gross_margin_rate", margins[scope]),
                ):
                    assessed, _ = assess_series(
                        months,
                        values,
                        series[scope],
                        scope=scope,
                        metric=metric,
                        cutoff=cutoff,
                    )
                    assessments.extend(assessed)
            assessments.sort(key=lambda r: (r["scope"], r["metric"], r["observation_month"]))
            _create_output_tables(con)
            _persist_forecasting(
                con, all_predictions, all_diagnostics, all_decisions, evaluations, future
            )
            _persist_anomalies(con, assessments, cutoff)
            checks["output"] = _validate_output(con, cutoff)
            objects = _objects(con, cutoff)
            for label in reversed(tuple(sources)):
                con.execute(f"DETACH {label}")
            con.execute("CHECKPOINT")
        except duckdb.Error as exc:
            raise ForecastContractError(f"forecasting SQL failed: {exc}") from exc
        finally:
            con.close()
        manifest = {
            "schema_id": "bsi.forecasting_anomalies_publication",
            "schema_version": 1,
            "stage": "forecasting",
            "publication_status": "passed",
            "pipeline_version": __version__,
            "source_batch_id": sources["warehouse"].manifest["source_batch_id"],
            "build_config_hash": configuration.deterministic_hash(),
            "source_manifests_sha256": {
                label: item.manifest_sha256 for label, item in sources.items()
            },
            "source_databases_sha256": {
                label: item.manifest["database"]["sha256"] for label, item in sources.items()
            },
            "source_silver_manifest_sha256": sources["warehouse"].manifest[
                "source_silver_manifest_sha256"
            ],
            "database": {"path": DATABASE_NAME, "sha256": _sha(db)},
            "objects": objects,
            "analysis_window": {
                "period_start": "2022-01-01",
                "as_of_date": str(cutoff),
                "historical_months": 57,
                "post_cutoff_excluded": True,
            },
            "methods": {
                "forecast_method_version": "expanding-monthly-v1",
                "models": list(MODELS),
                "minimum_training_months": MIN_TRAIN,
                "origin_cadence": "monthly expanding; refit each candidate at each eligible origin",
                "horizons_months": list(HORIZONS),
                "selection_min_paired_origins": MIN_PAIRED,
                "minimum_relative_wape_gain": MIN_RELATIVE_WAPE_GAIN,
                "interval": (
                    "symmetric higher-quantile absolute error; 80/95%; target available by origin"
                ),
                "minimum_calibration_errors": MIN_CALIBRATION,
                "reconciliation": (
                    "bottom-up country point forecast; joint reconciled-error company interval"
                ),
                "negative_prediction": "preserved; canonical net revenue may be negative",
                "anomaly": METHOD_VERSION,
                "return_rate_anomaly": "not_estimable: no approved mature cohort duration",
                "private_s14_validation": "pending_private_qa",
            },
            "runtime": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "packages": {
                    name: importlib.metadata.version(name)
                    for name in ("duckdb", "numpy", "statsmodels")
                },
            },
            "validations": checks,
        }
        (staged / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        if not publish:
            return ForecastBuildResult(manifest, checks)
        _publish_directory(staged, output_directory, force)
    return ForecastBuildResult(manifest, checks, output_directory, output_directory / DATABASE_NAME)
