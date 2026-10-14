"""Hand-calculated and seeded miniature statistical fixtures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
import statsmodels.formula.api as smf

from baltic_sales_intelligence.statistics.estimators import (
    CohortReturn,
    CohortSale,
    basket_associations,
    bh_adjust,
    fit_discount,
    fit_retention,
    market_margin_contrast,
    mature_cohort_rate,
    price_mix_cost_bridge,
    wilson_interval,
)


def test_mature_cohort_explicit_fixture_rule_and_partial_returns() -> None:
    sales = [CohortSale("old", date(2026, 6, 1), 3), CohortSale("new", date(2026, 9, 15), 2)]
    returns = [
        CohortReturn("r1", "old", date(2026, 7, 1), 1, True),
        CohortReturn("r2", "old", date(2026, 8, 1), 1, True),
        CohortReturn("r3", "old", date(2026, 8, 2), 1, False),
        CohortReturn("r4", "old", date(2026, 10, 1), 1, True),
        CohortReturn("r5", "new", date(2026, 9, 20), 1, True),
    ]
    result = mature_cohort_rate(sales, returns, as_of=date(2026, 9, 30), maturity_days=30)
    assert result == {"mature_units": 3, "accepted_units": 2, "rate": 2 / 3, "eligible_lines": 1}
    by_category = mature_cohort_rate(
        [*sales, CohortSale("other", date(2026, 6, 1), 10, "other")],
        returns,
        as_of=date(2026, 9, 30),
        maturity_days=30,
        category_id="fixture",
    )
    assert by_category == result
    assert wilson_interval(4, 8) is not None
    assert wilson_interval(4, 0) is None


def test_price_mix_cost_bridge_exact_frozen_fixture() -> None:
    rows = [
        {
            "period": "prior",
            "product_id": "A",
            "units": 10,
            "fulfilled_sales_eur": Decimal("50"),
            "fulfilled_cogs_eur": Decimal("30"),
        },
        {
            "period": "current",
            "product_id": "A",
            "units": 12,
            "fulfilled_sales_eur": Decimal("72"),
            "fulfilled_cogs_eur": Decimal("42"),
        },
        {
            "period": "prior",
            "product_id": "B",
            "units": 20,
            "fulfilled_sales_eur": Decimal("40"),
            "fulfilled_cogs_eur": Decimal("20"),
        },
        {
            "period": "current",
            "product_id": "B",
            "units": 15,
            "fulfilled_sales_eur": Decimal("37.50"),
            "fulfilled_cogs_eur": Decimal("18"),
        },
    ]
    market = [
        {
            "period": "prior",
            "fulfilled_sales_eur": Decimal("90"),
            "net_gross_profit_eur": Decimal("40"),
        },
        {
            "period": "current",
            "fulfilled_sales_eur": Decimal("109.50"),
            "net_gross_profit_eur": Decimal("49.50"),
        },
    ]
    result = price_mix_cost_bridge(rows, market)
    assert result["components"] == {
        "pure_volume_price": Decimal("-9.00"),
        "matched_mix_price": Decimal("9.00"),
        "matched_price": Decimal("19.50"),
        "matched_cost_volume_mix": Decimal("-1.00"),
        "matched_unit_cost": Decimal("-9.00"),
        "new_exited_fulfilled_gp": Decimal("0.00"),
        "return_timing_gp": Decimal("0.00"),
        "rounding_or_unallocated_residual": Decimal("0.00"),
    }
    assert result["observed_change_eur"] == Decimal("9.50")


def test_price_bridge_exposes_cent_rounding_residual() -> None:
    rows = [
        {
            "period": period,
            "product_id": sku,
            "units": units,
            "fulfilled_sales_eur": Decimal(sale),
            "fulfilled_cogs_eur": Decimal(cost),
        }
        for period, sku, units, sale, cost in (
            ("prior", "A", 6, "4.09", "4.54"),
            ("prior", "B", 11, "1.99", "1.24"),
            ("current", "A", 9, "2.93", "4.24"),
            ("current", "B", 10, "2.19", "5.69"),
        )
    ]
    market = [
        {
            "period": "prior",
            "fulfilled_sales_eur": Decimal("6.08"),
            "net_gross_profit_eur": Decimal("0.30"),
        },
        {
            "period": "current",
            "fulfilled_sales_eur": Decimal("5.12"),
            "net_gross_profit_eur": Decimal("-4.81"),
        },
    ]
    result = price_mix_cost_bridge(rows, market)
    assert result["observed_change_eur"] == Decimal("-5.11")
    assert result["components"]["rounding_or_unallocated_residual"] == Decimal("-0.01")
    assert sum(result["components"].values(), Decimal(0)) == Decimal("-5.11")


def test_market_contrast_and_seeded_block_bootstrap() -> None:
    rows = []
    for i, month in enumerate(pd.date_range("2024-10-01", periods=24, freq="MS")):
        for country in ("EE", "LT", "LV"):
            margin = Decimal("0.2") if country == "EE" and i >= 12 else Decimal("0.3")
            rows.append(
                {
                    "reporting_month": month,
                    "home_country_code": country,
                    "net_revenue_eur": Decimal("100"),
                    "net_gross_profit_eur": Decimal("100") * margin,
                }
            )
    frame = pd.DataFrame(rows)
    first = market_margin_contrast(frame, replicates=200)
    assert first == market_margin_contrast(frame, replicates=200)
    assert first["status"] == "association_only"
    assert abs(first["estimate"] + 10) < 1e-10
    assert abs(first["ci_low"] + 10) < 1e-9
    assert abs(first["ci_high"] + 10) < 1e-9


def _discount_fixture(effect: float) -> pd.DataFrame:
    rng = np.random.default_rng(17)
    rows = []
    months = pd.date_range("2025-10-01", periods=5, freq="MS")
    for customer in range(80):
        discount = rng.uniform(0.02, 0.25)
        cluster_noise = rng.normal(0, 0.015)
        for j, month in enumerate(months):
            rows.append(
                {
                    "customer_id": f"C{customer}",
                    "reporting_month": month,
                    "category_id": "A" if customer % 2 else "B",
                    "segment_code": "S1" if customer % 3 else "S2",
                    "home_country_code": "LT" if customer % 4 else "EE",
                    "tenure_months": 12 + customer % 11 + j,
                    "prior_account_discount_rate": discount,
                    "realised_margin_rate": 0.4
                    + effect * discount
                    + cluster_noise
                    + rng.normal(0, 0.004),
                    "net_revenue_eur": Decimal("100.00"),
                }
            )
    return pd.DataFrame(rows)


def test_discount_known_effect_null_and_collinearity() -> None:
    affected = fit_discount(_discount_fixture(-0.5))
    assert affected["status"] == "association_only"
    assert -6.5 < affected["estimate"] < -3.5  # pp for +10 pp prior discount
    assert affected["ci_high"] < 0
    assert affected["clusters"] == 80
    null = fit_discount(_discount_fixture(0))
    assert null["status"] == "association_only"
    assert null["ci_low"] < 0 < null["ci_high"]
    constant = _discount_fixture(-0.5)
    constant["prior_account_discount_rate"] = 0.1
    assert fit_discount(constant)["status"] == "insufficient_evidence"
    collinear = _discount_fixture(-0.5)
    collinear["prior_account_discount_rate"] = (
        collinear.groupby("reporting_month").ngroup() + 1
    ) / 100
    assert fit_discount(collinear)["reason"] == "collinear_or_overparameterized"
    missing = _discount_fixture(-0.5)
    missing.loc[0, "prior_account_discount_rate"] = None
    assert fit_discount(missing)["missing"] == 1


def test_discount_interval_preserves_customer_dependence() -> None:
    sample = _discount_fixture(-0.5)
    result = fit_discount(sample)
    sample["reporting_month"] = sample["reporting_month"].astype(str)
    formula = (
        "realised_margin_rate ~ prior_account_discount_rate + np.log1p(tenure_months) "
        "+ C(category_id) + C(segment_code) + C(home_country_code) + C(reporting_month)"
    )
    independent = smf.wls(formula, data=sample, weights=sample.net_revenue_eur.astype(float)).fit(
        cov_type="HC1"
    )
    cluster_width = result["ci_high"] - result["ci_low"]
    independent_width = 2 * 1.959963984540054 * independent.bse["prior_account_discount_rate"] * 10
    assert cluster_width > independent_width * 1.5


def _retention_fixture() -> pd.DataFrame:
    rng = np.random.default_rng(21)
    rows = []
    for customer in range(100):
        segment = "A" if customer % 2 else "B"
        tendency = rng.normal(0, 0.08)
        for year in (2024, 2025):
            probability = 0.35 + (0.3 if segment == "B" else 0) + tendency
            rows.append(
                {
                    "customer_id": f"C{customer}",
                    "landmark_month": date(year, 1, 1),
                    "segment_code": segment,
                    "home_country_code": "LT",
                    "tenure_months": (12 if year == 2024 else 24) + customer % 7,
                    "prior_12m_orders": 1 + customer % 4,
                    "outcome_mature": True,
                    "retained_next_12m": int(rng.random() < probability),
                }
            )
    rows.append(
        {
            "customer_id": "censored",
            "landmark_month": date(2026, 1, 1),
            "segment_code": "A",
            "home_country_code": "LT",
            "tenure_months": 12,
            "prior_12m_orders": 1,
            "outcome_mature": False,
            "retained_next_12m": None,
        }
    )
    return pd.DataFrame(rows)


def test_retention_standardization_censoring_and_future_leakage() -> None:
    frame = _retention_fixture()
    result = fit_retention(frame, draws=120)
    assert result["status"] == "association_only"
    assert result["censored"] == 1
    assert result["contrasts"][0]["estimate"] > 0
    changed = frame.copy()
    changed.loc[~changed.outcome_mature, "retained_next_12m"] = 1
    assert fit_retention(changed, draws=120)["contrasts"] == result["contrasts"]


def test_retention_complete_segment_separation_is_not_estimable() -> None:
    frame = _retention_fixture()
    frame.loc[frame.outcome_mature & frame.segment_code.eq("A"), "retained_next_12m"] = 1
    result = fit_retention(frame, draws=120)
    assert result["status"] == "not_estimable"
    assert result["reason"] == "complete_separation:A"
    assert result["failure_category"] == "nonidentifiable_model"
    assert result["separated_segments"]["A"] == {"retained": 100, "not_retained": 0}
    assert result["n"] == 200
    assert result["clusters"] == 100
    assert result["censored"] == 1
    assert "contrasts" not in result


def test_retention_invalid_input_and_unexpected_exception_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _retention_fixture()
    invalid = frame.copy()
    invalid.loc[0, "retained_next_12m"] = 2
    result = fit_retention(invalid)
    assert result["reason"] == "invalid_input:retention_sample"
    assert result["invalid_counts"]["nonbinary_outcome"] == 1

    def unexpected(*args: object, **kwargs: object) -> None:
        raise ValueError("unexpected model-programming defect")

    monkeypatch.setattr(smf, "glm", unexpected)
    with pytest.raises(ValueError, match="unexpected model-programming defect"):
        fit_retention(frame)


def test_basket_popularity_cluster_bootstrap_and_bh() -> None:
    rows = []
    for order in range(80):
        items = (
            ("A", "B") if order < 30 else ("A",) if order < 40 else ("B",) if order < 50 else ("C",)
        )
        for item in items:
            rows.append(
                {"order_id": f"O{order}", "customer_id": f"C{order // 2}", "subcategory_id": item}
            )
        if order == 0:
            rows.append({"order_id": "O0", "customer_id": "C0", "subcategory_id": "A"})
    rule = pd.DataFrame(
        [
            {
                "subcategory_a_id": "A",
                "subcategory_b_id": "B",
                "basket_count": 80,
                "item_a_baskets": 40,
                "item_b_baskets": 40,
                "joint_baskets": 30,
            }
        ]
    )
    result = basket_associations(pd.DataFrame(rows), rule, replicates=200)
    assert result == basket_associations(pd.DataFrame(rows), rule, replicates=200)
    pair = result["rows"][0]
    assert pair["customer_clusters"] == 40
    assert pair["pair_support"] == 0.375
    assert pair["confidence_a_to_b"] == 0.75
    assert pair["lift"] == 1.5
    assert pair["expected_joint_baskets"] == 20
    assert pair["q_value"] == pair["p_value"]
    assert bh_adjust([0.01, 0.04, 0.03]) == [0.03, 0.04, 0.04]
