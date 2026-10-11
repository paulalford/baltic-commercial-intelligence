"""Focused SPEC-06 mechanism, QA, cutoff and leakage contracts."""

from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from itertools import pairwise

import numpy as np
import pandas as pd
import pytest

import baltic_sales_intelligence.generation.transactions as transaction_generation
from baltic_sales_intelligence.generation.financial import sale_postings
from baltic_sales_intelligence.generation.generator import build_manifest
from baltic_sales_intelligence.generation.reference_data import (
    PRIVATE_REGISTRY_FIELDS,
    PUBLIC_COLUMNS,
)
from baltic_sales_intelligence.generation.scenario_controls import (
    REGISTRY_COLUMNS,
    build_truth_registry,
    load_scenario_controls,
    resolve_targets,
)
from baltic_sales_intelligence.generation.scenario_validation import (
    REPORT_COLUMNS,
    Evidence,
    _peer_repeat_rates,
    _s05,
    _s06,
    _s11,
    _s14,
    _s15,
    publish_scenario_qa,
    validate_scenarios,
)
from baltic_sales_intelligence.generation.transactions import (
    _apply_s09_pairing,
    _apply_s14_demand_weight,
    _s01_retained_probability,
)
from baltic_sales_intelligence.generation.validation import (
    GenerationValidationError,
    validate_reference_data,
)


@pytest.fixture(scope="module")
def scenario_report(small_tables, small_configuration) -> pd.DataFrame:
    return validate_scenarios(small_tables, small_configuration)


def test_private_config_has_all_predeclared_controls_and_thresholds() -> None:
    controls = load_scenario_controls()
    assert tuple(control.story_id for control in controls) == tuple(
        f"S{number:02d}" for number in range(1, 16)
    )
    assert all(control.strength for control in controls)
    assert all(control.exposure_requirements for control in controls)
    assert all(control.validation_thresholds for control in controls)
    assert len({control.seed_component for control in controls}) == 15


def test_registry_is_exact_and_deterministic(small_tables) -> None:
    controls = load_scenario_controls()
    first = build_truth_registry(controls, small_tables)
    second = build_truth_registry(controls, small_tables)
    assert tuple(first.columns) == REGISTRY_COLUMNS
    pd.testing.assert_frame_equal(first, second)
    assert len(first) == 15
    assert first["observed_effect"].isna().all()


@pytest.mark.parametrize("story_id", [f"S{number:02d}" for number in range(1, 16)])
def test_each_scenario_validator_reports_required_evidence(
    story_id: str, scenario_report: pd.DataFrame
) -> None:
    row = scenario_report.set_index("scenario_id").loc[story_id]
    assert tuple(scenario_report.columns) == REPORT_COLUMNS
    assert row["status"] in {"PASS", "FAIL", "NOT_ESTIMABLE"}
    assert row["baseline_sample_size"] >= 0
    assert row["exposed_sample_size"] >= 0
    assert json.loads(row["predeclared_threshold"])
    assert isinstance(json.loads(row["observed_effect"]), dict)
    assert isinstance(json.loads(row["confounding_overlap_diagnostics"]), dict)
    if row["status"] != "PASS":
        assert row["reason"]


def test_small_fixture_distinguishes_not_estimable_from_fail(scenario_report) -> None:
    assert "NOT_ESTIMABLE" in set(scenario_report["status"])
    not_estimable = scenario_report[scenario_report["status"] == "NOT_ESTIMABLE"]
    assert not_estimable["reason"].str.contains("insufficient|zero variation").all()


def test_historical_cutoff_excludes_future_return_events(small_tables, small_configuration) -> None:
    baseline = validate_scenarios(small_tables, small_configuration)
    changed = {name: frame.copy(deep=True) for name, frame in small_tables.items()}
    cutoff = small_configuration.application.reporting.default_as_of_date
    future = changed["returns"]["return_decision_date"].fillna(cutoff) > cutoff
    assert future.any()
    changed["returns"].loc[future, "accepted_quantity"] = 999
    changed["returns"].loc[future, "posted_refund_ex_vat_eur"] = Decimal("999999.99")
    observed = validate_scenarios(changed, small_configuration)
    pd.testing.assert_frame_equal(baseline, observed)


def test_private_registry_fields_are_rejected_by_public_contracts(
    small_tables, small_configuration
) -> None:
    assert set(REGISTRY_COLUMNS).issubset(PRIVATE_REGISTRY_FIELDS)
    assert all(not (set(columns) & set(REGISTRY_COLUMNS)) for columns in PUBLIC_COLUMNS.values())
    manifest = build_manifest(small_configuration, small_tables)
    manifest_columns = {
        column for dataset in manifest["datasets"].values() for column in dataset["columns"]
    }
    assert not (manifest_columns & set(REGISTRY_COLUMNS))
    changed = {name: frame.copy(deep=True) for name, frame in small_tables.items()}
    changed["orders"]["target_effect"] = None
    with pytest.raises(GenerationValidationError, match=r"output columns|private label"):
        validate_reference_data(changed, small_configuration)


def test_scenario_generation_preserves_posting_arithmetic(small_tables) -> None:
    orders = small_tables["orders"].set_index("order_id")
    fulfilled = small_tables["order_lines"][
        small_tables["order_lines"]["order_id"].map(orders["order_status"]) == "fulfilled"
    ].head(100)
    for row in fulfilled.itertuples(index=False):
        expected = sale_postings(
            row.quantity_ordered,
            row.list_unit_price_eur,
            row.discount_rate,
            row.unit_cost_eur,
            row.vat_rate_fraction,
            fulfilled=True,
        )
        assert expected == (
            row.posted_list_value_eur,
            row.posted_discount_eur,
            row.posted_net_sales_eur,
            row.posted_cogs_eur,
            row.posted_taxable_base_eur,
            row.posted_vat_amount_eur,
            row.posted_invoice_total_eur,
        )


def test_qa_publication_stays_in_separate_output(
    tmp_path, small_tables, small_configuration
) -> None:
    output = tmp_path / "qa" / "scenarios"
    report, registry = publish_scenario_qa(small_tables, small_configuration, output)
    assert len(report) == len(registry) == 15
    assert (output / "scenario_validation_report.csv").is_file()
    assert (output / "private_truth_registry.csv").is_file()
    metadata = json.loads((output / "scenario_qa_metadata.json").read_text(encoding="utf-8"))
    assert metadata["artifact_classification"] == "restricted_generator_qa"
    assert "not a formal security boundary" in metadata["security_boundary_note"]


def _control(story_id: str):
    control = next(item for item in load_scenario_controls() if item.story_id == story_id)
    return replace(
        control,
        exposure_requirements={
            key: 180 if key == "repeat_observation_days" else 1
            for key in control.exposure_requirements
        },
    )


def test_s01_convex_retention_curve_has_clearance_below_half() -> None:
    control = next(item for item in load_scenario_controls() if item.story_id == "S01")
    duration = (control.end_date - control.start_date).days
    dates = [
        control.start_date + timedelta(days=round(duration * step / 1000))
        for step in range(1001)
    ]
    probabilities = [_s01_retained_probability(control, event_date) for event_date in dates]

    assert probabilities[0] == pytest.approx(1.0)
    assert probabilities[-1] == pytest.approx(0.0)
    assert all(left >= right for left, right in pairwise(probabilities))
    assert np.mean(probabilities) < 0.5


def test_s05_generator_credit_is_confined_to_stable_lv_portfolio(small_tables) -> None:
    controls = load_scenario_controls()
    control = next(item for item in controls if item.story_id == "S05")
    target = resolve_targets(controls, small_tables)["S05"]
    orders = small_tables["orders"]
    target_orders = orders[
        (orders["credited_salesperson_id"] == target) & (orders["order_date"] >= control.start_date)
    ]
    initial = (
        small_tables["customer_attribute_history"]
        .sort_values("valid_from")
        .groupby("customer_id", sort=False)
        .first()
    )
    modulus = int(control.strength["portfolio_modulus"])
    assert not target_orders.empty
    assert all(int(customer_id[-5:]) % modulus == 0 for customer_id in target_orders["customer_id"])
    assert set(target_orders["customer_id"].map(initial["home_country_code"])) == {"LV"}


def test_s05_comparator_excludes_incidental_cross_market_credits() -> None:
    rows = []
    for customer, month in (("T1", 1), ("T1", 2), ("T2", 1), ("T2", 2)):
        rows.append(
            (
                date(2025, month, 1),
                "SP-T",
                "LV",
                "LV",
                customer,
                date(2025, month, 1),
                date(2020, 1, 1),
                50.0,
                25.0,
            )
        )
    rows.extend(
        [
            (
                date(2026, 9, 15),
                "SP-T",
                "LV",
                "LV",
                "T3",
                date(2026, 9, 1),
                date(2026, 9, 1),
                10.0,
                5.0,
            ),
            (
                date(2025, 1, 1),
                "SP-P",
                "LV",
                "LV",
                "P1",
                date(2025, 1, 1),
                date(2020, 1, 1),
                500.0,
                150.0,
            ),
            (
                date(2025, 2, 1),
                "SP-P",
                "LV",
                "LV",
                "P2",
                date(2025, 2, 1),
                date(2020, 1, 1),
                500.0,
                150.0,
            ),
            (
                date(2025, 1, 1),
                "SP-X",
                "EE",
                "LV",
                "X1",
                date(2025, 1, 1),
                date(2020, 1, 1),
                1.0,
                0.1,
            ),
        ]
    )
    sales = pd.DataFrame(
        rows,
        columns=(
            "fulfilled_date",
            "credited_salesperson_id",
            "credited_salesperson_country_code",
            "home_country_code",
            "customer_id",
            "month",
            "acquisition_date",
            "net_sales_eur",
            "gross_profit_eur",
        ),
    )
    result = _s05(
        _control("S05"), "SP-T", Evidence(sales, pd.DataFrame(), pd.DataFrame()), date(2026, 9, 30)
    )
    assert result.status == "PASS"
    assert result.observed_effect["revenue_to_peer_median_ratio"] == pytest.approx(0.21)
    assert result.observed_effect["repeat_customer_rate_gap"] == pytest.approx(1.0)
    assert result.confounding_overlap_diagnostics["cross_market_credits_excluded"] == 1
    assert result.confounding_overlap_diagnostics["repeat_eligible_target_customers"] == 2


def test_s05_repeat_comparator_preserves_salesperson_customer_relationships() -> None:
    rows = []
    for rep in ("SP-P1", "SP-P2"):
        rows.extend(
            [
                (rep, "SHARED", date(2025, 1, 1), date(2025, 1, 1)),
                (rep, f"{rep}-ONLY", date(2025, 2, 1), date(2025, 2, 1)),
                (rep, f"{rep}-RECENT", date(2026, 9, 1), date(2026, 9, 1)),
            ]
        )
    sales = pd.DataFrame(
        rows,
        columns=("credited_salesperson_id", "customer_id", "month", "fulfilled_date"),
    )
    rates, counts, relationships = _peer_repeat_rates(sales, date(2026, 4, 3))
    assert rates.to_dict() == {"SP-P1": 0.0, "SP-P2": 0.0}
    assert counts.to_dict() == {"SP-P1": 2, "SP-P2": 2}
    assert "SP-P1-RECENT" not in relationships.index.get_level_values("customer_id")


def test_s05_credit_strength_does_not_change_s01_exposure(
    monkeypatch, small_tables, small_configuration
) -> None:
    controls = load_scenario_controls()

    def generated_with_affinity(affinity: float):
        changed = tuple(
            replace(
                control,
                strength={**control.strength, "portfolio_affinity": affinity},
            )
            if control.story_id == "S05"
            else control
            for control in controls
        )
        monkeypatch.setattr(transaction_generation, "load_scenario_controls", lambda: changed)
        return transaction_generation.generate_orders_and_lines(small_configuration, small_tables)

    baseline_orders, _ = generated_with_affinity(0.0)
    changed_orders, _ = generated_with_affinity(1.0)
    targets = resolve_targets(controls, small_tables)
    s01_target = targets["S01"]
    pd.testing.assert_frame_equal(
        baseline_orders[baseline_orders["customer_id"] == s01_target].reset_index(drop=True),
        changed_orders[changed_orders["customer_id"] == s01_target].reset_index(drop=True),
    )


def test_s06_uses_target_category_mature_sold_unit_denominators() -> None:
    sales = pd.DataFrame(
        [
            ("B1", date(2024, 6, 1), "CAT-07", 100.0),
            ("E1", date(2025, 6, 1), "CAT-07", 150.0),
            ("X1", date(2025, 6, 1), "CAT-08", 1.0),
        ],
        columns=("order_line_id", "fulfilled_date", "category_id", "quantity"),
    )
    accepted = pd.DataFrame(
        [
            ("B1", "CAT-07", 1),
            ("E1", "CAT-07", 4),
            ("X1", "CAT-08", 999),
        ],
        columns=("order_line_id", "category_id", "accepted_quantity"),
    )
    result = _s06(
        _control("S06"), "CAT-07", Evidence(sales, accepted, pd.DataFrame()), date(2026, 9, 30)
    )
    assert result.status == "PASS"
    assert result.observed_effect["sold_unit_growth"] == pytest.approx(0.5)
    assert result.observed_effect["accepted_return_rate_gap"] == pytest.approx(1 / 60)
    assert result.baseline_sample_size == 100
    assert result.exposed_sample_size == 150


def test_s11_uses_common_product_units_per_market_order() -> None:
    rows = []
    for number in range(10):
        rows.append(
            (
                date(2024, 6, 1),
                f"B{number}",
                "CAT-03",
                f"P{number % 5}",
                10.0,
                100.0,
                60.0,
                40.0,
            )
        )
    for number in range(20):
        rows.append(
            (
                date(2025, 6, 1),
                f"E{number}",
                "CAT-03",
                f"P{number % 5}",
                8.0,
                91.2,
                60.0,
                31.2,
            )
        )
    sales = pd.DataFrame(
        rows,
        columns=(
            "fulfilled_date",
            "order_id",
            "category_id",
            "product_id",
            "quantity",
            "net_sales_eur",
            "gross_profit_eur",
            "cogs_eur",
        ),
    )
    result = _s11(
        _control("S11"),
        "CAT-03",
        Evidence(sales, pd.DataFrame(), pd.DataFrame()),
        date(2026, 9, 30),
    )
    assert result.status == "PASS"
    assert result.observed_effect["realised_unit_price_growth"] == pytest.approx(0.14)
    assert result.observed_effect["sold_unit_growth"] == pytest.approx(-0.20)
    assert result.confounding_overlap_diagnostics[
        "raw_common_product_unit_growth"
    ] == pytest.approx(0.60)


def test_s09_pairing_uses_preconsumed_component_draws() -> None:
    categories = np.array(["CAT-04", "CAT-05", "CAT-10", "CAT-05"], dtype=object)
    selected = np.array([0, 2])

    _apply_s09_pairing(selected, categories, "CAT-04", "CAT-05", 0.72, 0.10, 0.90)

    assert selected.tolist() == [0, 3]
    assert set(categories[selected]) == {"CAT-04", "CAT-05"}


@pytest.mark.parametrize(
    ("event_date", "expected_weight"),
    [
        (date(2025, 3, 31), 1.0),
        (date(2025, 4, 1), 3.9),
        (date(2025, 6, 30), 3.9),
        (date(2025, 7, 1), 1.0),
    ],
)
def test_s14_weight_follows_fulfilment_boundaries(
    event_date: date, expected_weight: float
) -> None:
    control = next(item for item in load_scenario_controls() if item.story_id == "S14")
    weights = np.ones(2)
    _apply_s14_demand_weight(
        weights,
        np.array(["CAT-10", "CAT-01"], dtype=object),
        "CAT-10",
        control,
        event_date,
    )
    assert weights.tolist() == pytest.approx([expected_weight, 1.0])


def test_s14_strength_isolated_from_history_and_s01(
    monkeypatch, small_tables, small_configuration
) -> None:
    controls = load_scenario_controls()

    def generated_with_strength(multiplier: float):
        changed = tuple(
            replace(
                control,
                strength={**control.strength, "demand_multiplier": multiplier},
            )
            if control.story_id == "S14"
            else control
            for control in controls
        )
        monkeypatch.setattr(transaction_generation, "load_scenario_controls", lambda: changed)
        return transaction_generation.generate_orders_and_lines(small_configuration, small_tables)

    low_orders, low_lines = generated_with_strength(1.0)
    high_orders, high_lines = generated_with_strength(20.0)
    s14 = next(item for item in controls if item.story_id == "S14")

    def effective_dates(orders: pd.DataFrame) -> pd.Series:
        return orders["fulfilled_date"].where(
            orders["fulfilled_date"].notna(), orders["order_date"]
        )

    low_pre_ids = set(low_orders.loc[effective_dates(low_orders) < s14.start_date, "order_id"])
    high_pre_ids = set(high_orders.loc[effective_dates(high_orders) < s14.start_date, "order_id"])
    assert low_pre_ids == high_pre_ids
    pd.testing.assert_frame_equal(
        low_orders[low_orders["order_id"].isin(low_pre_ids)].reset_index(drop=True),
        high_orders[high_orders["order_id"].isin(high_pre_ids)].reset_index(drop=True),
    )
    pd.testing.assert_frame_equal(
        low_lines[low_lines["order_id"].isin(low_pre_ids)].reset_index(drop=True),
        high_lines[high_lines["order_id"].isin(high_pre_ids)].reset_index(drop=True),
    )

    s01_target = resolve_targets(controls, small_tables)["S01"]
    identity_columns = (
        "order_id",
        "customer_id",
        "order_date",
        "fulfilled_date",
        "order_status",
    )
    pd.testing.assert_frame_equal(
        low_orders.loc[low_orders["customer_id"] == s01_target, list(identity_columns)].reset_index(
            drop=True
        ),
        high_orders.loc[
            high_orders["customer_id"] == s01_target, list(identity_columns)
        ].reset_index(
            drop=True
        ),
    )

    product_category = (
        small_tables["products"][["product_id", "subcategory_id"]]
        .merge(
            small_tables["product_subcategories"][["subcategory_id", "category_id"]],
            on="subcategory_id",
            validate="many_to_one",
        )
        .set_index("product_id")["category_id"]
    )

    def eligible_target_units(orders: pd.DataFrame, lines: pd.DataFrame) -> int:
        event_dates = effective_dates(orders)
        eligible_ids = set(
            orders.loc[
                (orders["order_status"] == "fulfilled")
                & event_dates.between(s14.start_date, s14.end_date),
                "order_id",
            ]
        )
        eligible = lines[
            lines["order_id"].isin(eligible_ids)
            & (lines["product_id"].map(product_category) == "CAT-10")
        ]
        return int(eligible["quantity_ordered"].sum())

    assert eligible_target_units(high_orders, high_lines) > eligible_target_units(
        low_orders, low_lines
    )


def test_s14_detects_only_the_three_month_category_spike() -> None:
    control = next(item for item in load_scenario_controls() if item.story_id == "S14")
    rows = []
    spike_factor = float(control.strength["demand_multiplier"])
    year_factor = {2022: 0.90, 2023: 1.00, 2024: 1.05, 2025: 0.95, 2026: 1.10}
    for month in pd.date_range("2022-01-01", "2026-09-01", freq="MS"):
        baseline_units = (100 + 2 * month.month) * year_factor[month.year]
        in_spike = control.start_date <= month.date() <= control.end_date
        units = baseline_units * (spike_factor if in_spike else 1.0)
        rows.append((month.date(), month.date(), "CAT-10", units))
        if in_spike:
            rows.append((month.date(), month.date(), "CAT-01", 10_000.0))
    sales = pd.DataFrame(
        rows,
        columns=("fulfilled_date", "month", "category_id", "quantity"),
    )

    detected = _s14(
        control, "CAT-10", Evidence(sales, pd.DataFrame(), pd.DataFrame()), date(2026, 9, 30)
    )
    assert detected.status == "PASS"
    assert detected.baseline_sample_size == 54
    assert detected.exposed_sample_size == 3
    assert detected.observed_effect["false_positive_months"] == 0
    assert detected.confounding_overlap_diagnostics["flagged_true_months"] == 3

    without_spike = sales.copy()
    spike_rows = (without_spike["category_id"] == "CAT-10") & without_spike[
        "month"
    ].between(control.start_date, control.end_date)
    without_spike.loc[spike_rows, "quantity"] /= spike_factor
    undetected = _s14(
        control,
        "CAT-10",
        Evidence(without_spike, pd.DataFrame(), pd.DataFrame()),
        date(2026, 9, 30),
    )
    assert undetected.status == "FAIL"
    assert undetected.confounding_overlap_diagnostics["flagged_true_months"] == 0


def test_s15_uses_fixed_mature_incumbent_cohort() -> None:
    sales = pd.DataFrame(
        [
            (date(2024, 6, 1), "I1", "LV", 50.0, 500.0, 200.0),
            (date(2024, 7, 1), "I2", "LV", 50.0, 500.0, 200.0),
            (date(2025, 6, 1), "I1", "LV", 20.0, 300.0, 75.0),
            (date(2025, 7, 1), "I2", "LV", 20.0, 300.0, 75.0),
            (date(2025, 8, 1), "N1", "LV", 60.0, 1600.0, 100.0),
        ],
        columns=(
            "fulfilled_date",
            "customer_id",
            "home_country_code",
            "quantity",
            "net_sales_eur",
            "gross_profit_eur",
        ),
    )
    customers = pd.DataFrame(
        [
            ("I1", date(2020, 1, 1), "LV"),
            ("I2", date(2020, 1, 1), "LV"),
            ("N1", date(2025, 2, 1), "LV"),
        ],
        columns=("customer_id", "acquisition_date", "home_country_code"),
    )
    result = _s15(
        _control("S15"), "LV", Evidence(sales, pd.DataFrame(), customers), date(2026, 9, 30)
    )
    assert result.status == "PASS"
    assert result.observed_effect["revenue_growth"] == pytest.approx(1.2)
    assert result.observed_effect["incumbent_unit_activity_change"] == pytest.approx(-0.6)
    assert result.confounding_overlap_diagnostics["fixed_mature_incumbent_cohort"] == 2


def test_scenario_module_help_has_no_preload_warning() -> None:
    result = subprocess.run(
        [
            ".venv/bin/python",
            "-W",
            "error",
            "-m",
            "baltic_sales_intelligence.generation.scenario_validation",
            "--help",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "found in sys.modules" not in result.stderr
