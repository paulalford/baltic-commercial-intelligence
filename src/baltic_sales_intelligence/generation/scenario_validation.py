"""Independent QA-only evidence checks for the fifteen SPEC-06 scenarios."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from baltic_sales_intelligence.config import ProjectConfiguration, load_configuration
from baltic_sales_intelligence.generation.scenario_controls import (
    REGISTRY_COLUMNS,
    ScenarioControl,
    build_truth_registry,
    controls_hash,
    load_scenario_controls,
    resolve_targets,
)

REPORT_COLUMNS = (
    "scenario_id",
    "baseline_sample_size",
    "exposed_sample_size",
    "observed_effect",
    "predeclared_threshold",
    "status",
    "reason",
    "confounding_overlap_diagnostics",
    "cutoff_date",
    "evidence_basis",
)


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    baseline_sample_size: int
    exposed_sample_size: int
    observed_effect: dict[str, Any]
    predeclared_threshold: dict[str, float]
    status: str
    reason: str
    confounding_overlap_diagnostics: dict[str, Any]
    cutoff_date: date
    evidence_basis: str


@dataclass(frozen=True)
class Evidence:
    sales: pd.DataFrame
    accepted_returns: pd.DataFrame
    customers: pd.DataFrame


def _normalise_json(value: object) -> object:
    if isinstance(value, dict):
        return {key: _normalise_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalise_json(item) for item in value]
    if isinstance(value, np.generic):
        return _normalise_json(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _json(value: object) -> str:
    return json.dumps(
        _normalise_json(value),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
        allow_nan=False,
    )


def _finite(value: float) -> bool:
    return bool(np.isfinite(value))


def _safe_ratio(numerator: float, denominator: float) -> float:
    return float(numerator / denominator) if denominator else float("nan")


def _margin(frame: pd.DataFrame) -> float:
    return _safe_ratio(float(frame["gross_profit_eur"].sum()), float(frame["net_sales_eur"].sum()))


def _periods(control: ScenarioControl, cutoff: date) -> tuple[date, date, date, date]:
    exposed_end = min(control.end_date, cutoff)
    exposed_days = max(1, (exposed_end - control.start_date).days + 1)
    baseline_end = control.start_date - timedelta(days=1)
    baseline_start = baseline_end - timedelta(days=exposed_days - 1)
    return baseline_start, baseline_end, control.start_date, exposed_end


def _period_frames(
    frame: pd.DataFrame, control: ScenarioControl, cutoff: date, column: str = "fulfilled_date"
) -> tuple[pd.DataFrame, pd.DataFrame]:
    baseline_start, baseline_end, exposed_start, exposed_end = _periods(control, cutoff)
    baseline = frame[frame[column].between(baseline_start, baseline_end)]
    exposed = frame[frame[column].between(exposed_start, exposed_end)]
    return baseline, exposed


def _result(
    control: ScenarioControl,
    cutoff: date,
    baseline_n: int,
    exposed_n: int,
    observed: dict[str, Any],
    estimable: bool,
    passed: bool,
    reason: str,
    diagnostics: dict[str, Any],
    basis: str,
) -> ScenarioResult:
    status = "NOT_ESTIMABLE" if not estimable else ("PASS" if passed else "FAIL")
    return ScenarioResult(
        control.story_id,
        int(baseline_n),
        int(exposed_n),
        observed,
        control.validation_thresholds,
        status,
        reason if status != "PASS" else "",
        diagnostics,
        cutoff,
        basis,
    )


def _prepare_evidence(tables: dict[str, pd.DataFrame], cutoff: date) -> Evidence:
    orders = tables["orders"]
    eligible_orders = orders[
        (orders["order_status"] == "fulfilled")
        & orders["fulfilled_date"].notna()
        & (orders["fulfilled_date"] <= cutoff)
    ]
    products = tables["products"][["product_id", "subcategory_id"]].merge(
        tables["product_subcategories"][["subcategory_id", "category_id"]],
        on="subcategory_id",
        validate="many_to_one",
    )
    sales = (
        tables["order_lines"]
        .merge(
            eligible_orders,
            on="order_id",
            validate="many_to_one",
            suffixes=("", "_order"),
        )
        .merge(products, on="product_id", validate="many_to_one")
    )
    history = tables["customer_attribute_history"]
    sales = sales.merge(history, on="customer_id", validate="many_to_many")
    sales = sales[
        (sales["valid_from"] <= sales["fulfilled_date"])
        & (sales["valid_to"].isna() | (sales["fulfilled_date"] < sales["valid_to"]))
    ].copy()
    sales = sales.merge(
        tables["customers"][["customer_id", "acquisition_date", "industry_code"]],
        on="customer_id",
        validate="many_to_one",
    )
    sales = sales.merge(
        tables["salespeople"][["salesperson_id", "primary_country_code"]].rename(
            columns={
                "salesperson_id": "credited_salesperson_id",
                "primary_country_code": "credited_salesperson_country_code",
            }
        ),
        on="credited_salesperson_id",
        how="left",
        validate="many_to_one",
    )
    sales["net_sales_eur"] = sales["posted_net_sales_eur"].map(float)
    sales["cogs_eur"] = sales["posted_cogs_eur"].map(float)
    sales["gross_profit_eur"] = sales["net_sales_eur"] - sales["cogs_eur"]
    sales["discount_eur"] = sales["posted_discount_eur"].map(float)
    sales["list_value_eur"] = sales["posted_list_value_eur"].map(float)
    sales["quantity"] = sales["quantity_ordered"].astype(float)
    sales["month"] = sales["fulfilled_date"].map(lambda value: value.replace(day=1))
    customers = tables["customers"].merge(
        history.sort_values("valid_from").groupby("customer_id", sort=False).first().reset_index(),
        on="customer_id",
        validate="one_to_one",
    )
    returns = tables["returns"]
    accepted = returns[
        (returns["return_status"] == "accepted")
        & returns["return_decision_date"].notna()
        & (returns["return_decision_date"] <= cutoff)
    ].merge(
        sales[
            [
                "order_line_id",
                "fulfilled_date",
                "category_id",
                "quantity",
                "home_country_code",
            ]
        ].drop_duplicates("order_line_id"),
        on="order_line_id",
        validate="many_to_one",
    )
    return Evidence(sales.reset_index(drop=True), accepted.reset_index(drop=True), customers)


def _s01(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    baseline, exposed = _period_frames(e.sales, c, cutoff)
    target_pre = baseline[baseline["customer_id"] == target]
    target_post = exposed[exposed["customer_id"] == target]
    comparable = (
        (e.sales["home_country_code"] == "LT")
        & (e.sales["company_size_band"] == "large")
        & (e.sales["customer_id"] != target)
    )
    comp_pre = baseline[comparable.loc[baseline.index]]
    comp_post = exposed[comparable.loc[exposed.index]]
    target_ratio = _safe_ratio(target_post["order_id"].nunique(), target_pre["order_id"].nunique())
    comp_ratio = _safe_ratio(comp_post["order_id"].nunique(), comp_pre["order_id"].nunique())
    effect = _safe_ratio(target_ratio, comp_ratio)
    req = c.exposure_requirements
    estimable = (
        target_pre["order_id"].nunique() >= req["baseline_orders"]
        and target_post["order_id"].nunique() >= req["exposed_orders"]
        and len(comp_pre) > 0
        and len(comp_post) > 0
    )
    threshold = c.validation_thresholds["maximum_normalised_post_pre_ratio"]
    return _result(
        c,
        cutoff,
        target_pre["order_id"].nunique(),
        target_post["order_id"].nunique(),
        {"normalised_post_pre_order_ratio": effect},
        estimable,
        effect <= threshold,
        "insufficient target/comparator observation window"
        if not estimable
        else "decline did not exceed threshold",
        {
            "comparator_pre_orders": comp_pre["order_id"].nunique(),
            "comparator_post_orders": comp_post["order_id"].nunique(),
            "seasonal_month_coverage": exposed["month"].nunique(),
        },
        "fulfilled orders by customer; comparator-normalised frequency",
    )


def _s02(c: ScenarioControl, _target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    baseline, exposed = _period_frames(e.sales[e.sales["home_country_code"] == "EE"], c, cutoff)
    days = max(1, (min(c.end_date, cutoff) - c.start_date).days + 1)
    revenue_growth = (
        _safe_ratio(exposed["net_sales_eur"].sum(), baseline["net_sales_eur"].sum()) - 1
    )
    margin_change = _margin(exposed) - _margin(baseline)
    t = c.validation_thresholds
    req = c.exposure_requirements
    estimable = len(baseline) >= req["baseline_lines"] and len(exposed) >= req["exposed_lines"]
    passed = (
        revenue_growth >= t["minimum_revenue_growth"]
        and margin_change <= t["maximum_margin_change"]
    )
    return _result(
        c,
        cutoff,
        len(baseline),
        len(exposed),
        {"revenue_growth": revenue_growth, "gross_margin_change": margin_change},
        estimable,
        passed,
        "insufficient EE line exposure"
        if not estimable
        else "growth and margin deterioration were not both present",
        {
            "baseline_product_count": baseline["product_id"].nunique(),
            "exposed_product_count": exposed["product_id"].nunique(),
            "price_change": _safe_ratio(
                exposed["net_sales_eur"].sum() / max(1, exposed["quantity"].sum()),
                baseline["net_sales_eur"].sum() / max(1, baseline["quantity"].sum()),
            )
            - 1,
            "window_days": days,
        },
        "fulfilled-date EE revenue and gross margin",
    )


def _s03(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    _baseline, exposed = _period_frames(e.sales, c, cutoff)
    target_rows = exposed[exposed["ship_to_city_id"] == target]
    comparison = exposed[
        (exposed["tax_jurisdiction_code"] == "LT") & (exposed["ship_to_city_id"] != target)
    ]
    target_by_product = target_rows.groupby("product_id")[["discount_eur", "list_value_eur"]].sum()
    comp_by_product = comparison.groupby("product_id")[["discount_eur", "list_value_eur"]].sum()
    common = target_by_product.join(
        comp_by_product, lsuffix="_target", rsuffix="_comp", how="inner"
    )
    weights = common["list_value_eur_target"]
    gaps = (
        common["discount_eur_target"] / common["list_value_eur_target"]
        - common["discount_eur_comp"] / common["list_value_eur_comp"]
    )
    effect = (
        float(np.average(gaps, weights=weights)) if len(common) and weights.sum() else float("nan")
    )
    req = c.exposure_requirements
    estimable = (
        len(target_rows) >= req["exposed_lines"]
        and len(comparison) >= req["baseline_lines"]
        and len(common) >= 5
    )
    threshold = c.validation_thresholds["minimum_mix_adjusted_discount_gap"]
    return _result(
        c,
        cutoff,
        len(comparison),
        len(target_rows),
        {"mix_adjusted_discount_gap": effect},
        estimable,
        effect >= threshold,
        "insufficient comparable product overlap"
        if not estimable
        else "mix-adjusted discount gap below threshold",
        {
            "common_products": len(common),
            "target_customer_count": target_rows["customer_id"].nunique(),
            "comparator_customer_count": comparison["customer_id"].nunique(),
        },
        "fulfilled lines; product-mix-standardised posted discount rate",
    )


def _s04(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    rows = e.sales[
        e.sales["fulfilled_date"].between(c.start_date, min(c.end_date, cutoff))
        & e.sales["credited_salesperson_id"].notna()
    ]
    grouped = rows.groupby("credited_salesperson_id").agg(
        revenue=("net_sales_eur", "sum"),
        gp=("gross_profit_eur", "sum"),
        lines=("order_line_id", "size"),
        customers=("customer_id", "nunique"),
    )
    if target not in grouped.index:
        return _result(
            c,
            cutoff,
            len(rows),
            0,
            {},
            False,
            False,
            "target salesperson has no recognised lines",
            {},
            "fulfilled-date credited salesperson economics",
        )
    grouped["margin"] = grouped["gp"] / grouped["revenue"]
    percentile = float(grouped["revenue"].rank(pct=True).loc[target])
    margin_gap = float(
        grouped.loc[target, "margin"] - grouped.drop(index=target)["margin"].median()
    )
    t = c.validation_thresholds
    estimable = (
        int(grouped.loc[target, "lines"]) >= c.exposure_requirements["exposed_lines"]
        and len(grouped) >= 3
    )
    passed = percentile >= t["minimum_revenue_percentile"] and margin_gap <= t["maximum_margin_gap"]
    return _result(
        c,
        cutoff,
        int(grouped.drop(index=target)["lines"].sum()),
        int(grouped.loc[target, "lines"]),
        {"revenue_percentile": percentile, "gross_margin_gap": margin_gap},
        estimable,
        passed,
        "insufficient salesperson exposure"
        if not estimable
        else "revenue rank and low-profit condition were not both met",
        {
            "target_customers": int(grouped.loc[target, "customers"]),
            "target_average_customer_revenue": float(
                grouped.loc[target, "revenue"] / max(1, grouped.loc[target, "customers"])
            ),
            "return_effect_excluded": (
                "validator uses sale GP; accepted returns reported separately"
            ),
        },
        "fulfilled-date credited revenue and gross profit with account-mix diagnostic",
    )


def _peer_repeat_rates(
    frame: pd.DataFrame, maturity_date: date
) -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """Return repeat rates for equally mature salesperson-customer relationships."""

    relationships = frame.groupby(["credited_salesperson_id", "customer_id"]).agg(
        first_purchase=("fulfilled_date", "min"),
        purchase_months=("month", "nunique"),
    )
    relationships = relationships[relationships["first_purchase"] <= maturity_date]
    return (
        (relationships["purchase_months"] >= 2)
        .groupby(level="credited_salesperson_id")
        .mean(),
        relationships.groupby(level="credited_salesperson_id").size(),
        relationships,
    )


def _s05(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    rows = e.sales[
        e.sales["fulfilled_date"].between(c.start_date, min(c.end_date, cutoff))
        & e.sales["credited_salesperson_id"].notna()
    ]
    target_rows = rows[rows["credited_salesperson_id"] == target]
    country = target_rows["credited_salesperson_country_code"].dropna()
    country_code = str(country.mode().iloc[0]) if len(country) else "LV"
    comparison = rows[
        (rows["credited_salesperson_id"] != target)
        & (rows["home_country_code"] == country_code)
        & (rows["credited_salesperson_country_code"] == country_code)
    ]
    target_margin, comp_margin = _margin(target_rows), _margin(comparison)
    observation_days = int(c.exposure_requirements["repeat_observation_days"])
    maturity_date = min(c.end_date, cutoff) - timedelta(days=observation_days)
    target_repeat_rates, target_customer_counts, _ = _peer_repeat_rates(
        target_rows, maturity_date
    )
    peer_repeat_rates, peer_customer_counts, peer_relationships = _peer_repeat_rates(
        comparison, maturity_date
    )
    target_repeat_rate = float(target_repeat_rates.get(target, float("nan")))
    comparator_repeat_rate = float(peer_repeat_rates.median())
    repeat_gap = target_repeat_rate - comparator_repeat_rate
    target_revenue = target_rows["net_sales_eur"].sum()
    other_revenues = comparison.groupby("credited_salesperson_id")["net_sales_eur"].sum()
    modest = bool(len(other_revenues) and target_revenue <= other_revenues.median())
    req, t = c.exposure_requirements, c.validation_thresholds
    estimable = (
        int(target_customer_counts.get(target, 0)) >= req["exposed_customers"]
        and len(peer_customer_counts) > 0
        and int(peer_customer_counts.min()) >= req["baseline_customers"]
    )
    passed = (
        modest
        and target_margin - comp_margin >= t["minimum_margin_gap"]
        and repeat_gap >= t["minimum_repeat_rate_gap"]
    )
    return _result(
        c,
        cutoff,
        int(peer_customer_counts.sum()),
        int(target_customer_counts.get(target, 0)),
        {
            "gross_margin_gap": target_margin - comp_margin,
            "repeat_customer_rate_gap": repeat_gap,
            "revenue_to_peer_median_ratio": _safe_ratio(
                target_revenue, float(other_revenues.median()) if len(other_revenues) else 0
            ),
        },
        estimable,
        passed,
        "insufficient portfolio exposure"
        if not estimable
        else "modest revenue, retention and margin conditions were not all met",
        {
            "target_tenure_months": target_rows["month"].nunique(),
            "comparator_tenure_months": comparison["month"].nunique(),
            "target_customer_count": target_rows["customer_id"].nunique(),
            "repeat_eligible_target_customers": int(target_customer_counts.get(target, 0)),
            "repeat_eligible_comparator_customers": int(
                peer_relationships.index.get_level_values("customer_id").nunique()
            ),
            "repeat_eligible_comparator_relationships": int(peer_customer_counts.sum()),
            "target_repeat_customer_rate": target_repeat_rate,
            "median_peer_repeat_customer_rate": comparator_repeat_rate,
            "minimum_peer_repeat_eligible_customers": (
                int(peer_customer_counts.min()) if len(peer_customer_counts) else 0
            ),
            "repeat_observation_days": observation_days,
            "comparator_rep_count": comparison["credited_salesperson_id"].nunique(),
            "cross_market_credits_excluded": int(
                (
                    (rows["home_country_code"] == country_code)
                    & (rows["credited_salesperson_country_code"] != country_code)
                ).sum()
            ),
        },
        "same-market credited fulfilled lines; repeat buying and gross margin",
    )


def _s06(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    sales = e.sales[e.sales["category_id"] == target]
    baseline, exposed = _period_frames(sales, c, cutoff)
    maturity = int(c.exposure_requirements["maturity_days"])
    mature_limit = cutoff - timedelta(days=maturity)
    baseline = baseline[baseline["fulfilled_date"] <= mature_limit]
    exposed = exposed[exposed["fulfilled_date"] <= mature_limit]
    accepted = e.accepted_returns[e.accepted_returns["category_id"] == target]
    pre_ids, post_ids = set(baseline["order_line_id"]), set(exposed["order_line_id"])
    pre_returned = accepted[accepted["order_line_id"].isin(pre_ids)]["accepted_quantity"].sum()
    post_returned = accepted[accepted["order_line_id"].isin(post_ids)]["accepted_quantity"].sum()
    unit_growth = _safe_ratio(exposed["quantity"].sum(), baseline["quantity"].sum()) - 1
    return_gap = _safe_ratio(post_returned, exposed["quantity"].sum()) - _safe_ratio(
        pre_returned, baseline["quantity"].sum()
    )
    req, t = c.exposure_requirements, c.validation_thresholds
    estimable = (
        baseline["quantity"].sum() >= req["baseline_sold_units"]
        and exposed["quantity"].sum() >= req["exposed_sold_units"]
    )
    passed = unit_growth >= t["minimum_unit_growth"] and return_gap >= t["minimum_return_rate_gap"]
    return _result(
        c,
        cutoff,
        int(baseline["quantity"].sum()),
        int(exposed["quantity"].sum()),
        {"sold_unit_growth": unit_growth, "accepted_return_rate_gap": return_gap},
        estimable,
        passed,
        "insufficient mature sold-unit exposure"
        if not estimable
        else "volume growth and mature return-rate increase were not both met",
        {
            "maturity_days": maturity,
            "baseline_return_events": int(
                accepted[accepted["order_line_id"].isin(pre_ids)].shape[0]
            ),
            "exposed_return_events": int(
                accepted[accepted["order_line_id"].isin(post_ids)].shape[0]
            ),
        },
        "mature fulfilled cohorts and accepted quantities by decision date",
    )


def _s07(c: ScenarioControl, _target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    customers = e.customers[e.customers["home_country_code"] == "LV"]
    baseline_start, baseline_end, exposed_start, exposed_end = _periods(c, cutoff)
    baseline = customers[customers["acquisition_date"].between(baseline_start, baseline_end)]
    exposed = customers[customers["acquisition_date"].between(exposed_start, exposed_end)]
    growth = _safe_ratio(len(exposed), len(baseline)) - 1
    first_orders = e.sales.groupby("customer_id")["fulfilled_date"].min()
    acquired_with_purchase = (
        exposed.set_index("customer_id")["acquisition_date"]
        .to_frame()
        .join(first_orders.rename("first_purchase"))
    )
    within = (
        acquired_with_purchase["first_purchase"] - acquired_with_purchase["acquisition_date"]
    ).map(lambda value: value.days if pd.notna(value) else 10**9) <= int(
        c.exposure_requirements["first_purchase_days"]
    )
    req = c.exposure_requirements
    estimable = (
        len(baseline) >= req["baseline_customers"] and len(exposed) >= req["exposed_customers"]
    )
    return _result(
        c,
        cutoff,
        len(baseline),
        len(exposed),
        {
            "annualised_acquisition_growth": growth,
            "timely_first_purchase_share": float(within.mean()) if len(within) else float("nan"),
        },
        estimable,
        growth >= c.validation_thresholds["minimum_acquisition_growth"],
        "insufficient acquisition cohorts"
        if not estimable
        else "LV acquisition growth below threshold",
        {
            "baseline_window": f"{baseline_start}/{baseline_end}",
            "exposed_window": f"{exposed_start}/{exposed_end}",
            "industry_overlap": len(set(baseline["industry_code"]) & set(exposed["industry_code"])),
        },
        "customer acquisition dates and first fulfilled purchase",
    )


def _s08(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    rows = e.sales[e.sales["fulfilled_date"].between(c.start_date, min(c.end_date, cutoff))]
    peaks = set(c.strength["peak_months"])
    target_rows = rows[rows["category_id"] == target]
    other = rows[rows["category_id"] != target]
    target_ratio = _safe_ratio(
        target_rows[target_rows["fulfilled_date"].map(lambda value: value.month in peaks)][
            "quantity"
        ].sum()
        / max(1, len(peaks)),
        target_rows[~target_rows["fulfilled_date"].map(lambda value: value.month in peaks)][
            "quantity"
        ].sum()
        / max(1, 12 - len(peaks)),
    )
    other_ratio = _safe_ratio(
        other[other["fulfilled_date"].map(lambda value: value.month in peaks)]["quantity"].sum()
        / max(1, len(peaks)),
        other[~other["fulfilled_date"].map(lambda value: value.month in peaks)]["quantity"].sum()
        / max(1, 12 - len(peaks)),
    )
    effect = _safe_ratio(target_ratio, other_ratio)
    peak_rows = target_rows[target_rows["fulfilled_date"].map(lambda value: value.month in peaks)]
    nonpeak_rows = target_rows[
        ~target_rows["fulfilled_date"].map(lambda value: value.month in peaks)
    ]
    req = c.exposure_requirements
    estimable = (
        len(nonpeak_rows) >= req["baseline_lines"]
        and len(peak_rows) >= req["exposed_lines"]
        and target_rows["fulfilled_date"].map(lambda value: value.year).nunique()
        >= req["minimum_years"]
    )
    return _result(
        c,
        cutoff,
        len(nonpeak_rows),
        len(peak_rows),
        {"category_specific_seasonal_ratio": effect},
        estimable,
        effect >= c.validation_thresholds["minimum_seasonal_ratio"],
        "insufficient multi-year seasonal exposure"
        if not estimable
        else "seasonal ratio below threshold",
        {
            "years": target_rows["fulfilled_date"].map(lambda value: value.year).nunique(),
            "other_category_peak_ratio": other_ratio,
            "target_peak_months": sorted(peaks),
        },
        "fulfilled category units by calendar month across years",
    )


def _s09(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    a, b = target.split("+")
    rows = e.sales[e.sales["fulfilled_date"].between(c.start_date, min(c.end_date, cutoff))]
    baskets = rows.groupby("order_id")["category_id"].agg(set)
    count = len(baskets)
    count_a = int(baskets.map(lambda values: a in values).sum())
    count_b = int(baskets.map(lambda values: b in values).sum())
    pair = int(baskets.map(lambda values: a in values and b in values).sum())
    lift = _safe_ratio(
        pair / count if count else 0,
        (count_a / count if count else 0) * (count_b / count if count else 0),
    )
    req = c.exposure_requirements
    estimable = (
        count >= req["baseline_orders"]
        and pair >= req["minimum_pair_orders"]
        and count_a
        and count_b
    )
    return _result(
        c,
        cutoff,
        count,
        pair,
        {"pair_lift": lift, "pair_order_count": pair},
        estimable,
        lift >= c.validation_thresholds["minimum_lift"],
        "insufficient supported pair baskets"
        if not estimable
        else "supported pair lift below threshold",
        {
            "orders_with_a": count_a,
            "orders_with_b": count_b,
            "order_denominator": count,
            "duplicate_lines_collapsed": True,
        },
        "distinct order baskets; joint support divided by marginal supports",
    )


def _s10(c: ScenarioControl, _target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    rows = e.sales[
        (e.sales["home_country_code"] == "EE")
        & e.sales["fulfilled_date"].between(c.start_date, min(c.end_date, cutoff))
    ]
    revenue = rows.groupby("customer_id")["net_sales_eur"].sum().sort_values(ascending=False)
    top5 = _safe_ratio(float(revenue.head(5).sum()), float(revenue.sum()))
    shares = revenue / revenue.sum() if revenue.sum() else revenue
    hhi = float((shares**2).sum()) if len(shares) else float("nan")
    req = c.exposure_requirements
    estimable = len(revenue) >= req["exposed_customers"]
    return _result(
        c,
        cutoff,
        len(revenue),
        min(5, len(revenue)),
        {"top5_revenue_share": top5, "customer_revenue_hhi": hhi},
        estimable,
        top5 >= c.validation_thresholds["minimum_top5_share"],
        "insufficient EE customer exposure" if not estimable else "top-five share below threshold",
        {
            "market_customer_count": len(revenue),
            "enterprise_share_top5": float(
                (
                    rows[rows["customer_id"].isin(revenue.head(5).index)]["segment_code"]
                    == "ENTERPRISE"
                ).mean()
            ),
        },
        "fulfilled-date net revenue by customer-home market",
    )


def _s11(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    market_baseline, market_exposed = _period_frames(e.sales, c, cutoff)
    baseline, exposed = _period_frames(e.sales[e.sales["category_id"] == target], c, cutoff)
    common_products = set(baseline["product_id"]) & set(exposed["product_id"])
    baseline = baseline[baseline["product_id"].isin(common_products)]
    exposed = exposed[exposed["product_id"].isin(common_products)]
    price_pre = _safe_ratio(baseline["net_sales_eur"].sum(), baseline["quantity"].sum())
    price_post = _safe_ratio(exposed["net_sales_eur"].sum(), exposed["quantity"].sum())
    gp_pre = _safe_ratio(baseline["gross_profit_eur"].sum(), baseline["quantity"].sum())
    gp_post = _safe_ratio(exposed["gross_profit_eur"].sum(), exposed["quantity"].sum())
    baseline_orders = market_baseline["order_id"].nunique()
    exposed_orders = market_exposed["order_id"].nunique()
    baseline_unit_intensity = _safe_ratio(baseline["quantity"].sum(), baseline_orders)
    exposed_unit_intensity = _safe_ratio(exposed["quantity"].sum(), exposed_orders)
    observed = {
        "realised_unit_price_growth": _safe_ratio(price_post, price_pre) - 1,
        "unit_gross_profit_growth": _safe_ratio(gp_post, gp_pre) - 1,
        "sold_unit_growth": _safe_ratio(exposed_unit_intensity, baseline_unit_intensity) - 1,
    }
    req, t = c.exposure_requirements, c.validation_thresholds
    common = len(common_products)
    estimable = (
        len(baseline) >= req["baseline_lines"]
        and len(exposed) >= req["exposed_lines"]
        and common >= 5
    )
    passed = (
        observed["realised_unit_price_growth"] >= t["minimum_price_growth"]
        and observed["unit_gross_profit_growth"] >= t["minimum_unit_gp_growth"]
        and observed["sold_unit_growth"] <= t["maximum_unit_growth"]
    )
    return _result(
        c,
        cutoff,
        len(baseline),
        len(exposed),
        observed,
        estimable,
        passed,
        "insufficient common-product exposure"
        if not estimable
        else "price, unit GP and demand response conditions were not all met",
        {
            "common_products": common,
            "baseline_average_cost": _safe_ratio(
                baseline["cogs_eur"].sum(), baseline["quantity"].sum()
            ),
            "exposed_average_cost": _safe_ratio(
                exposed["cogs_eur"].sum(), exposed["quantity"].sum()
            ),
            "raw_common_product_unit_growth": _safe_ratio(
                exposed["quantity"].sum(), baseline["quantity"].sum()
            )
            - 1,
            "market_order_growth": _safe_ratio(exposed_orders, baseline_orders) - 1,
            "activity_denominator": "fulfilled market orders",
        },
        "common-product fulfilled lines; price, GP/unit and units per market order",
    )


def _s12(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    rows = e.sales[e.sales["fulfilled_date"] <= min(c.end_date, cutoff)]
    customer_gp = (
        rows.groupby(["customer_id", "segment_code"])["gross_profit_eur"].sum().reset_index()
    )
    acquisition = e.customers.set_index("customer_id")["acquisition_date"]
    customer_gp["exposure_years"] = customer_gp["customer_id"].map(
        lambda value: max(1.0, (cutoff - acquisition[value]).days) / 365.25
    )
    customer_gp["annualised_gp"] = customer_gp["gross_profit_eur"] / customer_gp["exposure_years"]
    target_rows = customer_gp[customer_gp["segment_code"] == target]
    comparison = customer_gp[customer_gp["segment_code"] != target]
    ratio = _safe_ratio(
        float(target_rows["annualised_gp"].median()), float(comparison["annualised_gp"].median())
    )
    req = c.exposure_requirements
    estimable = (
        len(target_rows) >= req["exposed_customers"]
        and len(comparison) >= req["baseline_customers"]
    )
    return _result(
        c,
        cutoff,
        len(comparison),
        len(target_rows),
        {"tenure_adjusted_median_gp_ratio": ratio},
        estimable,
        ratio >= c.validation_thresholds["minimum_tenure_adjusted_gp_ratio"],
        "insufficient tenure-eligible customers"
        if not estimable
        else "tenure-adjusted lifetime GP ratio below threshold",
        {
            "target_median_exposure_years": float(target_rows["exposure_years"].median())
            if len(target_rows)
            else None,
            "comparator_median_exposure_years": float(comparison["exposure_years"].median())
            if len(comparison)
            else None,
            "industry_overlap": len(
                set(rows[rows["segment_code"] == target]["industry_code"])
                & set(rows[rows["segment_code"] != target]["industry_code"])
            ),
        },
        "customer lifetime sale GP annualised by acquisition exposure",
    )


def _s13(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    baseline, exposed = _period_frames(e.sales[e.sales["category_id"] == target], c, cutoff)
    revenue_growth = (
        _safe_ratio(exposed["net_sales_eur"].sum(), baseline["net_sales_eur"].sum()) - 1
    )
    margin_change = _margin(exposed) - _margin(baseline)
    cost_growth = (
        _safe_ratio(
            _safe_ratio(exposed["cogs_eur"].sum(), exposed["quantity"].sum()),
            _safe_ratio(baseline["cogs_eur"].sum(), baseline["quantity"].sum()),
        )
        - 1
    )
    req, t = c.exposure_requirements, c.validation_thresholds
    common = len(set(baseline["product_id"]) & set(exposed["product_id"]))
    estimable = (
        len(baseline) >= req["baseline_lines"]
        and len(exposed) >= req["exposed_lines"]
        and common >= 5
    )
    passed = (
        revenue_growth >= t["minimum_revenue_growth"]
        and margin_change <= t["maximum_margin_change"]
        and cost_growth > 0
    )
    return _result(
        c,
        cutoff,
        len(baseline),
        len(exposed),
        {
            "revenue_growth": revenue_growth,
            "gross_margin_change": margin_change,
            "unit_cost_growth": cost_growth,
        },
        estimable,
        passed,
        "insufficient common-product exposure"
        if not estimable
        else "healthy revenue and cost-driven GP deterioration were not both present",
        {
            "common_products": common,
            "discount_rate_change": _safe_ratio(
                exposed["discount_eur"].sum(), exposed["list_value_eur"].sum()
            )
            - _safe_ratio(baseline["discount_eur"].sum(), baseline["list_value_eur"].sum()),
        },
        "same-category fulfilled revenue, cost and gross margin",
    )


def _s14(c: ScenarioControl, target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    rows = e.sales[(e.sales["category_id"] == target) & (e.sales["fulfilled_date"] <= cutoff)]
    monthly = rows.groupby("month")["quantity"].sum().sort_index()
    frame = monthly.rename("units").reset_index()
    frame["calendar_month"] = frame["month"].map(lambda value: value.month)
    seasonal = frame.groupby("calendar_month")["units"].transform("median")
    residual = np.log((frame["units"] + 1) / (seasonal + 1))
    outside = ~frame["month"].between(c.start_date.replace(day=1), c.end_date.replace(day=1))
    centre = float(residual[outside].median()) if outside.any() else float("nan")
    mad = float((residual[outside] - centre).abs().median()) if outside.any() else float("nan")
    scale = max(1e-9, 1.4826 * mad)
    frame["robust_z"] = (residual - centre) / scale
    true_rows = frame[~outside]
    false_positives = int(
        (frame.loc[outside, "robust_z"] >= c.validation_thresholds["minimum_robust_zscore"]).sum()
    )
    score = float(true_rows["robust_z"].max()) if len(true_rows) else float("nan")
    req, t = c.exposure_requirements, c.validation_thresholds
    estimable = (
        int(outside.sum()) >= req["baseline_months"]
        and len(true_rows) >= req["exposed_months"]
        and mad > 0
    )
    passed = score >= t["minimum_robust_zscore"] and false_positives <= t["maximum_false_positives"]
    return _result(
        c,
        cutoff,
        int(outside.sum()),
        len(true_rows),
        {"maximum_true_period_robust_zscore": score, "false_positive_months": false_positives},
        estimable,
        passed,
        "insufficient non-spike monthly baseline or zero variation"
        if not estimable
        else "spike detector sensitivity/specificity threshold failed",
        {
            "robust_scale": scale,
            "flagged_true_months": int((true_rows["robust_z"] >= t["minimum_robust_zscore"]).sum()),
            "detector_uses_scenario_id": False,
        },
        "monthly category units; month-of-year median residual detector",
    )


def _s15(c: ScenarioControl, _target: str, e: Evidence, cutoff: date) -> ScenarioResult:
    lv = e.sales[e.sales["home_country_code"] == "LV"]
    baseline, exposed = _period_frames(lv, c, cutoff)
    baseline_start, _baseline_end, _exposed_start, _exposed_end = _periods(c, cutoff)
    incumbents = set(
        e.customers[
            (e.customers["home_country_code"] == "LV")
            & (e.customers["acquisition_date"] <= baseline_start)
        ]["customer_id"]
    )
    pre_inc = baseline[baseline["customer_id"].isin(incumbents)]
    post_inc = exposed[exposed["customer_id"].isin(incumbents)]
    observed = {
        "revenue_growth": _safe_ratio(
            exposed["net_sales_eur"].sum(), baseline["net_sales_eur"].sum()
        )
        - 1,
        "gross_margin_change": _margin(exposed) - _margin(baseline),
        "incumbent_unit_activity_change": _safe_ratio(
            _safe_ratio(post_inc["quantity"].sum(), len(incumbents)),
            _safe_ratio(pre_inc["quantity"].sum(), len(incumbents)),
        )
        - 1,
    }
    req, t = c.exposure_requirements, c.validation_thresholds
    estimable = (
        baseline["customer_id"].nunique() >= req["baseline_customers"]
        and exposed["customer_id"].nunique() >= req["exposed_customers"]
        and len(pre_inc)
        and len(post_inc)
    )
    passed = (
        observed["revenue_growth"] >= t["minimum_revenue_growth"]
        and observed["gross_margin_change"] <= t["maximum_margin_change"]
        and observed["incumbent_unit_activity_change"] <= t["maximum_incumbent_activity_change"]
    )
    return _result(
        c,
        cutoff,
        baseline["customer_id"].nunique(),
        exposed["customer_id"].nunique(),
        observed,
        estimable,
        passed,
        "insufficient LV cohort exposure"
        if not estimable
        else "surface growth and underlying health deterioration were not both present",
        {
            "baseline_incumbents": pre_inc["customer_id"].nunique(),
            "exposed_incumbents": post_inc["customer_id"].nunique(),
            "fixed_mature_incumbent_cohort": len(incumbents),
            "incumbent_active_rate_change": _safe_ratio(
                post_inc["customer_id"].nunique(), len(incumbents)
            )
            - _safe_ratio(pre_inc["customer_id"].nunique(), len(incumbents)),
            "new_customer_exposed_revenue_share": _safe_ratio(
                exposed[~exposed["customer_id"].isin(incumbents)]["net_sales_eur"].sum(),
                exposed["net_sales_eur"].sum(),
            ),
        },
        "LV fulfilled revenue/margin plus fixed mature-incumbent cohort activity",
    )


_VALIDATORS: tuple[Callable[[ScenarioControl, str, Evidence, date], ScenarioResult], ...] = (
    _s01,
    _s02,
    _s03,
    _s04,
    _s05,
    _s06,
    _s07,
    _s08,
    _s09,
    _s10,
    _s11,
    _s12,
    _s13,
    _s14,
    _s15,
)


def validate_scenarios(
    tables: dict[str, pd.DataFrame],
    configuration: ProjectConfiguration,
    *,
    cutoff: date | None = None,
    controls: tuple[ScenarioControl, ...] | None = None,
) -> pd.DataFrame:
    """Compute held-out observable evidence without reading intended effects as results."""

    selected_cutoff = cutoff or configuration.application.reporting.default_as_of_date
    if selected_cutoff > configuration.application.reporting.default_as_of_date:
        raise ValueError("scenario QA cutoff cannot exceed the default historical cutoff")
    selected_controls = controls or load_scenario_controls()
    targets = resolve_targets(selected_controls, tables)
    evidence = _prepare_evidence(tables, selected_cutoff)
    results = [
        validator(control, targets[control.story_id], evidence, selected_cutoff)
        for control, validator in zip(selected_controls, _VALIDATORS, strict=True)
    ]
    rows = []
    for item in results:
        payload = asdict(item)
        for key in ("observed_effect", "predeclared_threshold", "confounding_overlap_diagnostics"):
            payload[key] = _json(payload[key])
        rows.append(payload)
    return pd.DataFrame(rows, columns=REPORT_COLUMNS)


def publish_scenario_qa(
    tables: dict[str, pd.DataFrame],
    configuration: ProjectConfiguration,
    output_directory: str | Path,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Publish the private registry and QA report outside public truth outputs."""

    controls = load_scenario_controls()
    report = validate_scenarios(tables, configuration, controls=controls)
    registry = build_truth_registry(controls, tables)
    observed = report.set_index("scenario_id")["observed_effect"]
    registry["observed_effect"] = registry["story_id"].map(observed)
    registry = registry.loc[:, REGISTRY_COLUMNS]
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    report.to_csv(output / "scenario_validation_report.csv", index=False, lineterminator="\n")
    registry.to_csv(output / "private_truth_registry.csv", index=False, lineterminator="\n")
    metadata = {
        "artifact_classification": "restricted_generator_qa",
        "cutoff_date": configuration.application.reporting.default_as_of_date.isoformat(),
        "private_controls_sha256": controls_hash(),
        "scenario_count": 15,
        "status_counts": report["status"].value_counts().sort_index().to_dict(),
        "security_boundary_note": (
            "Local filesystem/module separation is a leakage-prevention convention, "
            "not a formal security boundary."
        ),
    }
    (output / "scenario_qa_metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report, registry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and publish restricted SPEC-06 QA")
    parser.add_argument("--config", default="config", help="Public configuration directory")
    parser.add_argument("--output", default="data/qa/scenarios", help="Ignored QA output directory")
    args = parser.parse_args(argv)
    configuration = load_configuration(Path(args.config))
    from baltic_sales_intelligence.generation.generator import generate_reference_tables

    tables = generate_reference_tables(configuration)
    report, _registry = publish_scenario_qa(tables, configuration, args.output)
    print(report[["scenario_id", "status", "reason"]].to_string(index=False))
    return 0 if bool((report["status"] == "PASS").all()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
