"""Build the independently governed Milestone 11 statistical publication."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import tempfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from baltic_sales_intelligence import __version__
from baltic_sales_intelligence.config import ProjectConfiguration
from baltic_sales_intelligence.customer.builder import _has_material_output, _publish_directory
from baltic_sales_intelligence.statistics.contracts import StatisticalContractError, validate_inputs
from baltic_sales_intelligence.statistics.estimators import (
    SEED,
    basket_associations,
    fit_discount,
    fit_retention,
    market_margin_contrast,
    price_mix_cost_bridge,
)

DATABASE_NAME = "statistical_analysis.duckdb"
MANIFEST_NAME = "statistical_analysis_manifest.json"


@dataclass(frozen=True)
class StatisticalBuildResult:
    manifest: dict[str, Any]
    validations: dict[str, Any]
    output_directory: Path | None = None
    database_path: Path | None = None


STUDIES: dict[str, dict[str, str]] = {
    "STAT-01": {
        "question": "Do high-discount accounts have lower adjusted realised margin?",
        "null": (
            "A 10 percentage point higher prior account discount has zero adjusted "
            "margin association."
        ),
        "alternative": "The adjusted association differs from zero.",
        "population": (
            "Customer-month-category cells with posted Gold commercial activity and "
            "positive net revenue."
        ),
        "grain": "Customer, reporting month, category, segment and posting home market.",
        "period": "2022-01 through 2026-09 inclusive.",
        "sources": "Gold mart_commercial_month; Warehouse dim_customer.",
        "metric": (
            "Gross profit / positive net revenue; prior cumulative account discount / "
            "prior list value."
        ),
        "method": (
            "Revenue-weighted OLS with customer-clustered covariance and fixed "
            "category, segment, market and month controls plus log tenure."
        ),
        "assumptions": (
            "Observed controls suffice for an adjusted association; account histories "
            "are measured before each month."
        ),
        "limitations": (
            "Selection, contract terms and unmeasured confounding remain; cells with "
            "nonpositive revenue or no prior discount history are excluded."
        ),
    },
    "STAT-02": {
        "question": (
            "Did Estonia's gross margin deteriorate beyond LT/LV variation, and what "
            "describes the EUR change?"
        ),
        "null": "EE minus pooled LT/LV change in 12-month gross margin is zero.",
        "alternative": "The EE-minus-peer change differs from zero.",
        "population": (
            "Posted commercial activity by customer home market at posting, plus EE "
            "fulfilled SKU sales for the bridge."
        ),
        "grain": "Market-month for contrast; SKU-period for descriptive bridge.",
        "period": "2024-10..2025-09 against 2025-10..2026-09; complete 12-month periods.",
        "sources": "Gold mart_commercial_month; Warehouse fact_order_line and dim_customer.",
        "metric": (
            "Net gross profit / net revenue in percentage points; exact posted-cent EE "
            "gross profit EUR bridge."
        ),
        "method": (
            "Difference of 12-month ratios with paired circular 3-month block "
            "bootstrap; matched-SKU price/mix/cost bridge."
        ),
        "assumptions": (
            "Three-month paired blocks preserve nearby serial dependence and "
            "same-month market co-movement."
        ),
        "limitations": (
            "Only two annual periods; bootstrap p-value is approximate; bridge terms "
            "are descriptive, with return timing and residual explicit."
        ),
    },
    "STAT-03": {
        "question": (
            "Is a worsening category return rate beyond sampling noise on mature sale cohorts?"
        ),
        "null": "Mature-cohort accepted-unit return rates do not differ.",
        "alternative": "Mature-cohort accepted-unit return rates differ.",
        "population": (
            "No production mature cohort can be selected without an approved maturity duration."
        ),
        "grain": "Original fulfilled sale line and accepted return decision event.",
        "period": "Historical reporting cutoff 2026-09-30; cohort windows await policy.",
        "sources": "Warehouse fact_order_line and fact_return_event; Product return policy.",
        "metric": "Accepted returned units / eligible fulfilled units; unestimated in production.",
        "method": (
            "No estimator run; explicit policy-dependent status. Fixture-only cohort "
            "arithmetic is tested."
        ),
        "assumptions": (
            "An approved maturation rule is required before cohort selection and "
            "uncertainty design."
        ),
        "limitations": (
            "The mature-cohort duration is unapproved; no rate, interval or "
            "significance is published."
        ),
    },
    "STAT-04": {
        "question": (
            "Do customer segments differ in retention after tenure and prior "
            "purchase-frequency standardisation?"
        ),
        "null": "Standardised next-12-month retention rates are equal across segments.",
        "alternative": "At least one segment contrast differs from zero.",
        "population": (
            "First fulfilled-purchase customers with a 12-month landmark and a "
            "complete next 12-month horizon."
        ),
        "grain": "Customer-landmark year; annual landmarks after first purchase.",
        "period": "Landmarks and outcomes ending by 2026-09-30; incomplete horizons censored.",
        "sources": "Customer mart_customer_value; Warehouse dim_customer and fact_order_line.",
        "metric": (
            "At least one fulfilled order in next 12 months; pooled-covariate "
            "standardised percentage-point contrasts."
        ),
        "method": (
            "Cluster-robust binomial GLM; prior 12-month order count, tenure, market "
            "and landmark-year controls; covariance simulation intervals; BH across "
            "segment contrasts."
        ),
        "assumptions": (
            "Segment is known at landmark; prior-frequency and tenure are pre-outcome features."
        ),
        "limitations": (
            "Observational segment differences can reflect unmeasured selection; "
            "sparse segments may be unestimable."
        ),
    },
    "STAT-05": {
        "question": (
            "Do eligible subcategory basket associations exceed independent popularity expectation?"
        ),
        "null": "Each eligible pair has lift equal to one.",
        "alternative": "The pair's lift differs from one.",
        "population": (
            "Fulfilled orders with distinct subcategory membership, restricted to "
            "approved Product eligible pairs."
        ),
        "grain": (
            "One unordered subcategory pair per qualifying order; resampling at customer level."
        ),
        "period": "2022-01 through 2026-09-30 inclusive.",
        "sources": "Product mart_basket_rules; Warehouse fact_order_line and dim_product.",
        "metric": "Joint support and directional confidence; lift = P(A,B)/(P(A)P(B)).",
        "method": (
            "Customer-cluster bootstrap of lift and log-lift normal-approximation "
            "p-values; BH across eligible pairs."
        ),
        "assumptions": (
            "Customers are resampling units; Product pair eligibility is fixed before inference."
        ),
        "limitations": (
            "Sparse pairs and catalog selection affect inference; association does not "
            "measure intervention lift."
        ),
    },
}


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_samples(con: duckdb.DuckDBPyConnection) -> dict[str, Any]:
    """Validate sample keys and temporal boundaries before model fitting."""
    checks: dict[str, Any] = {}
    specs = {
        "stat_discount_candidates": (
            "customer_id,reporting_month,category_id,segment_code,home_country_code",
            "reporting_month",
        ),
        "stat_market_month": ("reporting_month,home_country_code", "reporting_month"),
        "stat_ee_sku_period": ("period,product_id", None),
        "stat_retention_candidates": ("customer_id,landmark_month", "landmark_month"),
        "stat_basket_membership": ("order_id,subcategory_id", None),
    }
    for table, (key, date_column) in specs.items():
        count, distinct = con.execute(
            f"SELECT count(*),count(*)-count(DISTINCT ({key})) FROM {table}"
        ).fetchone()
        if distinct:
            raise StatisticalContractError(f"{table} duplicate analysis grain")
        if date_column:
            beyond = con.execute(
                f"SELECT count(*) FROM {table} WHERE {date_column} > "
                "(SELECT as_of_date FROM build_parameters)"
            ).fetchone()[0]
            if beyond:
                raise StatisticalContractError(f"{table} contains post-cutoff rows")
        checks[table] = {"status": "passed", "violations": 0, "row_count": count}
    if con.execute(
        "SELECT count(*) FROM stat_discount_candidates WHERE "
        "prior_list_eur < 0 OR current_list_eur < 0 OR source_gold_rows < 1"
    ).fetchone()[0]:
        raise StatisticalContractError("discount sample has invalid denominators")
    raw_gold_rows = con.execute(
        "SELECT count(*) FROM gold.mart_commercial_month g CROSS JOIN build_parameters p "
        "WHERE g.reporting_month BETWEEN p.period_start AND p.as_of_month"
    ).fetchone()[0]
    sampled_gold_rows = con.execute(
        "SELECT coalesce(sum(source_gold_rows),0) FROM stat_discount_candidates"
    ).fetchone()[0]
    if raw_gold_rows != sampled_gold_rows:
        raise StatisticalContractError("discount sample dropped or multiplied Gold rows")
    expected_landmarks = con.execute(
        "SELECT count(*) FROM customer.mart_customer_value c "
        "CROSS JOIN range(1,5) n(n) CROSS JOIN build_parameters p "
        "WHERE c.first_purchase_cohort_month IS NOT NULL AND "
        "c.first_purchase_cohort_month+n.n*INTERVAL '12 months'<=p.as_of_date"
    ).fetchone()[0]
    if expected_landmarks != checks["stat_retention_candidates"]["row_count"]:
        raise StatisticalContractError("retention landmark SCD coverage mismatch")
    orphan_products = con.execute(
        "SELECT count(*) FROM warehouse.fact_order_line s "
        "LEFT JOIN warehouse.dim_product d USING(product_id) "
        "CROSS JOIN build_parameters p WHERE s.order_status='fulfilled' "
        "AND s.fulfilled_date BETWEEN p.period_start AND p.as_of_date "
        "AND d.product_id IS NULL"
    ).fetchone()[0]
    if orphan_products:
        raise StatisticalContractError("fulfilled basket contains orphan products")
    checks["sample_join_coverage"] = {"status": "passed", "violations": 0}
    return checks


def _interpret(study: str, result: dict[str, Any]) -> str:
    status = result["status"]
    if status != "association_only":
        if study == "STAT-02" and "ee_observed_gp_change_eur" in result:
            return (
                f"Margin contrast unavailable: {result.get('reason', status)}. "
                f"The descriptive EE gross-profit change is EUR "
                f"{result['ee_observed_gp_change_eur']}; see the exact-cent bridge."
            )
        return f"No supported numerical conclusion: {result.get('reason', status)}."
    if study == "STAT-01":
        return (
            f"A 10 percentage point higher prior account discount is associated with "
            f"{result['estimate']:.2f} percentage points of adjusted margin difference "
            f"(95% CI {result['ci_low']:.2f} to {result['ci_high']:.2f}); this is observational."
        )
    if study == "STAT-02":
        return (
            f"EE's 12-month margin change versus pooled LT/LV is {result['estimate']:.2f} "
            f"percentage points (95% block-bootstrap CI {result['ci_low']:.2f} to "
            f"{result['ci_high']:.2f}). EE gross profit changed by EUR "
            f"{result['ee_observed_gp_change_eur']}; bridge components describe this change."
        )
    if study == "STAT-04":
        strongest = min(result["contrasts"], key=lambda row: row["q_value"])
        qualified = strongest["q_value"] < 0.05 and (
            strongest["ci_low"] > 0 or strongest["ci_high"] < 0
        )
        conclusion = (
            "passes the prespecified BH and interval criteria"
            if qualified
            else "does not pass the prespecified BH and interval criteria"
        )
        return (
            f"The {strongest['segment']} minus {strongest['baseline']} adjusted retention "
            f"contrast is {strongest['estimate']:.2f} percentage points "
            f"(95% CI {strongest['ci_low']:.2f} to {strongest['ci_high']:.2f}, "
            f"BH q={strongest['q_value']:.3f}) and {conclusion}; observational."
        )
    significant = sum(
        x.get("q_value") is not None
        and x["q_value"] < 0.05
        and x.get("ci_low") is not None
        and x["ci_low"] > 1
        for x in result["rows"]
    )
    return (
        f"{significant} of {result['pairs_tested']} eligible subcategory pairs have lift above "
        "one with a 95% cluster interval above one and BH q below 0.05."
    )


def _prepare_tables(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("""CREATE TABLE study_catalogue(
        study_id VARCHAR PRIMARY KEY, business_question VARCHAR, null_hypothesis VARCHAR,
        alternative_hypothesis VARCHAR, analysis_population VARCHAR, analysis_grain VARCHAR,
        comparison_periods VARCHAR, source_datasets VARCHAR, metric_definition VARCHAR,
        estimation_method VARCHAR, assumptions VARCHAR, limitations VARCHAR,
        evidence_classification VARCHAR, status VARCHAR, n_observations BIGINT,
        n_clusters BIGINT, missing_count BIGINT, management_interpretation VARCHAR,
        source_batch_id VARCHAR, as_of_date DATE)""")
    con.execute("""CREATE TABLE study_estimate(
        estimate_id VARCHAR PRIMARY KEY, study_id VARCHAR, contrast VARCHAR, effect_unit VARCHAR,
        estimate DOUBLE, ci_low DOUBLE, ci_high DOUBLE, p_value DOUBLE, q_value DOUBLE,
        n_observations BIGINT, n_clusters BIGINT, status VARCHAR, reason VARCHAR)""")
    con.execute("""CREATE TABLE study_diagnostic(
        study_id VARCHAR, diagnostic_name VARCHAR, numeric_value DOUBLE, text_value VARCHAR,
        PRIMARY KEY(study_id,diagnostic_name))""")
    con.execute("""CREATE TABLE study_sensitivity(
        study_id VARCHAR, check_name VARCHAR, estimate DOUBLE, effect_unit VARCHAR,
        detail VARCHAR, amount_eur DECIMAL(18,2), PRIMARY KEY(study_id,check_name))""")
    con.execute("""CREATE TABLE estonia_gp_bridge(
        component VARCHAR PRIMARY KEY, amount_eur DECIMAL(18,2), method VARCHAR)""")
    con.execute("""CREATE TABLE basket_pair_result(
        subcategory_a_id VARCHAR, subcategory_b_id VARCHAR, basket_count BIGINT,
        item_a_baskets BIGINT, item_b_baskets BIGINT, joint_baskets BIGINT,
        pair_support DOUBLE, confidence_a_to_b DOUBLE, confidence_b_to_a DOUBLE,
        expected_joint_baskets DOUBLE, lift DOUBLE, ci_low DOUBLE, ci_high DOUBLE,
        p_value DOUBLE, q_value DOUBLE, customer_clusters BIGINT,
        bootstrap_valid_replicates INTEGER, status VARCHAR, reason VARCHAR,
        PRIMARY KEY(subcategory_a_id,subcategory_b_id))""")


def _insert_result(
    con: duckdb.DuckDBPyConnection, study: str, result: dict[str, Any], cutoff: date
) -> None:
    detail = STUDIES[study]
    n = result.get("n", result.get("basket_count", result.get("n_month_pairs")))
    clusters = result.get("clusters")
    if study == "STAT-03":
        n = clusters = 0
    con.execute(
        "INSERT INTO study_catalogue VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            study,
            detail["question"],
            detail["null"],
            detail["alternative"],
            detail["population"],
            detail["grain"],
            detail["period"],
            detail["sources"],
            detail["metric"],
            detail["method"],
            detail["assumptions"],
            detail["limitations"],
            "association_only" if result["status"] == "association_only" else "no_estimate",
            result["status"],
            n,
            clusters,
            result.get("missing"),
            _interpret(study, result),
            "8e33878d4c2719cc",
            cutoff,
        ],
    )
    for name, value in sorted(result.items()):
        if name in {"rows", "contrasts", "components", "standardized_rates"}:
            continue
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            con.execute(
                "INSERT INTO study_diagnostic VALUES (?,?,?,NULL)", [study, name, float(value)]
            )
        elif isinstance(value, (str, dict)):
            con.execute(
                "INSERT INTO study_diagnostic VALUES (?,?,NULL,?)",
                [
                    study,
                    name,
                    json.dumps(value, sort_keys=True) if isinstance(value, dict) else value,
                ],
            )


def _add_estimate(
    con: duckdb.DuckDBPyConnection,
    key: str,
    study: str,
    label: str,
    unit: str,
    result: dict[str, Any],
    *,
    n: int | None = None,
    clusters: int | None = None,
) -> None:
    con.execute(
        "INSERT INTO study_estimate VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            key,
            study,
            label,
            unit,
            result.get("estimate"),
            result.get("ci_low"),
            result.get("ci_high"),
            result.get("p_value"),
            result.get("q_value"),
            n if n is not None else result.get("n"),
            clusters if clusters is not None else result.get("clusters"),
            result["status"],
            result.get("reason"),
        ],
    )


def _materialize(
    con: duckdb.DuckDBPyConnection,
    results: dict[str, dict[str, Any]],
    bridge: dict[str, Any] | None,
    cutoff: date,
) -> None:
    _prepare_tables(con)
    for study in STUDIES:
        _insert_result(con, study, results[study], cutoff)
    for study, key, label, unit in (
        (
            "STAT-01",
            "discount_10pp",
            "10 pp higher prior account discount",
            "margin percentage points",
        ),
        (
            "STAT-02",
            "ee_vs_peers",
            "EE margin change minus pooled LT/LV change",
            "margin percentage points",
        ),
    ):
        _add_estimate(con, key, study, label, unit, results[study])
    _add_estimate(
        con,
        "mature_return_rate",
        "STAT-03",
        "Mature category return rate",
        "accepted units / eligible fulfilled units",
        results["STAT-03"],
        n=0,
        clusters=0,
    )
    retention = results["STAT-04"]
    if retention["status"] == "association_only":
        for segment, rate in sorted(retention["standardized_rates"].items()):
            _add_estimate(
                con,
                f"retention_rate_{segment}",
                "STAT-04",
                f"Standardised retention {segment}",
                "proportion",
                {"status": "association_only", "estimate": rate},
                n=retention["n"],
                clusters=retention["clusters"],
            )
        for row in retention["contrasts"]:
            _add_estimate(
                con,
                f"retention_{row['segment']}_vs_{row['baseline']}",
                "STAT-04",
                f"{row['segment']} minus {row['baseline']}",
                "retention percentage points",
                {"status": "association_only", **row},
                n=retention["n"],
                clusters=retention["clusters"],
            )
    else:
        _add_estimate(
            con,
            "retention_adjusted",
            "STAT-04",
            "Adjusted segment retention",
            "retention percentage points",
            retention,
        )
    basket = results["STAT-05"]
    if basket["status"] == "association_only":
        for row in basket["rows"]:
            con.execute(
                "INSERT INTO basket_pair_result VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    row["item_a"],
                    row["item_b"],
                    row.get("basket_count"),
                    row.get("item_a_baskets"),
                    row.get("item_b_baskets"),
                    row.get("joint_baskets"),
                    row.get("pair_support"),
                    row.get("confidence_a_to_b"),
                    row.get("confidence_b_to_a"),
                    row.get("expected_joint_baskets"),
                    row.get("lift"),
                    row.get("ci_low"),
                    row.get("ci_high"),
                    row.get("p_value"),
                    row.get("q_value"),
                    row.get("customer_clusters"),
                    row.get("bootstrap_valid_replicates"),
                    row["status"],
                    row.get("reason"),
                ],
            )
            _add_estimate(
                con,
                f"basket_{row['item_a']}_{row['item_b']}",
                "STAT-05",
                f"Lift {row['item_a']} + {row['item_b']}",
                "lift ratio",
                {
                    "status": row["status"],
                    "estimate": row.get("lift"),
                    "ci_low": row.get("ci_low"),
                    "ci_high": row.get("ci_high"),
                    "p_value": row.get("p_value"),
                    "q_value": row.get("q_value"),
                    "reason": row.get("reason"),
                },
                n=row.get("basket_count"),
                clusters=row.get("customer_clusters"),
            )
    else:
        _add_estimate(
            con,
            "basket_popularity",
            "STAT-05",
            "Popularity-adjusted basket lift",
            "lift ratio",
            basket,
            n=basket.get("basket_count"),
            clusters=basket.get("clusters"),
        )
    if bridge:
        for component, amount in bridge["components"].items():
            con.execute(
                "INSERT INTO estonia_gp_bridge VALUES (?,?,?)",
                [
                    component,
                    amount,
                    "descriptive matched-SKU price/mix/cost with exact EUR residual",
                ],
            )
        con.execute(
            "INSERT INTO study_sensitivity VALUES (?,?,?,?,?,?)",
            [
                "STAT-02",
                "bridge_reconciliation",
                None,
                "gross profit EUR",
                json.dumps(
                    {
                        "matched_sku_count": bridge["matched_sku_count"],
                        "new_or_exited_sku_count": bridge["new_or_exited_sku_count"],
                        "residual_eur": str(
                            bridge["components"]["rounding_or_unallocated_residual"]
                        ),
                    },
                    sort_keys=True,
                ),
                bridge["observed_change_eur"],
            ],
        )
    discount = results["STAT-01"]
    if "drop_largest_customer_estimate" in discount:
        con.execute(
            "INSERT INTO study_sensitivity VALUES (?,?,?,?,?,?)",
            [
                "STAT-01",
                "drop_largest_customer",
                discount["drop_largest_customer_estimate"],
                "margin percentage points",
                "Same model after removing largest revenue customer",
                None,
            ],
        )


def build_statistical_analysis(
    configuration: ProjectConfiguration,
    warehouse_directory: Path,
    gold_directory: Path,
    customer_directory: Path,
    product_directory: Path,
    output_directory: Path,
    *,
    publish: bool = True,
    force: bool = False,
) -> StatisticalBuildResult:
    """Validate all four sources, estimate five studies and atomically publish."""
    inputs, checks = validate_inputs(
        configuration,
        Path(warehouse_directory),
        Path(gold_directory),
        Path(customer_directory),
        Path(product_directory),
    )
    output_directory = Path(output_directory)
    if publish and _has_material_output(output_directory) and not force:
        raise StatisticalContractError(
            f"statistical output exists; use --force: {output_directory}"
        )
    if publish:
        output_directory.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_directory.name}.staging-",
        dir=output_directory.parent if publish else None,
    ) as tmp:
        staged = Path(tmp) / output_directory.name
        staged.mkdir()
        (staged / ".gitkeep").touch()
        db = staged / DATABASE_NAME
        con = duckdb.connect(str(db))
        try:
            con.execute("PRAGMA threads=1")
            for label, source in inputs.items():
                safe = str(source.database_path.resolve()).replace("'", "''")
                con.execute(f"ATTACH '{safe}' AS {label} (READ_ONLY)")
            reporting = configuration.application.reporting
            cutoff = reporting.default_as_of_date
            as_of_month = date(cutoff.year, cutoff.month, 1)
            month_index = cutoff.year * 12 + cutoff.month - 1
            prior_start = date((month_index - 23) // 12, (month_index - 23) % 12 + 1, 1)
            current_start = date((month_index - 11) // 12, (month_index - 11) % 12 + 1, 1)
            con.execute(
                """CREATE TEMP TABLE build_parameters AS SELECT
                ?::DATE period_start, ?::DATE as_of_date, ?::DATE as_of_month,
                ?::DATE prior_start_month, ?::DATE current_start_month""",
                [reporting.period_start, cutoff, as_of_month, prior_start, current_start],
            )
            sql = (Path(__file__).parents[3] / "sql/analysis/statistical_samples.sql").read_text()
            con.execute(sql)
            checks["samples"] = _check_samples(con)
            discount = fit_discount(
                con.execute(
                    "SELECT * FROM stat_discount_candidates "
                    "ORDER BY customer_id,reporting_month,category_id,"
                    "segment_code,home_country_code"
                ).df()
            )
            monthly = con.execute(
                "SELECT * FROM stat_market_month ORDER BY reporting_month,home_country_code"
            ).df()
            market = market_margin_contrast(monthly, seed=SEED, replicates=1000)
            sku_rows = (
                con.execute("SELECT * FROM stat_ee_sku_period ORDER BY period,product_id")
                .df()
                .to_dict("records")
            )
            ee = monthly[monthly.home_country_code == "EE"].copy()
            ee["period"] = ee.reporting_month.apply(
                lambda value: "prior" if value.date() < current_start else "current"
            )
            money = ("fulfilled_sales_eur", "net_gross_profit_eur")
            market_rows = [
                {
                    "period": period,
                    **{
                        field: sum((Decimal(str(v)) for v in group[field]), Decimal(0))
                        for field in money
                    },
                }
                for period, group in ee.groupby("period", sort=True)
            ]
            bridge = price_mix_cost_bridge(sku_rows, market_rows) if len(market_rows) == 2 else None
            if bridge is None:
                raise StatisticalContractError("EE market bridge periods are incomplete")
            market["ee_observed_gp_change_eur"] = str(bridge["observed_change_eur"])
            market["ee_bridge_residual_eur"] = str(
                bridge["components"]["rounding_or_unallocated_residual"]
            )
            retention = fit_retention(
                con.execute(
                    "SELECT * FROM stat_retention_candidates ORDER BY customer_id,landmark_month"
                ).df(),
                seed=SEED,
                draws=400,
            )
            membership = con.execute(
                "SELECT * FROM stat_basket_membership ORDER BY order_id,customer_id,subcategory_id"
            ).df()
            rules = con.execute("""SELECT subcategory_a_id,subcategory_b_id,basket_count,
                item_a_baskets,item_b_baskets,joint_baskets FROM product.mart_basket_rules
                ORDER BY subcategory_a_id,subcategory_b_id""").df()
            basket = basket_associations(membership, rules, seed=SEED, replicates=400)
            results = {
                "STAT-01": discount,
                "STAT-02": market,
                "STAT-03": {
                    "status": "not_estimable",
                    "reason": "mature_cohort_duration_not_approved",
                    "missing_policy": (
                        "An approved sale-cohort maturity duration or rule is required."
                    ),
                },
                "STAT-04": retention,
                "STAT-05": basket,
            }
            _materialize(con, results, bridge, cutoff)
            objects = {}
            for table, (grain, key) in {
                "study_catalogue": ("one mandatory study", ["study_id"]),
                "study_estimate": (
                    "one estimate or explicit unavailable contrast",
                    ["estimate_id"],
                ),
                "study_diagnostic": ("one study diagnostic", ["study_id", "diagnostic_name"]),
                "study_sensitivity": ("one study sensitivity result", ["study_id", "check_name"]),
                "estonia_gp_bridge": ("one descriptive GP bridge component", ["component"]),
                "basket_pair_result": (
                    "one approved unordered subcategory pair",
                    ["subcategory_a_id", "subcategory_b_id"],
                ),
            }.items():
                objects[table] = {
                    "schema_id": f"bsi.statistics.{table}",
                    "schema_version": 1,
                    "object_type": "table",
                    "grain": grain,
                    "key": key,
                    "row_count": con.execute(f"SELECT count(*) FROM {table}").fetchone()[0],
                    "columns": [
                        {"name": x[1], "type": x[2], "nullable": not x[3]}
                        for x in con.execute(f"PRAGMA table_info('{table}')").fetchall()
                    ],
                    "as_of_date": str(cutoff),
                    "source_batch_id": inputs["warehouse"].manifest["source_batch_id"],
                }
            if objects["study_catalogue"]["row_count"] != 5:
                raise StatisticalContractError("study catalogue must contain all five studies")
            checks["output"] = {"status": "passed", "violations": 0, "study_count": 5}
            for label in reversed(tuple(inputs)):
                con.execute(f"DETACH {label}")
            con.execute("CHECKPOINT")
        except duckdb.Error as exc:
            raise StatisticalContractError(f"statistical SQL failed: {exc}") from exc
        finally:
            con.close()
        manifest = {
            "schema_id": "bsi.statistical_analysis_publication",
            "schema_version": 1,
            "stage": "statistics",
            "pipeline_version": __version__,
            "publication_status": "passed",
            "source_batch_id": inputs["warehouse"].manifest["source_batch_id"],
            "build_config_hash": configuration.deterministic_hash(),
            "source_manifests_sha256": {label: x.manifest_sha256 for label, x in inputs.items()},
            "source_databases_sha256": {
                label: x.manifest["database"]["sha256"] for label, x in inputs.items()
            },
            "source_silver_manifest_sha256": inputs["warehouse"].manifest[
                "source_silver_manifest_sha256"
            ],
            "database": {"path": DATABASE_NAME, "sha256": _sha(db)},
            "objects": objects,
            "analysis_window": {
                "period_start": str(reporting.period_start),
                "as_of_date": str(cutoff),
                "post_cutoff_excluded": True,
                "prior_start_month": str(prior_start),
                "current_start_month": str(current_start),
            },
            "reproducibility": {
                "seed": SEED,
                "method_version": "statistical-analysis-v1",
                "market_bootstrap_replicates": 1000,
                "market_block_months": 3,
                "basket_customer_bootstrap_replicates": 400,
                "retention_covariance_draws": 400,
                "python": platform.python_version(),
                "packages": {
                    name: importlib.metadata.version(name)
                    for name in ("duckdb", "numpy", "pandas", "scipy", "statsmodels", "patsy")
                },
            },
            "policies": {
                "causality": "observational associations only; no causal design",
                "multiple_testing": (
                    "Benjamini-Hochberg within STAT-04 contrasts and STAT-05 eligible pairs"
                ),
                "mature_return_rate": "STAT-03 not estimable: maturity duration unapproved",
            },
            "study_statuses": {study: result["status"] for study, result in results.items()},
            "validations": checks,
        }
        (staged / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        if not publish:
            return StatisticalBuildResult(manifest, checks)
        _publish_directory(staged, output_directory, force)
    return StatisticalBuildResult(
        manifest, checks, output_directory, output_directory / DATABASE_NAME
    )
